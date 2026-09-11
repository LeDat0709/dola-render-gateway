#!/usr/bin/env bash
# Cài gateway lên VPS Ubuntu 22.04/24.04 đặt ở Nhật hoặc Hàn (Vultr Tokyo/Osaka/Seoul…) để nhiều máy ở VN dùng chung.
# Chạy trên VPS (user thường có sudo). Chạy lại = cập nhật code + khởi động lại.
#   bash <(curl -fsSL https://raw.githubusercontent.com/LeDat0709/dola-render-gateway/main/deploy/vps-setup.sh)
# Xong nó in 3 dòng (địa chỉ, API key, Admin key) → dán vào app: Cài đặt → "Máy chủ render từ xa".
set -euo pipefail
REPO="${DOLA_REPO:-https://github.com/LeDat0709/dola-render-gateway.git}"
DIR="${DOLA_DIR:-$HOME/dola-render-gateway}"
PORT="${DOLA_PORT:-8000}"

echo "==> Gói hệ thống + Google Chrome (worker mở Chrome thật — giả lập tốt hơn Chromium kèm theo)"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-pip curl openssl >/dev/null
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  sudo apt-get install -y -qq /tmp/chrome.deb >/dev/null
  rm -f /tmp/chrome.deb
fi

echo "==> Mã nguồn + Python"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Khoá + .env.local (chỉ sinh lần đầu; chạy lại không đổi khoá)"
PUBLIC_IP="$(curl -fsS --max-time 8 https://api.ipify.org || hostname -I | awk '{print $1}')"
if [ ! -f .env.local ]; then
  # dola.com chỉ mở cho IP Nhật/Hàn. VPS ở nơi khác (VN…) phải đi qua proxy Nhật, nếu không mọi nick lỗi ngay.
  COUNTRY="$(curl -fsS --max-time 8 "http://ip-api.com/json/$PUBLIC_IP?fields=countryCode" | sed -n 's/.*"countryCode":"\([A-Z]*\)".*/\1/p')"
  if [ -z "${DOLA_PROXY:-}" ] && [ "$COUNTRY" != "JP" ] && [ "$COUNTRY" != "KR" ]; then
    echo "❌ VPS này ở '${COUNTRY:-?}' (IP $PUBLIC_IP), không phải Nhật/Hàn → dola.com sẽ chặn."
    echo "   Chạy lại kèm proxy Nhật/Hàn:  DOLA_PROXY=http://user:pass@host:port bash <(curl -fsSL .../deploy/vps-setup.sh)"
    exit 1
  fi
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
DOLA_MAX_CONCURRENCY=2
DOLA_LOGIN_CONCURRENCY=2
EOF
  chmod 600 .env.local
fi

echo "==> systemd: dola-gateway (tự chạy lại khi lỗi và khi VPS khởi động)"
sudo tee /etc/systemd/system/dola-gateway.service >/dev/null <<EOF
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
sudo systemctl daemon-reload
sudo systemctl enable dola-gateway >/dev/null
sudo systemctl restart dola-gateway

sleep 4
if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null; then
  echo
  echo "✅ Gateway đang chạy. Dán 3 dòng này vào app (Cài đặt → Máy chủ render từ xa):"
  echo "   Địa chỉ  : http://$PUBLIC_IP:$PORT"
  echo "   API key  : $(grep '^DOLA_API_KEYS=' .env.local | cut -d= -f2-)"
  echo "   Admin key: $(grep '^DOLA_ADMIN_KEY=' .env.local | cut -d= -f2-)"
  echo
  echo "Mở cổng $PORT trên firewall của nhà cung cấp VPS (Vultr: Firewall → TCP $PORT) nếu chưa."
  echo "Xem log: journalctl -u dola-gateway -f"
else
  echo "❌ Gateway chưa lên. Xem: journalctl -u dola-gateway -n 50"; exit 1
fi
