@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --collect-data customtkinter --name Mikroskop-Capture mikroskop_capture.py
echo.
echo Fertig: dist\Mikroskop-Capture.exe
pause
