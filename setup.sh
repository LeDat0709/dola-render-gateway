#!/usr/bin/env bash
# Cài Dola Studio trên máy mới. Chạy: bash setup.sh
set -e
cd "$(dirname "$0")"
echo "== Dola Studio · cài đặt =="

# 1) Python 3
PY=$(command -v python3.12 || command -v python3 || true)
[ -z "$PY" ] && { echo "❌ Chưa có Python 3. Cài: https://www.python.org/downloads/ (hoặc: brew install python@3.12)"; exit 1; }
echo "• Python: $($PY --version)"

# 2) Node.js
command -v npm >/dev/null || { echo "❌ Chưa có Node.js. Cài: https://nodejs.org (bản LTS)"; exit 1; }
echo "• Node: $(node --version)"

# 3) môi trường Python + thư viện
echo "• Tạo .venv + cài thư viện Python (vài phút)…"
"$PY" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

# 4) trình duyệt ẩn cho patchright (Chromium ~150MB)
echo "• Tải Chromium cho patchright…"
./.venv/bin/python -m patchright install chromium

# 5) thư viện Electron
echo "• Cài Electron…"
npm --prefix desktop install --no-audit --no-fund

# 6) .env.local với khoá ngẫu nhiên (nếu chưa có)
if [ ! -f .env.local ]; then
  KEY=$(./.venv/bin/python -c "import secrets;print('dola_'+secrets.token_hex(16))")
  ADM=$(./.venv/bin/python -c "import secrets;print(secrets.token_hex(16))")
  printf 'DOLA_HOST=127.0.0.1\nDOLA_API_KEYS=%s\nDOLA_ADMIN_KEY=%s\nDOLA_PROXY=\n' "$KEY" "$ADM" > .env.local
  echo "• Đã tạo .env.local (khoá riêng của máy này)"
fi

echo ""
echo "✅ Xong! Chạy app:  bash run.sh   (hoặc: npm --prefix desktop start)"
