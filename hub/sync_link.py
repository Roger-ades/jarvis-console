"""Le hub, qui tourne sous le même compte que Syncthing, inscrit un poste sur un dossier.

Il parle à l'API REST de Syncthing (clé lue dans sa configuration) : chaque étape se vérifie,
et un projet d'équipe qui n'a pas encore de dossier sur le serveur en reçoit un, versionné.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DEVICE = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")
FOLDER = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
PROJECTS = Path(os.environ.get("JARVIS_PROJECTS", "/var/lib/jarvis/projets"))
KEEP = "7776000"  # 90 jours de versions échelonnées


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


def _config_file() -> Path:
    forced = os.environ.get("STHOMEDIR")
    if forced:
        return Path(forced) / "config.xml"
    home = Path.home()
    for candidate in (home / ".local/state/syncthing/config.xml", home / ".config/syncthing/config.xml"):
        if candidate.is_file():
            return candidate
    return home / ".local/state/syncthing/config.xml"


def endpoint() -> tuple[str, str]:
    """(adresse de l'API, clé). L'environnement l'emporte sur config.xml."""
    url = os.environ.get("JARVIS_SYNCTHING_URL", "")
    key = os.environ.get("JARVIS_SYNCTHING_KEY", "")
    if not (url and key):
        try:
            root = ET.parse(_config_file()).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ValueError("configuration Syncthing illisible") from exc
        gui = root.find("gui")
        key = key or (gui.findtext("apikey") if gui is not None else "") or ""
        if not url:
            address = (gui.findtext("address") if gui is not None else "") or "127.0.0.1:8384"
            tls = gui is not None and gui.get("tls") == "true"
            url = f"{'https' if tls else 'http'}://{address}"
    if not key:
        raise ValueError("clé de l'API Syncthing introuvable")
    return url.rstrip("/"), key


def _rest(method: str, path: str, body=None):
    url, key = endpoint()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        detail = exc.read().decode("utf-8", "replace").strip()[:200]
        raise ValueError(f"Syncthing a refusé ({exc.code} {detail})") from exc
    except OSError as exc:
        raise ValueError("Syncthing injoignable sur le serveur") from exc
    return json.loads(raw) if raw.strip() else None


def register(device_id: str, folder_id: str, label: str = "", name: str = "") -> None:
    """Le poste est connu du serveur et partage le dossier du projet. Sans effet s'il l'est déjà."""
    if not DEVICE.match(device_id or ""):
        raise ValueError("identifiant invalide")
    if not FOLDER.match(folder_id or ""):
        raise ValueError("dossier invalide")
    q = urllib.parse.quote
    if _rest("GET", f"/rest/config/devices/{q(device_id)}") is None:
        _rest("PUT", f"/rest/config/devices/{q(device_id)}",
              {"deviceID": device_id, "name": name or device_id[:7], "addresses": ["dynamic"]})
    folder = _rest("GET", f"/rest/config/folders/{q(folder_id)}")
    if folder is None:
        path = PROJECTS / folder_id
        path.mkdir(parents=True, exist_ok=True)
        _rest("PUT", f"/rest/config/folders/{q(folder_id)}", {
            "id": folder_id, "label": label or folder_id, "path": str(path), "type": "sendreceive",
            "devices": [{"deviceID": device_id}],
            "versioning": {"type": "staggered", "params": {"maxAge": KEEP}, "cleanupIntervalS": 3600},
        })
        return
    devices = folder.get("devices") or []
    if not any(d.get("deviceID") == device_id for d in devices):
        _rest("PATCH", f"/rest/config/folders/{q(folder_id)}",
              {"devices": [*devices, {"deviceID": device_id}]})
