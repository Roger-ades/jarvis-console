#!/bin/bash
# JARVIS (macOS) : construit l'application de bureau puis ouvre son image disque (docs/electron.md,
# « Installer l'application ») ; glisser JARVIS dans Applications. Il faut Node.js 22.12 ou plus récent.
# Ensuite, l'application se met à jour avec JARVIS (git pull) : relancer ce script seulement quand
# l'application le demande. Signature et notarisation (pour d'autres Mac) : CSC_LINK, CSC_KEY_PASSWORD,
# APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD et APPLE_TEAM_ID avant de lancer ce script.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/shell"
command -v node >/dev/null || { echo "Node.js est introuvable : installe Node.js 22.12 ou plus récent (https://nodejs.org)."; exit 1; }
if [ ! -d node_modules/electron/dist ]; then
  echo "Installation d'Electron (une seule fois) ..."
  npm install --no-audit --no-fund
fi
echo "Construction de l'application (quelques minutes la première fois) ..."
rm -rf dist
npm run dist:mac
# au premier démarrage, l'application retrouve ce dossier
mkdir -p "$HOME/Library/Application Support/JARVIS"
printf '{\n  "root": "%s"\n}\n' "$ROOT" > "$HOME/Library/Application Support/JARVIS/dossier.json"
open dist/*.dmg
