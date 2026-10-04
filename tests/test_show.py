"""The console's own MCP server: Claude opens files in the interface with mcp__jarvis__afficher."""
import os

from console.engine import CONSOLE_MCP, PRESENT_SPEC, PROPOSE_SPEC, RESULT_SPEC, SHOW_SPEC

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
    tools = eng._console_mcp("x", CONSOLE_MCP, {"id": 2, "method": "tools/list"})["result"]["tools"]
    assert tools == [SHOW_SPEC, RESULT_SPEC, PRESENT_SPEC, PROPOSE_SPEC]
    # always in Claude's prompt: a tool deferred behind tool search shows only its name, its parameters get guessed
    assert all(t["_meta"] == {"anthropic/alwaysLoad": True} for t in tools)
    assert eng._console_mcp("x", CONSOLE_MCP, {"method": "notifications/initialized"}) == {"jsonrpc": "2.0", "result": {}}
    assert eng._console_mcp("x", CONSOLE_MCP, {"id": 3, "method": "resources/list"})["error"]["code"] == -32601
    assert "error" in eng._console_mcp("x", "autre", {"id": 4, "method": "tools/list"})
    other = eng._console_mcp("x", CONSOLE_MCP, {"id": 5, "method": "tools/call", "params": {"name": "effacer", "arguments": {}}})
    assert other["result"]["isError"]
    # wrong parameters (seen: « content »): the answer says which ones to pass
    wrong = eng._console_mcp("x", CONSOLE_MCP, {"id": 6, "method": "tools/call",
                                               "params": {"name": "afficher", "arguments": {"content": "# Devis"}}})
    assert wrong["result"]["isError"] and "passe « fichiers »" in wrong["result"]["content"][0]["text"]


def test_the_pages_of_the_mcp_servers_applications_open_without_asking(engine, tmp_path):
    cfg = engine.cfg.model_copy(deep=True)
    cfg.profile("work").mcp.extra_servers = {
        "odoo": {"command": "uvx", "args": ["mcp-server-odoo"], "env": {"ODOO_URL": "https://erp.example.com", "ODOO_API_KEY": "k"}}}
    engine.cfg_store.save(cfg, "tests")
    wd = tmp_path / "projet"
    wd.mkdir()
    task, events = run(engine, "SHOW https://erp.example.com/odoo/sale.order/42 | https://autre.example.net/x", wd)
    shown = [e["data"] for e in events if e["kind"] == "show"]
    assert shown == [{"files": [], "urls": ["https://erp.example.com/odoo/sale.order/42"], "ask": ["https://autre.example.net/x"]}]
    assert "Affiché dans la console JARVIS : https://erp.example.com/odoo/sale.order/42" in task["result"]


def test_a_file_opens_at_the_passage_or_the_page_claude_points_at(engine, tmp_path):
    import time
    (tmp_path / "contrat.pdf").write_bytes(b"%PDF-1.4")
    tid = engine.create_task("x", profile="work", preset="lecture", workdir=str(tmp_path), not_before=time.time() + 3600)["id"]

    def call(args):
        out = engine._console_mcp(tid, CONSOLE_MCP, {"id": 1, "method": "tools/call", "params": {"name": "afficher", "arguments": args}})
        return out["result"]["content"][0]["text"]

    text = call({"fichiers": ["contrat.pdf"], "passage": "  Clause de\n résiliation ", "page": 3})
    shown = [e["data"] for e in engine.store.events(tid) if e["kind"] == "show"]
    assert shown[-1]["focus"] == {"text": "Clause de résiliation", "page": 3}
    assert "(page 3, passage surligné s'il est trouvé)" in text
    call({"fichiers": ["contrat.pdf"], "page": "deux"})
    assert "focus" not in [e["data"] for e in engine.store.events(tid) if e["kind"] == "show"][-1]


def test_the_console_writes_the_address_of_an_odoo_record(engine):
    """The model guessed /odoo/sale/1538 (an action that does not exist) for /odoo/sales/1538: it now names the
    record, and the console writes its address."""
    import time
    cfg = engine.cfg.model_copy(deep=True)
    cfg.profile("work").mcp.extra_servers = {
        "odoo": {"command": "uvx", "args": ["mcp-server-odoo"], "env": {"ODOO_URL": "https://erp.example.com/"}}}
    engine.cfg_store.save(cfg, "tests")
    def later():  # (a task keeps the servers it was created with)
        return engine.create_task("x", profile="work", not_before=time.time() + 3600)["id"]

    tid = later()

    def call(args):
        out = engine._console_mcp(tid, CONSOLE_MCP, {"id": 1, "method": "tools/call", "params": {"name": "afficher", "arguments": args}})
        return out["result"]["content"][0]["text"], out["result"]["isError"]

    text, failed = call({"enregistrements": [{"modele": "sale.order", "id": 1538}, {"modele": "res.partner", "id": "7"},
                                             {"modele": "website", "id": 2}, {"modele": "sale/../x", "id": 3},
                                             {"modele": "account.move", "id": "abc"}]})
    urls = ["https://erp.example.com/odoo/sales/1538", "https://erp.example.com/odoo/res.partner/7",
            "https://erp.example.com/odoo/m-website/2"]
    assert not failed and [e["data"] for e in engine.store.events(tid) if e["kind"] == "show"] == [
        {"files": [], "urls": urls, "ask": []}]
    assert "Affiché dans la console JARVIS : " + ", ".join(urls) in text
    assert "sale/../x 3 : passe « modele »" in text and "account.move abc : passe « modele »" in text
    # several Odoo: the server is named, else the call says which ones there are
    cfg.profile("work").mcp.extra_servers["test"] = {"command": "odoo-mcp", "env": {"ODOO_URL": "https://test.example.com"}}
    engine.cfg_store.save(cfg, "tests")
    tid = later()
    text, failed = call({"enregistrements": [{"modele": "sale.order", "id": 5}]})
    assert failed and "précise « application » : odoo, test" in text
    text, failed = call({"enregistrements": [{"modele": "sale.order", "id": 5, "application": "test"}]})
    assert not failed and "https://test.example.com/odoo/sales/5" in text
    # no Odoo address known: Claude is told to pass the page's address
    cfg.profile("work").mcp.extra_servers = {}
    engine.cfg_store.save(cfg, "tests")
    tid = later()
    text, failed = call({"enregistrements": [{"modele": "sale.order", "id": 5}]})
    assert failed and "adresse d'Odoo inconnue" in text


def test_the_records_parameter_is_described_to_claude():
    props = SHOW_SPEC["inputSchema"]["properties"]
    assert "required" not in SHOW_SPEC["inputSchema"] and set(props) == {"fichiers", "enregistrements", "passage", "page"}
    assert props["enregistrements"]["items"]["required"] == ["modele", "id"]
