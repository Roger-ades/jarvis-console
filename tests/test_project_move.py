"""A project moves to another folder of the disk with everything it has; the old folder is left as it was."""
import json

import pytest

from console import brief as brief_mod
from console import library, odoo_link, office_link
from console.engine import TaskError
from console.permissions import norm

from .conftest import task_status, wait_for
from .test_move import write_session


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def test_a_project_moves_with_everything_it_has(engine, tmp_path):
    old, new = tmp_path / "ancien", tmp_path / "nouveau"
    old.mkdir()
    new.mkdir()
    (old / "plan.txt").write_text("garde-moi", encoding="utf-8")
    (old / "CLAUDE.md").write_text("Consignes du chantier", encoding="utf-8")
    (old / ".claude" / "commands").mkdir(parents=True)
    (old / ".claude" / "commands" / "maj.md").write_text("Mets à jour", encoding="utf-8")
    (new / "BRIEF.md").write_text("déjà là", encoding="utf-8")
    (old / brief_mod.FILE_NAME).write_text("ancien suivi", encoding="utf-8")
    engine.save_project({"folder": str(old), "name": "Chantier", "profile": "work",
                         "brief": {"enabled": True, "mails": False, "office_tasks": False, "calendar": False, "odoo": False}})
    src = engine.projects()[0]["folder"]
    engine.add_project_rules(src, ["Bash(npm test)"])
    note = engine.save_note({"text": "Appeler le client", "folder": src})
    general = engine.save_note({"text": "Générale"})
    engine.save_routine({"name": "Relevé", "prompt": "bonjour", "profile": "work", "preset": "lecture", "workdir": src,
                         "schedule": {"kind": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]}})
    old_brief = engine.routines[brief_mod.project_routine_id(src)]
    old_brief.last_run = 123.0
    engine.store.kv_set("action_pins", {norm(src): {"maj": "abc"}})
    engine.store.kv_set(odoo_link.kv_key(norm(src)), {"tasks": [{"id": 1}]})
    engine.store.kv_set(office_link.kv_key(norm(src)), {"todos": [1]})
    prof = engine.cfg.profile("work")
    mem = library.memory_dir(prof, src)
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("souvenir", encoding="utf-8")
    t = engine.create_task("ARGS", profile="work", workdir=src)
    settle(engine, t["id"])
    sid = write_session(prof, src, sid=engine.tasks[t["id"]]["session_id"])
    other = write_session(prof, src)  # a session run elsewhere (Claude Desktop, CLI)
    cli = library.config_dir(prof) / ".claude.json"
    cli.write_text(json.dumps({"projects": {src: {"mcpServers": {"x": {"command": "x"}}}}}), encoding="utf-8")

    saved = engine.move_project(src, str(new))
    dst, report = saved["folder"], saved["report"]
    assert norm(dst) == norm(str(new)) and saved["name"] == "Chantier"
    assert [p["folder"] for p in engine.projects()] == [dst]
    assert [r.pattern for r in engine.cfg.project_rules if norm(r.folder) == norm(dst)] == ["Bash(npm test)"]
    assert engine.store.get_note(note["id"])["folder"] == dst and engine.store.get_note(general["id"])["folder"] == ""
    assert [r.name for r in engine.routines.values() if r.workdir == dst and not r.brief_project] == ["Relevé"]
    brief = engine.routines[brief_mod.project_routine_id(dst)]
    assert brief.last_run == 123.0 and brief.brief_project == dst and brief_mod.project_routine_id(src) not in engine.routines
    assert engine.store.kv_get("action_pins")[norm(dst)] == {"maj": "abc"} and norm(src) not in engine.store.kv_get("action_pins")
    assert engine.store.kv_get(odoo_link.kv_key(norm(dst))) == {"tasks": [{"id": 1}]}
    assert engine.store.kv_get(office_link.kv_key(norm(dst))) == {"todos": [1]}
    # Claude's memory is moved, not duplicated
    assert (library.memory_dir(prof, dst) / "MEMORY.md").read_text(encoding="utf-8") == "souvenir" and not mem.exists()
    # the sessions and the discussion follow
    for s in (sid, other):
        assert library.find_transcript(prof, s).parent.name == library.project_slug(dst)
    assert engine.tasks[t["id"]]["workdir"] == dst
    assert report["sessions"] == 2 and report["discussions"] == 1 and report["notes"] == 1 and report["mémoire"] == 1
    # Claude Code's settings of the folder
    assert json.loads(cli.read_text(encoding="utf-8"))["projects"][dst]["mcpServers"] == {"x": {"command": "x"}}
    # the project's own files are copied, never replacing one already there; the old folder is untouched
    assert (new / "CLAUDE.md").read_text(encoding="utf-8") == "Consignes du chantier"
    assert (new / ".claude" / "commands" / "maj.md").read_text(encoding="utf-8") == "Mets à jour"
    assert (new / "BRIEF.md").read_text(encoding="utf-8") == "déjà là" and not (new / "plan.txt").exists()
    assert sorted(report["fichiers"]) == [".claude/commands/maj.md", "CLAUDE.md"]
    assert (old / "plan.txt").read_text(encoding="utf-8") == "garde-moi" and (old / "CLAUDE.md").is_file()
    assert "erreurs" not in report


def test_memory_already_in_the_new_folder_is_kept(engine, tmp_path):
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    new.mkdir()
    engine.save_project({"folder": str(old), "name": "A", "profile": "work"})
    src = engine.projects()[0]["folder"]
    prof = engine.cfg.profile("work")
    for folder, text in ((src, "ancien"), (str(new.resolve()), "nouveau")):
        d = library.memory_dir(prof, folder)
        d.mkdir(parents=True)
        (d / "MEMORY.md").write_text(text, encoding="utf-8")
    dst = engine.move_project(src, str(new))["folder"]
    merged = (library.memory_dir(prof, dst) / "MEMORY.md").read_text(encoding="utf-8")
    assert merged.startswith("nouveau") and merged.endswith("ancien")


def test_a_move_is_refused_where_it_makes_no_sense(engine, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    engine.save_project({"folder": str(a), "name": "A"})
    engine.save_project({"folder": str(b), "name": "B"})
    fa = next(p["folder"] for p in engine.projects() if p["name"] == "A")
    with pytest.raises(TaskError, match="déjà le projet « B »"):
        engine.move_project(fa, str(b))
    with pytest.raises(TaskError, match="déjà dans ce dossier"):
        engine.move_project(fa, fa)
    with pytest.raises(TaskError, match="introuvable"):
        engine.move_project(fa, str(tmp_path / "absent"))
    with pytest.raises(TaskError, match="Projet introuvable"):
        engine.move_project(str(tmp_path), str(b))
    c = tmp_path / "c"
    c.mkdir()
    t = engine.create_task("SLEEP 3", workdir=fa)
    wait_for(lambda: t["id"] in engine.runs)
    with pytest.raises(TaskError, match="en cours"):
        engine.move_project(fa, str(c))
    settle(engine, t["id"])
