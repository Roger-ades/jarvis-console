import json

from console import team
from console.config import TeamSettings

from .conftest import task_status, wait_for


def roles(chief):
    agents, models, prompt = team.build(chief, TeamSettings())
    return models, prompt


def test_roles_depend_on_the_chief():
    assert roles("opus")[0] == {"eclaireur": "haiku", "executant": "sonnet"}          # delegates down
    assert roles("sonnet")[0] == {"eclaireur": "haiku", "expert": "opus"}             # escalates up
    assert roles("claude-opus-5-5")[0] == {"eclaireur": "haiku", "executant": "sonnet"}
    assert roles("haiku")[0] == {"expert": "opus"}  # never a worker dearer than the chief
    assert "expert" in roles("sonnet")[1] and "moins de trois actions" in roles("sonnet")[1]


def test_default_resolves_through_the_account_models():
    models, _ = team.build("default", TeamSettings(), {"default": "claude-sonnet-5-5"})[1:], None
    assert models[0] == {"eclaireur": "haiku", "expert": "opus"}


def test_scout_is_read_only():
    agents, _, _ = team.build("opus", TeamSettings())
    assert set(agents["eclaireur"]["tools"]) <= {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite"}


def test_team_task_passes_agents_and_subagent_model(engine):
    t = engine.create_task("TEAM", profile="work", model="opus", preset="lecture", team=True)
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    task = engine.tasks[t["id"]]
    assert task["team"] and task["team_agents"] == {"eclaireur": "haiku", "executant": "sonnet"}
    res = task["result"]
    assert json.loads(res.split("AGENTS=")[1].splitlines()[0]) == task["team_agents"]
    assert "SUBMODEL=sonnet" in res
    assert "Agent" in res.split("TOOLS=")[1]  # the read-only preset still lets the chief delegate


def test_without_team_nothing_changes(engine):
    t = engine.create_task("TEAM", profile="work", model="opus", preset="lecture")
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    res = engine.tasks[t["id"]]["result"]
    assert "AGENTS={}" in res and "SUBMODEL=(aucun)" in res and "Agent" not in res.split("TOOLS=")[1]


def test_delegation_is_allowed_but_subagent_actions_are_still_checked(engine):
    inp = json.dumps({"subagent_type": "eclaireur", "description": "lire", "prompt": "x"})
    t = engine.create_task(f'TOOL Agent {inp}\nTOOL Bash {{"command": "echo hi"}}', profile="work",
                           model="opus", preset="lecture", team=True)
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    res = engine.tasks[t["id"]]["result"]
    assert "Agent:ok" in res and "Bash:refus" in res
