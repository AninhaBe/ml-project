@echo off
taskkill /F /IM chrome.exe /T 2>nul
timeout /t 3 /nobreak >nul
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data" --profile-directory="Profile 9" --no-first-run --no-default-browser-check
