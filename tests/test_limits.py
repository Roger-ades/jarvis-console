"""Plan usage limits of each account, read from the rate-limit events of Claude Code."""
import pytest

from console import claude_cli
from console.engine import Engine

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def test_every_task_updates_the_limits(engine):
    t = engine.create_task("bonjour", profile="work")
    done(engine, t["id"])
    lim = engine.limits["work"]
    assert lim["status"] == "allowed" and lim["type"] == "five_hour"
    assert lim["windows"]["five_hour"]["used"] == pytest.approx(0.25) and lim["windows"]["seven_day"]["used"] == pytest.approx(0.6)
    assert lim["overage"] == {"status": "rejected", "reason": "org_level_disabled", "using": False}
    assert engine.store.kv_get("limits")["work"]["windows"]["seven_day"]["used"] == pytest.approx(0.6)  # kept across restarts
    assert "personal" not in engine.limits  # per account


def test_documented_fields_alone_update_the_current_window(engine):
    t = engine.create_task("LIMIT 0.97 rejected", profile="work")
    done(engine, t["id"])
    lim = engine.limits["work"]
    assert lim["status"] == "rejected" and lim["windows"]["five_hour"]["used"] == pytest.approx(0.97)
    assert lim["windows"]["seven_day"]["used"] == pytest.approx(0.6)  # the other window is kept
    assert lim["overage"]["reason"] == "org_level_disabled"  # a partial event does not erase what was known


@pytest.mark.parametrize("given, fraction", [(0.52, 0.52), (52, 0.52), ("0.05", 0.05), (None, None), ("x", None)])
def test_fraction(given, fraction):
    assert Engine._fraction(given) == (pytest.approx(fraction) if fraction is not None else None)


def test_refresh_asks_claude_with_a_tiny_request(engine):
    assert engine.refresh_limits("personal") == {"started": True}
    wait_for(lambda: "personal" in engine.limits and "personal" not in engine._limits_busy)
    assert engine.limits["personal"]["windows"]["five_hour"]["used"] == pytest.approx(0.25)


def test_refresh_error_is_kept(engine, monkeypatch):
    monkeypatch.setattr(claude_cli, "limits_probe", lambda *a, **k: {"error": "Not logged in"})
    engine.refresh_limits("work")
    wait_for(lambda: engine.limits.get("work", {}).get("error") == "Not logged in")


def test_probe_command_is_minimal(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        from types import SimpleNamespace
        return SimpleNamespace(stdout='{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n', stderr="")
    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    assert claude_cli.limits_probe(["claude"], {}, ".") == {"events": [{"status": "allowed"}]}
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd and "--max-turns" in cmd
    assert seen["env"]["MAX_THINKING_TOKENS"] == "0"  # Haiku has no effort levels: thinking off is the lowest


def test_stale_limits_are_refreshed_at_start(engine):
    engine.limits = {"work": {"updated": 0}}
    started = []
    engine.refresh_limits = lambda pid: started.append(pid)
    import console.engine as mod
    real_sleep = mod.time.sleep
    mod.time.sleep = lambda s: None
    try:
        engine._refresh_stale_limits()
    finally:
        mod.time.sleep = real_sleep
    assert started == ["work", "personal"]


def test_limits_api(client):  # noqa: F811
    assert client.get("/api/limits").status_code == 401
    h = {"X-Console-Token": client.token}
    assert client.get("/api/limits", headers=h).json() == {"limits": {}, "busy": []}
    assert client.post("/api/limits/inconnu/refresh", headers=h).status_code == 404
