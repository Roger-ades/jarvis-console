"""Sharing a module between two JARVIS: a zip through the OneDrive of the company.

The sender's console writes `<id>-v<version>.jarvis.zip` into a folder of his OneDrive, synchronised by the
OneDrive client ("JARVIS - Modules partagés" by default), and gives Claude the file's web address (computed
from the synchronised libraries the client registers) for a mail draft. Shared once with the team, the folder
shows up in the colleagues' OneDrive. The recipient's console looks for such zips in the synchronised folders,
Downloads and the Desktop, and installs one in addons/ after the user's click; a module already there is kept
in addons/.anciens/. The zip holds the module's folder and partage.json (who, when, which version), no storage.
A zip is data from someone else: every name is checked before anything is written, nothing leaves the module.
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
from urllib.parse import quote

from . import addons

SHARE_FOLDER = "JARVIS - Modules partagés"
SUFFIX = ".jarvis.zip"
INFO = "partage.json"
OLD = ".anciens"
_NAME = re.compile(r"\.jarvis(?:\s*\(\d+\))?\.zip$", re.I)   # a browser's "x.jarvis (1).zip" too
WALK_DIRS = 4000         # folders looked at, at most, when looking for received modules
FOUND = 20


class ShareError(Exception):
    pass


# ---------------------------------------------------------------- where the drive is
def drive_roots() -> list[dict]:
    """The folders the OneDrive client synchronises, with their web address: [{"path", "url", "personal"}]."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(path: str, url: str = "", personal: bool = False):
        key = os.path.normcase(os.path.normpath(path or ""))
        if not path or key in seen or not os.path.isdir(path):
            return
        seen.add(key)
        out.append({"path": os.path.normpath(path), "url": url, "personal": personal})

    if os.name == "nt":
        try:
            import winreg
            base = r"Software\SyncEngines\Providers\OneDrive"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(k, name) as sub:
                            vals = {}
                            for v in ("MountPoint", "UrlNamespace", "LibraryType"):
                                try:
                                    vals[v] = str(winreg.QueryValueEx(sub, v)[0] or "")
                                except OSError:
                                    vals[v] = ""
                    except OSError:
                        continue
                    add(vals["MountPoint"], vals["UrlNamespace"], vals["LibraryType"] in ("personal", "mysite"))
        except (ImportError, OSError):
            pass
    for var in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer"):
        add(os.environ.get(var, ""), "", True)
    # the personal OneDrive first: it is where a module is shared from
    return sorted(out, key=lambda r: not r["personal"])


def account() -> str:
    """Who shares: the address of the work OneDrive account, else the Windows user."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts\Business1") as k:
                mail = str(winreg.QueryValueEx(k, "UserEmail")[0] or "")
                if mail:
                    return mail
        except (ImportError, OSError):
            pass
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def default_dir(roots: list[dict]) -> Path | None:
    return Path(roots[0]["path"]) / SHARE_FOLDER if roots else None


def web_url(path: Path, roots: list[dict]) -> str:
    """The SharePoint/OneDrive address of a synchronised file or folder, "" when it is not in one."""
    p = os.path.normcase(os.path.normpath(str(path)))
    for r in sorted(roots, key=lambda r: -len(r["path"])):
        root = os.path.normcase(r["path"])
        if r["url"] and (p == root or p.startswith(root.rstrip("\\/") + os.sep)):
            rel = os.path.relpath(str(path), r["path"]).replace("\\", "/")
            return r["url"].rstrip("/") + "/" + ("" if rel == "." else quote(rel))
    return ""


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


# ---------------------------------------------------------------- what was received
def places(roots: list[dict]) -> list[tuple[Path, int]]:
    """Where a received module is looked for, with how deep: the synchronised folders, Downloads, the Desktop."""
    home = Path.home()
    out = [(Path(r["path"]), 4) for r in roots]
    out += [(home / "Downloads", 1), (home / "Téléchargements", 1), (home / "Desktop", 1), (home / "Bureau", 1)]
    seen, kept = set(), []
    for p, depth in out:
        key = os.path.normcase(str(p))
        if key not in seen and p.is_dir():
            seen.add(key)
            kept.append((p, depth))
    return kept


def find(where: list[tuple[Path, int]], limit: int = FOUND) -> list[Path]:
    """The module zips (name *.jarvis.zip) of these folders, most recent first. Only names are read: a file
    kept online by OneDrive is not downloaded for that."""
    found, budget = [], WALK_DIRS
    for base, depth in where:
        stack = [(base, 0)]
        while stack and budget > 0:
            d, lvl = stack.pop()
            budget -= 1
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.name.startswith((".", "$")) or e.name in addons.SKIP:
                            continue
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if lvl < depth and e.name.lower() not in ("node_modules", "appdata"):
                                    stack.append((Path(e.path), lvl + 1))
                            elif _NAME.search(e.name):
                                found.append(Path(e.path))
                        except OSError:
                            continue
            except OSError:
                continue

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(found, key=mtime, reverse=True)[:limit]
