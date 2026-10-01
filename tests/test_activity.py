"""What a session did, from its transcripts: every agent's tools (background sub-agents included),
MCP servers offered and used, tokens per model, the lead's context."""
import json

from console import activity
from console.config import Profile

from .conftest import task_status, wait_for


def finish(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)
    return engine.tasks[tid]


def test_background_sub_agents_and_their_mcp_calls_are_seen(engine):
    tid = engine.create_task("AGENTS 0.01\nau travail", profile="work", preset="lecture")["id"]
    finish(engine, tid)
    a = engine.activity(tid)
    assert a["available"]
    kinds = {g["type"]: g for g in a["agents"]}
    assert set(kinds) == {"chef", "eclaireur", "executant"}
    assert kinds["chef"]["tools"] == {"Agent": 2}
    assert kinds["eclaireur"]["tools"] == {"mcp__codegraph__codegraph_explore": 1, "Read": 1, "Grep": 1}
    assert kinds["eclaireur"]["background"] and kinds["eclaireur"]["tool_use_id"].startswith("toolu_")
    assert kinds["executant"]["models"] == ["claude-sonnet-5-5"] and kinds["executant"]["calls"] == 3
    # MCP servers offered to the session, and how often each was really used
    assert a["mcp"] == {"codegraph": 1, "odoo": 0}
    assert {"claude-haiku-4-5-20251001", "claude-sonnet-5-5", "fake-model"} <= set(a["models"])
    # latest first: the worker's last command, and the lead's Agent calls further down
    assert (a["recent"][0]["agent"], a["recent"][0]["tool"], a["recent"][0]["target"]) == ("executant", "Bash", "gradlew test")
    assert [r["tool"] for r in a["recent"] if r["agent"] == "chef"] == ["Agent", "Agent"]


def test_context_gauge_is_filled_for_sessions_idle_since_the_console_started(engine):
    tid = engine.create_task("CTX 90000\nbonjour", profile="work", preset="lecture")["id"]
    t = finish(engine, tid)
    for k in ("context_tokens", "context_at", "context_limit"):
        t.pop(k, None)  # as a task of an earlier version, or one that has not answered since
    a = engine.activity(tid)
    assert a["context"]["tokens"] == 91510
    assert t["context_tokens"] == 91510 and t["context_at"] and t["context_limit"] == 200_000


def test_nothing_before_the_session_exists(engine):
    tid = engine.create_task("bonjour", profile="work", preset="lecture", not_before=10**10)["id"]
    assert engine.activity(tid) == {"available": False}


def line(**entry):
    return json.dumps({"timestamp": "2026-09-30T10:00:00.000Z", **entry})


def test_transcripts_are_read_incrementally(tmp_path):
    prof = Profile(id="p", name="P", color="#ffffff", config_dir=str(tmp_path), workdir="~")
    path = activity.transcript(prof, r"C:\projet", "s1")
    path.parent.mkdir(parents=True)
    call = lambda i, name: line(type="assistant", message={"id": f"m{i}", "model": "claude-opus-5-5",  # noqa: E731
                                                            "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}],
                                                            "usage": {"input_tokens": 1, "cache_read_input_tokens": 1000 * i}})
    path.write_text(call(1, "Read") + "\n" + call(2, "Bash") + "\n", encoding="utf-8")
    a = activity.session_activity(prof, r"C:\projet", "s1")
    assert a["agents"][0]["tools"] == {"Read": 1, "Bash": 1} and a["context"]["tokens"] == 2001
    with open(path, "a", encoding="utf-8") as f:
        f.write(call(2, "Bash") + "\n" + call(3, "Grep")[:30])  # the same block again, and a line still being written
    a = activity.session_activity(prof, r"C:\projet", "s1")
    assert a["agents"][0]["tools"] == {"Read": 1, "Bash": 1} and a["agents"][0]["calls"] == 2
    with open(path, "a", encoding="utf-8") as f:
        f.write(call(3, "Grep")[30:] + "\n")
    a = activity.session_activity(prof, r"C:\projet", "s1")
    assert a["agents"][0]["tools"] == {"Read": 1, "Bash": 1, "Grep": 1} and a["context"]["tokens"] == 3001
    path.write_text(call(9, "Write") + "\n", encoding="utf-8")  # rewritten shorter: read again from the start
    assert activity.session_activity(prof, r"C:\projet", "s1")["agents"][0]["tools"] == {"Write": 1}


def test_mcp_server_names_are_merged():
    assert activity.server_key("claude.ai Claude Docs") == activity.server_key("claude_ai_Claude_Docs")
