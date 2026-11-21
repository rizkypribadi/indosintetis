@echo off
rem Indonesia Sintetis - buka dashboard dengan satu klik.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Menyiapkan lingkungan Python untuk pertama kali...
  python -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 atau lebih baru belum terpasang. Unduh dari python.org lalu jalankan lagi.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m sintetis status
echo.
echo Dashboard: http://127.0.0.1:8000  (tutup jendela ini untuk berhenti)
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m sintetis serve
pause
