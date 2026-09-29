import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from console import library
from console.engine import TaskError
from console.routines import Schedule

from .conftest import task_status, wait_for


def write_session(engine, pid="work", title=None, desktop_title=None, cwd=None):
    prof = engine.cfg.profile(pid)
    sid = str(uuid.uuid4())
    cwd = cwd or str(Path(prof.workdir))
    Path(cwd).mkdir(parents=True, exist_ok=True)
    proj = library.config_dir(prof) / "projects" / "C--proj"
    proj.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-09-28T10:00:00Z", "entrypoint": "claude-desktop",
         "message": {"role": "user", "content": "Prépare le devis Dupont"}},
        {"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": "2026-09-28T10:00:05Z",
         "message": {"content": [{"type": "text", "text": "Je regarde le mail."},
                                 {"type": "tool_use", "name": "Read", "input": {"file_path": "mail.eml"}}]}},
        {"type": "user", "sessionId": sid, "isSidechain": True, "message": {"content": "consigne de sous-agent"}},
        {"type": "user", "sessionId": sid, "message": {"content": [{"type": "tool_result", "content": "..."}]}},
    ]
    if title:
        lines.append({"type": "custom-title", "customTitle": title, "sessionId": sid})
    (proj / f"{sid}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    if desktop_title:
        d = library.desktop_dir(prof) / "claude-code-sessions" / "acc" / "org"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"local_{uuid.uuid4()}.json").write_text(json.dumps({
            "cliSessionId": sid, "title": desktop_title, "cwd": cwd, "isArchived": False,
            "lastActivityAt": int(time.time() * 1000), "model": "opus"}), encoding="utf-8")
    return sid


# ------------------------------------------------ sessions library
def test_sessions_are_listed_with_desktop_titles(engine):
    s1 = write_session(engine, desktop_title="Devis Dupont (desktop)")
    s2 = write_session(engine, title="JARVIS - Résumé des mails")
    s3 = write_session(engine, pid="personal")
    rows = {r["id"]: r for r in engine.sessions()}
    assert rows[s1]["origin"] == "desktop" and rows[s1]["title"] == "Devis Dupont (desktop)"
    assert rows[s2]["origin"] == "console" and rows[s2]["title"] == "Résumé des mails"
    assert rows[s3]["profile"] == "personal" and rows[s3]["title"] == "Prépare le devis Dupont"
    assert rows[s1]["prompts"] == 1 and rows[s1]["resumable"]
    assert {r["id"] for r in engine.sessions("personal")} == {s3}


def test_transcript_skips_subagent_and_tool_output(engine):
    sid = write_session(engine)
    items = engine.session_transcript("work", sid)["items"]
    assert [i["role"] for i in items] == ["user", "assistant", "tool"]
    assert items[2]["name"] == "Read" and "mail.eml" in items[2]["target"]


def test_resume_forks_the_session_in_its_own_folder(engine, tmp_path):
    folder = tmp_path / "projet-client"
    sid = write_session(engine, desktop_title="Devis", cwd=str(folder))
    t = engine.resume_session("work", sid, "ARGS", preset="assiste")
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    task = engine.tasks[t["id"]]
    argv = json.loads(task["result"].split("ARGV=", 1)[1])
    assert argv[argv.index("--resume") + 1] == sid and "--fork-session" in argv
    assert Path(task["workdir"]) == folder.resolve()
    kinds = [e["kind"] for e in engine.store.events(t["id"])]
    assert kinds[0] == "history" and task["origin"] == "reprise desktop"
    # a follow-up continues the fork, without forking again
    engine.followup(t["id"], "ARGS")
    wait_for(lambda: engine.tasks[t["id"]]["status"] in ("queued", "running"))
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    argv = json.loads(engine.tasks[t["id"]]["result"].split("ARGV=", 1)[1])
    assert "--resume" in argv and "--fork-session" not in argv


def test_bad_session_ids_are_refused(engine):
    with pytest.raises(TaskError):
        engine.session_transcript("work", "../../etc")
    assert library.find_transcript(engine.cfg.profile("work"), "..\\x") is None


# ------------------------------------------------ routines
def test_schedule_next_run():
    monday_7h = datetime(2026, 9, 28, 7, 0).timestamp()  # a Monday
    daily = Schedule(kind="daily", time="08:30", days=[0, 1, 2, 3, 4])
    assert datetime.fromtimestamp(daily.next_after(monday_7h)).strftime("%a %H:%M") == datetime(2026, 9, 28, 8, 30).strftime("%a %H:%M")
    friday_9h = datetime(2026, 10, 2, 9, 0).timestamp()
    assert datetime.fromtimestamp(daily.next_after(friday_9h)).weekday() == 0  # next Monday
    every = Schedule(kind="interval", every_min=30)
    assert every.next_after(1000.0, 1000.0) == 1000.0 + 1800
    once = Schedule(kind="once", at=monday_7h + 3600)
    assert once.next_after(monday_7h) == monday_7h + 3600 and once.next_after(monday_7h, monday_7h + 3600) is None
    with pytest.raises(ValueError):
        Schedule(kind="daily", time="25:00")


def test_routine_run_now_creates_a_tagged_task(engine):
    r = engine.save_routine({"name": "Mails du matin", "prompt": "écho routine", "profile": "work", "preset": "lecture",
                             "schedule": {"kind": "daily", "time": "08:00", "days": [0, 1, 2, 3, 4]}})
    assert r["next_run"] and r["schedule_label"] == "en semaine à 08:00"
    t = engine.run_routine(r["id"], manual=True)
    wait_for(lambda: task_status(engine, t["id"]) == "done" and t["id"] not in engine.runs)
    assert engine.tasks[t["id"]]["routine"]["name"] == "Mails du matin"
    wait_for(lambda: engine.routines[r["id"]].runs[0]["status"] == "done")


def test_due_routine_runs_by_itself(engine):
    r = engine.save_routine({"name": "Toutes les 5 min", "prompt": "écho", "profile": "personal", "preset": "web",
                             "schedule": {"kind": "interval", "every_min": 5}, "open_window": False})
    engine.routines[r["id"]].next_run = time.time() - 1
    wait_for(lambda: engine.routines[r["id"]].runs, timeout=12)
    run = engine.routines[r["id"]].runs[0]
    assert run["task_id"] and engine.tasks[run["task_id"]]["closed"] is True
    assert engine.routines[r["id"]].next_run > time.time() + 200


def test_routines_refuse_presets_needing_confirmation(engine):
    engine.cfg.preset("complet").enabled = True
    with pytest.raises(TaskError):
        engine.save_routine({"name": "x", "prompt": "y", "profile": "work", "preset": "complet"})


def test_missed_routine_is_recorded_at_startup(data_dir):
    from console.config import ConfigStore
    from console.engine import Engine
    from console.store import Store
    from .conftest import FAKE, PORT
    eng = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE, start_threads=False)
    r = eng.save_routine({"name": "Hier", "prompt": "x", "profile": "work", "preset": "lecture",
                          "schedule": {"kind": "interval", "every_min": 60}})
    eng.routines[r["id"]].next_run = time.time() - 3600
    eng._persist_routines()
    eng.store.close()
    eng2 = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE, start_threads=False)
    r2 = eng2.routines[r["id"]]
    assert r2.runs[0]["status"] == "manquée" and r2.next_run > time.time()
    eng2.store.close()
