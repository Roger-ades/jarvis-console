"""Sending skills from one JARVIS to another through the team hub (hub/app.py, /api/v1/competences).

From the page Compétences the user ticks one skill, several, or whole groups, and the colleagues. The console
zips the skills' folders with partage.json (who, when, and each skill with its group and description) and gives
it to the hub. The zip waits in the recipient's inbox (console/equipe_boite.py), where one click installs the
skills in the account they pick, in the same groups; a skill already there of the same name is kept in
skills-anciennes/ beside skills/. Claude takes no part in it. A zip is data from someone else: every name is
checked before anything is written, nothing leaves the skills folder.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import time
import zipfile
from pathlib import Path

from . import knowledge, project_tools

INFO = "partage.json"
OLD = "skills-anciennes"      # beside skills/: Claude Code does not load what is kept there
MAX_SKILLS = 200
MAX_FILES = 400
MAX_FILE = 5 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
SKIP = {".git", "node_modules", "__pycache__", ".DS_Store"}


class ShareError(Exception):
    pass


def _files(folder: Path) -> list[Path]:
    out = []
    for dirpath, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not os.path.islink(os.path.join(dirpath, d)))
        for f in sorted(files):
            p = Path(dirpath, f)
            if f not in SKIP and not p.is_symlink():
                out.append(p)
    return out


# ---------------------------------------------------------------- export
def export(base: Path, names: list[str], groups: dict[str, str], dest_dir: Path, sender: str = "") -> tuple[Path, list[dict]]:
    """The skills `names` of the Claude Code folder `base` (an account's configuration folder or a project's
    .claude), with their group (groups: {nom: "Métier/Incendie"}), zipped into dest_dir."""
    known = {s["name"]: s for s in project_tools.skills(base)}
    names = sorted({str(n or "").strip().lower() for n in names if str(n or "").strip()})
    if not names:
        raise ShareError("Choisis au moins une compétence.")
    if len(names) > MAX_SKILLS:
        raise ShareError(f"{MAX_SKILLS} compétences au plus par envoi.")
    missing = [n for n in names if n not in known]
    if missing:
        raise ShareError(f"Compétence introuvable : {', '.join(missing)}.")
    skills, entries, size = [], [], 0
    for n in names:
        s = known[n]
        folder = base / "skills" / s["folder"]
        for p in _files(folder):
            sz = p.stat().st_size
            if sz > MAX_FILE:
                raise ShareError(f"{n}/{p.relative_to(folder).as_posix()} : plus de {MAX_FILE // 1024 // 1024} Mo.")
            size += sz
            entries.append((p, f"{n}/{p.relative_to(folder).as_posix()}"))
        skills.append({"nom": n, "groupe": groups.get(n, ""), "description": s["description"]})
    if len(entries) > MAX_FILES:
        raise ShareError(f"{len(entries)} fichiers : {MAX_FILES} au plus par envoi.")
    if size > MAX_TOTAL:
        raise ShareError(f"{size // 1024 // 1024} Mo : {MAX_TOTAL // 1024 // 1024} Mo au plus par envoi.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"competences-{secrets.token_hex(3)}.zip"
    info = {"format": 1, "type": "competences", "de": sender, "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "competences": skills}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(INFO, json.dumps(info, ensure_ascii=False, indent=1))
        for p, arc in entries:
            z.write(p, arc)
    return dest, skills


# ---------------------------------------------------------------- what a zip holds
def inspect(path: Path) -> dict:
    """A received zip, checked: {"de", "date", "competences": [{"nom", "groupe", "description", "fichiers"}],
    "taille"}; raises ShareError with the reason when it is not skills or would write outside them."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ShareError(f"zip illisible ({exc}).") from None
    with z:
        entries = [e for e in z.infolist() if not e.is_dir()]
        if len(entries) > MAX_FILES + 1:
            raise ShareError(f"{len(entries)} fichiers : {MAX_FILES} au plus.")
        counts: dict[str, int] = {}
        size = 0
        for e in entries:
            name = e.filename
            parts = name.split("/")
            if (not name or "\\" in name or name.startswith("/") or ":" in name
                    or any(x in ("", ".", "..") for x in parts)):
                raise ShareError(f"nom de fichier refusé dans le zip : {name[:120]}")
            if stat.S_ISLNK(e.external_attr >> 16):
                raise ShareError(f"lien symbolique refusé dans le zip : {name[:120]}")
            if e.file_size > MAX_FILE:
                raise ShareError(f"{name[:120]} : plus de {MAX_FILE // 1024 // 1024} Mo.")
            size += e.file_size
            if name == INFO:
                continue
            if len(parts) < 2:
                raise ShareError(f"fichier hors d'une compétence dans le zip : {name[:120]}")
            counts[parts[0]] = counts.get(parts[0], 0) + 1
        if size > MAX_TOTAL:
            raise ShareError(f"{size // 1024 // 1024} Mo : {MAX_TOTAL // 1024 // 1024} Mo au plus.")
        try:
            info = json.loads(z.read(INFO).decode("utf-8-sig"))
        except (KeyError, ValueError):
            raise ShareError(f"{INFO} manquant ou illisible.") from None
        if not isinstance(info, dict) or info.get("type") != "competences" or not isinstance(info.get("competences"), list):
            raise ShareError("ce zip ne contient pas de compétences JARVIS.")
        listed = {}
        for c in info["competences"]:
            n = str((c or {}).get("nom") or "") if isinstance(c, dict) else ""
            if not project_tools.NAME.match(n) or n in knowledge.RESERVED:
                raise ShareError(f"nom de compétence invalide : {n[:60]}")
            try:
                g = knowledge.group_path(c.get("groupe"))
            except knowledge.KnowledgeError:
                g = ""
            listed[n] = {"nom": n, "groupe": g, "description": " ".join(str(c.get("description") or "").split())[:300]}
        if not listed or set(listed) != set(counts):
            raise ShareError("le zip ne correspond pas à sa liste de compétences.")
        names = set(z.namelist())
        for n in listed:
            if f"{n}/SKILL.md" not in names:
                raise ShareError(f"SKILL.md manquant dans la compétence {n}.")
    return {"de": " ".join(str(info.get("de") or "").split())[:120], "date": str(info.get("date") or "")[:25],
            "competences": [{**listed[n], "fichiers": counts[n]} for n in sorted(listed)], "taille": size}


def install(path: Path, base: Path, only: list[str] | None = None, keep: bool = True) -> dict:
    """Unzips the skills (all, or those of `only`) into base/skills; one already there of the same name goes to
    base/skills-anciennes/<nom>-<date>, or is dropped when not `keep` (a shared skill's previous version, already
    on the hub). Returns the inspection, with "installees": [{"nom", "groupe", "ancien"}]."""
    info = inspect(path)
    chosen = [c for c in info["competences"] if only is None or c["nom"] in set(only)]
    if not chosen:
        raise ShareError("aucune compétence choisie.")
    root = base / "skills"
    root.mkdir(parents=True, exist_ok=True)
    present = {s["name"]: s["folder"] for s in project_tools.skills(base)}
    # staged beside skills/, never inside: Claude Code would see a half-written skill
    stage = base / f".import-competences-{secrets.token_hex(3)}"
    done = []
    try:
        wanted = {c["nom"] for c in chosen}
        with zipfile.ZipFile(path) as z:
            for e in z.infolist():
                if e.is_dir() or e.filename == INFO or e.filename.split("/")[0] not in wanted:
                    continue
                target = stage / e.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(e) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for c in chosen:
            n, kept = c["nom"], None
            for old in dict.fromkeys(x for x in (present.get(n), n) if x):
                if (root / old).exists() and not keep:
                    gone = stage / f".ancienne-{old}"
                    (root / old).rename(gone)
                elif (root / old).exists():
                    to = base / OLD / f"{n}-{stamp}"
                    to = to if not to.exists() else to.with_name(f"{to.name}-{secrets.token_hex(2)}")
                    to.parent.mkdir(parents=True, exist_ok=True)
                    (root / old).rename(to)
                    kept = kept or to
            (stage / n).rename(root / n)
            done.append({"nom": n, "groupe": c["groupe"], "ancien": str(kept) if kept else ""})
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return {**info, "installees": done}
