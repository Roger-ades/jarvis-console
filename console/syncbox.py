"""Syncthing lancé par JARVIS, sans logiciel de synchro à part."""
from __future__ import annotations

import io
import re
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

VERSION = "2.1.7"
DOWNLOAD = (f"https://github.com/syncthing/syncthing/releases/download/v{VERSION}/"
            f"syncthing-windows-amd64-v{VERSION}.zip")
DEVICE = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")


def home_of(data_dir: Path) -> Path:
    return Path(data_dir) / "syncthing" / "state"


def binary_of(data_dir: Path) -> Path:
    return Path(data_dir) / "syncthing" / "syncthing.exe"


def install(data_dir: Path) -> Path:
    dest = binary_of(data_dir)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(DOWNLOAD, timeout=120) as response:
        blob = response.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = next(n for n in archive.namelist() if n.endswith("/syncthing.exe") or n == "syncthing.exe")
        dest.write_bytes(archive.read(name))
    return dest


def start(data_dir: Path) -> None:
    exe = binary_of(data_dir)
    if not exe.is_file():
        exe = install(data_dir)
    home = home_of(data_dir)
    home.mkdir(parents=True, exist_ok=True)
    if _up():
        return
    subprocess.Popen(
        [str(exe), "serve", "--no-browser", "--no-restart", "--home", str(home),
         "--gui-address", "127.0.0.1:8384"],
        cwd=str(exe.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(20):
        if _up():
            return
        time.sleep(0.5)
    raise TimeoutError("Syncthing ne répond pas sur ce poste.")


def device_id(data_dir: Path) -> str:
    exe = binary_of(data_dir)
    if not exe.is_file():
        raise FileNotFoundError("syncthing")
    out = subprocess.check_output(
        [str(exe), "--device-id", "--home", str(home_of(data_dir))],
        text=True, timeout=20,
    )
    found = out.strip().splitlines()[-1].strip()
    if not DEVICE.match(found):
        raise ValueError("identifiant Syncthing illisible")
    return found


def share(data_dir: Path, folder: str, folder_id: str, remote: str) -> str:
    """Partage le dossier local avec l'appareil Debian et rend l'identifiant de ce poste."""
    if not DEVICE.match(remote or ""):
        raise ValueError("identifiant du serveur illisible")
    start(data_dir)
    exe = str(binary_of(data_dir))
    home = str(home_of(data_dir))
    base = [exe, "--home", home, "cli"]
    subprocess.run(base + ["config", "devices", "add", "--device-id", remote, "--introducer"],
                   timeout=20, check=False)
    subprocess.run(base + ["config", "folders", "add", "--id", folder_id, "--label", folder_id, "--path", folder],
                   timeout=20, check=False)
    subprocess.run(base + ["config", "folders", folder_id, "devices", "add", "--device-id", remote],
                   timeout=20, check=False)
    return device_id(data_dir)


def _up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8384", timeout=1) as response:
            return response.status < 500
    except OSError:
        return False
