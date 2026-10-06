#!/bin/bash
# JARVIS Console (macOS) : double-clic pour lancer. La première fois : chmod +x start.command
# Crée l'environnement Python .venv au premier lancement, puis démarre le serveur en
# arrière-plan (journal dans data/console.log) et ouvre la console dans sa propre fenêtre.
# "./start.command --console" garde le serveur au premier plan pour le dépannage.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "Création de l'environnement Python .venv ..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10 ou plus récent est introuvable : installe-le depuis https://www.python.org/downloads/"
    read -r -p "Entrée pour fermer" _; exit 1
  fi
  python3 -m venv .venv || { read -r -p "Échec. Entrée pour fermer" _; exit 1; }
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installation des dépendances ..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt \
    || { echo "L'installation a échoué : vérifie la connexion internet."; read -r -p "Entrée pour fermer" _; exit 1; }
  cp requirements.txt .venv/requirements.installed
fi

if [ "$1" = "--console" ]; then
  shift
  exec .venv/bin/python -m console "$@"
fi

nohup .venv/bin/python -m console --background "$@" >/dev/null 2>&1 &
echo "JARVIS Console lancée en arrière-plan. Tu peux fermer cette fenêtre."
# Close this Terminal window when Terminal allows it (the server keeps running).
(sleep 1; osascript -e 'tell application "Terminal" to close (every window whose name contains "start.command")' >/dev/null 2>&1) &
exit 0
