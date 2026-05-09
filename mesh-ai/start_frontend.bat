@echo off
:: Refresh PATH so Node.js is visible in this session
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"Path\",\"Machine\")"') do set "PATH=%%i;%PATH%"

:: Allow PowerShell scripts (npm.ps1) to run
powershell -NoProfile -Command "Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force" >nul 2>&1

cd /d "%~dp0frontend"
echo Installing dependencies...
call npm install
echo.
echo Starting MeshAI frontend on http://localhost:3000
echo.
call npm run dev
pause
