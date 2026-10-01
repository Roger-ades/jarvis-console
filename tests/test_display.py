"""What Claude shows the user: web addresses (approved domains or a click), HTML on the preview origin,
results of tools shown as they are (afficher_resultat)."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from console import content, results
from console.app import create_app
from console.config import Security, trusted_url, web_domain

from .conftest import FAKE, PORT, task_status, wait_for

PREVIEW = f"{content.HOST}:{PORT}"


def run(engine, prompt, wd):
    t = engine.create_task(prompt, profile="work", preset="lecture", workdir=str(wd))
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    return engine.tasks[t["id"]], [e for e in engine.store.events(t["id"])]


def trust(store, *domains):
    cfg = store.config.model_copy(deep=True)
    cfg.security.trusted_domains = list(domains)
    store.save(cfg, "tests")


# -------------------------------------------------------- approved domains
@pytest.mark.parametrize("raw, domain", [
    ("https://Ades.odoo.com/web#id=3", "ades.odoo.com"),
    ("*.sharepoint.com", "sharepoint.com"),
    ("ades.odoo.com:443", "ades.odoo.com"),
    ("https://user:pw@ades.odoo.com/x", "ades.odoo.com"),
    ("https://ades.odoo.com\\@evil.com/", "ades.odoo.com"),  # the browser reads "\\" as "/": the host is ades.odoo.com
    ("localhost", ""),
    ("pas un domaine", ""),
])
def test_web_domain(raw, domain):
    assert web_domain(raw) == domain


def test_trusted_url_covers_subdomains_and_https_only():
    domains = ["odoo.com", "sharepoint.com"]
    assert trusted_url("https://ades.odoo.com/odoo/sales/12", domains)
    assert trusted_url("https://sharepoint.com/", domains)
    assert not trusted_url("http://ades.odoo.com/", domains)
    assert not trusted_url("https://odoo.com.evil.example/", domains)
    assert not trusted_url("https://evilodoo.com/", domains)
    assert not trusted_url("https://evil.example/@ades.odoo.com", domains)
    assert not trusted_url("https://ades.odoo.com@evil.example/", domains)


def test_trusted_domains_are_normalized_in_the_config():
    assert Security(trusted_domains=["https://Ades.odoo.com/web", "ades.odoo.com", " "]).trusted_domains == ["ades.odoo.com"]
    with pytest.raises(ValidationError):
        Security(trusted_domains=["pas un domaine"])


def test_approved_domains_open_others_wait_for_the_user(engine, tmp_path):
    trust(engine.cfg_store, "odoo.com")
    wd = tmp_path / "projet"
    wd.mkdir()
    task, events = run(engine, "SHOW https://ades.odoo.com/odoo/sales/12 | https://site.example/page", wd)
    assert [e["data"] for e in events if e["kind"] == "show"] == [
        {"files": [], "urls": ["https://ades.odoo.com/odoo/sales/12"], "ask": ["https://site.example/page"]}]
    assert "Affiché dans la console JARVIS : https://ades.odoo.com" in task["result"]
    assert "Proposé à l'utilisateur" in task["result"] and "affichage refusé" not in task["result"]


# -------------------------------------------------------- results of tools
MAIL = {"subject": "Devis 2026-118", "bodyPreview": "Bonjour",
        "body": {"contentType": "html", "content": "<p style='color:red'>Bonjour</p><img src='https://t.example/p.gif'>"},
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [{"emailAddress": {"name": "Roger", "address": "roger@example.com"}}, {"address": "b@example.com"}],
        "receivedDateTime": "2026-09-30T08:15:00Z", "webLink": "javascript:alert(1)"}


def test_view_of_a_mail():
    v = results.view([{"type": "text", "text": json.dumps(MAIL)}])
    assert v["kind"] == "mail" and v["title"] == "Devis 2026-118" and "color:red" in v["page"]
    assert v["meta"]["from"] == "Alice <alice@example.com>"
    assert v["meta"]["to"] == "Roger <roger@example.com>, b@example.com"
    assert v["weblink"] == ""  # only https:// links
    assert content.remote_refs(v["page"]) == 1


def test_view_picks_a_mail_of_a_search_and_keeps_text_mails_as_text():
    other = {**MAIL, "subject": "Facture", "body": {"contentType": "text", "content": "<b>pas du HTML</b>"}}
    glued = json.dumps(MAIL) + json.dumps(other)  # the search tool glues its results together
    assert results.view(glued)["title"] == "Devis 2026-118"
    v = results.view(glued, needle="facture")
    assert v["title"] == "Facture" and v["count"] == 2
    assert "&lt;b&gt;pas du HTML&lt;/b&gt;" in v["page"]


def test_view_of_other_results():
    rec = results.view(json.dumps({"contents": [{"text": json.dumps([{"name": "S00118", "amount": 1200}, {"name": "S00119"}])}]}))
    assert rec["kind"] == "json" and "S00118" in rec["text"]
    one = results.view(json.dumps([{"name": "S00118"}, {"name": "S00119"}]), needle="s00119")
    assert one["title"] == "S00119"
    page = results.view(json.dumps({"title": "Rapport", "html": "<div>ok</div>"}))
    assert page["kind"] == "html" and page["title"] == "Rapport"
    assert results.view("simple texte")["kind"] == "text"
    img = results.view([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}])
    assert img["kind"] == "image" and img["image"] == "data:image/png;base64,AAAA"


def test_saved_results_are_read_only_from_the_tool_results_folder(tmp_path):
    cfg = tmp_path / "cfg"
    ok = cfg / "projects" / "p" / "session" / "tool-results" / "mcp-x.txt"
    ok.parent.mkdir(parents=True)
    ok.write_text(json.dumps(MAIL), encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("{}", encoding="utf-8")
    msg = "Error: result exceeds maximum allowed tokens. Output has been saved to {}.\nFormat: Plain text"
    assert results.saved_file(msg.format(ok), cfg) == ok
    assert results.view(msg.format(ok), config_dir=cfg)["kind"] == "mail"
    assert results.saved_file(msg.format(secret), cfg) is None
    assert results.saved_file(msg.format(cfg / "projects" / "p" / "x.txt"), cfg) is None


def test_claude_shows_a_mail_it_read_without_copying_it(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    task, events = run(engine, 'MAIL Devis 2026-118\nRESULT {"outil": "read_resource"}', wd)
    shown = [e["data"] for e in events if e["kind"] == "show"]
    assert len(shown) == 1 and shown[0]["results"][0]["kind"] == "mail"
    assert shown[0]["results"][0]["title"] == "Devis 2026-118"
    assert "Affiché dans la console JARVIS : mail « Devis 2026-118 »" in task["result"]
    assert not [e for e in events if e["kind"] in ("approval", "policy")]
    v = engine.result_view(task["id"], shown[0]["results"][0]["id"])
    assert v["kind"] == "mail" and v["meta"]["from"] == "Alice Martin <alice@example.com>"
    assert v["meta"]["attachments"] == [{"name": "devis.pdf", "size": 48213}]
    assert v["inline_images"] == 1 and v["remote"] == 1 and v["weblink"].startswith("https://outlook.office365.com/")
    assert v["url"].startswith(f"http://{PREVIEW}/v/") and "page" not in v
    e = engine.contents.get(v["url"].rstrip("/").rsplit("/", 1)[1])
    assert not e.remote and b"color:#c00" in e.page and b'<base target="_blank">' in e.page


def test_long_results_saved_to_a_file_and_older_ones_from_the_transcript(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    task, events = run(engine, 'BIGMAIL Rapport annuel\nRESULT {"contient": "rapport annuel"}', wd)
    shown = [e["data"] for e in events if e["kind"] == "show"]
    assert shown and shown[0]["results"][0]["title"] == "Rapport annuel", task["result"]
    cid = shown[0]["results"][0]["id"]
    engine._calls.clear()  # a console restarted since: the session's transcript still has the result
    assert engine.result_view(task["id"], cid)["title"] == "Rapport annuel"


def test_nothing_matches(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    task, events = run(engine, 'RESULT {"outil": "get_record"}', wd)
    assert "Aucun résultat d'outil ne correspond (outil = get_record)" in task["result"]
    assert not [e for e in events if e["kind"] == "show"]


# -------------------------------------------------------- the preview origin
@pytest.fixture
def client(data_dir):
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)
    with TestClient(app) as c:
        c.token = app.state.auth.token
        c.engine = app.state.engine
        yield c
    app.state.store.close()


def test_preview_origin_serves_pages_only(client):
    cid = client.engine.contents.put("<style>p{color:red}</style><p style='margin:0'>Bonjour</p><img src='https://t.example/p.gif'>")
    r = client.get(f"/v/{cid}/", headers={"Host": PREVIEW})
    assert r.status_code == 200 and "Bonjour" in r.text and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "sandbox allow-popups" in csp and "allow-scripts" not in csp and "script-src" not in csp
    assert f"style-src http://{PREVIEW} 'unsafe-inline';" in csp and "https:" not in csp
    assert f"frame-ancestors http://127.0.0.1:{PORT} http://localhost:{PORT} http://testserver" in csp
    assert r.headers["cross-origin-resource-policy"] == "cross-origin" and "x-frame-options" not in r.headers
    # nothing else on that origin: neither the API (even with the token) nor the console page
    assert client.get("/api/state", headers={"Host": PREVIEW, "X-Console-Token": client.token}).status_code == 404
    assert client.get("/", headers={"Host": PREVIEW}).status_code == 404
    # and the console's own origin never serves the pages
    assert client.get(f"/v/{cid}/").status_code == 404
    assert client.get("/v/inconnu/", headers={"Host": PREVIEW}).status_code == 404
    web = client.engine.contents.put("<img src='https://t.example/p.gif'>", remote=True)
    assert "img-src http://" + PREVIEW + " data: https: http:" in client.get(f"/v/{web}/", headers={"Host": PREVIEW}).headers["content-security-policy"]


def test_prepared_page_keeps_a_single_doctype():
    page = content.prepare("<!DOCTYPE html>\n<html><link rel=preconnect href='https://x.example'><p>Bonjour</p></html>")
    assert page.lower().count("<!doctype") == 1 and page.startswith('<!doctype html><meta charset="utf-8"><base target="_blank">')
    assert "preconnect" not in page and "<p>Bonjour</p>" in page
    assert content.prepare("<p>x <!doctype html></p>").endswith("<p>x <!doctype html></p>")


def test_console_page_may_frame_the_preview_origin(client):
    csp = client.get("/").headers["content-security-policy"]
    assert f"frame-src 'self' blob: https: http://{PREVIEW};" in csp and "frame-ancestors 'none'" in csp


def test_html_file_of_a_task_with_its_images(client, tmp_path):
    wd = tmp_path / "site"
    (wd / "img").mkdir(parents=True)
    (wd / "page.html").write_bytes("<meta http-equiv='refresh' content='0;url=https://x.example'>"
                                   "<link rel=preconnect href='https://x.example'><p>Été</p><img src='img/a.png'>".encode("cp1252"))
    (wd / "img" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (wd / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "dehors.png").write_bytes(b"x")
    h = {"X-Console-Token": client.token}
    t = client.post("/api/tasks", json={"prompt": "écho", "workdir": str(wd), "confirmed": True}, headers=h).json()
    f = client.get(f"/api/tasks/{t['id']}/frame", params={"path": "page.html"}, headers=h).json()
    assert f["url"].startswith(f"http://{PREVIEW}/v/") and f["remote"] == 0
    base = f["url"].split(PREVIEW, 1)[1]
    page = client.get(base, headers={"Host": PREVIEW})
    assert "Été" in page.text and "refresh" not in page.text and "preconnect" not in page.text
    assert client.get(base + "img/a.png", headers={"Host": PREVIEW}).content.startswith(b"\x89PNG")
    assert client.get(base + ".env", headers={"Host": PREVIEW}).status_code == 404
    assert client.get(base + "../dehors.png", headers={"Host": PREVIEW}).status_code == 404
    assert client.get(base + "img/%2e%2e/%2e%2e/dehors.png", headers={"Host": PREVIEW}).status_code == 404
    other = client.get(f"/api/tasks/{t['id']}/frame", params={"path": "img/a.png"}, headers=h)
    assert other.status_code == 400


def test_trust_a_domain_from_the_console(client):
    h = {"X-Console-Token": client.token}
    r = client.post("/api/security/trust", json={"domain": "https://Ades.odoo.com/odoo/sales"}, headers=h)
    assert r.status_code == 200 and r.json()["domain"] == "ades.odoo.com"
    assert r.json()["config"]["security"]["trusted_domains"] == ["ades.odoo.com"]
    assert client.post("/api/security/trust", json={"domain": "x"}, headers=h).status_code == 422
    assert client.post("/api/security/trust", json={"domain": "a.com"}).status_code == 401
