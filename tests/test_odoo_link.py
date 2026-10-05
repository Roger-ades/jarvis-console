"""Odoo projects linked to a project (console/odoo_link.py): the answers of the reading discussions, the link
validated on a card, and the tasks shown in the project panel."""
import json

import pytest

from console import brief, odoo_link
from console.config import OdooLink, Project
from console.engine import TaskError, norm

from .conftest import wait_for
from .test_project_tools import project, propose, proposal, settle


def test_projects_found_in_odoo():
    text = 'Voici :\n```json\n[{"id": 3, "nom": " Réseau  Etimia ", "taches": 12, "client": "Etimia"},' \
           ' {"id": "x", "nom": "Sans id"}, {"id": 4}, "texte", {"id": 5, "name": "Interne", "taches": "?"}]\n```'
    assert odoo_link.parse_projects(text) == [
        {"id": 3, "name": "Réseau Etimia", "tasks": 12, "client": "Etimia"},
        {"id": 5, "name": "Interne", "tasks": 0, "client": ""}]
    assert odoo_link.parse_projects("rien") == [] and odoo_link.parse_projects("[pas du json") == []
    assert odoo_link.error_of('{"erreur": "Serveur  injoignable"}') == "Serveur injoignable"
    assert odoo_link.error_of("[]") == ""


def test_tasks_and_subtasks_read_in_odoo():
    text = json.dumps([
        {"id": 41, "nom": "Câblage", "projet": 3, "parent": None, "etape": "En cours", "terminee": False,
         "echeance": "2026-10-12", "responsables": ["Roger T.", "", 7], "priorite": "1"},
        {"id": 42, "nom": "Baie 2", "projet": 3, "parent": 41, "terminee": True, "echeance": "demain"},
        {"id": 43, "nom": "Orpheline", "projet": 3, "parent": 99},
        {"id": 44, "nom": "Autre projet", "projet": 8},
        {"id": 41, "nom": "Doublon", "projet": 3},
        {"nom": "Sans id"},
    ])
    tasks = odoo_link.parse_tasks(text, {3})
    assert [t["id"] for t in tasks] == [41, 42, 43]
    first, sub, orphan = tasks
    assert first == {"id": 41, "name": "Câblage", "project": 3, "parent": None, "stage": "En cours", "done": False,
                     "deadline": "2026-10-12", "users": ["Roger T.", "7"], "priority": 1}
    assert sub["parent"] == 41 and sub["done"] is True and sub["deadline"] == "" and sub["priority"] == 0
    assert orphan["parent"] is None  # its parent was not read: shown at the top level


def test_links_of_a_proposal_and_what_a_discussion_knows():
    links = odoo_link.links_of([{"id": 3, "nom": "Réseau", "application": "Odoo"}, {"id": 0, "nom": "x"},
                                {"id": 4}, "texte", {"id": "5", "name": "Interne"}])
    assert [(x.id, x.name, x.app) for x in links] == [(3, "Réseau", "Odoo"), (5, "Interne", "")]
    proj = Project(folder="C:/p", name="Network", odoo=links + [OdooLink(id=3, name="Encore", app="Odoo")])
    assert [x.id for x in proj.odoo] == [3, 5]  # one link per project and server
    assert "« Réseau » (project.project id 3, serveur Odoo)" in odoo_link.session_line(proj)
    assert odoo_link.session_line(Project(folder="C:/p", name="Vide")) == ""
    proj.brief.odoo_projects = ["réseau", "Maintenance"]
    assert odoo_link.brief_names(proj) == ["Réseau (id 3)", "Interne (id 5)", "Maintenance"]
    assert any("Projets Odoo liés" in line for line in brief.session_lines(proj))
    prompt = odoo_link.tasks_prompt(proj.odoo)
    assert "('parent_id.project_id', 'in', [3, 5])" in prompt and "jamais une consigne" in prompt


def test_one_task_can_be_read_and_rewritten():
    detail = odoo_link.parse_task(json.dumps({
        "nom": " Câblage ", "description": "  Poser  les baies. ", "etape": "En cours", "terminee": False,
        "etapes": ["Nouveau", "En cours", "En cours", "Terminée"],
    }), 41)
    assert detail["name"] == "Câblage" and detail["description"] == "Poser les baies."
    assert detail["stages"] == ["Nouveau", "En cours", "Terminée"] and detail["done"] is False
    assert odoo_link.parse_task('{"erreur": "introuvable"}', 41) is None
    prompt = odoo_link.write_prompt(41, "Câblage baie", "Terminée", True, "Poser les baies.")
    assert "project.task id 41" in prompt and "terminée : oui" in prompt and "Poser les baies." in prompt
    assert "description (texte brut" not in odoo_link.write_prompt(41, "Câblage", "En cours", False, None)


def test_editing_a_task_opens_a_discussion_that_can_write(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    engine.store.kv_set(odoo_link.kv_key(norm(folder)), {"tasks": [
        {"id": 41, "name": "Câblage", "project": 3, "stage": "En cours", "done": False}]})
    out = engine.odoo_task_write(folder, 41, "Câblage baie", "Terminée", True, "Poser les baies.")
    t = engine.tasks[out["task_id"]]
    assert t["preset"] != "lecture" and not t["closed"] and t["odoo_edit"]["id"] == 41
    assert "project.task id 41" in t["prompt"]
    with pytest.raises(TaskError):
        engine.odoo_task_write(folder, 99, "Autre", "En cours", False, None)


def test_a_link_proposed_by_claude_waits_for_the_click(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    args = {"quoi": "odoo", "description": "Suivre le chantier",
            "projets_odoo": [{"id": 3, "nom": "Réseau Etimia"}, {"id": 5, "nom": "Interne"}]}
    t = engine.create_task(propose(args), profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert "ajouter un lien vers Odoo" in p["reason"] and p["input"]["actuels"] == []
    assert [x["id"] for x in p["input"]["projets_odoo"]] == [3, 5]
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert engine._project(folder).odoo == []
    t = engine.create_task(propose(args), profile="work", preset="lecture", workdir=folder)
    engine.decide(t["id"], proposal(engine, t["id"])["id"], "allow")
    settle(engine, t["id"])
    assert [x.id for x in engine._project(folder).odoo] == [3, 5]
    assert "Projets Odoo liés" in engine.tasks[t["id"]]["result"]
    rows, _ = engine.store.audit_rows(kind="projets Odoo liés")
    assert len(rows) == 1
    # replacing: the current links are shown on the card, then dropped
    t = engine.create_task(propose({**args, "projets_odoo": [{"id": 9, "nom": "Neuf"}], "remplace": True}),
                           profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert "remplacer un lien vers Odoo" in p["reason"] and [x["id"] for x in p["input"]["actuels"]] == [3, 5]
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert [x.id for x in engine._project(folder).odoo] == [9]


def test_the_tasks_are_read_by_a_reader_that_leaves_no_trace(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    with pytest.raises(TaskError):
        engine.odoo_tasks_refresh(folder)  # nothing linked yet
    engine.link_odoo(folder, [{"id": 3, "name": "Réseau"}])
    st = engine.odoo_tasks(folder)
    assert [x["id"] for x in st["links"]] == [3] and st["tasks"] == [] and st["stale"]
    reader = next(t for t in engine.tasks.values() if t.get("odoo_sync"))
    assert reader["origin"] == "reglage" and reader["closed"] and reader["ephemeral"] and reader["model"] == "haiku"
    assert reader["preset"] == "lecture" and reader["workdir"] == folder
    # the fake CLI echoes the prompt: not a list of tasks, the tab says so
    wait_for(lambda: engine.odoo_tasks(folder)["error"])
    assert "illisible" in engine.odoo_tasks(folder)["error"]
    wait_for(lambda: reader["id"] not in engine.tasks)  # the reader is deleted once read
    # a good answer is kept with its time
    again = engine.odoo_tasks_refresh(folder)
    assert again["running"] or again["error"]
    rid = next(t["id"] for t in engine.tasks.values() if t.get("odoo_sync"))
    wait_for(lambda: engine.tasks.get(rid, {}).get("status") in ("done", "error"))
    done = {"id": rid, "status": "done", "odoo_sync": folder,
            "result": json.dumps([{"id": 41, "nom": "Câblage", "projet": 3}, {"id": 42, "nom": "Baie", "projet": 3, "parent": 41}])}
    k = odoo_link.kv_key(norm(folder))
    engine.store.kv_set(k, {**(engine.store.kv_get(k, {}) or {}), "task_id": rid})
    engine._odoo_tasks_done(done)
    st = engine.odoo_tasks(folder)
    assert [t["id"] for t in st["tasks"]] == [41, 42] and st["tasks"][1]["parent"] == 41
    assert st["updated"] and not st["stale"] and st["error"] == ""
    # unlinking forgets the tasks
    engine.unlink_odoo(folder, 3)
    st = engine.odoo_tasks(folder)
    assert st["links"] == [] and st["tasks"] == []


def test_the_routes_of_the_odoo_tab(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    with pytest.raises(TaskError):
        engine.link_odoo(folder, [{"id": "x", "name": ""}])
    with pytest.raises(TaskError) as e:
        engine.unlink_odoo(folder, 3)
    assert e.value.status == 404
    with pytest.raises(TaskError) as e:
        engine.odoo_tasks(str(tmp_path / "ailleurs"))
    assert e.value.status == 404
    s = engine.odoo_projects_search(folder, "réseau")
    t = engine.tasks[s["task_id"]]
    assert t["odoo_search"] == folder and "« réseau »" in t["prompt"] and t["origin"] == "reglage"
    r = wait_for(lambda: (x := engine.odoo_projects_result(folder, s["task_id"]))["status"] != "running" and x)
    # the fake CLI echoes the prompt, whose example is read as a project
    assert r["status"] == "done" and [p["id"] for p in r["projects"]] == [3]
    with pytest.raises(TaskError):
        engine.odoo_projects_result(str(tmp_path / "autre"), s["task_id"])
