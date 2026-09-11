@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Dola Studio
if not exist .venv (
  echo [X] Chua cai dat. Bam doi setup.bat truoc.
  pause & exit /b 1
)
if not exist desktop\node_modules (
  echo [X] Thieu Electron. Bam doi setup.bat truoc.
  pause & exit /b 1
)
if not exist desktop\renderer\dist\index.html (
  echo [!] Khong thay giao dien moi - app se chay giao dien du phong cu.
)
call npm --prefix desktop start
