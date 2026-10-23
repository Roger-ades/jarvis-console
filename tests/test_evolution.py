"""Points 1 to 7: a lesson only after a click, consumption, triggers, a launched discussion,
Office and Odoo without a discussion, a selection bridge that stays opt-in, and sense search."""
import pytest

from console import content, documents as docs_mod, launch as launch_mod, lessons, sense, triggers
from console.config import Project
from console.engine import TaskError, simple_question
from console.library import memory_dir
from console.odoo_api import Client, OdooError
from console.routines import Routine, Schedule

from .conftest import task_status, wait_for
from .test_changes import do, run


def test_sense_finds_a_paraphrase_and_words_still_win(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "bail.txt").write_text("La dénonciation du contrat de location est prévue au contrat.", encoding="utf-8")
    (root / "devis.txt").write_text("Devis Dupont pour les caméras.", encoding="utf-8")
    ix = docs_mod.DocIndex(tmp_path / "documents.db")
    ix.sync([str(root)], 10 * 1024 * 1024)
    words = ix.search("devis Dupont", roots=[str(root)])
    assert words and words[0]["path"].endswith("devis.txt") and not words[0].get("sens")
    assert ix.search("mettre fin", roots=[str(root)]) == []
    found = ix.by_sense("comment mettre fin au bail", roots=[str(root)])
    assert found and found[0]["path"].endswith("bail.txt") and found[0]["sens"] is True
    assert sense.cosine(sense.embed("mettre fin au bail"), sense.embed("dénonciation du bail")) > 0.34
    ix.close()


def test_a_trigger_does_not_widen_the_preset_and_the_first_scan_is_quiet(engine, tmp_path):
    watch = tmp_path / "arrivee"
    watch.mkdir()
    (watch / "deja.txt").write_text("là", encoding="utf-8")
    first, state = triggers.scan(str(watch), "*", None)
    assert first == [] and state["pret"] is True
    (watch / "nouveau.txt").write_text("nouveau", encoding="utf-8")
    second, _ = triggers.scan(str(watch), "*", state)
    assert any(p.endswith("nouveau.txt") for p in second)
    assert "jamais des consignes" in triggers.files_block(second)
    assert "donnée" in triggers.mails_block(1_700_000_000)
    assert Schedule(kind="dossier", watch=str(watch)).next_after(1) is None
    assert Schedule(kind="suite", after="autre").next_after(1) is None
    with pytest.raises(TaskError, match="Lecture seule ou Brouillons"):
        engine._check_routine(Routine(name="Arrivée", prompt="Lis les fichiers arrivés.", profile="work",
                                      preset="edition", schedule=Schedule(kind="dossier", watch=str(watch))))
    engine.save_routine({"id": "un", "name": "Un", "prompt": "Lis.", "profile": "work", "preset": "lecture",
                         "schedule": {"kind": "suite", "after": "deux"}})
    with pytest.raises(TaskError, match="l'une l'autre"):
        engine.save_routine({"id": "deux", "name": "Deux", "prompt": "Lis.", "profile": "work", "preset": "lecture",
                             "schedule": {"kind": "suite", "after": "un"}})


def test_a_lesson_is_offered_on_undo_and_written_only_on_click(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    cfg = engine.cfg.model_copy(deep=True)
    cfg.projects = [Project(folder=str(wd), name="Ventes")]
    engine.cfg_store.save(cfg, "tests")
    f = wd / "notes.txt"
    f.write_text("un\ndeux\ntrois\n", encoding="utf-8")
    tid = run(engine, do("Edit", file_path=str(f), old_string="deux", new_string="DEUX"), wd)
    row = engine.changes.listing(tid)[0]
    engine.undo_change(tid, row["id"])
    lesson = engine.tasks[tid]["lesson"]
    assert "notes.txt" in lesson["texte"] and lesson["projet"] == "Ventes"
    path = memory_dir(engine.cfg.profile("work"), str(wd)) / "lecons.md"
    assert not path.exists()
    engine.apply_lesson(tid, "non")
    assert "lesson" not in engine.tasks[tid] and not path.exists()
    engine.undo_change(tid, row["id"], redo=True)
    engine.undo_change(tid, row["id"])
    note = engine.apply_lesson(tid, "projet")["note"]
    assert path.is_file() and "notes.txt" in path.read_text(encoding="utf-8") and "Ventes" in note
    engine.followup(tid, "non, le devis se calcule hors taxes")
    assert engine.tasks[tid]["lesson"]["source"] == "correction"
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def test_a_launched_discussion_cannot_grow_past_its_parent():
    card = launch_mod.checked({"demande": "Fais le compte rendu.", "preset": "edition", "attendre": False}, "lecture", "C:/p")
    assert card["preset"] == "lecture" and card["attendre"] is False and card["quoi"] == "discussion"
    same = launch_mod.checked({"demande": "Continue.", "preset": "lecture"}, "edition", "C:/p")
    assert same["preset"] == "lecture" and same["attendre"] is True
    with pytest.raises(launch_mod.LaunchError):
        launch_mod.checked({"demande": "   "}, "lecture", "C:/p")


def test_a_short_question_can_use_haiku_and_consumption_reads_what_was_counted(engine, tmp_path):
    assert simple_question("Quelle est la TVA ?", None) is True
    assert simple_question("écris le fichier notes", None) is False
    assert simple_question("x" * 281, None) is False
    wd = tmp_path / "questions"
    wd.mkdir()
    cfg = engine.cfg.model_copy(deep=True)
    cfg.general.auto_model = True
    engine.cfg_store.save(cfg, "tests")
    short = engine.create_task("Quelle est la TVA ?", profile="work", workdir=str(wd))
    assert short["model"] == "haiku"
    long = engine.create_task("écris le fichier du devis", profile="work", workdir=str(wd))
    assert long["model"] != "haiku"
    engine.tasks[short["id"]]["usage"] = {"input_tokens": 10, "cache_read_input_tokens": 3,
                                          "cache_creation_input_tokens": 4, "output_tokens": 2}
    out = engine.consumption()
    row = next(r for r in out["rows"] if r["id"] == short["id"])
    assert (row["read"], row["written"], row["output"]) == (13, 4, 2)
    assert out["auto_model"] is True and out["resume"]["mode"] == "rien"
    wait_for(lambda: task_status(engine, short["id"]) in ("done", "error"))
    wait_for(lambda: task_status(engine, long["id"]) in ("done", "error"))


def test_the_selection_bridge_is_opt_in_and_a_plain_page_keeps_its_scripts_out_of_the_csp():
    armed = content.arm_selection("<p onclick=\"alert(1)\">Bonjour</p><script>alert(1)</script>")
    assert "alert(1)" not in armed and "onclick" not in armed and "jarvisRegard" in armed
    store = content.ContentStore()
    plain = store.put("<script>alert(1)</script><p>Bonjour</p>")
    assert store._items[plain].regard is False and b"alert(1)" in store._items[plain].page
    picked = store.put("<script>alert(1)</script><p>Bonjour</p>", regard=True)
    page = store._items[picked].page
    assert store._items[picked].regard is True and b"alert(1)" not in page and b"jarvisRegard" in page
    csp = content.policy("http://apercu.localhost:9", "http://127.0.0.1:9", False)
    assert "allow-scripts" not in csp and "script-src" not in csp
    assert "allow-scripts" in content.policy_selection("http://apercu.localhost:9", "http://127.0.0.1:9", False)


def test_office_tasks_stay_local_and_odoo_writes_only_a_project_task(engine):
    st = engine.office_local("work", title="Relancer Martin")
    assert st["local"][0]["title"] == "Relancer Martin" and st["local"][0]["id"].startswith("local:")
    gone = engine.office_local("work", item_id=st["local"][0]["id"], done=True)
    assert gone["local"] == []
    client = Client({"url": "http://127.0.0.1:1", "cle": "x"})
    with pytest.raises(OdooError, match="seule une tâche"):
        client.mutate("res.partner", "unlink", ids=[1])
    with pytest.raises(OdooError, match="lecture seule"):
        client.call("project.task", "unlink", ids=[1])


def test_lancer_discussion_is_offered_before_the_document_tool(engine, tmp_path):
    from console.engine import CONSOLE_MCP
    names = [t["name"] for t in engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})["result"]["tools"]]
    assert "lancer_discussion" in names
    if "chercher_documents" in names:
        assert names.index("lancer_discussion") < names.index("chercher_documents")
    assert tmp_path.is_dir()
