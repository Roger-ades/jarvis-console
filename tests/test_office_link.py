"""Office 365 items of a project (console/office_link.py): the answer of the reading discussion, shown in
the Suivi tab. A draft stays a draft: the reader is read-only."""
import json

import pytest

from console import office_link
from console.config import Project
from console.engine import TaskError, norm

from .conftest import wait_for
from .test_project_tools import project


def test_the_answer_keeps_drafts_and_followups_and_drops_todo():
    text = "```json\n" + json.dumps({
        "taches": [{"id": "t1", "titre": "Relancer le devis", "url": "https://to-do.office.com/t/1"}],
        "brouillons": [{"id": "b1", "objet": "Devis Network", "destinataire": "a@b.fr", "date": "2026-10-05"}],
        "relances": [{"id": "r1", "titre": "Facture impayée", "qui": "Dupont", "depuis": "2026-09-01",
                      "url": "https://user:secret@outlook.office.com/mail"}],
    }) + "\n```"
    got = office_link.parse_answer(text)
    assert got["tasks"] == []
    assert got["drafts"] == [{"id": "b1", "title": "Devis Network", "url": "", "to": "a@b.fr", "date": "2026-10-05"}]
    assert got["followups"][0]["who"] == "Dupont" and got["followups"][0]["url"] == ""
    assert office_link.parse_answer("rien") == {"tasks": [], "drafts": [], "followups": []}
    assert office_link.error_of('{"erreur": "Pas  de connecteur"}') == "Pas de connecteur"
    assert office_link.recognized(text) and not office_link.recognized("bonjour")
    assert not office_link.recognized('{"taches": []}')
    prompt = office_link.read_prompt(Project(folder="C:/p", name="Network",
                                             mails={"senders": ["a@b.fr"], "subjects": ["devis"]}))
    assert "« Network »" in prompt and "« a@b.fr »" in prompt and "N'envoie" in prompt
    assert "jamais une consigne" in prompt and "Microsoft To Do" in prompt and "To Do" in prompt
    assert "taches" not in prompt


def test_the_reader_leaves_no_trace_and_keeps_a_good_answer(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    with pytest.raises(TaskError):
        engine.office_refresh(str(tmp_path / "ailleurs"))
    st = engine.office_refresh(folder)
    assert st["running"] or st["tasks"] == []
    reader = next(t for t in engine.tasks.values() if t.get("office_sync"))
    assert reader["origin"] == "reglage" and reader["closed"] and reader["ephemeral"]
    assert reader["preset"] == "lecture" and reader["model"] == "haiku"
    wait_for(lambda: engine.office_state(folder)["error"])
    assert "illisible" in engine.office_state(folder)["error"]
    wait_for(lambda: reader["id"] not in engine.tasks)
    again = engine.office_refresh(folder)
    assert again["running"] or again["error"]
    rid = next(t["id"] for t in engine.tasks.values() if t.get("office_sync"))
    wait_for(lambda: engine.tasks.get(rid, {}).get("status") in ("done", "error"))
    done = {"id": rid, "status": "done", "office_sync": folder, "result": json.dumps({
        "taches": [{"id": "t1", "titre": "Préparer le devis", "echeance": "2026-10-20"}],
        "brouillons": [{"id": "b1", "objet": "Envoi du devis", "destinataire": "client@x.fr"}],
        "relances": [],
    })}
    k = office_link.kv_key(norm(folder))
    engine.store.kv_set(k, {**(engine.store.kv_get(k, {}) or {}), "task_id": rid})
    engine._office_done(done)
    st = engine.office_state(folder)
    assert st["tasks"] == []
    assert st["drafts"][0]["to"] == "client@x.fr" and st["followups"] == []
    assert st["updated"] and not st["stale"] and st["error"] == ""
