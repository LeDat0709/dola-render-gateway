#!/usr/bin/env bash
# Cài gateway lên VPS Ubuntu 20.04+ (x86-64) để nhiều máy ở VN dùng chung. VPS nên ở Nhật/Hàn;
# ở nơi khác (VN…) BẮT BUỘC truyền proxy Nhật/Hàn qua DOLA_PROXY=.
# Chạy trên VPS (user thường có sudo, hoặc root). Chạy lại = cập nhật code + đổi cổng/proxy + khởi động lại.
#   bash <(curl -fsSL https://raw.githubusercontent.com/LeDat0709/dola-render-gateway/main/deploy/vps-setup.sh)
#   DOLA_PORT=8010 DOLA_PROXY=http://user:pass@host:port bash <(curl -fsSL .../deploy/vps-setup.sh)
# Xong nó in 3 dòng (địa chỉ, API key, Admin key) → dán vào app: Cài đặt → "Máy chủ render từ xa".
set -euo pipefail
REPO="${DOLA_REPO:-https://github.com/LeDat0709/dola-render-gateway.git}"
DIR="${DOLA_DIR:-$HOME/dola-render-gateway}"
PORT="${DOLA_PORT:-8000}"
SUDO="$([ "$(id -u)" = 0 ] && echo "" || echo sudo)"

echo "==> Cổng $PORT có rảnh không"
OWNER="$($SUDO ss -ltnp 2>/dev/null | awk -v p=":$PORT" 'index($4, p) == length($4) - length(p) + 1 {print $NF}' | head -1)"
if [ -n "$OWNER" ] && ! printf '%s' "$OWNER" | grep -q "python\|uvicorn"; then
  echo "❌ Cổng $PORT đang bị dịch vụ khác chiếm: $OWNER"
  echo "   Chạy lại với cổng khác, ví dụ:  DOLA_PORT=8010 bash <(curl -fsSL .../deploy/vps-setup.sh)"
  exit 1
fi

echo "==> Gói hệ thống + Google Chrome (worker mở Chrome thật — giả lập tốt hơn Chromium kèm theo)"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq git python3 python3-venv python3-pip curl openssl >/dev/null
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  $SUDO apt-get install -y -qq /tmp/chrome.deb >/dev/null
  rm -f /tmp/chrome.deb
fi

echo "==> Python ≥ 3.10 (code dùng 'str | None' ở chữ ký hàm; Ubuntu 20.04 chỉ có 3.8)"
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  # Ưu tiên uv: tải CPython 3.11 dựng sẵn từ GitHub — VPS VN hay không tới được archive.ubuntu.com
  # (gặp 11/9: "Connection failed [IP: 185.125.190.83]") nên deadsnakes chỉ là dự phòng.
  echo "   python3 hệ thống quá cũ ($(python3 --version 2>&1)) → tải Python 3.11 bằng uv (từ GitHub, không cần kho Ubuntu)"
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || true
  fi
  if command -v uv >/dev/null && uv python install 3.11 >/dev/null 2>&1; then
    PY="$(uv python find 3.11)"
  else
    echo "   uv không tải được → thử deadsnakes (cần tới được kho Ubuntu)"
    $SUDO apt-get install -y -qq software-properties-common >/dev/null
    $SUDO add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq python3.11 python3.11-venv python3.11-dev >/dev/null
    PY=python3.11
  fi
fi
echo "   dùng $PY ($("$PY" --version 2>&1))"

echo "==> Mã nguồn + venv"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"
# venv tạo bởi lần chạy trước bằng python cũ → bỏ, tạo lại (chỉ là thư viện, cài lại được).
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "   .venv cũ là $(.venv/bin/python --version 2>&1) → tạo lại"
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> .env.local (khoá chỉ sinh lần đầu; cổng / địa chỉ / proxy cập nhật theo lần chạy này)"
PUBLIC_IP="$(curl -fsS --max-time 8 https://api.ipify.org || hostname -I | awk '{print $1}')"
if [ ! -f .env.local ]; then
  # dola.com chỉ mở cho IP Nhật/Hàn. VPS ở nơi khác (VN…) phải đi qua proxy Nhật, nếu không mọi nick lỗi ngay.
  COUNTRY="$(curl -fsS --max-time 8 "http://ip-api.com/json/$PUBLIC_IP?fields=countryCode" | sed -n 's/.*"countryCode":"\([A-Z]*\)".*/\1/p')"
  if [ -z "${DOLA_PROXY:-}" ] && [ "$COUNTRY" != "JP" ] && [ "$COUNTRY" != "KR" ]; then
    echo "❌ VPS này ở '${COUNTRY:-?}' (IP $PUBLIC_IP), không phải Nhật/Hàn → dola.com sẽ chặn."
    echo "   Chạy lại kèm proxy Nhật/Hàn:  DOLA_PROXY=http://user:pass@host:port bash <(curl -fsSL .../deploy/vps-setup.sh)"
    exit 1
  fi
  # Mỗi Chrome ≈ 1.2 GB lúc gửi lệnh. Số luồng theo RAM trống, 1–4. Chỉnh tay trong app sau nếu muốn.
  AVAIL_MB="$(free -m | awk '/^Mem:/{print $7}')"
  CONC=$(( ${AVAIL_MB:-0} / 1200 )); [ "$CONC" -lt 1 ] && CONC=1; [ "$CONC" -gt 4 ] && CONC=4
  cat > .env.local <<EOF
# Sinh bởi deploy/vps-setup.sh. KHÔNG commit, KHÔNG chia sẻ.
DOLA_HOST=0.0.0.0
DOLA_PORT=$PORT
DOLA_PUBLIC_BASE=http://$PUBLIC_IP:$PORT
DOLA_API_KEYS=sk-$(openssl rand -hex 20)
DOLA_ADMIN_KEY=$(openssl rand -hex 20)
# VPS ở Nhật/Hàn → để trống (nối thẳng). VPS nơi khác → proxy Nhật/Hàn (truyền qua DOLA_PROXY=... lúc cài).
DOLA_PROXY=${DOLA_PROXY:-}
DOLA_HEADLESS=1
DOLA_MAX_CONCURRENCY=$CONC
DOLA_LOGIN_CONCURRENCY=$CONC
EOF
  chmod 600 .env.local
  echo "   RAM trống ${AVAIL_MB:-?} MB → $CONC luồng gửi cùng lúc"
fi
# Chạy lại với DOLA_PORT / DOLA_PROXY khác → ghi đè đúng 3 khoá này, giữ nguyên khoá bí mật.
upsert() {
  local v; v="$(printf '%s' "$2" | sed 's/[&|\\]/\\&/g')"
  if grep -q "^$1=" .env.local; then sed -i "s|^$1=.*|$1=$v|" .env.local; else echo "$1=$2" >> .env.local; fi
}
upsert DOLA_PORT "$PORT"
upsert DOLA_PUBLIC_BASE "http://$PUBLIC_IP:$PORT"
[ -n "${DOLA_PROXY:-}" ] && upsert DOLA_PROXY "$DOLA_PROXY"

echo "==> systemd: dola-gateway (tự chạy lại khi lỗi và khi VPS khởi động)"
$SUDO tee /etc/systemd/system/dola-gateway.service >/dev/null <<EOF
[Unit]
Description=Dola render gateway
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable dola-gateway >/dev/null
$SUDO systemctl restart dola-gateway

sleep 4
# Phải là JSON của CHÍNH gateway (có "accounts"), kẻo dịch vụ khác cùng cổng "qua mặt" (đã gặp: Django/gunicorn ở 8000).
if curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"accounts"'; then
  echo
  echo "✅ Gateway đang chạy. Dán 3 dòng này vào app (Cài đặt → Máy chủ render từ xa):"
  echo "   Địa chỉ  : http://$PUBLIC_IP:$PORT"
  echo "   API key  : $(grep '^DOLA_API_KEYS=' .env.local | cut -d= -f2-)"
  echo "   Admin key: $(grep '^DOLA_ADMIN_KEY=' .env.local | cut -d= -f2-)"
  echo "   Proxy    : $(grep '^DOLA_PROXY=' .env.local | cut -d= -f2- | sed 's#//[^@]*@#//***@#')"
  echo
  echo "Mở cổng $PORT trên firewall của nhà cung cấp VPS nếu chưa. Xem log: journalctl -u dola-gateway -f"
else
  echo "❌ Gateway chưa lên ở cổng $PORT. Xem: journalctl -u dola-gateway -n 50 --no-pager"; exit 1
fi
