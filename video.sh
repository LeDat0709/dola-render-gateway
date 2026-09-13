#!/bin/sh
# Gui task tao video hoac poll trang thai. Key doc tu .env.local -> khong bao gio phai paste.
#   ./video.sh "mo ta video"        -> gui task (mac dinh 10s, 9:16, seedance-2.0)
#   ./video.sh video_<id>           -> xem trang thai
#   DURATION=15 RATIO=16:9 ./video.sh "mo ta"   -> doi thoi luong / ti le
cd "$(dirname "$0")" || exit 1
KEY=$(grep '^DOLA_API_KEYS=' .env.local | cut -d= -f2- | cut -d, -f1)
BASE=http://127.0.0.1:8000
case "$1" in
  video_*)
    curl -s "$BASE/v1/videos/$1" -H "Authorization: Bearer $KEY"; echo ;;
  "")
    echo "usage: $0 \"prompt\"   |   $0 video_<id>" >&2; exit 2 ;;
  *)
    PROMPT_JSON=$(printf '%s' "$1" | .venv/bin/python -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
    curl -s -X POST "$BASE/v1/videos/generations" \
      -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "{\"model\":\"seedance-2.0\",\"prompt\":$PROMPT_JSON,\"duration\":${DURATION:-10},\"ratio\":\"${RATIO:-9:16}\"}"
    echo ;;
esac
