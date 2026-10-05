"""« On va travailler dans le projet Network » (console/project_nav.py): the project found by its name, the
tool projet (open the panel; move the discussion once the user accepts on a card) and the prompt line."""
import json

from console import presence, project_nav
from console.config import Project
from console.engine import CONSOLE_MCP, PROJECT_TOOL, norm

from .conftest import wait_for
from .test_project_tools import project, proposal, settle


def tool(args) -> str:
    return "PROJECT " + json.dumps(args, ensure_ascii=False)


def test_a_project_is_found_by_its_name_or_its_folder():
    net = Project(folder="C:/clients/reseau-etimia", name="Network")
    bourse = Project(folder="C:/perso/Bourse", name="Bourse")
    bourse2 = Project(folder="D:/archives/bourse-2024", name="Bourse 2024")
    projects = [net, bourse, bourse2]
    assert project_nav.fold("  Réseau-ÉTIMIA ! ") == "reseau etimia"
    assert project_nav.find("network", projects) == (net, [])
    assert project_nav.find("Réseau Etimia", projects) == (net, [])  # the folder's name
    assert project_nav.find("bourse", projects) == (bourse, [])       # exact before partial
    assert project_nav.find("netw", projects) == (net, [])            # one partial match
    found, many = project_nav.find("bour", projects)
    assert found is None and set(p.name for p in many) == {"Bourse", "Bourse 2024"}
    assert project_nav.find("", projects) == (None, [])
    assert project_nav.find("inconnu", projects) == (None, [])


def test_the_prompt_lists_the_projects_in_a_stable_order():
    projects = [Project(folder="C:/b", name="Bourse"), Project(folder="C:/a", name="Network")]
    line = project_nav.prompt_line(projects)
    assert line == project_nav.prompt_line(list(reversed(projects)))  # same text: the cache holds
    assert line.index("« Bourse »") < line.index("« Network »") and "rattacher" in line and "ouvrir" in line
    assert project_nav.prompt_line([]) == ""
    many = [Project(folder=f"C:/p{i:02d}", name=f"P{i:02d}") for i in range(project_nav.MAX_LISTED + 5)]
    assert project_nav.prompt_line(many).count("« P") == project_nav.MAX_LISTED


def test_the_tool_is_offered_to_claude(engine, tmp_path):
    project(engine, tmp_path, name="Network")
    listed = engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})
    names = [x["name"] for x in listed["result"]["tools"]]
    assert "projet" in names and PROJECT_TOOL == f"mcp__{CONSOLE_MCP}__projet"
    line = project_nav.prompt_line(engine._account_projects("work"))
    t = engine.create_task("bonjour", profile="work", preset="lecture")
    settle(engine, t["id"])
    text = presence.prompt(engine.tasks[t["id"]], engine.cfg.profile("work"), engine.cfg.preset("lecture"), projects=line)
    assert "« Network »" in text and "outil projet" in text


def test_open_shows_the_panel_and_leaves_the_discussion_where_it_is(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    seen = []
    engine.bus.publish = (lambda pub: lambda kind, data: (seen.append((kind, data)), pub(kind, data))[1])(engine.bus.publish)
    t = engine.create_task(tool({"action": "ouvrir", "nom": "network"}), profile="work", preset="lecture")
    settle(engine, t["id"])
    opened = [d for k, d in seen if k == "project_open"]
    assert opened and norm(opened[0]["folder"]) == norm(folder) and opened[0]["asked"] is True
    assert norm(engine.tasks[t["id"]]["workdir"]) != norm(folder)
    assert "Panneau du projet « Network » ouvert" in engine.tasks[t["id"]]["result"]
    # an unknown project: the projects of the account are listed back
    t = engine.create_task(tool({"action": "ouvrir", "nom": "inconnu"}), profile="work", preset="lecture")
    settle(engine, t["id"])
    assert "Projet introuvable" in engine.tasks[t["id"]]["result"] and "« Network »" in engine.tasks[t["id"]]["result"]


def test_attach_waits_for_the_click_then_moves_at_the_end_of_the_turn(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    t = engine.create_task(tool({"action": "rattacher", "nom": "Network"}), profile="work", preset="lecture")
    before = engine.tasks[t["id"]]["workdir"]
    p = proposal(engine, t["id"])
    assert p["tool"] == PROJECT_TOOL and "« Network »" in p["reason"]
    assert p["input"]["quoi"] == "rattacher" and norm(p["input"]["dossier"]) == norm(folder)
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert engine.tasks[t["id"]]["workdir"] == before and "garde la discussion" in engine.tasks[t["id"]]["result"]

    t = engine.create_task(tool({"action": "rattacher", "nom": "Network"}), profile="work", preset="lecture")
    engine.decide(t["id"], proposal(engine, t["id"])["id"], "allow")
    settle(engine, t["id"])
    wait_for(lambda: norm(engine.tasks[t["id"]]["workdir"]) == norm(folder))
    task = engine.tasks[t["id"]]
    assert "move_to" not in task and task["preset"] == "lecture"
    notes = [e["data"].get("text") or "" for e in engine.store.events(t["id"]) if e["kind"] == "info"]
    assert len([n for n in notes if "Discussion déplacée dans le projet" in n or "passée dans le projet" in n]) == 1
    rows, _ = engine.store.audit_rows(kind="discussion rattachée à un projet")
    assert len(rows) == 1
    # already in the project: nothing to ask
    t = engine.create_task(tool({"action": "rattacher", "nom": "Network"}), profile="work", preset="lecture", workdir=folder)
    settle(engine, t["id"])
    assert "déjà dans le projet" in engine.tasks[t["id"]]["result"] and not engine.tasks[t["id"]].get("pending")
