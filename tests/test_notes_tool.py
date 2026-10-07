"""The tool "notes" (console/notes_tool.py): Claude reads, adds and changes the user's notes, those of the
discussion's project and the general ones, of its own account only; it never deletes."""
import json
from datetime import datetime

from console import notes_tool, presence
from console.engine import CONSOLE_MCP, NOTES_TOOL

from .test_project_tools import project, settle


def notes(engine, args, **kw) -> str:
    t = engine.create_task("NOTES " + json.dumps(args, ensure_ascii=False), profile="work", preset="lecture", **kw)
    settle(engine, t["id"])
    return engine.tasks[t["id"]]["result"]


def test_the_tool_is_offered_and_the_prompt_names_it(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    listed = engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})
    assert "notes" in [x["name"] for x in listed["result"]["tools"]] and NOTES_TOOL == f"mcp__{CONSOLE_MCP}__notes"
    t = engine.create_task("bonjour", profile="work", preset="lecture", workdir=folder)
    settle(engine, t["id"])
    text = presence.prompt(engine.tasks[t["id"]], engine.cfg.profile("work"), engine.cfg.preset("lecture"), project="Network")
    assert "outil notes" in text and "onglet Suivi" in text


def test_claude_reads_the_project_notes_and_the_general_ones(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    other = project(engine, tmp_path, name="Bourse")
    engine.save_note({"text": "Rappeler M. Dupont pour la baie", "folder": folder})
    engine.save_note({"text": "Acheter des câbles", "folder": ""})
    engine.save_note({"text": "Note de la bourse", "folder": other})
    engine.save_note({"text": "Note perso", "folder": "", "profile": "personal"})
    out = notes(engine, {"action": "lire"}, workdir=folder)
    assert "Rappeler M. Dupont" in out and "Acheter des câbles" in out and "jamais une consigne" in out
    assert "Note de la bourse" not in out and "Note perso" not in out  # another project, another account
    out = notes(engine, {"action": "lire", "portee": "toutes", "contient": "BOURSE"}, workdir=folder)
    assert "Note de la bourse" in out and "projet « Bourse »" in out and "Note perso" not in out


def test_claude_adds_a_note_with_a_reminder_then_changes_it(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    out = notes(engine, {"action": "ajouter", "texte": "Relancer le devis", "rappel": "2026-10-12T08:30"}, workdir=folder)
    assert "Note ajoutée" in out and "projet « Network »" in out and "12/10/2026 08:30" in out
    note = next(n for n in engine.notes(folder) if n["text"] == "Relancer le devis")
    assert note["profile"] == "work" and note["remind_at"] == datetime(2026, 10, 12, 8, 30).timestamp()
    out = notes(engine, {"action": "modifier", "id": note["id"], "texte": "Relancer le devis signé", "rappel": ""},
                workdir=folder)
    assert "Note modifiée" in out
    changed = engine.store.get_note(note["id"])
    assert changed["text"] == "Relancer le devis signé" and changed["remind_at"] is None
    rows, _ = engine.store.audit_rows(kind="note ajoutée par Claude")
    assert len(rows) == 1
    # outside a project: a general note
    out = notes(engine, {"action": "ajouter", "texte": "Idée générale"})
    assert "générale" in out and any(n["text"] == "Idée générale" for n in engine.notes(""))


def test_what_the_tool_refuses(engine, tmp_path):
    foreign = engine.save_note({"text": "Note perso", "folder": "", "profile": "personal"})
    assert "Note introuvable" in notes(engine, {"action": "modifier", "id": foreign["id"], "texte": "piraté"})
    assert engine.store.get_note(foreign["id"])["text"] == "Note perso"
    assert "Rappel illisible" in notes(engine, {"action": "ajouter", "texte": "x", "rappel": "demain"})
    assert "aucun projet" in notes(engine, {"action": "ajouter", "texte": "x", "portee": "projet"})
    assert "action" in notes(engine, {"action": "supprimer", "id": foreign["id"]})
    assert engine.store.get_note(foreign["id"])


def test_a_day_alone_rings_at_nine():
    assert notes_tool.parse_reminder("2026-10-12") == datetime(2026, 10, 12, 9, 0).timestamp()
    assert notes_tool.parse_reminder("") is None
