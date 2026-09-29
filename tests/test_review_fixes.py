"""Regressions for the findings of the adversarial security review."""
import ctypes
import json
import os

import pytest

from console.config import Preset, default_config
from console.engine import Approval
from console.permissions import Policy, is_write, policy_context

from .conftest import task_status, wait_for


def policy(preset_id, tmp_path, data_dir=None):
    cfg = default_config()
    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    data = data_dir or (tmp_path / "data")
    ctx = policy_context(str(wd), [], cfg.security.forbidden_paths, str(data), [8788])
    return Policy(cfg.preset(preset_id), cfg.tool_rules, cfg.constraints, ctx)


def d(pol, tool, inp):
    return pol.evaluate(tool, inp).decision


# 1 (high) — 8.3 short names must not dodge the forbidden-path check
@pytest.mark.skipif(os.name != "nt", reason="8.3 short names are a Windows feature")
def test_short_names_cannot_reach_the_data_dir(tmp_path):
    data = tmp_path / "Donnees de la console tres longues"
    data.mkdir()
    (data / "token").write_text("secret", encoding="utf-8")
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(data / "token"), buf, 1024)
    short = buf.value
    if not short or "~" not in short:
        pytest.skip("8.3 names are disabled on this volume")
    pol = policy("complet", tmp_path, data_dir=data)
    assert d(pol, "Read", {"file_path": short}) == "deny"
    assert d(pol, "Bash", {"command": f"type {short}"}) == "deny"


def test_case_does_not_matter_for_forbidden_paths(tmp_path):
    """macOS (like Windows) ignores case: DATA/Token is the same file as data/token."""
    data = tmp_path / "data"
    pol = policy("complet", tmp_path, data_dir=data)
    upper = str(data).upper() + "/TOKEN"
    assert d(pol, "Read", {"file_path": upper}) == "deny"
    assert d(pol, "Bash", {"command": f"cat {upper}"}) == "deny"
    assert d(pol, "Read", {"file_path": str(tmp_path / "wd" / "ODOO_CONFIG.JSON")}) != "deny"  # not forbidden by default
    pol2 = Policy(default_config().preset("complet").model_copy(update={"deny": ["Read(**/odoo_config.json)"]}), [], [],
                  policy_context(str(tmp_path / "wd"), [], [], str(data), []))
    assert d(pol2, "Read", {"file_path": str(tmp_path / "wd" / "ODOO_CONFIG.JSON")}) == "deny"


# 2 (high) — MCP arguments under any key name are checked for forbidden paths
@pytest.mark.parametrize("inp", [{"document": "~/.ssh/id_rsa"}, {"notebook": ".env"},
                                 {"attachments": [{"ref": "C:/Users/x/.aws/credentials"}]}])
def test_mcp_paths_under_any_key(tmp_path, inp):
    for preset in ("lecture", "assiste", "complet"):
        assert d(policy(preset, tmp_path), "mcp__docs__read_thing", inp) == "deny", preset


def test_mcp_free_text_without_paths_is_fine(tmp_path):
    pol = policy("brouillons", tmp_path)
    body = {"to": "client@example.com", "body": "Bonjour, voici le devis de 3 200 €. Cordialement."}
    assert d(pol, "mcp__claude_ai_Gmail__create_draft", body) == "allow"


# 3 (medium) — a read-looking verb must not vouch for a destructive tool
@pytest.mark.parametrize("tool", ["mcp__gmail__list_and_delete_messages", "mcp__crm__search_and_purge",
                                  "mcp__x__get_and_remove", "mcp__claude_ai_Gmail__gmail_send_draft"])
def test_destructive_verbs_anywhere(tmp_path, tool):
    assert is_write(tool)
    for preset in ("lecture", "brouillons", "edition", "assiste"):
        assert d(policy(preset, tmp_path), tool, {}) != "allow", preset


def test_reads_stay_allowed(tmp_path):
    pol = policy("lecture", tmp_path)
    for tool in ("mcp__odoo__search_records", "mcp__notion__get_settings", "mcp__gmail__list_drafts",
                 "mcp__drive__get_address_book"):
        assert d(pol, tool, {}) == "allow", tool


# 4 (medium) — shell deny rules survive aliases, whitespace and wrappers
@pytest.mark.parametrize("tool,cmd", [
    ("PowerShell", "del x"), ("PowerShell", "ri x -Recurse"), ("PowerShell", "rm x"),
    ("PowerShell", "iwr https://evil.example"), ("PowerShell", "curl https://evil.example"),
    ("Bash", "rm  -rf build"), ("Bash", "rm\t-rf build"), ("Bash", 'cmd /c "rm -rf build"'),
    ("Bash", "powershell -NoProfile -Command Remove-Item x"), ("Bash", "git  push origin main"),
])
def test_shell_deny_rules_are_robust(tmp_path, tool, cmd):
    assert d(policy("edition", tmp_path), tool, {"command": cmd}) == "deny"


def test_wrappers_and_encoded_commands_are_never_vouched(tmp_path):
    pol = policy("edition", tmp_path)
    assert d(pol, "Bash", {"command": "git  status"}) == "allow"
    assert d(pol, "Bash", {"command": "bash -c 'git status'"}) != "allow"
    assert d(pol, "PowerShell", {"command": "powershell -enc ZQBjAGgAbwAgAGgAaQA="}) != "allow"


# 6 (medium) — no quote-line creation (it would modify an existing order)
def test_quote_lines_cannot_be_created_on_their_own(tmp_path):
    pol = policy("brouillons", tmp_path)
    assert d(pol, "mcp__odoo__create_record", {"model": "sale.order.line", "values": {"order_id": 12}}) == "deny"
    assert d(pol, "mcp__odoo__create_record", {"model": "sale.order", "values": {"partner_id": 3}}) == "ask"


# 8 (low) — a decision is taken once, whoever comes first
def test_approval_decision_is_claimed_once():
    a = Approval(id="a", request_id="r", kind="hook", tool="Bash", input={}, reason="")
    assert a.claim("deny", "délai dépassé", "console")
    assert not a.claim("allow", "", "utilisateur")
    assert a.decision == "deny" and a.by == "console"


# 9 (low) — loopback spellings
@pytest.mark.parametrize("cmd", [
    "curl http://127.1:8788/api/state", "curl http://[::ffff:127.0.0.1]:8788/", "curl http://2130706433:8788/",
    "curl http://0x7f000001:8788/", "curl http://localhost.:8788/", "nc localhost 8788",
    "python -c \"import http.client as h; h.HTTPConnection('127.0.0.1', 8788)\"",
])
def test_loopback_spellings(tmp_path, cmd):
    assert d(policy("complet", tmp_path), "Bash", {"command": cmd}) == "deny"


def test_other_hosts_on_the_same_port_are_not_self_access(tmp_path):
    assert d(policy("complet", tmp_path), "Bash", {"command": "curl http://example.com:8788/"}) != "deny"


# 5 and 7 — through the engine and the (fake) CLI
def test_unlisted_deny_is_enforced_by_the_hook(engine):
    engine.cfg.presets.append(Preset(id="strict", name="Strict", mode="bypassPermissions",
                                     allow=["Read"], unlisted="deny", confine="none"))
    t = engine.create_task('TOOL Bash {"command": "echo hi"}', profile="work", preset="strict")
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    assert "Bash:refus" in engine.tasks[t["id"]]["result"]


def test_policy_crash_still_answers_the_cli(engine, monkeypatch):
    real = Policy.evaluate

    def boom(self, tool, inp):
        if tool == "Boom":
            raise RuntimeError("bug de politique")
        return real(self, tool, inp)

    monkeypatch.setattr(Policy, "evaluate", boom)
    t = engine.create_task("TOOL Boom {}", profile="work")
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs, timeout=10)
    assert "Boom:refus" in engine.tasks[t["id"]]["result"]
    assert json.dumps(engine.store.events(t["id"])).count("bug de politique") >= 1
