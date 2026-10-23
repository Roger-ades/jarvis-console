"""Le hub, qui tourne sous le même compte que Syncthing, inscrit un poste sur un dossier."""
from __future__ import annotations

import os
import re
import subprocess

DEVICE = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")
FOLDER = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def binary() -> str:
    forced = os.environ.get("SYNCTHING_BIN")
    if forced:
        return forced
    return "/usr/bin/syncthing" if os.path.isfile("/usr/bin/syncthing") else "syncthing"


def local_id() -> str:
    # Syncthing 2 n'accepte plus --device-id : c'est une sous-commande.
    try:
        out = subprocess.check_output([binary(), "device-id"], text=True, timeout=20)
    except subprocess.SubprocessError as exc:
        raise ValueError("Syncthing n'a pas donné son identifiant") from exc
    lines = out.strip().splitlines()
    found = lines[-1].strip() if lines else ""
    if not DEVICE.match(found):
        raise ValueError("identifiant Syncthing illisible")
    return found


def register(device_id: str, folder_id: str) -> None:
    if not DEVICE.match(device_id or ""):
        raise ValueError("identifiant invalide")
    if not FOLDER.match(folder_id or ""):
        raise ValueError("dossier invalide")
    exe = binary()
    subprocess.run([exe, "cli", "config", "devices", "add", "--device-id", device_id], timeout=20, check=False)
    subprocess.run([exe, "cli", "config", "folders", folder_id, "devices", "add", "--device-id", device_id],
                   timeout=20, check=False)
