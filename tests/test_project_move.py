"""A project points at another folder of the disk: what the console keeps for it follows, the disk is untouched."""
import pytest

from console import brief as brief_mod
from console import library, odoo_link, office_link
from console.engine import TaskError
from console.permissions import norm


def test_a_project_moves_with_what_the_console_keeps_for_it(engine, tmp_path):
    old, new = tmp_path / "ancien", tmp_path / "nouveau"
    old.mkdir()
    new.mkdir()
    (old / "plan.txt").write_text("garde-moi", encoding="utf-8")
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

    saved = engine.move_project(src, str(new))
    dst = saved["folder"]
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
    assert (library.memory_dir(prof, dst) / "MEMORY.md").read_text(encoding="utf-8") == "souvenir"
    # nothing moved or deleted on the disk
    assert (old / "plan.txt").read_text(encoding="utf-8") == "garde-moi" and not list(new.iterdir())
    assert (mem / "MEMORY.md").is_file()


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
