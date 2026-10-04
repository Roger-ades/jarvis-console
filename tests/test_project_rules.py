"""« Toujours autoriser ceci pour ce projet »: remembered per folder, never above the protections."""
import json

import pytest

from console.permissions import match_command, project_rule_problem, suggest_rules

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture

CREATE = {"model": "sale.order", "values": {"partner_id": 1}}


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def call(tool, inp):
    return f"TOOL {tool} {json.dumps(inp)}"


def test_suggestions_are_narrow():
    assert suggest_rules("Bash", {"command": "python _scripts/maj.py --ecrire && git status -s"}) == \
        ["Bash(python _scripts/maj.py:*)", "Bash(git status:*)"]
    assert suggest_rules("Edit", {"file_path": "a.txt"}) == ["Edit(./**)"]
    assert suggest_rules("WebFetch", {"url": "https://www.visiotech.es/x"}) == ["WebFetch(domain:www.visiotech.es)"]
    assert suggest_rules("mcp__odoo__create_record", {}) == ["mcp__odoo__create_record"]
    assert match_command("python _scripts/maj.py:*", "python _scripts/maj.py --ecrire")


@pytest.mark.parametrize("pattern, tool", [("Bash", "Bash"), ("Bash(*)", "Bash"), ("mcp__*__create_record", "mcp__odoo__create_record"),
                                           ("Edit(**)", "Edit"), ("Write(./**)", "Edit")])
def test_too_broad_rules_are_refused(pattern, tool):
    assert project_rule_problem(pattern, tool)


def test_remember_then_the_same_action_goes_through(engine):
    t = engine.create_task(call("mcp__odoo__create_record", CREATE), profile="work", preset="brouillons")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")
    pending = engine.tasks[t["id"]]["pending"][0]
    assert pending["suggest"] == ["mcp__odoo__create_record"]
    res = engine.decide(t["id"], pending["id"], "allow", remember=pending["suggest"])
    assert res["remembered"] == ["mcp__odoo__create_record"]
    done(engine, t["id"])
    folder = engine.tasks[t["id"]]["workdir"]
    assert engine.project_allow(folder) == ["mcp__odoo__create_record"]
    # next time in this project: no validation
    engine.followup(t["id"], call("mcp__odoo__create_record", CREATE))
    done(engine, t["id"])
    assert "mcp__odoo__create_record:ok" in engine.tasks[t["id"]]["result"]
    assert not any(e["kind"] == "approval" for e in engine.store.events(t["id"])[-6:])


def test_a_remembered_create_is_not_filtered_by_the_model(engine):
    """Once create_record is remembered, a task is created the same way as a quote. Odoo accepts or refuses."""
    t = engine.create_task(call("mcp__odoo__create_record", CREATE), profile="work", preset="brouillons")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")
    p = engine.tasks[t["id"]]["pending"][0]
    engine.decide(t["id"], p["id"], "allow", remember=["mcp__odoo__create_record"])
    done(engine, t["id"])
    task = {"model": "project.task", "values": {"name": "Relance"}}
    engine.followup(t["id"], call("mcp__odoo__create_record", task))
    done(engine, t["id"])
    assert "mcp__odoo__create_record:ok" in engine.tasks[t["id"]]["result"]


def test_rule_belongs_to_its_folder_only(engine, tmp_path):
    folder = str(tmp_path / "Visiotech")
    engine.add_project_rules(folder, ["mcp__odoo__create_record"], tool="mcp__odoo__create_record")
    t = engine.create_task(call("mcp__odoo__create_record", CREATE), profile="work", preset="brouillons")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")  # another folder: still asked
    engine.decide(t["id"], engine.tasks[t["id"]]["pending"][0]["id"], "deny")
    done(engine, t["id"])


def test_refused_rule_does_not_allow_the_action(engine):
    t = engine.create_task(call("mcp__odoo__create_record", CREATE), profile="work", preset="brouillons")
    wait_for(lambda: task_status(engine, t["id"]) == "awaiting")
    p = engine.tasks[t["id"]]["pending"][0]
    with pytest.raises(Exception, match="exact"):
        engine.decide(t["id"], p["id"], "allow", remember=["mcp__*__create_record"])
    assert engine.tasks[t["id"]]["pending"]  # still waiting for a decision
    engine.decide(t["id"], p["id"], "deny")
    done(engine, t["id"])


def test_rules_in_the_project_panel(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    eng = client.app.state.engine
    folder = client.get("/api/workspace", params={"profile": "work"}, headers=h).json()["folder"]
    eng.add_project_rules(folder, ["WebFetch(domain:visiotech.es)"], tool="WebFetch")
    ws = client.get("/api/workspace", params={"profile": "work", "folder": folder}, headers=h).json()
    assert [r["pattern"] for r in ws["rules"]] == ["WebFetch(domain:visiotech.es)"]
    q = {"profile": "work", "folder": folder, "pattern": "WebFetch(domain:visiotech.es)"}
    assert client.delete("/api/workspace/rules", params=q, headers=h).status_code == 200
    assert eng.project_allow(folder) == []
    assert client.delete("/api/workspace/rules", params=q, headers=h).status_code == 404
