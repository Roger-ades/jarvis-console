"""The console's own MCP server: Claude opens files in the interface with mcp__jarvis__afficher."""
import os

from console.engine import CONSOLE_MCP, PRESENT_SPEC, RESULT_SPEC, SHOW_SPEC

from .conftest import task_status, wait_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def run(engine, prompt, wd, preset="lecture"):
    t = engine.create_task(prompt, profile="work", preset=preset, workdir=str(wd))
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    return engine.tasks[t["id"]], [e for e in engine.store.events(t["id"])]


def test_claude_shows_files_in_the_interface_even_in_read_only_mode(engine, tmp_path):
    wd = tmp_path / "projet"
    (wd / "images").mkdir(parents=True)
    (wd / "images" / "logo.png").write_bytes(PNG)
    (wd / "devis.pdf").write_bytes(b"%PDF-1.4")
    task, events = run(engine, "SHOW logo.png | devis.pdf | absent.png | https://example.com/page | http://site.test/x", wd)
    shown = [e["data"] for e in events if e["kind"] == "show"]
    assert shown == [{"files": [os.path.realpath(wd / "images" / "logo.png"), os.path.realpath(wd / "devis.pdf")],
                      "urls": [], "ask": ["https://example.com/page"]}]
    assert "Affiché dans la console JARVIS" in task["result"]
    assert "Proposé à l'utilisateur" in task["result"] and "https://example.com/page" in task["result"]
    assert "absent.png : Fichier introuvable" in task["result"] and "seules les adresses https://" in task["result"]
    # no approval asked, no refusal, and the console's server is not listed with the user's MCP servers
    assert not [e for e in events if e["kind"] in ("approval", "policy")]
    assert [s["name"] for s in task["mcp"]] == ["odoo"]


def test_protected_or_outside_files_are_never_shown(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    (wd / ".env").write_text("SECRET=1", encoding="utf-8")
    outside = tmp_path / "ailleurs.png"
    outside.write_bytes(PNG)
    # (a full path named in the conversation counts as cited, as for previews: a relative one never does)
    task, events = run(engine, "SHOW .env | ../ailleurs.png", wd)
    assert not [e for e in events if e["kind"] == "show"]
    assert task["result"].startswith("affichage refusé : Non affiché")
    assert "protégé" in task["result"] and "Aperçu limité" in task["result"]


def test_server_protocol(engine):
    eng = engine
    init = eng._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"]
    assert eng._console_mcp("x", CONSOLE_MCP, {"id": 2, "method": "tools/list"})["result"]["tools"] == [SHOW_SPEC, RESULT_SPEC, PRESENT_SPEC]
    assert eng._console_mcp("x", CONSOLE_MCP, {"method": "notifications/initialized"}) == {"jsonrpc": "2.0", "result": {}}
    assert eng._console_mcp("x", CONSOLE_MCP, {"id": 3, "method": "resources/list"})["error"]["code"] == -32601
    assert "error" in eng._console_mcp("x", "autre", {"id": 4, "method": "tools/list"})
    other = eng._console_mcp("x", CONSOLE_MCP, {"id": 5, "method": "tools/call", "params": {"name": "effacer", "arguments": {}}})
    assert other["result"]["isError"]
