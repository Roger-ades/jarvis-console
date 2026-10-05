from pathlib import Path

import pytest

from console.config import default_config
from console.permissions import (Policy, cli_permission_args, is_write, policy_context,
                                 split_commands)

HOME = str(Path.home())


def policy(preset_id, tmp_path, rules=True):
    cfg = default_config()
    pre = cfg.preset(preset_id)
    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    ctx = policy_context(str(wd), [], cfg.security.forbidden_paths, str(tmp_path / "data"), [8788])
    return Policy(pre, cfg.tool_rules if rules else [], cfg.constraints, ctx), wd


def d(pol, tool, inp=None):
    return pol.evaluate(tool, inp or {}).decision


# ------------------------------------------------ Lecture seule (acceptance criterion)

def test_read_only_cannot_write_or_run(tmp_path):
    pol, wd = policy("lecture", tmp_path)
    assert d(pol, "Write", {"file_path": str(wd / "a.txt"), "content": "x"}) == "deny"
    assert d(pol, "Edit", {"file_path": str(wd / "a.txt")}) == "deny"
    assert d(pol, "Bash", {"command": "echo hi"}) == "deny"
    assert d(pol, "PowerShell", {"command": "Get-ChildItem"}) == "deny"
    assert d(pol, "mcp__odoo__create_record", {"model": "sale.order", "values": {}}) == "deny"
    assert d(pol, "Read", {"file_path": str(wd / "a.txt")}) == "allow"
    assert d(pol, "mcp__odoo__search_records", {"model": "res.partner"}) == "allow"
    # Microsoft 365 puts the verb at the end (outlook_email_search), not first.
    assert d(pol, "mcp__claude_ai_Microsoft_365__outlook_email_search", {"query": "projet"}) == "allow"
    assert d(pol, "mcp__claude_ai_Microsoft_365__read_resource", {}) == "allow"
    assert d(pol, "mcp__claude_ai_Microsoft_365__outlook_create_draft", {}) == "deny"
    assert d(pol, "mcp__claude_ai_Microsoft_365__outlook_update_draft", {}) == "deny"
    assert d(pol, "mcp__claude_ai_Microsoft_365__outlook_create_reply_draft", {}) == "deny"
    assert d(pol, "mcp__claude_ai_Microsoft_365__outlook_modify_labels", {}) != "allow"


def test_read_only_flags_restrict_the_cli_itself(tmp_path):
    cfg = default_config()
    args = cli_permission_args(cfg.preset("lecture"), cfg.tool_rules)
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep,TodoWrite"
    deny = args[args.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Write", "Edit", "PowerShell"} <= set(deny)
    # Globs on tool names are enforced by the console hook, never passed to the CLI.
    assert not any("*" in r.split("(")[0] for r in deny)


def test_read_outside_workdir_is_confined(tmp_path):
    pol, _ = policy("lecture", tmp_path)
    assert d(pol, "Read", {"file_path": str(tmp_path / "elsewhere.txt")}) == "deny"


# ------------------------------------------------ Odoo follows the session preset; the MCP user on Odoo decides the models

@pytest.mark.parametrize("preset", ["lecture", "brouillons", "edition"])
def test_delete_record_is_refused_by_a_preset_that_does_not_write(tmp_path, preset):
    pol, _ = policy(preset, tmp_path)
    v = pol.evaluate("mcp__odoo__delete_record", {"model": "sale.order", "record_id": 3})
    assert v.decision == "deny" and not v.locked


def test_delete_record_is_not_a_permanent_refusal(tmp_path):
    pol, _ = policy("complet", tmp_path, rules=False)
    v = pol.evaluate("mcp__odoo__delete_record", {"record_id": 1})
    assert v.decision == "default" and not v.locked


def test_odoo_writes_follow_the_preset(tmp_path):
    pol, _ = policy("brouillons", tmp_path)
    for model in ("sale.order", "res.partner", "project.task", "sale.order.line"):
        assert d(pol, "mcp__odoo__create_record", {"model": model, "values": {"name": "x"}}) == "ask"
    assert d(pol, "mcp__odoo__create_record", {"values": {}}) == "ask"
    assert d(pol, "mcp__odoo__create_record", {"model": "sale.order", "values": {"state": "sale"}}) == "ask"
    assert d(pol, "mcp__odoo__update_record", {"model": "project.task", "record_id": 1, "values": {}}) == "deny"
    assert d(pol, "mcp__odoo__post_message", {"record_id": 1}) == "deny"
    assisted, _ = policy("assiste", tmp_path)
    assert d(assisted, "mcp__odoo__create_record", {"model": "project.task", "values": {"name": "Relance"}}) == "ask"
    assert d(assisted, "mcp__odoo__update_record", {"model": "project.task", "record_id": 1, "values": {}}) == "ask"


def test_gmail_drafts_but_never_send(tmp_path):
    pol, _ = policy("brouillons", tmp_path)
    assert d(pol, "mcp__claude_ai_Gmail__create_draft", {"to": "a@b.c"}) == "allow"
    assert d(pol, "mcp__claude_ai_Gmail__send_draft", {"id": "x"}) == "deny"
    assert d(pol, "mcp__claude_ai_Gmail__search_threads", {"query": "is:unread"}) == "allow"


def test_web_only_has_no_files_nor_mcp(tmp_path):
    pol, wd = policy("web", tmp_path)
    assert d(pol, "WebSearch", {"query": "x"}) == "allow"
    assert d(pol, "Read", {"file_path": str(wd / "a")}) == "deny"
    assert d(pol, "mcp__odoo__search_records", {}) == "deny"


# ------------------------------------------------ hard blocks

@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": HOME + "/.ssh/id_rsa"}),
    ("Read", {"file_path": "C:\\Users\\x\\.ssh\\config"}),
    ("Bash", {"command": "cat ~/.ssh/id_ed25519"}),
    ("PowerShell", {"command": "Get-Content $env:USERPROFILE\\.aws\\credentials"}),
    ("Read", {"file_path": "project/.env"}),
    ("Grep", {"pattern": "KEY", "path": ".env"}),
    ("Bash", {"command": "type server.pem"}),
])
def test_forbidden_paths(tmp_path, tool, inp):
    pol, _ = policy("complet", tmp_path)
    v = pol.evaluate(tool, inp)
    assert v.decision == "deny" and v.locked


def test_forbidden_paths_ignore_file_contents(tmp_path):
    pol, wd = policy("edition", tmp_path)
    inp = {"file_path": str(wd / "README.md"), "content": "Ne commitez jamais ~/.ssh ni .env"}
    assert d(pol, "Write", inp) == "allow"


def test_console_data_dir_is_forbidden(tmp_path):
    pol, _ = policy("complet", tmp_path)
    assert d(pol, "Read", {"file_path": str(tmp_path / "data" / "token")}) == "deny"


@pytest.mark.parametrize("cmd", ["curl http://127.0.0.1:8788/api/state",
                                 "Invoke-WebRequest -Uri http://localhost:8788/"])
def test_agents_cannot_call_the_console(tmp_path, cmd):
    pol, _ = policy("complet", tmp_path)
    assert d(pol, "Bash", {"command": cmd}) == "deny"
    assert d(pol, "WebFetch", {"url": "http://127.0.0.1:8788/"}) == "deny"


# ------------------------------------------------ shell rules

def test_allow_rule_must_cover_every_command(tmp_path):
    pol, _ = policy("edition", tmp_path)
    assert d(pol, "Bash", {"command": "git status"}) == "allow"
    assert d(pol, "Bash", {"command": "git status && git diff"}) == "allow"
    # the whitelist does not vouch for the second command, and this preset denies unlisted ones
    assert d(pol, "Bash", {"command": "git status && npm install"}) == "default"
    # del is Remove-Item: caught by the deny rule whatever the shell
    assert d(pol, "Bash", {"command": "git status && del /q *"}) == "deny"
    assert d(pol, "Bash", {"command": "git log $(whoami)"}) == "default"
    assert d(pol, "Bash", {"command": "git diff > out.txt"}) == "default"


def test_deny_beats_allow(tmp_path):
    pol, _ = policy("edition", tmp_path)
    assert d(pol, "Bash", {"command": "git status; git push origin main"}) == "deny"
    assert d(pol, "Bash", {"command": "rm -rf build"}) == "deny"


def test_assisted_preset_asks_for_writes(tmp_path):
    pol, wd = policy("assiste", tmp_path)
    assert d(pol, "Edit", {"file_path": str(wd / "a.py")}) == "ask"
    assert d(pol, "Bash", {"command": "npm install"}) == "ask"
    assert d(pol, "Read", {"file_path": "C:/Windows/win.ini"}) == "allow"  # reads are not confined
    assert d(pol, "Write", {"file_path": str(tmp_path / "outside.txt")}) == "deny"  # writes are
    assert d(pol, "mcp__notion__list_pages", {}) == "allow"
    assert d(pol, "mcp__notion__archive_page", {}) == "ask"


def test_split_and_classification():
    assert split_commands('echo "a;b" && ls | wc') == ['echo "a;b"', "ls", "wc"]
    assert split_commands("echo $(id)") is None
    assert is_write("mcp__odoo__create_record") and not is_write("mcp__odoo__search_records")
    assert is_write("mcp__x__frobnicate")  # unknown verb: treated as a write
    assert not is_write("Read") and is_write("PowerShell")
