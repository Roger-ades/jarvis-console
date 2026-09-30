"""Team mode: sub-agents still working in the background after the lead's turn must keep being
answered (permissions), and the session only closes once they are done."""
from .conftest import task_status, wait_for


def done(engine, tid, timeout=20):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs, timeout=timeout)


def test_background_subagent_is_still_answered_after_the_turn(engine):
    t = engine.create_task("BG 1.0\nlance les inventaires", profile="work", preset="edition")
    done(engine, t["id"])
    result = engine.tasks[t["id"]]["result"]
    assert "Rapport du sous-agent : Read:ok" in result, result
    assert "doesn't want" not in result


def test_session_still_closes_without_background_work(engine):
    t = engine.create_task("bonjour", profile="work", preset="edition")
    done(engine, t["id"], timeout=10)
    assert task_status(engine, t["id"]) == "done"
