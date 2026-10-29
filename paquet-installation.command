#!/bin/bash
# JARVIS (macOS) : prépare le paquet d'installation d'un nouveau poste (README, « Installer sur un autre
# poste »). Le hub ne sert le dépôt qu'aux postes déjà inscrits : le premier exemplaire part donc de ce
# poste. Le paquet est un dépôt Git propre (ni data/, ni .venv, ni fichiers locaux), à la dernière
# version poussée sur le hub, dont l'origine est le dépôt du hub : après l'inscription au hub, la mise à
# jour en un clic fonctionne sur le nouveau poste. Résultat : JARVIS-installation.zip sur le Bureau.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
command -v git >/dev/null || { echo "Git est introuvable : installe les outils de ligne de commande (xcode-select --install)."; exit 1; }
SHA="$(git rev-parse --verify --quiet origin/main)" \
  || { echo "Ce dossier n'a pas de branche origin/main : il faut une copie de JARVIS reliée au dépôt du hub."; exit 1; }
# même adresse que console/hub_client.py (HUB_URL) et console/updater.py (SERVER_REPO)
HUB="$(sed -n 's/^HUB_URL = "\(.*\)"/\1/p' console/hub_client.py)"
[ -n "$HUB" ] || { echo "Adresse du hub introuvable dans console/hub_client.py."; exit 1; }
WORK="${TMPDIR:-/tmp}/jarvis-paquet"
ZIP="$HOME/Desktop/JARVIS-installation.zip"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Des fichiers modifiés sur ce poste ne seront pas dans le paquet : il prend la version du hub."
fi
AHEAD="$(git rev-list --count origin/main..HEAD)"
if [ "$AHEAD" != "0" ]; then
  echo "$AHEAD commit(s) pas encore poussé(s) ne seront pas dans le paquet : pousse-les d'abord pour les inclure."
fi

echo "Préparation du paquet (version ${SHA:0:7}) ..."
rm -rf "$WORK"
git clone --quiet --no-hardlinks "$ROOT" "$WORK/JARVIS"
cd "$WORK/JARVIS"
git checkout --quiet -B main "$SHA"
git update-ref refs/remotes/origin/main "$SHA"
git remote set-url origin "$HUB/git/jarvis-console.git"
git branch --quiet --set-upstream-to=origin/main main
rm -f "$ZIP"
ditto -c -k --keepParent "$WORK/JARVIS" "$ZIP"
rm -rf "$WORK"
echo
echo "Paquet prêt : $ZIP"
echo "Sur le nouveau poste : décompresser dans Documents, puis double-clic sur build-app.command"
echo "(ou start.command). L'inscription au hub se fait au premier lancement, au bureau ou par le VPN."
