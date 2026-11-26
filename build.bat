@echo off
rem Build Vaultline.exe and vaultline-cli.exe into .\dist (or the folder given as the first argument).
rem Needs Python 3.12+ from python.org. Double-click, or run:  build.bat
setlocal
cd /d "%~dp0"
set "OUT=%~1"
if "%OUT%"=="" set "OUT=dist"

if not exist ".venv\Scripts\python.exe" (
    echo Creating a private Python environment in .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto :fail

pyinstaller --noconfirm --onefile --windowed --name Vaultline --icon assets\vaultline.ico ^
    --distpath "%OUT%" --workpath build\pyinstaller ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf ^
    run.py || goto :fail
pyinstaller --noconfirm --onefile --console --name vaultline-cli ^
    --distpath "%OUT%" --workpath build\pyinstaller ^
    --exclude-module PySide6 --exclude-module pykeepass --exclude-module lxml ^
    run_cli.py || goto :fail

echo.
echo Done: %OUT%\Vaultline.exe and %OUT%\vaultline-cli.exe
if "%~1"=="" pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.12 or newer from https://www.python.org and try again.
if "%~1"=="" pause
exit /b 1

:fail
echo Build failed. See the messages above.
if "%~1"=="" pause
exit /b 1
