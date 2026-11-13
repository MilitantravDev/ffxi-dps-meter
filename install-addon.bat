@echo off
rem Copies the DPSLink addon from this repo into Windower\addons\DPSLink.
rem Usage: install-addon.bat ["C:\path\to\Windower"]
rem A real copy is used on purpose: a junction/symlink into a OneDrive-synced Windower
rem folder can stall file reads on the game thread and freeze the client.
setlocal
set "WIN=%~1"
if "%WIN%"=="" if exist "%USERPROFILE%\OneDrive\Documents\Windower\windower.exe" set "WIN=%USERPROFILE%\OneDrive\Documents\Windower"
if "%WIN%"=="" if exist "%USERPROFILE%\Documents\Windower\windower.exe" set "WIN=%USERPROFILE%\Documents\Windower"
if "%WIN%"=="" if exist "C:\Program Files (x86)\Windower\windower.exe" set "WIN=C:\Program Files (x86)\Windower"
if "%WIN%"=="" (
  echo Could not find Windower. Run: install-addon.bat "C:\path\to\Windower"
  exit /b 1
)
if not exist "%WIN%\addons" (echo "%WIN%\addons" not found & exit /b 1)

rem Refuse to write through an existing junction/symlink.
fsutil reparsepoint query "%WIN%\addons\DPSLink" >nul 2>&1 && (
  echo "%WIN%\addons\DPSLink" is a link. Remove it first with: rmdir "%WIN%\addons\DPSLink"
  exit /b 1
)

if not exist "%WIN%\addons\DPSLink" mkdir "%WIN%\addons\DPSLink"
copy /Y "%~dp0DPSLink\DPSLink.lua" "%WIN%\addons\DPSLink\DPSLink.lua" >nul || exit /b 1
echo Installed DPSLink to "%WIN%\addons\DPSLink". In game: //lua reload dpslink
