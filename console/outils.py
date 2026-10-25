"""Project outils-scripts: deterministic functions Claude calls like tools, after a validation card.

Files live under ``.claude/outils/<nom>.py`` with ``<nom>.json`` (inputSchema). Fingerprinted like
actions: a change drops the tool until the user validates again. Execution inherits the discussion's
preset and path policy; args arrive as JSON on stdin.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_FILE = 200_000
MAX_OUT = 200_000
MAX_OUTILS = 12
TIMEOUT = 60

PROPOSE_EXTRA = {
    "quoi_outil": "outil",
    "description": (
        "outil : une fonction déterministe du projet (.claude/outils/<nom>.py + <nom>.json) que Claude "
        "appellera comme un outil. L'utilisateur voit le code et valide ; rien n'est écrit sans son clic."
    ),
}


def folder_of(project: Path) -> Path:
    return Path(project) / ".claude" / "outils"


def _read(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) > MAX_FILE:
        raise ValueError(f"{path.name} dépasse {MAX_FILE // 1000} Ko.")
    return data


def fingerprint(py: Path, meta: Path) -> str:
    h = hashlib.sha256()
    for p in (py, meta):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(_read(p) if p.is_file() else b"")
        h.update(b"\0")
    return h.hexdigest()


def list_outils(project: str, approved: dict[str, str] | None = None) -> list[dict]:
    """Approved map: nom → fingerprint. Without a match the outil is « à valider »."""
    approved = approved or {}
    root = folder_of(Path(project))
    if not root.is_dir():
        return []
    out = []
    for meta in sorted(root.glob("*.json"))[:MAX_OUTILS]:
        nom = meta.stem
        if not NAME.match(nom):
            continue
        py = meta.with_suffix(".py")
        if not py.is_file():
            continue
        try:
            spec = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fp = fingerprint(py, meta)
        status = "ok" if approved.get(nom) == fp else ("modifiee" if nom in approved else "nouvelle")
        out.append({
            "nom": nom,
            "description": str(spec.get("description") or "")[:500],
            "ecrit": bool(spec.get("ecrit")),
            "schema": spec.get("inputSchema") or {"type": "object", "properties": {}},
            "hash": fp,
            "status": status,
            "fichier": str(py),
        })
    return out


def propose(args: dict, project: str) -> dict:
    nom = str(args.get("nom") or "").strip().lower()
    if not NAME.match(nom):
        raise ValueError("« nom » : minuscules, chiffres, tirets (40 caractères au plus).")
    code = str(args.get("consigne") or args.get("code") or "")
    if not code.strip():
        raise ValueError("Le code Python de l'outil est requis (champ consigne).")
    if len(code.encode("utf-8")) > MAX_FILE:
        raise ValueError("Code trop volumineux.")
    schema = args.get("schema") if isinstance(args.get("schema"), dict) else {
        "type": "object", "properties": {},
    }
    meta = {
        "name": nom,
        "description": str(args.get("description") or "")[:500],
        "ecrit": bool(args.get("ecrit")),
        "inputSchema": schema,
    }
    root = folder_of(Path(project))
    existing_py = root / f"{nom}.py"
    remplace = existing_py.read_text(encoding="utf-8") if existing_py.is_file() else None
    return {
        "quoi": "outil", "nom": nom, "description": meta["description"], "ecrit": meta["ecrit"],
        "contenu": code, "meta": meta, "fichier": str(existing_py),
        "projet": Path(project).name, "dossier": project,
        **({"remplace": remplace} if remplace is not None else {}),
    }


def write(prop: dict) -> str:
    root = folder_of(Path(prop["dossier"]))
    root.mkdir(parents=True, exist_ok=True)
    py = root / f"{prop['nom']}.py"
    meta = root / f"{prop['nom']}.json"
    py.write_text(prop["contenu"], encoding="utf-8", newline="\n")
    meta.write_text(json.dumps(prop["meta"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fingerprint(py, meta)


def run(project: str, nom: str, params: dict | None, *, cwd: str, timeout: int = TIMEOUT) -> dict:
    root = folder_of(Path(project))
    py = root / f"{nom}.py"
    if not py.is_file():
        raise FileNotFoundError(f"Outil « {nom} » introuvable.")
    payload = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_FILE:
        raise ValueError("Paramètres trop volumineux.")
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(py)],
            input=payload, capture_output=True, timeout=timeout, cwd=cwd or project,
            env={k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")},
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Outil « {nom} » : délai de {timeout}s dépassé.") from exc
    out = (proc.stdout or b"")[:MAX_OUT].decode("utf-8", errors="replace")
    err = (proc.stderr or b"")[:MAX_OUT].decode("utf-8", errors="replace")
    return {
        "code": proc.returncode, "sortie": out, "erreur": err,
        "duree": round(time.time() - started, 2),
    }
