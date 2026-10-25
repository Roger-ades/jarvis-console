"""OneDrive / SharePoint sync status for a project folder (client of Windows, never Graph).

JARVIS never copies files itself: it shows whether a folder is synchronised, and Claude may propose
(``proposer``, quoi « lien ») to move it into OneDrive or open the client's sync link.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import addon_share

MAX_NAME = 80


def sync_of(path: str) -> dict:
    """Whether ``path`` (or a parent) sits under a OneDrive / SharePoint root of this PC."""
    roots = addon_share.drive_roots()
    p = os.path.normcase(os.path.normpath(str(path or "")))
    if not p or not os.path.isdir(p):
        return {"path": path or "", "synced": False, "label": "Dossier introuvable", "roots": _public(roots)}
    best = None
    for r in sorted(roots, key=lambda x: -len(x["path"])):
        root = os.path.normcase(r["path"])
        if p == root or p.startswith(root.rstrip("\\/") + os.sep):
            best = r
            break
    if not best:
        return {"path": path, "synced": False, "label": "Sur ce PC seulement",
                "hint": "Pas dans OneDrive ni SharePoint synchronisés sur ce poste.",
                "roots": _public(roots)}
    kind = "OneDrive" if best.get("personal") else "SharePoint / OneDrive entreprise"
    name = Path(best["path"]).name
    rel = os.path.relpath(path, best["path"])
    return {
        "path": path, "synced": True, "label": f"Synchronisé avec {kind} · {name}",
        "root": best["path"], "url": best.get("url") or "", "personal": bool(best.get("personal")),
        "rel": "" if rel == "." else rel.replace("\\", "/"),
        "roots": _public(roots),
    }


def _public(roots: list[dict]) -> list[dict]:
    return [{"path": r["path"], "personal": bool(r.get("personal")),
             "name": Path(r["path"]).name} for r in roots]


def propose_move(folder: str, root: str | None = None) -> dict:
    """What the card shows before moving a project folder into a OneDrive root."""
    info = sync_of(folder)
    if info["synced"]:
        raise ValueError("Ce dossier est déjà synchronisé : " + info["label"])
    roots = addon_share.drive_roots()
    if not roots:
        raise ValueError("Aucun OneDrive synchronisé sur ce PC. Lance OneDrive, puis réessaie.")
    dest_root = None
    if root:
        key = os.path.normcase(os.path.normpath(root))
        dest_root = next((r for r in roots if os.path.normcase(r["path"]) == key), None)
    if not dest_root:
        dest_root = roots[0]
    name = Path(folder).name[:MAX_NAME] or "Projet"
    dest = str(Path(dest_root["path"]) / name)
    if os.path.exists(dest):
        raise ValueError(f"Le dossier « {dest} » existe déjà dans OneDrive.")
    return {
        "quoi": "lien", "mode": "deplacer", "dossier": folder, "destination": dest,
        "racine": dest_root["path"], "nom": name,
        "description": f"Déplacer le projet dans {Path(dest_root['path']).name} (OneDrive).",
    }


def move_into(folder: str, destination: str) -> str:
    """Move the folder on disk into OneDrive. The caller updates the project path afterwards."""
    src, dst = Path(folder), Path(destination)
    if not src.is_dir():
        raise ValueError("Dossier source introuvable.")
    if dst.exists():
        raise ValueError(f"« {dst} » existe déjà.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return str(dst)
