@echo off
rem JARVIS Console : double-clic pour lancer (cree le venv Python au premier lancement).
setlocal
cd /d "%~dp0"
title JARVIS - Console d'agents Claude

if exist ".venv\Scripts\python.exe" goto deps
echo Creation de l'environnement Python .venv ...
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto nopython

:deps
rem Reinstalle les dependances seulement si requirements.txt a change.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if %errorlevel%==0 goto run
echo Installation des dependances ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto piperror
copy /y requirements.txt ".venv\requirements.installed" >nul

:run
rem Sans fenetre : le serveur tourne en arriere-plan (journal dans data\console.log).
rem "start.bat --console" le garde au premier plan pour le depannage.
if /i "%~1"=="--console" goto foreground
start "" ".venv\Scripts\pythonw.exe" -m console %*
exit /b 0

:foreground
".venv\Scripts\python.exe" -m console %2 %3 %4 %5 %6 %7 %8 %9
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo Python 3.10 ou plus recent est introuvable.
echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add Python to PATH".
pause
exit /b 1

:piperror
echo.
echo L'installation des dependances a echoue : verifie la connexion internet puis relance.
pause
exit /b 1
