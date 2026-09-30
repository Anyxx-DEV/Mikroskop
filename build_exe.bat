@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
set EXTRA=--add-data "assets;assets"
if exist fonts set EXTRA=%EXTRA% --add-data "fonts;fonts"
python -m PyInstaller --noconfirm --onefile --windowed --collect-data customtkinter %EXTRA% --name Mikroskop-Capture mikroskop_capture.py
echo.
echo Fertig: dist\Mikroskop-Capture.exe
pause
