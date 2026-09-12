# Dựng bản .exe một-cú-bấm cho Windows: Python di động + Chromium + Electron, gói hết vào 1 file.
# Chạy TRÊN WINDOWS:  powershell -ExecutionPolicy Bypass -File scripts\build-win.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "build"
$py = Join-Path $build "python"
$browsers = Join-Path $build "browsers"
$pyver = "3.12.7"

New-Item -ItemType Directory -Force -Path $build | Out-Null

if (-not (Test-Path "$py\python.exe")) {
  Write-Host "==> Tải Python $pyver (bản embeddable, chạy không cần cài)"
  $zip = Join-Path $build "python-embed.zip"
  Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $zip
  Expand-Archive $zip -DestinationPath $py -Force
  # Bản embeddable tắt sẵn site-packages → không bật thì pip cài xong vẫn không import được.
  $pth = (Get-ChildItem "$py\python*._pth" | Select-Object -First 1).FullName
  (Get-Content $pth) -replace '^#\s*import site', 'import site' | Set-Content $pth
  Add-Content $pth "Lib\site-packages"
  Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $build "get-pip.py")
  & "$py\python.exe" (Join-Path $build "get-pip.py") --no-warn-script-location
}

# Python embeddable có file ._pth chạy "isolated": sys.path CHỈ gồm các dòng trong ._pth — không có
# thư mục của script, không có thư mục làm việc, bỏ qua PYTHONPATH. Các script Electron gọi
# (import_cookies.py, login_profile.py, clear_cookies.py…) nằm ở resources\app-python và được chạy
# từ thư mục dữ liệu → không có dòng này thì "ModuleNotFoundError: No module named 'browser'".
# Đường dẫn tính từ vị trí file ._pth (resources\python) → ..\app-python.
$pth = (Get-ChildItem "$py\python*._pth" | Select-Object -First 1).FullName
if (-not (Select-String -Path $pth -Pattern 'app-python' -Quiet)) { Add-Content $pth "..\app-python" }

Write-Host "==> Cài thư viện Python vào runtime"
& "$py\python.exe" -m pip install --no-warn-script-location --upgrade -r (Join-Path $root "requirements.txt")

Write-Host "==> Tải Chromium cho patchright (đóng gói kèm, máy đích không cần cài gì)"
$env:PLAYWRIGHT_BROWSERS_PATH = $browsers
& "$py\python.exe" -m patchright install chromium

# Chốt kiểm tra: nạp thử server bằng đúng runtime sắp đóng gói. Nếu thiếu/lệch module thì
# build phải đỏ ở đây, chứ không đóng gói ra một file .exe crash ngay khi mở.
Write-Host "==> Kiểm tra runtime nạp được server"
& "$py\python.exe" (Join-Path $root "scripts\smoke_import.py")
if ($LASTEXITCODE -ne 0) { throw "Runtime khong nap duoc server.py - thieu module hoac module cu." }

Write-Host "==> Build giao diện + đóng gói .exe"
npm --prefix (Join-Path $root "desktop\renderer") ci
npm --prefix (Join-Path $root "desktop\renderer") run build
npm --prefix (Join-Path $root "desktop") ci
npm --prefix (Join-Path $root "desktop") run dist:win

# Chốt sau đóng gói: đúng python.exe đã đóng gói, chạy từ một thư mục lạ (như DATA_DIR lúc app
# chạy thật), phải tự thấy browser.py trong resources\app-python. Đỏ ở đây = ._pth thiếu app-python.
Write-Host "==> Kiểm tra runtime đã đóng gói thấy app-python"
$packedPy = Join-Path $root "dist-app\win-unpacked\resources\python\python.exe"
Push-Location $env:TEMP
& $packedPy -c "import browser, cookie_service, facebook_login; print('packaged runtime OK')"
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { throw "python.exe da dong goi khong import duoc browser.py - kiem tra dong ..\app-python trong python*._pth" }

Write-Host "==> Xong. File .exe nằm ở: $(Join-Path $root 'dist-app')"
