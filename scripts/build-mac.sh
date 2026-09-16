#!/usr/bin/env bash
# Dựng bản macOS (.app + .zip) cho Dola Studio: Python di động + Chromium + Electron, tự chứa —
# chép sang Mac khác CÙNG kiến trúc (Apple Silicon hoặc Intel) là chạy, không cần cài Python/Node.
# Chạy TRÊN MAC:  bash scripts/build-mac.sh
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
build="$root/build"; py="$build/python"; browsers="$build/browsers"
# astral-sh/python-build-standalone: CPython tự chứa, chép đi đâu cũng chạy (venv thì không).
PBS_TAG=20260901; PYVER=3.12.14
case "$(uname -m)" in
  arm64)  arch=aarch64; ebarch=arm64 ;;
  x86_64) arch=x86_64;  ebarch=x64 ;;
  *) echo "Kiến trúc lạ: $(uname -m)"; exit 1 ;;
esac
mkdir -p "$build"

if [ ! -x "$py/bin/python3" ]; then
  echo "==> Tải Python $PYVER (bản tự chứa cho $arch)"
  tgz="$build/python-$arch.tar.gz"
  curl -fL --retry 3 -o "$tgz" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PYVER+$PBS_TAG-$arch-apple-darwin-install_only.tar.gz"
  rm -rf "$py"; tar -xzf "$tgz" -C "$build"     # tar có sẵn thư mục python/ → build/python
fi

echo "==> Cài thư viện Python vào runtime"
# Chốt chặn rò rỉ: bộ lọc đóng gói là "**/*.py" trừ test_/check_/fix_, nên MỌI file .py lạ ở gốc repo đều chui
# vào app. Đã từng có script VPS kèm mật khẩu root nằm đây. File chưa được git theo dõi = không thuộc tool →
# dừng build, bắt dọn trước, thay vì lặng lẽ gói nó cho cả team.
la=$(cd "$root" && git ls-files --others --exclude-standard -- '*.py' 2>/dev/null)
if [ -n "$la" ]; then
  echo "DỪNG: có file .py chưa được git theo dõi ở repo — chúng sẽ bị đóng gói vào app:" >&2
  echo "$la" | sed 's/^/  /' >&2
  echo "Hãy xoá, chuyển đi, hoặc 'git add' nếu thật sự thuộc tool." >&2
  exit 1
fi

"$py/bin/python3" -m pip install -q --disable-pip-version-check --upgrade -r "$root/requirements.txt"

echo "==> Tải Chromium cho patchright (đóng gói kèm — Mac chưa có Chrome vẫn chạy)"
PLAYWRIGHT_BROWSERS_PATH="$browsers" "$py/bin/python3" -m patchright install chromium

# Chốt kiểm tra trước khi đóng gói: nạp thử server bằng đúng runtime sắp mang đi.
echo "==> Kiểm tra runtime nạp được server"
"$py/bin/python3" "$root/scripts/smoke_import.py"

# Số phiên bản lấy từ tag git mới nhất (như CI Windows), không sửa package.json.
ver=$(git -C "$root" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//'); ver=${ver:-1.0.0}
echo "==> Build giao diện + đóng gói .app (phiên bản $ver)"
[ -d "$root/desktop/renderer/node_modules" ] || npm --prefix "$root/desktop/renderer" ci
npm --prefix "$root/desktop/renderer" run build
[ -d "$root/desktop/node_modules" ] || npm --prefix "$root/desktop" ci
( cd "$root/desktop" && CSC_IDENTITY_AUTO_DISCOVERY=false \
    ./node_modules/.bin/electron-builder --mac dir -c.extraMetadata.version="$ver" )

app=$(ls -d "$root"/dist-app/mac*/"Dola Studio.app" | head -1)
# Không có chứng chỉ Apple → ký ad-hoc: Apple Silicon từ chối chạy binary không chữ ký, và
# electron-builder đổi tên/Info.plist làm chữ ký gốc của Electron lệch → ký lại toàn bộ.
echo "==> Ký ad-hoc: $app"
codesign --force --deep --sign - "$app"
codesign --verify --deep --strict "$app"

# Chốt sau đóng gói: đúng python3 trong .app, chạy từ thư mục lạ (như lúc app chạy thật), phải
# import được browser.py. Tới được dòng "usage:" nghĩa là mọi import của script đã xong.
res="$app/Contents/Resources"
out=$(cd /tmp && "$res/python/bin/python3" "$res/app-python/import_cookies.py" 2>&1 || true)
case "$out" in
  *usage:*) echo "==> Runtime trong .app OK" ;;
  *) echo "$out"; echo "python3 trong .app không chạy được import_cookies.py"; exit 1 ;;
esac

zip="$root/dist-app/DolaStudio-$ver-mac-$ebarch.zip"
rm -f "$zip"; ditto -c -k --keepParent "$app" "$zip"     # ditto giữ chữ ký; zip thường thì không
# .dmg bằng hdiutil có sẵn của macOS (không cần dmg-builder): mở ra kéo app vào Applications.
dmg="$root/dist-app/DolaStudio-$ver-mac-$ebarch.dmg"
rm -f "$dmg"; hdiutil create -quiet -volname "Dola Studio" -srcfolder "$app" -ov -format UDZO "$dmg"
echo "==> Xong:"
echo "    $app"
echo "    $zip  ($(du -h "$zip" | cut -f1))"
echo "    $dmg  ($(du -h "$dmg" | cut -f1))"
echo "Mở lần đầu trên Mac khác nếu bị chặn: xattr -dr com.apple.quarantine \"/Applications/Dola Studio.app\""
