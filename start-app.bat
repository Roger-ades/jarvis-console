@echo off
rem JARVIS : application de bureau (docs\electron.md). Si elle est installee (build-app.bat), la demarre.
rem Sinon, il faut Node.js 22.12 ou plus recent : la premiere fois, installe Electron dans
rem shell\node_modules ; ensuite, demarre l'application depuis ce dossier.
rem Le serveur de la console est demarre au besoin (start.bat --no-browser).
setlocal
if exist "%LOCALAPPDATA%\Programs\jarvis\JARVIS.exe" (
  start "" "%LOCALAPPDATA%\Programs\jarvis\JARVIS.exe"
  exit /b 0
)
cd /d "%~dp0shell"
if exist "node_modules\electron\dist\electron.exe" goto run
where node >nul 2>nul
if errorlevel 1 goto nonode
if exist "node_modules\electron\install.js" goto binary
echo Installation de l'application de bureau (une seule fois) ...
call npm install --no-audit --no-fund
if errorlevel 1 goto fail

:binary
rem Electron telecharge son executable a part (environ 100 Mo), depuis GitHub.
if exist "node_modules\electron\dist\electron.exe" goto run
echo Telechargement d'Electron (une seule fois, environ 100 Mo) ...
node "node_modules\electron\install.js"
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
echo L'installation a echoue. Verifie la connexion internet (le telechargement vient de github.com), puis relance.
echo Si le probleme continue : supprime le dossier shell\node_modules et relance.
pause
exit /b 1
