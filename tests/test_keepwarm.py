"""Keeping a session's prompt cache warm: a throwaway copy of the session reads it every 50 minutes
while the session is idle, without writing anything in the conversation."""
import subprocess
import time

import pytest

import console.engine as engine_mod
from console.engine import WARM_EVERY, TaskError

from .conftest import task_status, wait_for


def finish(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)
    return engine.tasks[tid]


def started(engine, prompt="CTX 150000\nbonjour"):
    tid = engine.create_task(prompt, profile="work", preset="lecture")["id"]
    finish(engine, tid)
    return tid


def test_a_ping_reads_the_cache_through_a_throwaway_copy(engine, monkeypatch):
    tid = started(engine)
    t = engine.tasks[tid]
    transcript = engine_mod.activity_mod.transcript(engine.cfg.profile("work"), t["workdir"], t["session_id"])
    before_log, before_events = transcript.read_text(encoding="utf-8"), len(engine.store.events(tid))
    seen = {}
    real = subprocess.Popen

    def spy(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        return real(cmd, **kw)

    monkeypatch.setattr(engine_mod.subprocess, "Popen", spy)
    engine.keep_warm(tid, 2)
    engine._warm(tid)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--resume") + 1] == t["session_id"]
    assert "--fork-session" in cmd and "--no-session-persistence" in cmd
    assert seen["env"]["DISABLE_AUTO_COMPACT"] == "1"
    # nothing written in the session: same transcript, only info lines in the task
    assert transcript.read_text(encoding="utf-8") == before_log
    new = engine.store.events(tid)[before_events:]
    assert [e["kind"] for e in new] == ["info", "info"] and new[-1]["data"]["text"].startswith("Maintien du cache : ")
    w = engine.tasks[tid]["warm"]
    assert w["pings"] == 1 and w["read"] > 20000 and w["last"]
    assert engine.tasks[tid]["status"] == "done" and engine.tasks[tid]["result"] == "écho:bonjour"


def test_the_scheduler_pings_idle_sessions_and_stops_by_itself(engine):
    tid = started(engine)
    t = engine.tasks[tid]
    engine.keep_warm(tid, 1)
    engine._keep_warm_tick(time.time())
    assert tid not in engine._warming                    # active less than 50 min ago: nothing to do
    t["context_at"] -= WARM_EVERY + 5
    engine._keep_warm_tick(time.time())
    wait_for(lambda: (t.get("warm") or {}).get("pings") == 1 and tid not in engine._warming)
    engine._keep_warm_tick(time.time())
    assert tid not in engine._warming                    # the ping restarted the hour
    engine._keep_warm_tick(time.time() + 3601)
    assert not t["keep_warm_until"]
    assert engine.store.events(tid)[-1]["data"]["text"] == "Maintien du cache arrêté : fin de la durée choisie."


def test_upkeep_stops_when_the_window_closes_or_the_quota_is_near(engine):
    tid = started(engine)
    engine.keep_warm(tid, 3)
    engine.update_task(tid, {"closed": True})
    assert not engine.tasks[tid]["keep_warm_until"]
    engine.update_task(tid, {"closed": False})
    engine.keep_warm(tid, 3)
    engine.tasks[tid]["context_at"] -= WARM_EVERY + 5
    engine.limits["work"] = {"status": "allowed", "windows": {"five_hour": {"used": 0.93}}}
    engine._keep_warm_tick(time.time())
    assert not engine.tasks[tid]["keep_warm_until"] and tid not in engine._warming
    assert "quota du compte" in engine.store.events(tid)[-1]["data"]["text"]


def test_keep_warm_needs_a_session_and_a_sane_duration(engine):
    tid = engine.create_task("bonjour", profile="work", preset="lecture", not_before=10**10)["id"]
    with pytest.raises(TaskError, match="pas encore démarré"):
        engine.keep_warm(tid, 1)
    tid2 = started(engine, "bonjour")
    with pytest.raises(TaskError, match="Durée"):
        engine.keep_warm(tid2, 13)
    engine.keep_warm(tid2, 1)
    engine.keep_warm(tid2, 0)
    assert not engine.tasks[tid2]["keep_warm_until"]
    assert engine.store.events(tid2)[-1]["data"]["text"] == "Maintien du cache arrêté : arrêté à ta demande."
