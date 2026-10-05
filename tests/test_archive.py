"""Archiving discussions (docs/ihm.md, « Archivage des discussions »): by hand, several at once, and after N days
of inactivity; an archived discussion stays findable and comes back when the user writes in it."""
import time

import pytest

from console.engine import TaskError

from .conftest import task_status, wait_for
from .test_project_tools import client  # noqa: F401 - fixture (with the engine running)


def done_task(engine, text="bonjour", **kw):
    t = engine.create_task(text, profile="work", preset="lecture", **kw)
    wait_for(lambda: task_status(engine, t["id"]) == "done" and t["id"] not in engine.runs)
    return engine.tasks[t["id"]]


def test_an_archived_discussion_leaves_the_lists_but_stays_findable(engine):
    t = done_task(engine, "facture Etimia")
    assert engine.public(t)["archived"] == 0
    r = engine.update_task(t["id"], {"archived": True})
    assert r["archived"] and r["closed"]
    assert engine.search("Etimia")["tasks"][0]["archived"]  # Ctrl+K still finds it
    assert engine.store.get_task(t["id"])["archived"]  # kept across restarts
    rows, _ = engine.store.audit_rows(kind="discussions archivées")
    assert len(rows) == 1
    # opening its window again brings it back
    r = engine.update_task(t["id"], {"closed": False})
    assert r["archived"] == 0 and not r["closed"]
    assert any("désarchivée" in (e["data"].get("text") or "") for e in engine.store.events(t["id"]) if e["kind"] == "info")


def test_writing_in_an_archived_discussion_brings_it_back(engine):
    t = done_task(engine)
    engine.update_task(t["id"], {"archived": True})
    engine.followup(t["id"], "encore une question")
    assert engine.tasks[t["id"]]["archived"] == 0
    wait_for(lambda: task_status(engine, t["id"]) == "done" and t["id"] not in engine.runs)


def test_a_running_discussion_is_not_archived(engine):
    t = engine.create_task("SLEEP 3", profile="work", preset="lecture")
    wait_for(lambda: task_status(engine, t["id"]) == "running")
    with pytest.raises(TaskError) as e:
        engine.update_task(t["id"], {"archived": True})
    assert e.value.status == 409
    other = done_task(engine)
    r = engine.archive_tasks([t["id"], other["id"], "inconnue"])
    assert r["changed"] == [other["id"]] and r["skipped"] == [t["id"]]
    engine.cancel(t["id"])


def test_several_at_once_through_the_route(client):  # noqa: F811
    eng, hd = client.engine, {"X-Console-Token": client.token}
    a, b = done_task(eng, "un"), done_task(eng, "deux")
    r = client.post("/api/tasks/archive", json={"ids": [a["id"], b["id"]]}, headers=hd).json()
    assert sorted(r["changed"]) == sorted([a["id"], b["id"]]) and all(t["archived"] for t in r["tasks"])
    r = client.post("/api/tasks/archive", json={"ids": [a["id"]], "archived": False}, headers=hd).json()
    assert r["changed"] == [a["id"]] and eng.tasks[a["id"]]["archived"] == 0 and eng.tasks[b["id"]]["archived"]
    assert client.post("/api/tasks/archive", json={"ids": []}, headers=hd).status_code == 400
    assert client.post("/api/tasks/archive", json={"ids": [a["id"]]}).status_code == 401


def test_archived_discussions_leave_the_inbox(engine):
    engine.inbox(start=True)
    engine._inbox_since = 0  # what ends now is to read
    t = done_task(engine)
    assert any(e.get("task_id") == t["id"] for e in engine.inbox()["entries"])
    engine.update_task(t["id"], {"archived": True})
    assert not any(e.get("task_id") == t["id"] for e in engine.inbox()["entries"])


def test_automatic_archiving_after_n_days(engine):
    old, pinned, warm, recent, widget = (done_task(engine, x) for x in ("vieille", "épinglée", "au chaud", "récente", "widget"))
    long_ago = time.time() - 20 * 86400
    for t in (old, pinned, warm, widget):
        t["created"] = t["started"] = t["ended"] = long_ago
    pinned["pinned"] = True
    warm["keep_warm_until"] = time.time() + 3600
    engine.widgets.append({"id": "w1", "task": widget["id"]})
    assert engine.auto_archive() == 0  # off by default
    engine.cfg.history.auto_archive_days = 14
    assert engine.auto_archive() == 1
    assert engine.tasks[old["id"]]["archived"]
    assert not any(engine.tasks[t["id"]]["archived"] for t in (pinned, warm, recent, widget))
    rows, _ = engine.store.audit_rows(kind="archivage automatique")
    assert len(rows) == 1
    assert engine.auto_archive() == 0  # already archived
    engine.keep_warm(warm["id"], 0)
