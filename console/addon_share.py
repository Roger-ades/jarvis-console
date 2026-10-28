"""Sending a module from one JARVIS to another through the team hub (hub/app.py, /api/v1/modules).

The sender's console zips the module (its folder and partage.json: who, when, which version; never its storage)
and gives it to the hub for the colleagues picked in the module's window. The zip waits in the recipient's inbox
(console/equipe_boite.py), where one click installs it in addons/; a module already there is kept in
addons/.anciens/. Claude takes no part in it. A zip is data from someone else: every name is checked before
anything is written, nothing leaves the module.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import time
import zipfile
from pathlib import Path

from . import addons

SUFFIX = ".jarvis.zip"
INFO = "partage.json"
OLD = ".anciens"


class ShareError(Exception):
    pass


# ---------------------------------------------------------------- export
def file_name(a: dict) -> str:
    version = re.sub(r"[^\w.\-]", "", a.get("version") or "")
    return f"{a['id']}{'-v' + version if version else ''}{SUFFIX}"


def export(a: dict, dest_dir: Path, sender: str = "") -> Path:
    """The module `a` (addons.read) zipped into dest_dir; the same version shared again replaces its zip."""
    folder = Path(a["folder"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_name(a)
    tmp = dest_dir / f".{dest.name}.{secrets.token_hex(3)}.tmp"
    info = {"format": 1, "id": a["id"], "nom": a["nom"], "version": a["version"], "description": a["description"],
            "de": sender, "date": time.strftime("%Y-%m-%dT%H:%M:%S"), "permissions": a["permissions"]}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(INFO, json.dumps(info, ensure_ascii=False, indent=1))
            for p in addons.files(folder):
                z.write(p, f"{a['id']}/{p.relative_to(folder).as_posix()}")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------- what a zip holds
def inspect(path: Path) -> dict:
    """A received zip, checked: {"id", "nom", "version", "de", "date", "permissions", "fichiers", "taille"};
    raises ShareError with the reason when it is not a module or would write outside it."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ShareError(f"{path.name} n'est pas un zip lisible ({exc}).") from None
    with z:
        entries = [e for e in z.infolist() if not e.is_dir()]
        if len(entries) > addons.MAX_FILES + 1:
            raise ShareError(f"{len(entries)} fichiers : un module en a {addons.MAX_FILES} au plus.")
        tops, size = set(), 0
        for e in entries:
            name = e.filename
            parts = name.split("/")
            if (not name or "\\" in name or name.startswith("/") or ":" in name
                    or any(x in ("", ".", "..") for x in parts)):
                raise ShareError(f"nom de fichier refusé dans le zip : {name[:120]}")
            if stat.S_ISLNK(e.external_attr >> 16):
                raise ShareError(f"lien symbolique refusé dans le zip : {name[:120]}")
            if e.file_size > addons.MAX_FILE:
                raise ShareError(f"{name[:120]} : plus de {addons.MAX_FILE // 1024 // 1024} Mo.")
            size += e.file_size
            if name != INFO:
                tops.add(parts[0] if len(parts) > 1 else "")
        if size > addons.MAX_TOTAL:
            raise ShareError(f"{size // 1024 // 1024} Mo : un module fait {addons.MAX_TOTAL // 1024 // 1024} Mo au plus.")
        if len(tops) != 1 or "" in tops:
            raise ShareError("le zip doit contenir un seul dossier de module (et partage.json).")
        aid = tops.pop()
        if not addons.ID_RX.match(aid):
            raise ShareError(f"nom de module invalide : {aid[:60]}")
        try:
            manifest = json.loads(z.read(f"{aid}/{addons.MANIFEST}").decode("utf-8-sig"))
        except KeyError:
            raise ShareError(f"{addons.MANIFEST} manquant dans le module {aid}.") from None
        except ValueError as exc:
            raise ShareError(f"{addons.MANIFEST} illisible : {exc}") from None
        try:
            info = json.loads(z.read(INFO).decode("utf-8-sig"))
        except (KeyError, ValueError):
            info = {}
        count = sum(1 for e in entries if e.filename != INFO)
    manifest = manifest if isinstance(manifest, dict) else {}
    info = info if isinstance(info, dict) else {}
    perms = manifest.get("permissions") or []
    perms = [perms] if isinstance(perms, str) else perms if isinstance(perms, list) else []
    return {"id": aid, "nom": addons._s(manifest.get("nom"), 80) or aid, "version": addons._s(manifest.get("version"), 20),
            "description": addons._s(manifest.get("description"), 400), "de": addons._s(info.get("de"), 120),
            "date": addons._s(info.get("date"), 25), "permissions": sorted({str(p)[:20] for p in perms}),
            "fichiers": count, "taille": size}


def install(path: Path, root: Path) -> dict:
    """Unzips the module into root (addons/); the one already there goes to root/.anciens/<id>-<date>.
    Returns the inspection, plus "ancien" (where the previous version is kept) and "module" (addons.read)."""
    info = inspect(path)
    aid = info["id"]
    root.mkdir(parents=True, exist_ok=True)
    stage = root / f".import-{aid}-{secrets.token_hex(3)}"
    try:
        with zipfile.ZipFile(path) as z:
            for e in z.infolist():
                if e.is_dir() or e.filename == INFO:
                    continue
                target = stage / e.filename[len(aid) + 1:]
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(e) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        final, kept = root / aid, None
        if final.exists():
            kept = root / OLD / f"{aid}-{time.strftime('%Y%m%d-%H%M%S')}"
            kept.parent.mkdir(parents=True, exist_ok=True)
            final.rename(kept)
        stage.rename(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return {**info, "ancien": str(kept) if kept else "", "module": addons.read(final)}
