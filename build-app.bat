@echo off
rem JARVIS : construit puis installe l'application de bureau (docs\electron.md, "Installer l'application").
rem Il faut Node.js 22.12 ou plus recent. L'installation se fait pour l'utilisateur, sans droits
rem d'administrateur : menu Demarrer, Bureau, %LOCALAPPDATA%\Programs\jarvis.
rem Ensuite, l'application se met a jour avec JARVIS (mise a jour en un clic, git pull) : relancer ce
rem script seulement quand l'application le demande (nouvelle version d'Electron).
rem Signature (facultatif, pour installer sur d'autres PC sans avertissement SmartScreen) : definir
rem CSC_LINK (chemin du certificat .pfx) et CSC_KEY_PASSWORD avant de lancer ce script.
setlocal
cd /d "%~dp0shell"
where node >nul 2>nul
if errorlevel 1 goto nonode
if exist "node_modules\electron\dist\electron.exe" goto build
echo Installation d'Electron (une seule fois) ...
call npm install --no-audit --no-fund
if errorlevel 1 goto fail
if not exist "node_modules\electron\dist\electron.exe" node "node_modules\electron\install.js"
if not exist "node_modules\electron\dist\electron.exe" goto fail

:build
echo Construction de l'installateur (quelques minutes la premiere fois) ...
if exist "dist" rmdir /s /q "dist"
call npm run dist:win
if errorlevel 1 goto fail
rem au premier demarrage, l'application retrouve ce dossier
node -e "const fs=require('fs'),p=require('path');const d=p.join(process.env.APPDATA,'JARVIS');fs.mkdirSync(d,{recursive:true});fs.writeFileSync(p.join(d,'dossier.json'),JSON.stringify({root:p.resolve(process.argv[1])},null,2))" "%~dp0."
set "SETUP="
for %%f in ("dist\JARVIS-installation-*.exe") do set "SETUP=%%~ff"
if not defined SETUP goto fail
echo Installation de JARVIS ...
start "" "%SETUP%"
exit /b 0

:nonode
echo.
echo Node.js est introuvable : installe Node.js 22.12 ou plus recent depuis https://nodejs.org puis relance.
pause
exit /b 1

:fail
echo.
echo La construction a echoue. Verifie la connexion internet (les outils viennent de npmjs.com et github.com),
echo puis relance. Si le probleme continue : supprime les dossiers shell\node_modules et shell\dist et relance.
pause
exit /b 1
