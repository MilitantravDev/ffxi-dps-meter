@echo off
rem Builds ..\dist\DPSMeter.exe from the Python sources.
rem Requires Python 3.10+ and: python -m pip install pyinstaller
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name DPSMeter --icon "%~dp0dpsmeter.ico" --add-data "%~dp0dpsmeter.ico;." ^
  --distpath "%~dp0..\dist" --workpath "%~dp0build" --specpath "%~dp0build" ^
  dpsmeter.py
if errorlevel 1 (echo Build failed & exit /b 1)
rmdir /s /q build 2>nul
echo Built ..\dist\DPSMeter.exe
