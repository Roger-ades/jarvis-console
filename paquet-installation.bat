@echo off
rem JARVIS : prepare le paquet d'installation d'un nouveau poste (README, "Installer sur un autre poste").
rem Le hub ne sert le depot qu'aux postes deja inscrits : le premier exemplaire part donc de ce poste.
rem Le paquet est un depot Git propre (ni data\, ni .venv, ni fichiers locaux), a la derniere version
rem poussee sur le hub, dont l'origine est le depot du hub : apres l'inscription au hub, la mise a jour
rem en un clic fonctionne sur le nouveau poste. Resultat : JARVIS-installation.zip sur le Bureau.
setlocal
cd /d "%~dp0"
set "ROOT=%cd%"
where git >nul 2>nul
if errorlevel 1 goto nogit
git rev-parse --verify --quiet origin/main >nul
if errorlevel 1 goto noremote
for /f "delims=" %%s in ('git rev-parse origin/main') do set "SHA=%%s"
rem meme adresse que console\hub_client.py (HUB_URL) et console\updater.py (SERVER_REPO)
set "HUB="
for /f "tokens=2 delims== " %%u in ('findstr /b /c:"HUB_URL =" console\hub_client.py') do set "HUB=%%~u"
if not defined HUB goto fail
set "WORK=%TEMP%\jarvis-paquet"
set "OUT="
for /f "delims=" %%d in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"') do set "OUT=%%d"
if not defined OUT set "OUT=%USERPROFILE%"
set "ZIP=%OUT%\JARVIS-installation.zip"

git status --porcelain --untracked-files=no | findstr . >nul
if not errorlevel 1 echo Des fichiers modifies sur ce poste ne seront pas dans le paquet : il prend la version du hub.
for /f %%n in ('git rev-list --count origin/main..HEAD') do if not "%%n"=="0" echo %%n commit^(s^) pas encore pousse^(s^) ne seront pas dans le paquet : pousse-les d'abord pour les inclure.

echo Preparation du paquet (version %SHA:~0,7%) ...
if exist "%WORK%" rmdir /s /q "%WORK%"
git clone --quiet --no-hardlinks "%ROOT%" "%WORK%\JARVIS"
if errorlevel 1 goto fail
pushd "%WORK%\JARVIS"
git checkout --quiet -B main %SHA%
if errorlevel 1 goto failpop
git update-ref refs/remotes/origin/main %SHA%
git remote set-url origin %HUB%/git/jarvis-console.git
if errorlevel 1 goto failpop
git branch --quiet --set-upstream-to=origin/main main
if errorlevel 1 goto failpop
popd
if exist "%ZIP%" del /q "%ZIP%"
tar -a -c -f "%ZIP%" -C "%WORK%" JARVIS
if errorlevel 1 goto fail
rmdir /s /q "%WORK%"
echo.
echo Paquet pret : %ZIP%
echo Sur le nouveau poste : decompresser dans Documents, puis double-clic sur build-app.bat
echo (ou start.bat). L'inscription au hub se fait au premier lancement, au bureau ou par le VPN.
pause
exit /b 0

:nogit
echo Git est introuvable : installe Git pour Windows (https://git-scm.com) puis relance.
pause
exit /b 1

:noremote
echo Ce dossier n'a pas de branche origin/main : il faut une copie de JARVIS reliee au depot du hub.
pause
exit /b 1

:failpop
popd
:fail
echo.
echo La preparation du paquet a echoue.
if exist "%WORK%" rmdir /s /q "%WORK%"
pause
exit /b 1
