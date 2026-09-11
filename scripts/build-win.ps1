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

Write-Host "==> Cài thư viện Python vào runtime"
& "$py\python.exe" -m pip install --no-warn-script-location --upgrade -r (Join-Path $root "requirements.txt")

Write-Host "==> Tải Chromium cho patchright (đóng gói kèm, máy đích không cần cài gì)"
$env:PLAYWRIGHT_BROWSERS_PATH = $browsers
& "$py\python.exe" -m patchright install chromium

Write-Host "==> Build giao diện + đóng gói .exe"
npm --prefix (Join-Path $root "desktop\renderer") ci
npm --prefix (Join-Path $root "desktop\renderer") run build
npm --prefix (Join-Path $root "desktop") ci
npm --prefix (Join-Path $root "desktop") run dist:win

Write-Host "==> Xong. File .exe nằm ở: $(Join-Path $root 'dist-app')"
