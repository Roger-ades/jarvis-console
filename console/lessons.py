"""A lesson the console offers after a correction, never written before the click.

An undone file, a refusal that says why, or a follow-up that corrects: the discussion shows a card.
"Ce projet" appends the text to the folder's memory. "Tous les projets" appends it to the account's
skill "lecons", the same files the tool "retenir" writes, and only because the user asked by clicking.
A mail or a document never reaches this card: only a gesture of the user does.
"""
from __future__ import annotations

import re
from datetime import datetime

SKILL = "lecons"
FILE = "SKILL.md"
DESCRIPTION = ("Leçons retenues par l'utilisateur pour tous ses projets, après une correction dans JARVIS. "
               "À charger quand une méthode, une règle de métier ou une erreur déjà corrigée revient.")
CORRECTION = re.compile(r"^(non\b|pas ça|pas ca|c'est faux|c est faux|mauvais\b|corrige\b)", re.I)


def _day(when: float | None) -> str:
    return datetime.fromtimestamp(when or 0).strftime("%d/%m/%Y")


def entry(text: str, why: str, project: str, when: float | None) -> str:
    """One dated line, the shape the skill already uses (lecons.md)."""
    where = f", projet « {project} »" if project else ""
    cause = f" — {why}" if why else ""
    body = " ".join(str(text or "").split())
    return f"- {_day(when)}{where}{cause} : {body}"


def from_undo(path: str, project: str, when: float | None) -> str:
    name = str(path or "").replace("\\", "/").rstrip("/").split("/")[-1] or path
    return entry(f"ne pas refaire la modification annulée de {name} ({path})", "fichier annulé", project, when)


def from_refusal(message: str, tool: str, project: str, when: float | None) -> str:
    tool = " ".join(str(tool or "").split())
    why = f"refus de {tool}" if tool else "refus"
    return entry(message, why, project, when)


def from_message(text: str, project: str, when: float | None) -> str:
    return entry(text, "correction", project, when)


def is_correction(text: str) -> bool:
    return bool(CORRECTION.match(" ".join(str(text or "").split())))


def skill_args(text: str, why: str) -> dict:
    """Arguments of the tool "retenir" for one accepted lesson (the account's skill)."""
    return {"action": "proposer", "competence": SKILL, "fichier": FILE, "mode": "ajouter",
            "texte": text.strip(), "raison": why[:300], "description": DESCRIPTION}
