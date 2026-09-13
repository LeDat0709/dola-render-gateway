#!/usr/bin/env bash
# Đóng gói bản GỬI ĐI (Win+Mac): loại nick, khoá, venv, node_modules, video, log, file nội bộ.
set -e
cd "$(dirname "$0")"
STAGE="$(mktemp -d)/DolaStudio"
mkdir -p "$STAGE"
rsync -a ./ "$STAGE"/ \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '.env.local' --exclude '.env' \
  --exclude 'accounts' --exclude 'downloads' --exclude 'logs' \
  --exclude '*.db' --exclude '*.mp4' --exclude '*.png' --exclude '.DS_Store' \
  --exclude 'cookies.txt' --exclude 'cookies.json' --exclude 'cookies*.txt' \
  --exclude '*cookie*.txt' --exclude '*cookie*.json' --exclude '*.session' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '*.exe' \
  --exclude 'test_*.py' --exclude 'check_*.py' --exclude 'fix_*.py' \
  --exclude 'inspect_*.py' --exclude 'clean_*.py' --exclude 'finish_login.py' \
  --exclude 'fb_login.py' --exclude 'dola.py' --exclude 'standalone_video_generator.py' \
  --exclude '*_ANALYSIS.md' --exclude 'VAN_DE_*.md' --exclude 'FB_README.md' \
  --exclude 'make-share.sh' --exclude 'video.sh'
# template watermark bị *.png loại -> chép lại (cần cho tự xoá logo)
mkdir -p "$STAGE/assets"; cp assets/dola_watermark.png "$STAGE/assets/" 2>/dev/null || true

# QUÉT rò rỉ bí mật
LEAK=$(grep -rIlE -e "xs=[0-9]+:[A-Za-z0-9_-]{6,}" -e "EAAAA[A-Za-z0-9]{10,}" -e "DOLA_API_KEYS=dola_[0-9a-f]{12,}" -e "c_user=6[0-9]{9,}" -e "sessionid=[A-Za-z0-9%_-]{8,}" -e "msToken=[A-Za-z0-9_-]{15,}" "$STAGE" 2>/dev/null || true)
if [ -n "$LEAK" ]; then echo "❌ CÓ THỂ RÒ RỈ bí mật, DỪNG:"; echo "$LEAK"; exit 1; fi

OUT="$HOME/Desktop/DolaStudio-share-$(date +%Y%m%d-%H%M).zip"
( cd "$(dirname "$STAGE")" && zip -qr "$OUT" DolaStudio )
echo "✅ Gói xong: $OUT"; du -h "$OUT"
echo "Nội dung chính:"; ls "$STAGE" | tr '\n' ' '; echo
