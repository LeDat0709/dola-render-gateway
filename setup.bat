@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion
title Dola Studio - Cai dat
echo ===============================================
echo   DOLA STUDIO - CAI DAT (chay 1 lan duy nhat)
echo ===============================================
echo.

rem ---- 1. Kiem tra Python ----
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul || (
  echo [X] Chua co Python 3.
  echo     Tai tai: https://www.python.org/downloads/
  echo     LUU Y: phai tick "Add Python to PATH" luc cai.
  pause & exit /b 1
)
for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set PYVER=%%v
echo * Python !PYVER!
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>nul || (
  echo [X] Can Python 3.10 tro len ^(dang co !PYVER!^). patchright khong chay tren ban cu hon.
  pause & exit /b 1
)

rem ---- 2. Kiem tra Node ----
where npm >nul 2>nul || (
  echo [X] Chua co Node.js. Tai tai: https://nodejs.org ^(ban LTS^)
  pause & exit /b 1
)
for /f "tokens=*" %%v in ('node -v') do echo * Node %%v

rem ---- 3. Moi truong Python ----
echo.
echo * Tao .venv va cai thu vien Python ^(vai phut, dung tat cua so^)...
%PY% -m venv .venv || ( echo [X] Tao .venv that bai & pause & exit /b 1 )
call .venv\Scripts\python -m pip install --upgrade pip -q
call .venv\Scripts\python -m pip install -q -r requirements.txt || ( echo [X] Cai thu vien Python that bai & pause & exit /b 1 )

echo * Tai Chromium cho patchright ^(~150MB, lau nhat o buoc nay^)...
call .venv\Scripts\python -m patchright install chromium || ( echo [X] Tai Chromium that bai - kiem tra mang & pause & exit /b 1 )

rem ---- 4. Electron ----
echo.
echo * Cai Electron...
call npm --prefix desktop install --no-audit --no-fund || ( echo [X] npm install that bai & pause & exit /b 1 )

rem ---- 5. Giao dien ----
if exist "desktop\renderer\dist\index.html" (
  echo * Giao dien: da co san ban dung roi, bo qua buoc build.
) else (
  echo * Khong thay giao dien dung san - dang build lai...
  call npm --prefix desktop run setup:ui --no-audit --no-fund
  call npm --prefix desktop run build:ui || ( echo [!] Build giao dien that bai - app se chay giao dien du phong & pause )
)

rem ---- 6. Cau hinh rieng cua may ----
if not exist .env.local (
  for /f "delims=" %%k in ('.venv\Scripts\python -c "import secrets;print(secrets.token_hex(16))"') do set KEY=%%k
  for /f "delims=" %%a in ('.venv\Scripts\python -c "import secrets;print(secrets.token_hex(16))"') do set ADM=%%a
  > .env.local echo DOLA_HOST=127.0.0.1
  >> .env.local echo DOLA_PORT=8000
  >> .env.local echo DOLA_API_KEYS=dola_!KEY!
  >> .env.local echo DOLA_ADMIN_KEY=!ADM!
  >> .env.local echo # Proxy: de TRONG neu ban dung VPN toan he thong ^(vi du ExpressVPN^).
  >> .env.local echo # Chi dien khi dung proxy cuc bo, vi du: DOLA_PROXY=http://127.0.0.1:7890
  >> .env.local echo # Dola chan theo vung IP - exit node phai o NHAT hoac HAN.
  >> .env.local echo DOLA_PROXY=
  echo * Da tao .env.local ^(khoa rieng cua may nay, khong chia se^)
) else (
  echo * Da co .env.local - giu nguyen.
)

echo.
echo ===============================================
echo   [OK] CAI XONG
echo ===============================================
echo   1. Bat VPN ^(exit node Nhat hoac Han^) truoc khi tao video.
echo   2. Bam doi run.bat de mo app.
echo.
pause
