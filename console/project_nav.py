"""The project follows the conversation (docs/ihm.md, « Le projet suit la conversation »).

« On va travailler dans le projet Network »: typed in the bar, the page recognizes the project before
sending (static/js/mention.js); said to Claude, or in the middle of a discussion, Claude uses the tool
"projet": `ouvrir` shows the project's panel at once (nothing is saved), `rattacher` asks the user, on a
card, to move this discussion into the project; the move happens at the end of the turn and the
discussion keeps its preset (never more rights).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .config import Project

MAX_LISTED = 40   # projects named in the system prompt

SPEC = {
    "name": "projet",
    "description": (
        "Agit sur les projets de la console JARVIS (un projet = un dossier nommé, avec ses consignes, ses "
        "discussions et ses réglages). action = ouvrir : ouvre tout de suite le panneau du projet à côté de "
        "cette discussion (rien n'est enregistré) ; à utiliser quand l'utilisateur dit qu'il va travailler "
        "dans un projet, ou demande à le voir. action = rattacher : propose à l'utilisateur, sur une carte, de "
        "faire passer CETTE discussion dans le dossier du projet ; s'il accepte, la console la déplace à la "
        "fin de ton tour et la suite continue là-bas, avec les mêmes autorisations. Termine alors ta réponse "
        "sans attendre. Nom : celui d'un projet de la liste donnée dans ton contexte."),
    "inputSchema": {
        "type": "object",
        "required": ["action", "nom"],
        "properties": {
            "action": {"type": "string", "enum": ["ouvrir", "rattacher"]},
            "nom": {"type": "string", "description": "Nom (ou dossier) du projet, tel que dans la liste des projets."},
        },
    },
}


def fold(text: str) -> str:
    """Lower case, without accents, words separated by one space."""
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _base(folder: str) -> str:
    return Path(str(folder or "").rstrip("\\/")).name


def find(name: str, projects: list[Project]) -> tuple[Project | None, list[Project]]:
    """The project a name designates: its name or its folder's name, then a unique partial match. Returns
    (project, []) or (None, the candidates when the name is ambiguous)."""
    q = fold(name)
    if not q:
        return None, []
    exact = [p for p in projects if q in (fold(p.name), fold(_base(p.folder)))]
    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact
    part = [p for p in projects if q in fold(p.name) or q in fold(_base(p.folder))]
    return (part[0], []) if len(part) == 1 else (None, part)


def prompt_line(projects: list[Project]) -> str:
    """The projects of the account, for the system prompt: sorted, so the prompt stays the same between turns."""
    if not projects:
        return ""
    shown = sorted(projects, key=lambda p: fold(p.name))[:MAX_LISTED]
    more = len(projects) - len(shown)
    names = " ; ".join(f"« {p.name} » ({p.folder})" for p in shown)
    return (f"- Projets de la console : {names}{f' ; et {more} autres' if more > 0 else ''}. Quand l'utilisateur dit "
            "qu'il va travailler dans l'un d'eux, ou demande à le voir, ouvre son panneau avec l'outil projet "
            "(action ouvrir). Si cette discussion doit continuer dans ce projet, propose-le avec l'outil projet "
            "(action rattacher) : il valide sur une carte.")
