"""Syncthing lancé par JARVIS, sans logiciel de synchro à part (Windows et macOS)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

VERSION = "2.1.7"
RELEASE = f"https://github.com/syncthing/syncthing/releases/download/v{VERSION}/"
GUI = "127.0.0.1:8384"
DEVICE = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")
SERVER_NAME = "SRV-DEBIAN"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def home_of(data_dir: Path) -> Path:
    return Path(data_dir) / "syncthing" / "state"


def binary_of(data_dir: Path) -> Path:
    return Path(data_dir) / "syncthing" / ("syncthing.exe" if sys.platform == "win32" else "syncthing")


def archive_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
        return f"syncthing-windows-{arch}-v{VERSION}.zip"
    if sys.platform == "darwin":
        return f"syncthing-macos-universal-v{VERSION}.zip"
    raise OSError("Synchro intégrée prévue pour Windows et macOS.")


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def install(data_dir: Path) -> Path:
    dest = binary_of(data_dir)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    name = archive_name()
    blob = _fetch(RELEASE + name)
    # La somme publiée avec la version : un téléchargement altéré n'est pas installé.
    sums = _fetch(RELEASE + "sha256sum.txt.asc").decode("utf-8", "replace")
    expected = next((parts[0] for parts in map(str.split, sums.splitlines())
                     if len(parts) == 2 and parts[1].lstrip("*") == name), "")
    if not expected or hashlib.sha256(blob).hexdigest() != expected.lower():
        raise OSError("Téléchargement de Syncthing non conforme.")
    wanted = dest.name
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        member = next(n for n in archive.namelist() if n.endswith("/" + wanted) or n == wanted)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(archive.read(member))
    if sys.platform != "win32":
        tmp.chmod(0o755)
    tmp.replace(dest)
    return dest


def start(data_dir: Path) -> None:
    exe = binary_of(data_dir)
    if not exe.is_file():
        exe = install(data_dir)
    home = home_of(data_dir)
    home.mkdir(parents=True, exist_ok=True)
    if _up():
        return
    # Pas de dossier « Default Folder » : seuls les projets d'équipe sont partagés.
    env = {**os.environ, "STNODEFAULTFOLDER": "1"}
    subprocess.Popen(
        [str(exe), "serve", "--no-browser", "--no-restart", "--home", str(home), "--gui-address", GUI],
        cwd=str(exe.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        creationflags=NO_WINDOW,
    )
    for _ in range(40):
        if _up():
            return
        time.sleep(0.5)
    raise TimeoutError("Syncthing ne répond pas sur ce poste.")


def device_id(data_dir: Path) -> str:
    exe = binary_of(data_dir)
    if not exe.is_file():
        raise FileNotFoundError("syncthing")
    # Syncthing 2 n'accepte plus --device-id : c'est une sous-commande, --home reste global.
    try:
        out = subprocess.check_output(
            [str(exe), "--home", str(home_of(data_dir)), "device-id"],
            text=True, timeout=20, creationflags=NO_WINDOW,
        )
    except subprocess.SubprocessError as exc:
        raise ValueError("Syncthing n'a pas donné l'identifiant de ce poste.") from exc
    lines = out.strip().splitlines()
    found = lines[-1].strip() if lines else ""
    if not DEVICE.match(found):
        raise ValueError("identifiant Syncthing illisible")
    return found


def _api_key(data_dir: Path) -> str:
    try:
        gui = ET.parse(home_of(data_dir) / "config.xml").getroot().find("gui")
    except (OSError, ET.ParseError) as exc:
        raise ValueError("configuration Syncthing illisible sur ce poste") from exc
    key = (gui.findtext("apikey") if gui is not None else "") or ""
    if not key:
        raise ValueError("clé de l'API Syncthing introuvable sur ce poste")
    return key


def _rest(data_dir: Path, method: str, path: str, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"http://{GUI}{path}", data=data, method=method, headers={
        "X-API-Key": _api_key(data_dir), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        detail = exc.read().decode("utf-8", "replace").strip()[:200]
        raise ValueError(f"Syncthing a refusé ({exc.code} {detail})") from exc
    except OSError as exc:
        raise ValueError("Syncthing ne répond pas sur ce poste.") from exc
    return json.loads(raw) if raw.strip() else None


def _same_path(a: str, b: str) -> bool:
    def norm(p):
        return os.path.normcase(os.path.normpath(os.path.expanduser(p)))
    return norm(a) == norm(b)


def share(data_dir: Path, folder: str, folder_id: str, remote: str, *, label: str = "", host: str = "") -> None:
    """Le dossier local est partagé avec Syncthing du serveur. Sans effet s'il l'est déjà."""
    if not DEVICE.match(remote or ""):
        raise ValueError("identifiant du serveur illisible")
    start(data_dir)
    q = urllib.parse.quote
    # L'adresse du hub d'abord : le VPN ne laisse pas toujours passer la découverte.
    addresses = ([f"tcp://{host}:22000"] if host else []) + ["dynamic"]
    device = _rest(data_dir, "GET", f"/rest/config/devices/{q(remote)}")
    if device is None:
        _rest(data_dir, "PUT", f"/rest/config/devices/{q(remote)}", {
            "deviceID": remote, "name": SERVER_NAME, "introducer": True, "addresses": addresses})
    elif not set(addresses) <= set(device.get("addresses") or []):
        known = [a for a in device.get("addresses") or [] if a not in addresses]
        _rest(data_dir, "PATCH", f"/rest/config/devices/{q(remote)}", {"addresses": addresses + known})
    current = _rest(data_dir, "GET", f"/rest/config/folders/{q(folder_id)}")
    if current is None:
        _rest(data_dir, "PUT", f"/rest/config/folders/{q(folder_id)}", {
            "id": folder_id, "label": label or folder_id, "path": folder, "type": "sendreceive",
            "devices": [{"deviceID": remote}]})
        return
    if not _same_path(current.get("path") or "", folder):
        raise ValueError(f"Ce projet est déjà synchronisé dans un autre dossier : {current.get('path')}")
    devices = current.get("devices") or []
    if not any(d.get("deviceID") == remote for d in devices):
        _rest(data_dir, "PATCH", f"/rest/config/folders/{q(folder_id)}",
              {"devices": [*devices, {"deviceID": remote}]})


def unshare(data_dir: Path, folder: str) -> int:
    """Ce poste cesse de synchroniser le dossier (le projet quitté, ou retiré) : les fichiers restent sur le
    disque. Rend le nombre de partages retirés ; sans Syncthing installé, rien à retirer."""
    if not binary_of(data_dir).is_file() or not (home_of(data_dir) / "config.xml").is_file():
        return 0
    start(data_dir)
    q = urllib.parse.quote
    gone = 0
    for current in _rest(data_dir, "GET", "/rest/config/folders") or []:
        if _same_path(current.get("path") or "", folder):
            _rest(data_dir, "DELETE", f"/rest/config/folders/{q(current.get('id') or '')}")
            gone += 1
    return gone


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"http://{GUI}/rest/noauth/health", timeout=1) as response:
            return response.status < 500
    except OSError:
        return False
