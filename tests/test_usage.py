"""Quota savers: context size per task, early compaction, per-model usage, costs counted once."""
import pytest

from console import claude_cli, team
from console.config import DEFAULT_TEAM_PROMPT, OLD_TEAM_PROMPTS, General, Profile, TeamSettings

from .conftest import task_status, wait_for


def finish(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)
    return engine.tasks[tid]


def test_context_size_and_compaction_threshold_are_tracked(engine):
    t = finish(engine, engine.create_task("bonjour", profile="work", preset="lecture")["id"])
    assert t["context_tokens"] == 21510 and t["context_at"]  # size of the lead's latest request
    assert t["context_limit"] == 200_000                     # 1M window, compacted at 200 k
    assert t["model_usage"]["fake-model"]["cacheReadInputTokens"] == 21000


def test_costs_and_usage_are_not_counted_twice(engine):
    """The CLI's total_cost_usd and modelUsage add up over its process: two answers in one process
    cost 0.02, not 0.01 + 0.02; a later follow-up (new process) adds its own."""
    tid = engine.create_task("SLEEP 1.5\nun", profile="work", preset="lecture")["id"]
    wait_for(lambda: engine.runs.get(tid) and engine.runs[tid].proc and engine.tasks[tid]["status"] == "running")
    engine.followup(tid, "deux")
    t = finish(engine, tid)
    assert t["cost_usd"] == pytest.approx(0.02)
    assert t["model_usage"]["fake-model"]["inputTokens"] == 20
    engine.followup(tid, "trois")
    t = finish(engine, tid)
    assert t["cost_usd"] == pytest.approx(0.03)
    assert t["model_usage"]["fake-model"]["inputTokens"] == 30


def test_team_usage_is_split_by_model(engine):
    t = finish(engine, engine.create_task("TEAM", profile="work", preset="lecture")["id"])
    assert set(t["model_usage"]) == {"fake-model", "claude-haiku-4-5-20251001"}
    assert t["model_usage"]["claude-haiku-4-5-20251001"]["contextWindow"] == 200000


def test_compact_on_request_then_continue(engine):
    tid = engine.create_task("CTX 250000\nbonjour", profile="work", preset="lecture")["id"]
    t = finish(engine, tid)
    assert t["context_tokens"] == 251510 and t["result"] == "écho:bonjour"
    engine.followup(tid, "", compact=True)
    t = finish(engine, tid)
    assert t["context_tokens"] == 12000
    assert t["result"] == "écho:bonjour"  # the empty answer of /compact does not replace the last one
    infos = [e["data"]["text"] for e in engine.store.events(tid) if e["kind"] == "info"]
    # (the stand-in CLI restarts from a small context in a new process; the real one resumes the session)
    assert any(i.startswith("Contexte compacté à ta demande : ") and i.endswith(" → 12 k tokens.") for i in infos)
    # compact first, then the message, in one go
    engine.followup(tid, "on reprend", compact=True)
    t = finish(engine, tid)
    assert t["result"] == "écho:on reprend" and t["context_tokens"] == 13510
    users = [e["data"]["text"] for e in engine.store.events(tid) if e["kind"] == "user"]
    assert "/compact" not in users and users[-1] == "on reprend"


def test_nothing_to_compact_before_the_session_exists(engine):
    tid = engine.create_task("bonjour", profile="work", preset="lecture", not_before=10**10)["id"]
    with pytest.raises(Exception, match="Rien à compacter"):
        engine.followup(tid, "", compact=True)


def test_compaction_threshold_is_passed_to_claude_code():
    prof = Profile(id="p", name="P", color="#ffffff", config_dir="", workdir="~")
    assert claude_cli.build_env(prof, General())["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "200000"
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in claude_cli.build_env(prof, General(compact_at_k=0))
    custom = Profile(id="p", name="P", color="#ffffff", config_dir="", workdir="~", env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "400000"})
    assert claude_cli.build_env(custom, General())["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "400000"


def test_team_scout_keeps_mcp_tools_and_everyone_is_told_to_use_the_code_index():
    agents, _, prompt = team.build("opus", TeamSettings())
    scout = agents["eclaireur"]
    assert "tools" not in scout and {"Write", "Edit", "Bash", "PowerShell"} <= set(scout["disallowedTools"])
    assert all("codegraph_explore" in a["prompt"] for a in agents.values())
    assert "N'explore pas le code toi-même" in prompt and "codegraph_explore" in prompt


def test_saved_team_rules_of_earlier_versions_are_upgraded():
    old = next(iter(OLD_TEAM_PROMPTS))
    assert TeamSettings(instructions=old).instructions == DEFAULT_TEAM_PROMPT
    assert TeamSettings(instructions="Mes propres règles").instructions == "Mes propres règles"
