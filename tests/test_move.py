"""Moving a session into a project: the same session (same id), moved, never duplicated."""
import json
import time
import uuid
from pathlib import Path

import pytest

from console import library

from .conftest import task_status, wait_for


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def argv_of(engine, tid):
    return json.loads(engine.tasks[tid]["result"].split("ARGV=", 1)[1].split("\n", 1)[0])


def lines(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_session(prof, cwd, sid=None, messages=("Prépare le devis Dupont.", "Devis prêt."), uuids=None) -> str:
    sid = sid or str(uuid.uuid4())
    folder = Path(prof.config_dir) / "projects" / library.project_slug(str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    uuids = uuids or [str(uuid.uuid4()) for _ in messages]
    rows = [{"type": "user" if i % 2 == 0 else "assistant", "uuid": u, "sessionId": sid, "cwd": str(cwd),
             "timestamp": f"2026-09-27T09:1{i}:00Z",
             "message": {"content": m} if i % 2 == 0 else {"content": [{"type": "text", "text": m}]}}
            for i, (m, u) in enumerate(zip(messages, uuids))]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return sid


def desktop_record(prof, sid, cwd) -> Path:
    meta = Path(prof.mcp.desktop_config).parent / "claude-code-sessions" / "acc" / "org"
    meta.mkdir(parents=True, exist_ok=True)
    f = meta / f"local_{uuid.uuid4()}.json"
    f.write_text(json.dumps({"cliSessionId": sid, "title": "Devis Dupont", "cwd": str(cwd), "model": "opus"}), encoding="utf-8")
    return f


def test_move_keeps_the_same_session(engine, tmp_path):
    prof = engine.cfg.profile("work")
    old, target = tmp_path / "ancien", tmp_path / "Visiotech"
    old.mkdir()
    target.mkdir()
    sid = write_session(prof, old)
    src = library.find_transcript(prof, sid)
    (src.parent / sid / "tool-results").mkdir(parents=True)
    (src.parent / sid / "tool-results" / "r.txt").write_text("résultat", encoding="utf-8")
    record = desktop_record(prof, sid, old)
    res = engine.move_session("work", sid, str(target))
    moved = library.find_transcript(prof, sid)
    assert res["session"] == sid and res["desktop"] is True
    assert moved.parent.name == library.project_slug(str(target.resolve())) and not src.exists()
    assert all(x["sessionId"] == sid and x["cwd"] == str(target.resolve()) for x in lines(moved))
    assert (moved.parent / sid / "tool-results" / "r.txt").read_text(encoding="utf-8") == "résultat"
    assert json.loads(record.read_text(encoding="utf-8"))["cwd"] == str(target.resolve())  # Claude Desktop follows
    rows = [r for r in library.list_sessions(prof) if r["id"] == sid]
    assert len(rows) == 1 and rows[0]["cwd"] == str(target.resolve())  # one session, not two


def test_resume_after_moving_uses_the_same_id(engine, tmp_path):
    prof = engine.cfg.profile("work")
    gone, target = tmp_path / "supprimé", tmp_path / "Projet"
    target.mkdir()
    sid = write_session(prof, gone)  # its folder no longer exists
    with pytest.raises(Exception, match="choisis un projet"):
        engine.resume_session("work", sid, "ARGS")
    t = engine.resume_session("work", sid, "ARGS", workdir=str(target), fork=False)
    done(engine, t["id"])
    argv = argv_of(engine, t["id"])
    assert argv[argv.index("--resume") + 1] == sid and "--fork-session" not in argv
    assert engine.tasks[t["id"]]["workdir"] == str(target.resolve())
    assert len([r for r in library.list_sessions(prof) if r["id"] == sid]) == 1


def test_move_a_console_discussion_in_place(engine, tmp_path):
    t = engine.create_task("bonjour", profile="work")
    done(engine, t["id"])
    prof = engine.cfg.profile("work")
    live = engine.tasks[t["id"]]
    sid = write_session(prof, live["workdir"], sid=live["session_id"])  # what Claude Code would have written
    target = tmp_path / "Devis"
    target.mkdir()
    out = engine.move_task(t["id"], str(target))
    assert out["id"] == t["id"] and out["workdir"] == str(target.resolve()) and out["session_id"] == sid
    engine.followup(t["id"], "ARGS")
    done(engine, t["id"])
    argv = argv_of(engine, t["id"])
    assert argv[argv.index("--resume") + 1] == sid and "--fork-session" not in argv
    assert library.find_transcript(prof, sid).parent.name == library.project_slug(str(target.resolve()))
    assert len(engine.tasks) == 1  # no new discussion appeared


def test_no_move_while_running_nor_into_a_protected_folder(engine, data_dir, tmp_path):
    t = engine.create_task("SLEEP 2\nx", profile="work")
    wait_for(lambda: t["id"] in engine.runs)
    with pytest.raises(Exception, match="en cours"):
        engine.move_task(t["id"], str(tmp_path))
    done(engine, t["id"])
    prof = engine.cfg.profile("work")
    sid = write_session(prof, prof.workdir)
    with pytest.raises(Exception, match="protégé"):
        engine.move_session("work", sid, str(data_dir))


# ------------------------------------------------------------ duplicates left by the former move
def test_merge_back_a_duplicate(engine, tmp_path):
    prof = engine.cfg.profile("work")
    old, target = tmp_path / "ancien", tmp_path / "Visiotech"
    old.mkdir()
    target.mkdir()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    orig = write_session(prof, old, messages=("Devis ?", "Voici."), uuids=ids[:2])
    record = desktop_record(prof, orig, old)
    time.sleep(0.05)
    # the former "move": a copy under a new id in the project, then continued in the console
    copy = write_session(prof, target, messages=("Devis ?", "Voici.", "Et la remise ?"), uuids=ids)
    t = engine.create_task("x", profile="work", preset="lecture")
    done(engine, t["id"])
    with engine._lock:
        engine.tasks[t["id"]]["session_id"] = copy
    dups = engine.moved_copies("work")
    assert [(d["original"], d["copy"]) for d in dups] == [(orig, copy)]
    res = engine.merge_moved_copies("work")
    assert [m["original"] for m in res["merged"]] == [orig] and not res["skipped"]
    kept = library.find_transcript(prof, orig)
    assert kept.parent.name == library.project_slug(str(target))
    assert [x["message"]["content"] if isinstance(x["message"]["content"], str) else x["message"]["content"][0]["text"]
            for x in lines(kept)] == ["Devis ?", "Voici.", "Et la remise ?"]
    assert all(x["sessionId"] == orig for x in lines(kept))
    assert library.find_transcript(prof, copy) is None
    assert json.loads(record.read_text(encoding="utf-8"))["cwd"] == str(target)
    assert engine.tasks[t["id"]]["session_id"] == orig
    assert engine.moved_copies("work") == []


def test_merge_refused_when_the_original_went_on(engine, tmp_path):
    prof = engine.cfg.profile("work")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    orig = write_session(prof, a, messages=("Q", "R", "suite dans Desktop"), uuids=ids)
    time.sleep(0.05)
    write_session(prof, b, messages=("Q", "R"), uuids=ids[:2])
    res = engine.merge_moved_copies("work")
    assert not res["merged"] and "à la main" in res["skipped"][0]["reason"]
    assert library.find_transcript(prof, orig).parent.name == library.project_slug(str(a))


def test_forks_in_the_same_folder_are_not_duplicates(engine, tmp_path):
    prof = engine.cfg.profile("work")
    ids = [str(uuid.uuid4()) for _ in range(2)]
    write_session(prof, tmp_path, uuids=ids)
    write_session(prof, tmp_path, uuids=ids)  # "Continuer dans une copie": wanted, same folder
    assert engine.moved_copies("work") == []
