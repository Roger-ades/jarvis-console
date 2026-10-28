"""Sharing a module between two JARVIS through the team's hub (console/addon_share.py, equipe.py, hub/): Partager
in the module's window sends its zip, the colleague's inbox shows it, one click installs it. Claude takes no part."""
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from console import addon_share, addons
from console.addons import SHARE_BY_HUB
from console.hub_client import HubError
from hub.app import create_app as create_hub
from hub.db import HubDB

from .test_addons import module, tool

MANIFEST = {"nom": "Surveillance des remises", "version": "1.1", "permissions": ["claude"]}


def zipped(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return path


# ------------------------------------------------------------ the zip
def test_a_module_goes_and_comes_back_with_its_files(tmp_path):
    src = module(tmp_path / "a", "surveillance-remises", MANIFEST, **{"app.js": "1", "_brut__x.json": "{}"})
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    path = addon_share.export(addons.read(src), tmp_path / "out", "Roger Martin")
    assert path.name == "surveillance-remises-v1.1.jarvis.zip"
    info = addon_share.inspect(path)
    assert (info["id"], info["nom"], info["version"], info["de"], info["fichiers"]) == \
        ("surveillance-remises", "Surveillance des remises", "1.1", "Roger Martin", 4)
    assert "__pycache__/x.pyc" not in " ".join(zipfile.ZipFile(path).namelist())
    # installed elsewhere; installed again: the first one is kept aside, out of the list of modules
    dest = tmp_path / "addons"
    done = addon_share.install(path, dest)
    assert not done["module"]["problemes"] and (dest / "surveillance-remises" / "_brut" / "x.json").is_file()
    assert done["ancien"] == ""
    again = addon_share.install(path, dest)
    assert Path(again["ancien"]).parent == dest / addon_share.OLD and Path(again["ancien"], "app.js").is_file()
    assert [a["id"] for a in addons.scan(dest)] == ["surveillance-remises"]


@pytest.mark.parametrize("entries, why", [
    ({"x/addon.json": "{}", "x/../../evil.txt": "1"}, "nom de fichier refusé"),
    ({"x/addon.json": "{}", "/etc/passwd": "1"}, "nom de fichier refusé"),
    ({"x/addon.json": "{}", "C:/x.txt": "1"}, "nom de fichier refusé"),
    ({"ab/addon.json": "{}", "cd/addon.json": "{}"}, "un seul dossier"),
    ({"addon.json": "{}"}, "un seul dossier"),
    ({"Mauvais_Nom/addon.json": "{}"}, "nom de module invalide"),
    ({"ab/index.html": "x"}, "addon.json manquant"),
    ({"ab/addon.json": "{x"}, "illisible"),
])
def test_a_zip_that_would_write_outside_its_module_is_refused(tmp_path, entries, why):
    path = zipped(tmp_path / "m.jarvis.zip", entries)
    with pytest.raises(addon_share.ShareError, match=why):
        addon_share.inspect(path)
    with pytest.raises(addon_share.ShareError):
        addon_share.install(path, tmp_path / "addons")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "addons").exists()


# ------------------------------------------------------------ Claude is out of it
@pytest.mark.parametrize("action", ["partager", "recus", "importer"])
def test_claude_only_says_where_to_click(engine, action):
    module(engine.addons_dir, "surveillance-remises", MANIFEST)
    assert tool(engine, {"action": action, "id": "surveillance-remises"}) == SHARE_BY_HUB
    assert "partager" not in addons.SPEC["inputSchema"]["properties"]["action"]["enum"]


# ------------------------------------------------------------ console -> hub -> console
class Bridge:
    """The console's HubClient.call, answered by a real hub in memory, as the account `who`."""

    def __init__(self, hub_client, tokens):
        self.hub, self.tokens, self.who = hub_client, tokens, "roger"

    def __call__(self, url, fingerprint, method, path, body=None, content=None, content_type=None, query="", raw=False):
        headers = {"Authorization": f"Bearer {self.tokens[self.who]}"}
        if content_type:
            headers["Content-Type"] = content_type
        params = {k: v[0] for k, v in parse_qs(query).items()}
        r = self.hub.request(method, path, params=params, json=body, content=content, headers=headers)
        if r.status_code >= 400:
            raise HubError(r.json().get("error") or r.text, r.status_code)
        return r.content if raw else r.json()

    def as_(self, who):
        self.who = who
        return lambda method, path: self(None, None, method, path)


@pytest.fixture
def team(data_dir, tmp_path, monkeypatch):
    from console.app import create_app
    from console.config import ConfigStore
    from tests.conftest import FAKE, PORT

    db = HubDB(tmp_path / "hub" / "hub.db")
    tokens = {}
    for u, name in (("roger", "Roger Martin"), ("paul", "Paul Durand"), ("anne", "Anne Petit")):
        tokens[u] = db.add_user(u)
        db.db.execute("UPDATE users SET name=? WHERE id=?", (name, u))
    store = ConfigStore(data_dir)
    cfg = store.config
    cfg.hub.enabled, cfg.hub.url, cfg.hub.fingerprint = True, "https://10.20.10.249:8443", "a" * 64
    store.save(cfg, "tests")
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)
    with TestClient(create_hub(db)) as hub_client, TestClient(app) as console:
        bridge = Bridge(hub_client, tokens)
        monkeypatch.setattr(app.state.hub, "call", bridge)
        monkeypatch.setattr(app.state.hub, "token", lambda: "jeton")
        console.headers["X-Console-Token"] = app.state.auth.token
        yield app, console, bridge, db
    app.state.store.close()
    db.close()


def test_a_module_sent_from_its_window_installs_in_one_click_from_the_inbox(team):
    app, console, bridge, db = team
    engine, feed = app.state.engine, app.state.team_feed
    module(engine.addons_dir, "surveillance-remises", MANIFEST, **{"app.js": "v1.1"})
    r = console.post("/api/hub/modules/send", json={"id": "surveillance-remises", "to": ["paul", "anne"]})
    assert r.status_code == 200 and r.json()["sent"] == 2
    assert console.post("/api/hub/modules/send", json={"id": "absent", "to": ["paul"]}).status_code == 404
    assert console.post("/api/hub/modules/send", json={"id": "surveillance-remises", "to": []}).status_code == 422
    assert console.post("/api/hub/modules/send", json={"id": "surveillance-remises", "to": ["inconnu"]}).status_code == 404

    # Paul's side (the same console here): an older version installed, the module waits in the inbox
    (engine.addons_dir / "surveillance-remises" / "addon.json").write_text(json.dumps({**MANIFEST, "version": "1.0"}), "utf-8")
    (engine.addons_dir / "surveillance-remises" / "app.js").write_text("v1.0", "utf-8")
    feed.poll(bridge.as_("paul"), {"id": "paul", "name": "Paul Durand"}, {})
    entry, = [e for e in engine.inbox()["entries"] if e["kind"] == "team_module"]
    assert entry["section"] == "todo" and entry["title"] == "Surveillance des remises" and entry["permissions"] == ["claude"]
    assert entry["summary"] == "Roger Martin t'envoie le module « Surveillance des remises » v1.1."
    assert entry["state"] == "remplacera le module installé (v1.0)" and entry["actions"] == ["install", "dismiss"]

    r = console.post(f"/api/hub/modules/{entry['module_id']}/install")
    assert r.status_code == 200 and r.json()["id"] == "surveillance-remises" and r.json()["ancien"]
    assert (engine.addons_dir / "surveillance-remises" / "app.js").read_text("utf-8") == "v1.1"
    kept, = (engine.addons_dir / addon_share.OLD).iterdir()
    assert (kept / "app.js").read_text("utf-8") == "v1.0"
    assert not [e for e in engine.inbox()["entries"] if e["kind"] == "team_module"]
    feed.poll(bridge.as_("paul"), {"id": "paul", "name": "Paul Durand"}, {})
    assert feed.modules == []                                  # the hub no longer offers it to Paul

    # Anne ignores hers: the zip leaves the hub once nobody waits for it
    feed.poll(bridge.as_("anne"), {"id": "anne", "name": "Anne Petit"}, {})
    mine, = feed.modules
    assert console.post(f"/api/hub/modules/{mine['id']}/dismiss").status_code == 200
    assert feed.modules == [] and not list((db.files / "modules").glob("*.zip"))
    kinds = [r["kind"] for r in db.db.execute("SELECT kind FROM audit WHERE kind LIKE 'module.%' ORDER BY id")]
    assert kinds == ["module.send", "module.install", "module.ignore"]


def test_a_broken_module_is_not_sent(team):
    app, console, _, _ = team
    module(app.state.engine.addons_dir, "casse", {"nom": "Cassé", "permissions": ["inconnue"]})
    r = console.post("/api/hub/modules/send", json={"id": "casse", "to": ["paul"]})
    assert r.status_code == 422 and "corrige-le" in r.json()["detail"]


# ------------------------------------------------------------ the hub alone
def _zip(aid="surveillance-remises", version="1.1", perms=("claude",)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{aid}/addon.json", json.dumps({"nom": "Remises", "version": version}))
        z.writestr("partage.json", json.dumps({"id": aid, "nom": "Remises", "version": version,
                                               "permissions": list(perms)}))
    return buf.getvalue()


def test_the_hub_keeps_one_copy_per_recipient_and_replaces_a_pending_one(tmp_path):
    db = HubDB(tmp_path / "hub.db")
    roger, paul = db.add_user("roger"), db.add_user("paul")
    auth = lambda t: {"Authorization": f"Bearer {t}", "Content-Type": "application/zip"}  # noqa: E731
    with TestClient(create_hub(db)) as api:
        assert api.post("/api/v1/modules", params={"to": "paul"}, content=b"pas un zip", headers=auth(roger)).status_code == 422
        assert api.post("/api/v1/modules", params={"to": "roger"}, content=_zip(), headers=auth(roger)).status_code == 400
        first = api.post("/api/v1/modules", params={"to": "paul"}, content=_zip("surveillance-remises", "1.0"), headers=auth(roger))
        assert first.status_code == 201
        api.post("/api/v1/modules", params={"to": "paul"}, content=_zip("surveillance-remises", "1.1"), headers=auth(roger))
        waiting = api.get("/api/v1/modules", headers=auth(paul)).json()["modules"]
        assert [(m["addon"], m["version"], m["from"]) for m in waiting] == [("surveillance-remises", "1.1", "roger")]
        assert len(list((db.files / "modules").glob("*.zip"))) == 1   # the 1.0 nobody waits for is gone
        mod = waiting[0]["id"]
        assert api.get(f"/api/v1/modules/{mod}/archive", headers=auth(roger)).status_code == 404  # not his
        got = api.get(f"/api/v1/modules/{mod}/archive", headers=auth(paul))
        assert got.status_code == 200 and got.content == _zip("surveillance-remises", "1.1")
        assert api.delete(f"/api/v1/modules/{mod}", params={"outcome": "installé"}, headers=auth(paul)).status_code == 200
        assert api.get("/api/v1/modules", headers=auth(paul)).json()["modules"] == []
        assert api.delete(f"/api/v1/modules/{mod}", params={"outcome": "installé"}, headers=auth(paul)).status_code == 404
    db.close()
