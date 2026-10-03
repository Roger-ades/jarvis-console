"""What Claude changed in files: copies before and after, the differences, undo and redo."""
import json
import os

import pytest

from console import changes as ch
from console.engine import TaskError

from .conftest import task_status, wait_for


def run(engine, prompt, wd, preset="edition", tid=None):
    if tid:
        engine.followup(tid, prompt)
    else:
        tid = engine.create_task(prompt, profile="work", preset=preset, workdir=str(wd))["id"]
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)
    return tid


def do(tool, **inp):
    return f"DO {tool} {json.dumps(inp)}"


def change_events(engine, tid, kind="change"):
    return [e["data"] for e in engine.store.events(tid) if e["kind"] == kind]


def test_an_edit_is_recorded_with_its_differences_then_undone_and_redone(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "notes.txt"
    f.write_text("un\ndeux\ntrois\n", encoding="utf-8")
    tid = run(engine, do("Edit", file_path=str(f), old_string="deux", new_string="DEUX\ndeux bis"), wd)
    assert "Edit:ok" in engine.tasks[tid]["result"]
    [ev] = change_events(engine, tid)
    assert ev["path"] == os.path.realpath(f) and ev["tool"] == "Edit" and ev["tool_use_id"].startswith("toolu_")
    assert (ev["added"], ev["removed"], ev["created"]) == (2, 1, False)
    assert engine.tasks[tid]["changed_files"] == 1

    [row] = engine.file_changes(tid)["changes"]
    assert row["status"] == "actuel" and row["state"] == "fait"
    d = engine.file_change(tid, row["id"])
    assert [(x["k"], x["t"]) for x in d["lines"] if x["k"] in "+-"] == [("-", "deux"), ("+", "DEUX"), ("+", "deux bis")]
    assert d["lines"][0]["k"] == "@"

    engine.undo_change(tid, row["id"])
    assert f.read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert engine.file_changes(tid)["changes"][0]["status"] == "annule"
    assert change_events(engine, tid, "change_state")[-1]["state"] == "annule"
    with pytest.raises(TaskError):
        engine.undo_change(tid, row["id"])  # already undone

    engine.undo_change(tid, row["id"], redo=True)
    assert f.read_text(encoding="utf-8") == "un\nDEUX\ndeux bis\ntrois\n"
    assert engine.file_changes(tid)["changes"][0]["status"] == "actuel"
    audit = [r["kind"] for r in engine.store.audit_rows(task_id=tid)[0]]
    assert "modification annulée" in audit and "modification rétablie" in audit


def test_claude_learns_with_the_next_message_what_the_user_undid(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "a.md"
    f.write_text("avant", encoding="utf-8")
    tid = run(engine, do("Write", file_path=str(f), content="après"), wd)
    [row] = engine.file_changes(tid)["changes"]
    engine.undo_change(tid, row["id"])
    run(engine, "Et maintenant ?", wd, tid=tid)
    result = engine.tasks[tid]["result"]
    assert "a annulé tes modifications" in result and str(os.path.realpath(f)) in result
    # said once
    run(engine, "Encore ?", wd, tid=tid)
    assert "a annulé" not in engine.tasks[tid]["result"]


def test_a_created_file_is_removed_by_undo_and_comes_back_with_redo(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "sous" / "nouveau.txt"
    tid = run(engine, do("Write", file_path=str(f), content="bonjour\n"), wd)
    [row] = engine.file_changes(tid)["changes"]
    assert row["created"] and row["added"] == 1
    engine.undo_change(tid, row["id"])
    assert not f.exists()
    engine.undo_change(tid, row["id"], redo=True)
    assert f.read_text(encoding="utf-8") == "bonjour\n"


def test_undo_refuses_a_file_changed_since_unless_forced_and_keeps_what_it_overwrote(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "doc.txt"
    f.write_text("v1", encoding="utf-8")
    tid = run(engine, do("Write", file_path=str(f), content="v2"), wd)
    [row] = engine.file_changes(tid)["changes"]
    f.write_text("v3 de l'utilisateur", encoding="utf-8")
    assert engine.file_changes(tid)["changes"][0]["status"] == "modifie"
    with pytest.raises(TaskError) as e:
        engine.undo_change(tid, row["id"])
    assert e.value.status == 409 and e.value.extra.get("conflict")
    assert f.read_text(encoding="utf-8") == "v3 de l'utilisateur"
    engine.undo_change(tid, row["id"], force=True)
    assert f.read_text(encoding="utf-8") == "v1"
    engine.undo_change(tid, row["id"], redo=True)
    assert f.read_text(encoding="utf-8") == "v3 de l'utilisateur"  # what the forced undo overwrote


def test_successive_changes_undo_newest_first_and_undo_file_goes_back_to_the_start(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "liste.txt"
    f.write_text("a\n", encoding="utf-8")
    tid = run(engine, "\n".join([do("Edit", file_path=str(f), old_string="a", new_string="b"),
                                 do("Edit", file_path=str(f), old_string="b", new_string="c")]), wd)
    first, second = engine.file_changes(tid)["changes"]
    assert (first["status"], second["status"]) == ("suivie", "actuel")
    with pytest.raises(TaskError) as e:
        engine.undo_change(tid, first["id"])  # the newer one is still there
    assert e.value.status == 409
    res = engine.undo_file(tid, first["path"])
    assert [r["id"] for r in res["undone"]] == [second["id"], first["id"]] and "stopped" not in res
    assert f.read_text(encoding="utf-8") == "a\n"


def test_parallel_edits_of_one_file_each_get_their_own_difference(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "p.txt"
    f.write_text("x\ny\n", encoding="utf-8")
    calls = [["Edit", {"file_path": str(f), "old_string": "x", "new_string": "X"}],
             ["Edit", {"file_path": str(f), "old_string": "y", "new_string": "Y"}]]
    tid = run(engine, "PAR " + json.dumps(calls), wd)
    first, second = engine.file_changes(tid)["changes"]
    assert [(x["k"], x["t"]) for x in engine.file_change(tid, first["id"])["lines"] if x["k"] in "+-"] == [("-", "x"), ("+", "X")]
    assert [(x["k"], x["t"]) for x in engine.file_change(tid, second["id"])["lines"] if x["k"] in "+-"] == [("-", "y"), ("+", "Y")]
    engine.undo_change(tid, second["id"])
    assert f.read_text(encoding="utf-8") == "X\ny\n"


def test_the_copy_is_taken_when_the_user_approves_the_write(engine, tmp_path):
    """Assisted preset: the write waits for the user; what is undone is the file as it was at approval."""
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "valide.txt"
    f.write_text("ancien", encoding="utf-8")
    tid = engine.create_task(do("Write", file_path=str(f), content="nouveau"), profile="work", preset="assiste",
                             workdir=str(wd))["id"]
    wait_for(lambda: task_status(engine, tid) == "awaiting")
    f.write_text("changé pendant la validation", encoding="utf-8")
    engine.decide(tid, engine.tasks[tid]["pending"][0]["id"], "allow")
    wait_for(lambda: task_status(engine, tid) == "done" and tid not in engine.runs)
    [row] = engine.file_changes(tid)["changes"]
    engine.undo_change(tid, row["id"])
    assert f.read_text(encoding="utf-8") == "changé pendant la validation"


def test_a_cli_that_gives_no_call_id_to_the_hook_is_followed_too(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "z.txt"
    f.write_text("0", encoding="utf-8")
    tid = run(engine, f"DO_SANS_ID Write {json.dumps({'file_path': str(f), 'content': '1'})}", wd)
    [row] = engine.file_changes(tid)["changes"]
    assert row["tool_use_id"].startswith("toolu_")
    engine.undo_change(tid, row["id"])
    assert f.read_text(encoding="utf-8") == "0"


def test_refused_failed_or_unchanged_writes_record_nothing(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "x.txt"
    f.write_text("même", encoding="utf-8")
    tid = run(engine, "\n".join([do("Write", file_path=str(f), content="même"),
                                 do("Edit", file_path=str(f), old_string="absent", new_string="z")]), wd)
    assert not engine.file_changes(tid)["changes"]
    tid = run(engine, do("Write", file_path=str(f), content="refusé"), wd, preset="lecture")
    assert "Write:refus" in engine.tasks[tid]["result"] and not engine.file_changes(tid)["changes"]
    assert f.read_text(encoding="utf-8") == "même"


def test_copies_go_with_the_task_and_protected_files_are_never_written(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    f = wd / "y.txt"
    tid = run(engine, do("Write", file_path=str(f), content="1"), wd)
    [row] = engine.file_changes(tid)["changes"]
    folder = engine.data_dir / "modifications" / tid
    assert folder.is_dir()
    cfg = engine.cfg.model_copy(deep=True)
    cfg.security.forbidden_paths.append("**/y.txt")
    engine.cfg_store.save(cfg, "tests")
    engine.tasks[tid]["spec"]["forbidden"] = list(cfg.security.forbidden_paths)
    with pytest.raises(TaskError) as e:
        engine.undo_change(tid, row["id"])
    assert e.value.status == 403 and f.exists()
    engine.delete_task(tid)
    assert not folder.exists()


def test_store_limits_and_binary_files(tmp_path):
    store = ch.Changes(tmp_path / "m")
    p = tmp_path / "img.bin"
    snap = ch.Snapshot("Write", str(p), b"\x00\x01")
    p.write_bytes(b"\x00\x02")
    row = store.record("abcd1234", snap, p.read_bytes())
    assert row["binary"] and store.diff("abcd1234", row["id"])["lines"] == []
    assert ch.snapshot("Write", {"file_path": str(tmp_path / "gros.txt")}, str(tmp_path)).before is None
    big = tmp_path / "gros.txt"
    big.write_bytes(b"a" * (ch.MAX_FILE + 1))
    assert ch.snapshot("Write", {"file_path": str(big)}, str(tmp_path)) is None  # not followed
    assert ch.snapshot("Bash", {"command": "echo"}, str(tmp_path)) is None
    with pytest.raises(ch.ChangeError):
        store.rows("../x")
    store.sweep(set())
    assert not (tmp_path / "m" / "abcd1234").exists()
