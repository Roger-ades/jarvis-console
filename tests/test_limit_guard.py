"""Launching with the account limits in mind: start later, defer a routine, keep a scheduled task."""
import time

from console.config import ConfigStore
from console.engine import Engine
from console.routines import Routine, Schedule
from console.store import Store

from .conftest import FAKE, PORT, task_status, wait_for


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def block(engine, pid, seconds):
    engine.limits[pid] = {"status": "rejected", "type": "five_hour", "resets_at": time.time() + seconds,
                          "windows": {"five_hour": {"used": 1.0, "resets_at": time.time() + seconds},
                                      "seven_day": {"used": 0.4, "resets_at": time.time() + 86400}}}


def test_start_waits_for_its_time(engine):
    t = engine.create_task("bonjour", profile="work", not_before=time.time() + 1.5)
    assert t["not_before"]
    time.sleep(0.8)
    assert task_status(engine, t["id"]) == "queued" and t["id"] not in engine.runs
    done(engine, t["id"])
    assert task_status(engine, t["id"]) == "done"


def test_limit_block(engine):
    assert engine.limit_block("work") is None
    block(engine, "work", 600)
    reset = engine.limit_block("work")
    assert reset and 590 < reset - time.time() <= 600
    block(engine, "work", -10)  # already reset
    assert engine.limit_block("work") is None
    engine.limits["work"] = {"status": "allowed", "windows": {"five_hour": {"used": 0.95, "resets_at": time.time() + 60}}}
    assert engine.limit_block("work") is None  # close to the limit is not blocked


def test_routine_is_deferred_when_the_account_is_full(engine):
    block(engine, "work", 3600)
    r = engine.save_routine(Routine(name="Veille", prompt="bonjour", profile="work", preset="lecture",
                                    schedule=Schedule(kind="daily", time="08:00")).model_dump())
    t = engine.run_routine(r["id"], manual=True)
    assert t["not_before"] and t["not_before"] > time.time() + 3500
    assert engine.routines[r["id"]].runs[0]["status"] == "reportée"
    assert task_status(engine, t["id"]) == "queued"


def test_scheduled_task_survives_a_restart(data_dir):
    eng = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE)
    t = eng.create_task("bonjour", profile="work", not_before=time.time() + 3600)
    eng.shutdown()
    eng.store.close()
    again = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE)
    try:
        assert again.tasks[t["id"]]["status"] == "queued" and t["id"] in again.queue
    finally:
        again.shutdown()
        time.sleep(0.2)
        again.store.close()
