@echo off
setlocal
rem C3 BLE direct is the default; S3 serial and C3 WiFi are optional.
rem Use the same virtualenv created by SETUP_STUDIO.ps1, which installs bleak.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [E-Brush] Python environment not found. Run SETUP_STUDIO.ps1 first.
  exit /b 1
)
cd /d "%~dp0studio"
"%PY%" -u main.py --camera-backend shm --brush-transport ble %*
exit /b %ERRORLEVEL%
