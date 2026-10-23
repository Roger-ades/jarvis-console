"""Prépare une discussion pour l'équipe : chemins du projet masqués, résultats d'outils retirables."""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from pathlib import Path

from . import library
from .config import Profile

PLACEHOLDER = "{{PROJET}}"
REMOVED = "[retiré au partage]"
_PATH = re.compile(r"[A-Za-z]:\\[^\"\\\s]+")


def variants(folder: str) -> list[str]:
    raw = str(folder).rstrip("\\/")
    forms = {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}
    doubled = raw.replace("\\", "\\\\")
    if doubled != raw:
        forms.add(doubled)
    return sorted(forms, key=len, reverse=True)


def mask(text: str, folder: str) -> str:
    for form in variants(folder):
        text = text.replace(form, PLACEHOLDER)
    return text


def unmask(text: str, folder: str) -> str:
    """Le chemin local reprend la place de {{PROJET}}, échappé comme dans le JSON d'origine."""
    local = str(folder).rstrip("\\/").replace("\\", "\\\\")
    return text.replace(PLACEHOLDER, local)


def _strip_results(obj: dict) -> None:
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            block["content"] = REMOVED


def _tool_name(obj: dict) -> str:
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return str(block.get("name") or "")
    return ""


def inspect_text(text: str, folder: str) -> dict:
    odoo = mail = sharepoint = outside = 0
    known = {v.lower() for v in variants(folder)}
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = {}
        name = _tool_name(obj).lower()
        if "odoo" in name:
            odoo += 1
        if "outlook" in name or name.endswith("mail") or "mail" in name:
            mail += 1
        if "sharepoint" in name or "office" in name:
            sharepoint += 1
        masked = mask(line, folder)
        for found in _PATH.findall(masked):
            if PLACEHOLDER in found:
                continue
            if any(found.lower().startswith(prefix) for prefix in known):
                continue
            outside += 1
    sensitive = bool(odoo or mail or sharepoint or outside)
    parts = []
    if odoo:
        parts.append(f"{odoo} résultat{'s' if odoo > 1 else ''} Odoo")
    if mail:
        parts.append(f"{mail} mail{'s' if mail > 1 else ''}")
    if sharepoint:
        parts.append(f"{sharepoint} fichier{'s' if sharepoint > 1 else ''} Office ou SharePoint")
    if outside:
        parts.append(f"{outside} chemin{'s' if outside > 1 else ''} hors projet")
    return {
        "odoo": odoo, "mail": mail, "sharepoint": sharepoint, "outside": outside,
        "sensitive": sensitive,
        "summary": ", ".join(parts) if parts else "Rien de sensible repéré.",
    }


def pack(text: str, folder: str, mode: str) -> bytes:
    if mode not in {"complete", "stripped"}:
        raise ValueError("mode inconnu")
    out = io.StringIO()
    for line in text.splitlines():
        body = line
        if mode == "stripped" and body.startswith("{"):
            try:
                obj = json.loads(body)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                _strip_results(obj)
                body = json.dumps(obj, ensure_ascii=False)
        out.write(mask(body, folder) + "\n")
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("transcript.jsonl", out.getvalue())
    return blob.getvalue()


def unpack(blob: bytes, folder: str) -> tuple[str, str]:
    """(nouveau id de session, texte jsonl prêt pour ce dossier)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        text = archive.read("transcript.jsonl").decode("utf-8")
    new_id = str(uuid.uuid4())
    lines = []
    for line in unmask(text, folder).splitlines():
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and obj.get("sessionId"):
                obj["sessionId"] = new_id
                line = json.dumps(obj, ensure_ascii=False)
        lines.append(line)
    return new_id, "\n".join(lines) + "\n"


def preview(profile: Profile, session_id: str, folder: str) -> dict:
    path = library.find_transcript(profile, session_id)
    if path is None:
        raise FileNotFoundError(session_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    found = inspect_text(text, folder)
    meta = library._summarize(path)
    return {"title": meta.get("title") or meta.get("first_prompt") or session_id, **found}


def archive(profile: Profile, session_id: str, folder: str, mode: str) -> tuple[bytes, dict]:
    path = library.find_transcript(profile, session_id)
    if path is None:
        raise FileNotFoundError(session_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    return pack(text, folder, mode), preview(profile, session_id, folder)


def install_transcript(profile: Profile, folder: str, blob: bytes) -> str:
    new_id, text = unpack(blob, folder)
    dest = library.config_dir(profile) / "projects" / library.project_slug(folder)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{new_id}.jsonl").write_text(text, encoding="utf-8", newline="\n")
    return new_id
