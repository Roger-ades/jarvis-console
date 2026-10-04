import json
import time

import pytest

from console.config import ConfigStore
from console.engine import Engine, TaskError
from console.store import Store

from .conftest import FAKE, PORT, task_status, wait_for


def done(engine, tid, statuses=("done", "error", "cancelled", "interrupted")):
    return wait_for(lambda: task_status(engine, tid) in statuses and tid not in engine.runs)


def test_task_runs_and_streams(engine):
    t = engine.create_task("bonjour", profile="work", preset="assiste")
    done(engine, t["id"])
    task = engine.tasks[t["id"]]
    assert task["status"] == "done", task["error"]
    assert "écho:bonjour" in task["result"]
    kinds = [e["kind"] for e in engine.store.events(t["id"])]
    assert {"user", "init", "text", "result"} <= set(kinds)
    assert task["session_started"] and task["mcp"][0]["name"] == "odoo"


def test_the_plan_is_the_leads_with_its_current_step(engine):
    t = engine.create_task("PLAN", profile="work", preset="lecture")
    done(engine, t["id"])
    todos = engine.tasks[t["id"]]["todos"]
    assert [x["content"] for x in todos] == ["Lire le mail", "Chiffrer", "Faire valider"]
    assert todos[1] == {"content": "Chiffrer", "status": "in_progress", "active": "Chiffrage en cours"}


def test_profile_isolation_and_clean_env(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    a = engine.create_task("ENV", profile="work")
    b = engine.create_task("@perso ENV")
    done(engine, a["id"]); done(engine, b["id"])
    ra, rb = engine.tasks[a["id"]]["result"], engine.tasks[b["id"]]["result"]
    assert engine.tasks[b["id"]]["profile"] == "personal"
    assert "cfg" in ra and ra.split("CONFIG_DIR=")[1].split()[0].endswith("work")
    assert rb.split("CONFIG_DIR=")[1].split()[0].endswith("personal")
    assert "API_KEY=absente" in ra and "API_KEY=absente" in rb


def test_three_tasks_in_parallel_across_profiles(engine):
    ids = [engine.create_task(f"SLEEP 1.5\nt{i}", profile=p)["id"]
           for i, p in enumerate(["work", "personal", "work"])]
    wait_for(lambda: sum(task_status(engine, i) == "running" for i in ids) == 3, timeout=10)
    for i in ids:
        done(engine, i)
    assert all(task_status(engine, i) == "done" for i in ids)


def test_per_profile_limit_queues(engine):
    cfg = engine.cfg
    cfg.profiles[0].max_concurrent = 1
    ids = [engine.create_task("SLEEP 1\nx", profile="work")["id"] for _ in range(2)]
    wait_for(lambda: task_status(engine, ids[0]) == "running")
    time.sleep(0.3)
    assert task_status(engine, ids[1]) == "queued"
    done(engine, ids[1])


def test_read_only_task_cannot_write(engine):
    wd = engine.cfg.profile("work").workdir
    t = engine.create_task(f'TOOL Write {json.dumps({"file_path": wd + "/x.txt", "content": "a"})}\n'
                           f'TOOL Bash {json.dumps({"command": "echo hi"})}', profile="work", preset="lecture")
    done(engine, t["id"])
    assert "Write:refus" in engine.tasks[t["id"]]["result"]
    assert "Bash:refus" in engine.tasks[t["id"]]["result"]
    policy = [e for e in engine.store.events(t["id"]) if e["kind"] == "policy"]
    assert len(policy) == 2


def test_approval_flow_allow_and_deny(engine):
    inp = {"model": "sale.order", "values": {"partner_id": 1}}
    t = engine.create_task(f"TOOL mcp__odoo__create_record {json.dumps(inp)}\n"
                           f"TOOL mcp__odoo__create_record {json.dumps(inp)}", profile="work", preset="brouillons")
    tid = t["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    first = engine.tasks[tid]["pending"][0]
    assert first["tool"] == "mcp__odoo__create_record"
    engine.decide(tid, first["id"], "allow")
    wait_for(lambda: engine.tasks[tid]["pending"] and engine.tasks[tid]["pending"][0]["id"] != first["id"])
    engine.decide(tid, engine.tasks[tid]["pending"][0]["id"], "deny", "pas ce client")
    done(engine, tid)
    res = engine.tasks[tid]["result"]
    assert res.count("mcp__odoo__create_record:ok") == 1 and res.count("mcp__odoo__create_record:refus") == 1
    audit, _ = engine.store.audit_rows(task_id=tid, kind="validation")
    assert {r["detail"]["décision"] for r in audit} == {"allow", "deny"}


def test_delete_record_waits_for_approval_in_assiste(engine):
    t = engine.create_task('TOOL mcp__odoo__delete_record {"model": "project.task", "record_id": 1}',
                           profile="work", preset="assiste")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")
    assert engine.tasks[t["id"]]["pending"][0]["tool"] == "mcp__odoo__delete_record"


def test_unlisted_tool_goes_to_permission_prompt(engine):
    t = engine.create_task('TOOL Bash {"command": "npm install"}', profile="work", preset="assiste")
    tid = t["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    assert engine.tasks[tid]["pending"][0]["kind"] == "hook"
    engine.decide(tid, engine.tasks[tid]["pending"][0]["id"], "allow")
    done(engine, tid)
    assert "Bash:ok" in engine.tasks[tid]["result"]


def test_ask_user_question(engine):
    t = engine.create_task("ASK", profile="personal")
    tid = t["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    appr = engine.tasks[tid]["pending"][0]
    assert appr["kind"] == "question"
    engine.decide(tid, appr["id"], "allow", answers={"Quelle couleur ?": "Bleu"})
    done(engine, tid)
    assert "Bleu" in engine.tasks[tid]["result"]


def test_cancel_kills_the_process(engine):
    t = engine.create_task("SLEEP 30", profile="work")
    wait_for(lambda: t["id"] in engine.runs and engine.runs[t["id"]].proc)
    engine.cancel(t["id"])
    done(engine, t["id"])
    assert task_status(engine, t["id"]) == "cancelled"


def test_followup_resumes_same_session(engine):
    t = engine.create_task("ARGS", profile="work")
    done(engine, t["id"])
    sid = engine.tasks[t["id"]]["session_id"]
    assert "--session-id" in engine.tasks[t["id"]]["result"]
    engine.followup(t["id"], "ARGS")
    wait_for(lambda: engine.tasks[t["id"]]["status"] in ("queued", "running"))
    done(engine, t["id"])
    res = engine.tasks[t["id"]]["result"]
    assert "--resume" in res and sid in res
    users = [e for e in engine.store.events(t["id"]) if e["kind"] == "user"]
    assert len(users) == 2


def test_emergency_stop(engine):
    t = engine.create_task("SLEEP 30", profile="work")
    wait_for(lambda: task_status(engine, t["id"]) == "running")
    engine.set_emergency(True)
    done(engine, t["id"])
    assert task_status(engine, t["id"]) == "cancelled"
    with pytest.raises(TaskError) as e:
        engine.create_task("x")
    assert e.value.status == 423
    engine.set_emergency(False)
    t2 = engine.create_task("x")
    done(engine, t2["id"])
    assert task_status(engine, t2["id"]) == "done"


def test_restart_marks_running_tasks_interrupted(data_dir):
    eng = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT, cli_command=FAKE)
    t = eng.create_task("SLEEP 30", profile="work")
    wait_for(lambda: task_status(eng, t["id"]) == "running")
    # A new server starting on the same data while the database still says "running"
    # is exactly what a crash leaves behind.
    eng2 = Engine(ConfigStore(data_dir), Store(data_dir / "console.db"), data_dir, PORT,
                  cli_command=FAKE, start_threads=False)
    assert eng2.tasks[t["id"]]["status"] == "interrupted"
    assert any(e["kind"] == "status" and e["data"]["status"] == "interrupted"
               for e in eng2.store.events(t["id"]))
    eng.shutdown()
    wait_for(lambda: not eng.runs)
    eng.store.close()
    eng2.store.close()


def test_complet_preset_is_locked_down(engine, tmp_path):
    with pytest.raises(TaskError) as e:
        engine.create_task("x", preset="complet")
    assert e.value.status == 403
    engine.cfg.preset("complet").enabled = True
    with pytest.raises(TaskError) as e:
        engine.create_task("x", preset="complet")
    assert e.value.status == 409 and e.value.extra["need_confirm"]
    from pathlib import Path
    with pytest.raises(TaskError):
        engine.create_task("x", preset="complet", confirmed=True, workdir=str(Path.home()))
    t = engine.create_task("x", preset="complet", confirmed=True)
    done(engine, t["id"])


def test_config_change_does_not_affect_running_task(engine):
    t = engine.create_task('SLEEP 1\nTOOL Bash {"command": "echo hi"}', profile="work", preset="lecture")
    engine.cfg.preset("lecture").deny = []
    engine.cfg.preset("lecture").allow.append("Bash")
    done(engine, t["id"])
    assert "Bash:refus" in engine.tasks[t["id"]]["result"]


def test_timeout(engine):
    engine.cfg.general.task_timeout_min = 1
    t = engine.create_task("SLEEP 30", profile="work")
    run = wait_for(lambda: engine.runs.get(t["id"]))
    run.started -= 61
    done(engine, t["id"])
    assert task_status(engine, t["id"]) == "error"
    assert "Durée maximale" in engine.tasks[t["id"]]["error"]


def test_probe_reports_account_skills_and_mcp_without_secrets(engine):
    res = engine.probe("work")
    assert res["ok"] and res["logged_in"], res
    assert res["account"]["email"] == "test@example.com"
    assert res["commands"][0]["name"] == "deep-research"
    assert res["mcp"][0]["name"] == "odoo" and res["mcp"][0]["status"] == "connected"
    assert "SECRET" not in json.dumps(res)


def test_error_result_marks_error(engine):
    t = engine.create_task("FAIL", profile="work")
    done(engine, t["id"])
    assert task_status(engine, t["id"]) == "error"
