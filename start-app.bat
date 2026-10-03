@echo off
rem JARVIS : application de bureau (prototype, docs\electron.md). Il faut Node.js 22.12 ou plus recent.
rem La premiere fois, installe Electron dans shell\node_modules ; ensuite, demarre l'application.
rem Le serveur de la console est demarre au besoin (start.bat --no-browser).
setlocal
cd /d "%~dp0shell"
if exist "node_modules\electron\dist\electron.exe" goto run
where npm >nul 2>nul
if errorlevel 1 goto nonode
echo Installation de l'application de bureau (une seule fois) ...
call npm install --no-audit --no-fund
if not exist "node_modules\electron\dist\electron.exe" goto fail

:run
start "" "node_modules\electron\dist\electron.exe" .
exit /b 0

:nonode
echo.
echo Node.js est introuvable : installe Node.js 22.12 ou plus recent depuis https://nodejs.org puis relance.
pause
exit /b 1

:fail
echo.
echo L'installation a echoue : verifie la connexion internet puis relance.
pause
exit /b 1
