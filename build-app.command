#!/bin/bash
# JARVIS (macOS) : construit l'application de bureau puis ouvre son image disque (docs/electron.md,
# « Installer l'application ») ; glisser JARVIS dans Applications. Il faut Node.js 22.12 ou plus récent.
# Python 3.10 ou plus est détecté ; s'il manque, Homebrew l'installe, sinon une copie est placée dans
# ~/Library/Application Support/JARVIS/python. Puis .venv et requirements.txt.
# Ensuite, l'application se met à jour avec JARVIS (git pull) : relancer ce script seulement quand
# l'application le demande. Signature et notarisation (pour d'autres Mac) : CSC_LINK, CSC_KEY_PASSWORD,
# APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD et APPLE_TEAM_ID avant de lancer ce script.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

python_ok() {
  [ -n "$1" ] && [ -x "$1" ] && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  PY=""
  local c
  for c in \
    "$(command -v python3 2>/dev/null || true)" \
    /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    "$HOME/Library/Application Support/JARVIS/python/bin/python3"
  do
    if python_ok "$c"; then PY="$c"; return 0; fi
  done
  return 1
}

install_python() {
  local brew=""
  if [ -x /opt/homebrew/bin/brew ]; then brew=/opt/homebrew/bin/brew
  elif [ -x /usr/local/bin/brew ]; then brew=/usr/local/bin/brew
  elif command -v brew >/dev/null 2>&1; then brew=brew
  fi
  if [ -n "$brew" ]; then
    echo "Installation de Python 3.12 (Homebrew) ..."
    if "$brew" install python@3.12; then
      local prefix c
      prefix="$("$brew" --prefix python@3.12)"
      for c in "$prefix/bin/python3.12" "$prefix/bin/python3"; do
        if python_ok "$c"; then PY="$c"; return 0; fi
      done
    fi
  fi
  echo "Téléchargement de Python 3.12 ..."
  local triple="x86_64-apple-darwin" arch dest archive url
  arch="$(uname -m)"
  if [ "$arch" = "arm64" ]; then triple="aarch64-apple-darwin"; fi
  dest="$HOME/Library/Application Support/JARVIS"
  archive="${TMPDIR:-/tmp}/jarvis-cpython.tar.gz"
  url="https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15+20261003-${triple}-install_only.tar.gz"
  mkdir -p "$dest"
  curl -fL --retry 3 -o "$archive" "$url" || { echo "Le téléchargement de Python a échoué."; return 1; }
  rm -rf "$dest/python"
  tar -xzf "$archive" -C "$dest" || { echo "L'archive Python est illisible."; return 1; }
  rm -f "$archive"
  PY="$dest/python/bin/python3"
  python_ok "$PY" || { echo "Python installé ne démarre pas."; return 1; }
}

ensure_python() {
  if python_ok "$ROOT/.venv/bin/python"; then
    :
  else
    if [ -e "$ROOT/.venv" ]; then
      echo "L'environnement .venv est inutilisable, il est recréé ..."
      rm -rf "$ROOT/.venv"
    fi
    find_python || true
    if ! python_ok "$PY"; then
      echo "Python 3.10 ou plus récent est introuvable."
      install_python
    fi
    echo "Création de l'environnement Python .venv ..."
    "$PY" -m venv "$ROOT/.venv"
  fi
  if ! cmp -s "$ROOT/requirements.txt" "$ROOT/.venv/requirements.installed"; then
    echo "Installation des dépendances Python ..."
    "$ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$ROOT/requirements.txt" \
      || { echo "L'installation des dépendances a échoué : vérifie la connexion internet."; return 1; }
    cp "$ROOT/requirements.txt" "$ROOT/.venv/requirements.installed"
  fi
}

ensure_python
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
