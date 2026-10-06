@echo off
rem JARVIS : construit puis installe l'application de bureau (docs\electron.md, "Installer l'application").
rem Il faut Node.js 22.12 ou plus recent. Python 3.10 ou plus est detecte ; s'il manque, il est installe
rem pour l'utilisateur (winget, sinon une copie dans %LOCALAPPDATA%\JARVIS\python), puis .venv et
rem requirements.txt. L'installation de l'application se fait sans droits d'administrateur :
rem menu Demarrer, Bureau, %LOCALAPPDATA%\Programs\jarvis.
rem Ensuite, l'application se met a jour avec JARVIS (mise a jour en un clic, git pull) : relancer ce
rem script seulement quand l'application le demande (nouvelle version d'Electron).
rem Signature (facultatif, pour installer sur d'autres PC sans avertissement SmartScreen) : definir
rem CSC_LINK (chemin du certificat .pfx) et CSC_KEY_PASSWORD avant de lancer ce script.
setlocal
cd /d "%~dp0"
set "ROOT=%cd%"
call :ensure_python
if errorlevel 1 goto pyfail
cd /d "%ROOT%\shell"
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

:pyfail
echo.
echo La preparation de Python a echoue. Verifie la connexion internet, puis relance.
pause
exit /b 1

rem Python 3.10+ , puis .venv et requirements.txt (meme tampon que start.bat).
:ensure_python
set "NEED_VENV=1"
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "NEED_VENV="
)
if not defined NEED_VENV goto pipdeps
if exist "%ROOT%\.venv" (
  echo L'environnement .venv est inutilisable, il est recree ...
  rmdir /s /q "%ROOT%\.venv"
)
call :find_python
if not errorlevel 1 goto makevenv
echo Python 3.10 ou plus recent est introuvable.
call :install_python
if errorlevel 1 exit /b 1
call :find_python
if errorlevel 1 exit /b 1
:makevenv
echo Creation de l'environnement Python .venv ...
"%PY%" -m venv "%ROOT%\.venv"
if errorlevel 1 exit /b 1
:pipdeps
fc /b "%ROOT%\requirements.txt" "%ROOT%\.venv\requirements.installed" >nul 2>&1
if not errorlevel 1 exit /b 0
echo Installation des dependances Python ...
"%ROOT%\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "%ROOT%\requirements.txt"
if errorlevel 1 exit /b 1
copy /y "%ROOT%\requirements.txt" "%ROOT%\.venv\requirements.installed" >nul
exit /b 0

:find_python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)"') do set "PY=%%I"
  )
)
if defined PY exit /b 0
where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%I in ('where python 2^>nul') do (
    echo %%I | findstr /I /C:"WindowsApps" >nul
    if errorlevel 1 (
      "%%I" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
      if not errorlevel 1 set "PY=%%I"
    )
  )
)
if defined PY exit /b 0
if exist "%LOCALAPPDATA%\JARVIS\python\python.exe" (
  "%LOCALAPPDATA%\JARVIS\python\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=%LOCALAPPDATA%\JARVIS\python\python.exe"
)
if defined PY exit /b 0
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%D\python.exe" (
    "%%D\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=%%D\python.exe"
  )
)
if defined PY exit /b 0
exit /b 1

:install_python
where winget >nul 2>&1
if errorlevel 1 goto fetch_python
echo Installation de Python 3.12 pour l'utilisateur (winget) ...
winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
call :find_python
if not errorlevel 1 exit /b 0
:fetch_python
echo Telechargement de Python 3.12 ...
set "PBS=20261003"
set "TRIPLE=x86_64-pc-windows-msvc"
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "TRIPLE=aarch64-pc-windows-msvc"
set "URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PBS%/cpython-3.12.15+%PBS%-%TRIPLE%-install_only.tar.gz"
set "ARCHIVE=%TEMP%\jarvis-cpython.tar.gz"
set "DEST=%LOCALAPPDATA%\JARVIS"
if not exist "%DEST%" mkdir "%DEST%"
curl.exe -fL --retry 3 -o "%ARCHIVE%" "%URL%"
if errorlevel 1 exit /b 1
if exist "%DEST%\python" rmdir /s /q "%DEST%\python"
tar -xzf "%ARCHIVE%" -C "%DEST%"
if errorlevel 1 exit /b 1
del /q "%ARCHIVE%" 2>nul
if not exist "%DEST%\python\python.exe" exit /b 1
exit /b 0
