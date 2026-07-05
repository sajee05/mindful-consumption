@echo off
title Mindful Consumption Setup
echo ☕ Brewing Mindful Consumption Setup...
echo.

:: Define the standard user-app installation directory
set "INSTALL_DIR=%LOCALAPPDATA%\Programs\MindfulConsumption"
mkdir "%INSTALL_DIR%" 2>nul

echo [1/3] Downloading the latest release from GitHub...
curl -L -o "%INSTALL_DIR%\MindfulConsumption.exe" "https://github.com/sajee05/mindful-consumption/releases/latest/download/MindfulConsumption.exe"

echo [2/3] Adding to Windows Startup (runs silently in background)...
powershell "$s=(New-Object -COM WScript.Shell).CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MindfulConsumption.lnk');$s.TargetPath='%INSTALL_DIR%\MindfulConsumption.exe';$s.WorkingDirectory='%INSTALL_DIR%';$s.Save()"

echo [3/3] Starting the application...
start "" "%INSTALL_DIR%\MindfulConsumption.exe"

echo.
echo ✅ Setup Complete! 
echo Look for the blue circle icon in your bottom-right system tray.
timeout /t 5 >nul