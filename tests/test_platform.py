"""macOS-specific pieces that can be checked from any platform."""
import shlex
import sys

from console import claude_cli
from console.config import Config, default_config, dump


def test_terminal_line_is_quoted_and_carries_no_api_key():
    env = {"CLAUDE_CONFIG_DIR": "/Users/chef/.claude perso", "ANTHROPIC_API_KEY": "sk-secret", "PATH": "/usr/bin:/bin",
           "HOME": "/Users/chef"}
    line = claude_cli.posix_command_line("/opt/homebrew/bin/claude", ["--resume", "1234-abcd"], env,
                                         "/Users/chef/Mes projets/Devis \"urgent\"")
    assert "sk-secret" not in line and "env -u ANTHROPIC_API_KEY" in line
    parts = shlex.split(line)
    assert parts[1] == "/Users/chef/Mes projets/Devis \"urgent\""
    assert "CLAUDE_CONFIG_DIR=/Users/chef/.claude perso" in parts
    assert parts[-3:] == ["/opt/homebrew/bin/claude", "--resume", "1234-abcd"]
    script = claude_cli.applescript_do(line)
    assert script.startswith('tell application "Terminal" to do script "') and '\\"' in script


def test_macos_defaults(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    cfg = default_config()
    work, perso = cfg.profile("work"), cfg.profile("personal")
    assert work.config_dir == "" and "Library/Application Support/Claude" in work.mcp.desktop_config
    assert perso.config_dir == "~/.claude-personal" and not perso.mcp.import_desktop
    Config.model_validate(dump(cfg))


def test_spawn_options_match_the_platform():
    kw = claude_cli.spawn_kwargs()
    assert ("creationflags" in kw) if sys.platform == "win32" else kw == {"start_new_session": True}
