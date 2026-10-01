"""« Renommer avec l'IA » : Claude reads the session and gives it a title, outside the conversation."""
import time

import pytest

from console.engine import TaskError, _clean_title

from .conftest import task_status, wait_for


def done_task(engine, prompt):
    t = engine.create_task(prompt, profile="work", preset="lecture")
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    return t["id"]


def test_claude_renames_the_task_from_its_content(engine):
    tid = done_task(engine, "Prépare le devis Dupont pour trois caméras dôme")
    events_before = len(engine.store.events(tid))
    t = engine.ai_title(tid)
    assert t["title"] == "Prépare le devis Dupont pour" == engine.tasks[tid]["title"]
    # nothing written in the conversation, the account's limits refreshed on the way
    assert len(engine.store.events(tid)) == events_before
    assert engine.limits["work"]["windows"]["five_hour"]["used"] == pytest.approx(0.3)


def test_context_keeps_the_first_request_and_the_latest_exchanges(engine):
    tid = done_task(engine, "Audit du réseau du site de Lyon")
    for n in range(40):
        engine._event(tid, "user", {"text": f"question {n} " + "x" * 300})
        engine._event(tid, "text", {"text": f"réponse {n}", "parent": None})
    engine._event(tid, "tool", {"name": "Write", "target": r"C:\clients\lyon\schema.png", "parent": None})
    engine._event(tid, "text", {"text": "détail d'un sous-agent", "parent": "toolu_x"})
    ctx = engine._title_context(engine.tasks[tid], budget=3000)
    assert ctx.startswith("Première demande : Audit du réseau du site de Lyon")
    assert "[…]" in ctx and "question 0 " not in ctx and "question 39 " in ctx
    assert ctx.endswith(r"Action : Write C:\clients\lyon\schema.png")
    assert "sous-agent" not in ctx and len(ctx) <= 3100


def test_errors_are_explained(engine):
    tid = done_task(engine, "TITRE_ECHEC de test")
    with pytest.raises(TaskError, match="Erreur simulée"):
        engine.ai_title(tid)
    assert engine.tasks[tid]["title"] == "TITRE_ECHEC de test"
    engine.limits["work"] = {"status": "rejected", "resets_at": time.time() + 600, "windows": {}}
    with pytest.raises(TaskError, match="Limite d'utilisation atteinte") as exc:
        engine.ai_title(tid)
    assert exc.value.status == 429


def test_title_cleanup():
    assert _clean_title("Titre : « Devis Dupont télésurveillance ».") == "Devis Dupont télésurveillance"
    assert _clean_title("**Titre :** Migration Odoo 17\n\nExplication…") == "Migration Odoo 17"
    assert _clean_title("# Pourquoi ça plante ?") == "Pourquoi ça plante ?"
    assert _clean_title("   \n") == ""
    assert len(_clean_title("mot " * 40)) <= 80
