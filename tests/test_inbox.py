"""The inbox (docs/boite-de-reception.md): what waits for the user, computed from the tasks, the routines and
the notes; only the marks (read, dismissed) are kept."""
import json
import time

import pytest

from console import inbox
from console.engine import TaskError

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture

ODOO = {"model": "sale.order", "values": {"partner_id": 1}}
ASK_TOOL = f"TOOL mcp__odoo__create_record {json.dumps(ODOO)}"  # waits for a validation with the preset Brouillons


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error", "cancelled") and tid not in engine.runs)


def ids(engine, section=None):
    return [e["id"] for e in engine.inbox()["entries"] if section in (None, e["section"])]


# ------------------------------------------------------------ the pure function

def test_plain_excerpt_has_no_link_nor_markup():
    text = ("# Bilan\n\n**3 devis** à relancer : [Dupont](https://evil.example/x) et `sale_order`.\n"
            "![logo](https://img.example/a.png)\n```python\nprint('code')\n```\n> cité\n- point | colonne")
    assert inbox.plain(text) == "Bilan 3 devis à relancer : Dupont et sale_order. cité point colonne"
    assert inbox.plain("a" * 400).endswith("…") and len(inbox.plain("a" * 400)) == inbox.EXCERPT


def _task(tid, status="done", ended=1000.0, **extra):
    return {"id": tid, "title": f"Tâche {tid}", "status": status, "profile": "work", "workdir": "/p",
            "ended": ended, "result": f"Réponse de {tid}", **extra}


def test_entries_sections_order_and_marks():
    now, since = 2000.0, 500.0
    pending = [{"id": "a1", "kind": "permission", "tool": "Bash", "target": "npm install", "reason": "écriture",
                "input": {"command": "npm install"}, "created": 1900.0},
               {"id": "a2", "kind": "hook", "tool": "Write", "target": "x.txt", "input": {}, "created": 1800.0},
               {"id": "q1", "kind": "question", "tool": "AskUserQuestion", "created": 1950.0,
                "input": {"questions": [{"question": "Quelle couleur ?"}]}}]
    tasks = [
        _task("t1", status="awaiting", ended=None, pending=pending),
        _task("t2"),                                              # ended, unread
        _task("t3", read_at=1500.0),                               # read
        _task("t4", ended=100.0),                                  # before the inbox existed
        _task("t5", status="error", error="Claude Code s'est arrêté"),
        _task("t6", status="cancelled"),                           # the user stopped it: never in the inbox
        _task("t7", ended=1100.0, routine={"id": "r1", "name": "Veille"}),
        _task("t8", ended=1200.0, routine={"id": "r1", "name": "Veille"}),
        _task("t9", status="running", ended=None, session_started=True,
              expired=[{"aid": "x1", "kind": "permission", "tool": "Bash", "target": "rm a", "ts": 1300.0},
                       {"aid": "x0", "kind": "question", "tool": "AskUserQuestion", "ts": 100.0}],
              waiting_displays=[{"key": "k1", "titre": "Offre", "ts": 1400.0}]),
    ]
    routines = [{"id": "r2", "name": "Relances", "profile": "work", "runs": [
        {"ts": 1600.0, "status": "non lancée", "error": "preset désactivé", "read": False},
        {"ts": 1500.0, "status": "non lancée", "error": "déjà vue", "read": True},
        {"ts": 1400.0, "status": "manquée", "error": "ancienne version"},      # no mark: read
        {"ts": 1300.0, "status": "done", "task_id": "t2", "read": False}]}]    # a launched run: its task counts
    notes = [{"id": "n1", "text": "Appeler Dupont\ndétails", "remind_at": 1990.0, "reminded": False, "profile": "work"},
             {"id": "n2", "text": "Plus tard", "remind_at": 9999.0, "reminded": False},
             {"id": "n3", "text": "Vu", "remind_at": 10.0, "reminded": True}]
    profiles = {"work": {"name": "Travail", "color": "#ffb347"}}
    out = inbox.entries(tasks, routines, notes, now=now, since=since, approval_timeout=1800, profiles=profiles)
    assert [e["id"] for e in out] == [
        "valider:t1:a2", "valider:t1:a1",        # the one that expires first, first
        "valider:t1:q1", "expiree:t9:x1", "affichage:t9:k1", "fin:t5",
        "execution:r2:1600.0", "routine:r1", "fin:t2",
        "rappel:n1"]
    by = {e["id"]: e for e in out}
    a1 = by["valider:t1:a1"]
    assert a1["actions"] == ["approve", "deny", "open"] and a1["expires"] == 1900.0 + 1800
    assert a1["input"] == {"command": "npm install"} and a1["summary"] == "npm install"
    assert a1["color"] == "#ffb347" and a1["profile_name"] == "Travail"
    assert by["valider:t1:q1"]["actions"] == ["open"] and by["valider:t1:q1"]["summary"] == "Quelle couleur ?"
    assert by["expiree:t9:x1"]["actions"][0] == "resume" and by["expiree:t9:x1"]["summary"] == "Bash · rm a"
    assert by["routine:r1"]["count"] == 2 and by["routine:r1"]["task_ids"] == ["t8", "t7"]
    assert by["routine:r1"]["title"] == "Veille" and by["routine:r1"]["excerpt"] == "Réponse de t8"
    assert by["fin:t5"]["section"] == "todo" and by["fin:t5"]["actions"][0] == "retry"
    assert by["rappel:n1"]["title"] == "Appeler Dupont"
    assert inbox.counts(out) == {"todo": 6, "read": 3, "reminder": 1}
    light = inbox.entries(tasks, routines, notes, now=now, since=since, approval_timeout=1800, light=True)
    assert "input" not in light[0] and all(not e.get("excerpt") for e in light)
    # before a page ever asked for the inbox: only what is live
    live = inbox.entries(tasks, routines, notes, now=now, since=None, approval_timeout=1800)
    assert [e["id"] for e in live] == ["valider:t1:a2", "valider:t1:a1", "valider:t1:q1", "rappel:n1"]


def test_resume_message_is_the_consoles_own():
    msg = inbox.resume_message({"kind": "permission", "tool": "Bash", "target": "npm install"}, 30)
    assert msg.startswith("[Console JARVIS]") and "Bash · npm install" in msg and "30 min" in msg
    assert "ta question" in inbox.resume_message({"kind": "question"}, 30)


# ------------------------------------------------------------ the engine

def test_a_finished_discussion_is_unread_until_looked_at(engine):
    engine.inbox(start=True)
    t = engine.create_task("bonjour", profile="work")
    settle(engine, t["id"])
    entry = next(e for e in engine.inbox()["entries"] if e["id"] == f"fin:{t['id']}")
    assert entry["section"] == "read" and entry["excerpt"] == "écho:bonjour"
    assert engine.state()["inbox"]["read"] == 1
    assert engine.inbox_mark(tasks=[t["id"]]) == {"marked": 0}
    assert f"fin:{t['id']}" not in ids(engine)
    engine.followup(t["id"], "encore")   # a new turn not looked at: unread again
    settle(engine, t["id"])
    assert f"fin:{t['id']}" in ids(engine, "read")
    assert engine.inbox_mark(section="read")["marked"] == 1 and not ids(engine, "read")


def test_what_ended_before_the_inbox_counts_as_read(engine):
    t = engine.create_task("avant", profile="work")
    settle(engine, t["id"])
    assert engine.state()["inbox"]["read"] == 0
    engine.inbox(start=True)
    assert f"fin:{t['id']}" not in ids(engine)
    assert engine.store.kv_get("inbox_since") == engine._inbox_since


def test_an_approval_is_decided_from_the_inbox(engine):
    engine.inbox(start=True)
    t = engine.create_task(ASK_TOOL, profile="work", preset="brouillons")
    tid = t["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    aid = engine.tasks[tid]["pending"][0]["id"]
    entry = engine.inbox()["entries"][0]
    assert entry["id"] == f"valider:{tid}:{aid}" and entry["actions"] == ["approve", "deny", "open"]
    assert entry["input"] == ODOO and entry["tool"] == "mcp__odoo__create_record"
    assert engine.state()["inbox"]["todo"] == 1
    assert engine.inbox_mark(ids=[entry["id"]], dismiss=True) == {"marked": 0}   # decided, never dismissed
    engine.decide(tid, aid, "deny", "pas ce client", via="boite")
    settle(engine, tid)
    assert f"valider:{tid}:{aid}" not in ids(engine)
    rows, _ = engine.store.audit_rows(task_id=tid, kind="validation")
    assert rows[0]["detail"]["par"] == "utilisateur (boîte de réception)"


def test_an_expired_approval_can_be_resumed(engine):
    engine.inbox(start=True)
    t = engine.create_task(ASK_TOOL, profile="work", preset="brouillons")
    tid = t["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    appr = next(iter(engine.runs[tid].approvals.values()))
    appr.created -= 3 * 3600   # the watchdog refuses it at its next round
    settle(engine, tid)
    rec = engine.tasks[tid]["expired"][0]
    assert rec["aid"] == appr.id and rec["tool"] == "mcp__odoo__create_record"
    entry = next(e for e in engine.inbox()["entries"] if e["kind"] == "expired")
    assert entry["id"] == f"expiree:{tid}:{appr.id}" and entry["actions"] == ["resume", "open", "dismiss"]
    engine.resume_expired(tid, appr.id)
    settle(engine, tid)
    assert not engine.tasks[tid].get("expired")
    asked = [e["data"]["text"] for e in engine.store.events(tid) if e["kind"] == "user"][-1]
    assert asked.startswith("[Console JARVIS] L'utilisateur est de retour.")
    assert "mcp__odoo__create_record" in asked
    with pytest.raises(TaskError) as e:
        engine.resume_expired(tid, appr.id)
    assert e.value.status == 404
    rows, _ = engine.store.audit_rows(task_id=tid, kind="reprise après expiration")
    assert rows


def test_a_cancelled_approval_is_not_an_expired_one(engine):
    engine.inbox(start=True)
    t = engine.create_task(ASK_TOOL, profile="work", preset="brouillons")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")
    engine.cancel(t["id"])
    settle(engine, t["id"])
    assert not engine.tasks[t["id"]].get("expired") and not ids(engine)


def test_a_display_waiting_for_a_click(engine):
    engine.inbox(start=True)
    offer = {"titre": "Offre", "blocs": [{"type": "choix", "question": "Laquelle ?", "options": ["A", "B"]}]}
    t = engine.create_task("PRESENT " + json.dumps(offer, ensure_ascii=False), profile="work", preset="lecture")
    tid = t["id"]
    settle(engine, tid)
    key = engine.tasks[tid]["waiting_displays"][0]["key"]
    entry = next(e for e in engine.inbox()["entries"] if e["kind"] == "choice")
    assert entry["id"] == f"affichage:{tid}:{key}" and entry["summary"] == "Offre" and entry["key"] == key
    engine.display_answer(tid, key, 0, choice=[1])   # the answer goes to the session: no longer waiting
    settle(engine, tid)
    assert not any(e["kind"] == "choice" for e in engine.inbox()["entries"])
    # a display without a choice never waits; one dismissed goes away
    t2 = engine.create_task("PRESENT " + json.dumps(offer, ensure_ascii=False), profile="work", preset="lecture")
    settle(engine, t2["id"])
    eid = next(e["id"] for e in engine.inbox()["entries"] if e["kind"] == "choice")
    assert engine.inbox_mark(ids=[eid]) == {"marked": 0}   # marking read is not dismissing
    assert engine.inbox_mark(ids=[eid], dismiss=True) == {"marked": 1}
    assert not engine.tasks[t2["id"]]["waiting_displays"]


def test_routine_results_and_runs_not_launched(engine):
    engine.inbox(start=True)
    r = engine.save_routine({"name": "Veille", "prompt": "écho veille", "profile": "work", "preset": "lecture",
                             "schedule": {"kind": "interval", "every_min": 60}, "open_window": False})
    tasks = [engine.run_routine(r["id"]) for _ in range(2)]
    for t in tasks:
        settle(engine, t["id"])
    entry = next(e for e in engine.inbox()["entries"] if e["kind"] == "routine")
    assert entry["id"] == f"routine:{r['id']}" and entry["count"] == 2 and entry["title"] == "Veille"
    assert set(entry["task_ids"]) == {t["id"] for t in tasks}
    assert engine.inbox_mark(ids=[entry["id"]]) == {"marked": 1}
    assert not any(e["kind"] == "routine" for e in engine.inbox()["entries"])
    # not launched: the run itself waits in the inbox, with why
    engine.cfg.preset("lecture").enabled = False
    engine.run_routine(r["id"])
    run = next(e for e in engine.inbox()["entries"] if e["kind"] == "run")
    assert run["status"] == "non lancée" and "désactivé" in run["summary"] and run["routine"]["name"] == "Veille"
    engine.inbox_mark(ids=[run["id"]])
    assert engine.routines[r["id"]].runs[0]["read"] is True
    assert not any(e["kind"] == "run" for e in engine.inbox()["entries"])
    # a manual click got its answer at once: nothing waits
    with pytest.raises(TaskError):
        engine.run_routine(r["id"], manual=True)
    assert not any(e["kind"] == "run" for e in engine.inbox()["entries"])


def test_a_reminder_due_and_dismissed(engine):
    note = engine.save_note({"text": "Rappeler Dupont", "remind_at": time.time() - 5})
    later = engine.save_note({"text": "Demain", "remind_at": time.time() + 3600})
    assert ids(engine, "reminder") == [f"rappel:{note['id']}"]
    engine.inbox_mark(ids=[f"rappel:{note['id']}"], dismiss=True)
    assert engine.store.get_note(note["id"])["reminded"] is True and not ids(engine, "reminder")
    assert engine.store.get_note(later["id"])["reminded"] is False


def test_the_inbox_is_published_once_per_burst(engine, monkeypatch):
    sent = []
    publish = engine.bus.publish
    monkeypatch.setattr(engine.bus, "publish", lambda kind, data: (sent.append((kind, data)), publish(kind, data)))
    engine.inbox(start=True)
    t = engine.create_task("bonjour", profile="work")
    settle(engine, t["id"])
    wait_for(lambda: any(k == "inbox" and any(e["id"] == f"fin:{t['id']}" for e in d["entries"]) for k, d in sent))
    n = sum(1 for k, _ in sent if k == "inbox")
    engine._inbox_changed()   # nothing changed: nothing sent
    time.sleep(0.6)
    assert sum(1 for k, _ in sent if k == "inbox") == n
    last = [d for k, d in sent if k == "inbox"][-1]
    assert last["counts"]["read"] == 1


# ------------------------------------------------------------ the API

def _h(c):
    return {"X-Console-Token": c.token}


def test_inbox_api(client):  # noqa: F811
    eng = client.app.state.engine
    assert client.get("/api/inbox").status_code == 401
    body = client.get("/api/inbox", headers=_h(client)).json()
    assert body["entries"] == [] and body["counts"] == {"todo": 0, "read": 0, "reminder": 0} and body["since"]
    now = time.time()
    eng._inbox_since = now - 100
    with eng._lock:
        eng.tasks["aaaa1111"] = {"id": "aaaa1111", "title": "Devis", "status": "done", "profile": "work",
                                 "created": now - 60, "ended": now - 10, "result": "Fait", "workdir": "",
                                 "session_started": True,
                                 "expired": [{"aid": "e1", "kind": "permission", "tool": "Bash", "target": "x", "ts": now - 10}]}
    body = client.get("/api/inbox", headers=_h(client)).json()
    assert [e["id"] for e in body["entries"]] == ["expiree:aaaa1111:e1", "fin:aaaa1111"]
    assert client.get("/api/state", headers=_h(client)).json()["inbox"] == {"todo": 1, "read": 1, "reminder": 0}
    r = client.post("/api/inbox/read", json={"section": "read"}, headers=_h(client))
    assert r.json() == {"marked": 1}
    r = client.post("/api/inbox/dismiss", json={"ids": ["expiree:aaaa1111:e1"]}, headers=_h(client))
    assert r.json() == {"marked": 1} and client.get("/api/inbox", headers=_h(client)).json()["entries"] == []
    assert client.post("/api/inbox/read", json={"section": "autre"}, headers=_h(client)).status_code == 422
    r = client.post("/api/tasks/aaaa1111/resume-expired", json={"aid": "e1"}, headers=_h(client))
    assert r.status_code == 404
    r = client.post("/api/tasks/aaaa1111/approvals/zz", json={"decision": "allow", "via": "boite"}, headers=_h(client))
    assert r.status_code == 409


# ------------------------------------------------------------ routines: errors only, at the top

def test_a_routine_set_to_errors_only_leaves_its_successes_read(engine):
    engine.inbox(start=True)
    r = engine.save_routine({"name": "Discrète", "prompt": "écho", "profile": "work", "preset": "lecture",
                             "schedule": {"kind": "interval", "every_min": 60}, "open_window": False, "inbox": "errors"})
    assert r["inbox"] == "errors"
    ok = engine.run_routine(r["id"])
    settle(engine, ok["id"])
    wait_for(lambda: engine.tasks[ok["id"]].get("read_at"))
    assert not any(e["kind"] in ("routine", "done") for e in engine.inbox()["entries"])
    assert engine.tasks[ok["id"]]["routine"]["inbox"] == "errors"
    engine.routines[r["id"]].prompt = "FAIL"   # the next run ends with an error
    failed = engine.run_routine(r["id"])
    settle(engine, failed["id"])
    assert f"fin:{failed['id']}" in ids(engine, "todo")   # an error always comes


def test_a_routine_at_the_top_shows_its_latest_result(engine):
    engine.inbox(start=True)
    offer = {"titre": "Brief", "blocs": [{"type": "texte", "texte": "3 devis"}]}
    r = engine.save_routine({"name": "Point du matin", "prompt": "PRESENT " + json.dumps(offer, ensure_ascii=False),
                             "profile": "work", "preset": "lecture", "schedule": {"kind": "interval", "every_min": 60},
                             "open_window": False, "headline": True})
    first = engine.run_routine(r["id"])
    settle(engine, first["id"])
    second = engine.run_routine(r["id"])
    settle(engine, second["id"])
    out = engine.inbox()
    head = [e for e in out["entries"] if e["section"] == "head"]
    assert [e["id"] for e in head] == [f"une:{r['id']}"] and head[0]["task_id"] == second["id"]
    assert head[0]["display"]["titre"] == "Brief" and head[0]["unread"] is True
    assert not any(e["kind"] == "routine" for e in out["entries"])   # not among the results to read
    assert out["counts"] == {"todo": 0, "read": 0, "reminder": 0}    # the top is not counted
    engine.inbox_mark(ids=[head[0]["id"]], dismiss=True)             # hidden until the next run
    assert not [e for e in engine.inbox()["entries"] if e["section"] == "head"]
    third = engine.run_routine(r["id"])
    settle(engine, third["id"])
    head = [e for e in engine.inbox()["entries"] if e["section"] == "head"]
    assert head and head[0]["task_id"] == third["id"]
