"""Le jeton d'équipe ne passe ni dans la configuration, ni dans un mauvais dossier."""
import json

from console.config import default_config, dump
from console.hub_client import HubClient, HubError, normalize_fingerprint


def test_the_token_stays_out_of_the_config(tmp_path):
    cfg = default_config()
    cfg.hub.url = "https://10.20.10.249:8443"
    cfg.hub.fingerprint = "ab" * 32
    text = json.dumps(dump(cfg))
    HubClient(tmp_path).set_token("SECRET-TOKEN-VALUE-123456")
    assert "SECRET-TOKEN-VALUE-123456" not in text
    assert "hub-token" not in text
    assert HubClient(tmp_path).token() == "SECRET-TOKEN-VALUE-123456"
    assert normalize_fingerprint("AB:CD") == "abcd"


def test_the_poste_device_id_uses_the_syncthing_2_subcommand(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from console import syncbox

    exe = syncbox.binary_of(tmp_path)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    calls = []

    def answer(cmd, **kwargs):
        calls.append(cmd)
        return device + "\n"

    monkeypatch.setattr(syncbox.subprocess, "check_output", answer)
    assert syncbox.device_id(tmp_path) == device
    assert calls[0][1:] == ["--home", str(syncbox.home_of(tmp_path)), "device-id"]

    def refuse(cmd, **kwargs):
        raise subprocess.CalledProcessError(80, cmd)

    monkeypatch.setattr(syncbox.subprocess, "check_output", refuse)
    with pytest.raises(ValueError):
        syncbox.device_id(tmp_path)


SERVER = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
POSTE = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"


class FakeHub:
    """Le hub vu par la console : projets, inscriptions Syncthing et membres."""

    def __init__(self):
        self.projects = {}
        self.synced = []
        self.left = []
        self.messages = {}

    def __call__(self, url, fingerprint, method, path, body=None, **kwargs):
        if path == "/api/v1/me":
            return {"user": {"id": "roger", "name": "Roger", "admin": True}}
        if path == "/api/v1/users":
            return {"users": [{"id": "paul", "name": "Paul"}, {"id": "roger", "name": "Roger"}]}
        if path.endswith("/members/me") and method == "DELETE":
            team = path.split("/")[-3]
            if team not in self.projects:
                raise HubError("projet introuvable", 404)
            self.left.append(team)
            del self.projects[team]
            return {"ok": True}
        if path.endswith("/messages"):
            return {"messages": self.messages.get(path.split("/")[-2], [])}
        if path.endswith("/conversations"):
            return {"conversations": []}
        if path == "/api/v1/projects" and method == "GET":
            return {"projects": list(self.projects.values())}
        if path == "/api/v1/projects" and method == "POST":
            self.projects[body["id"]] = {**body, "color": body.get("color", "#40dcff"), "role": "administrateur"}
            return {"project": self.projects[body["id"]]}
        if path == "/api/v1/syncthing":
            return {"device_id": SERVER}
        if path.endswith("/sync"):
            self.synced.append((path, body["device_id"]))
            return {"ok": True}
        raise AssertionError(path)


def _team_client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from console import equipe
    from console.app import create_app
    from console.config import ConfigStore
    from tests.conftest import FAKE, PORT

    store = ConfigStore(data_dir)
    cfg = store.config
    cfg.hub.enabled, cfg.hub.url, cfg.hub.fingerprint = True, "https://10.20.10.249:8443", "a" * 64
    store.save(cfg, "tests")
    shared = []
    monkeypatch.setattr(equipe.syncbox, "start", lambda d: None)
    monkeypatch.setattr(equipe.syncbox, "device_id", lambda d: POSTE)
    monkeypatch.setattr(equipe.syncbox, "share", lambda *a, **k: shared.append((a, k)))
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)
    hub = FakeHub()
    monkeypatch.setattr(app.state.hub, "call", hub)
    monkeypatch.setattr(app.state.hub, "token", lambda: "jeton")
    return app, TestClient(app), hub, shared


def test_joining_a_team_project_creates_the_jarvis_project_and_links_syncthing(data_dir, tmp_path, monkeypatch):
    app, client, hub, shared = _team_client(data_dir, monkeypatch)
    hub.projects["offre"] = {"id": "offre", "name": "Offres", "color": "#123456", "syncthing_folder": "offre"}
    folder = tmp_path / "Offres"
    folder.mkdir()
    h = {"X-Console-Token": app.state.auth.token}
    with client:
        r = client.post("/api/hub/join", json={"team_id": "offre", "folder": str(folder)}, headers=h)
        assert r.status_code == 200 and r.json()["syncthing"]["ok"] is True
        proj = next(p for p in app.state.engine.cfg.projects if p.team_id == "offre")
        assert proj.name == "Offres" and proj.color == "#123456"
        assert (folder / ".jarvis-projet").read_text(encoding="utf-8").strip() == "offre"
        assert hub.synced == [("/api/v1/projects/offre/sync", POSTE)]
        (args, kwargs), = shared
        assert args[2:] == ("offre", SERVER) and kwargs == {"label": "Offres", "host": "10.20.10.249"}
        elsewhere = tmp_path / "Ailleurs"
        elsewhere.mkdir()
        twice = client.post("/api/hub/join", json={"team_id": "offre", "folder": str(elsewhere)}, headers=h)
        assert twice.status_code == 409
        missing = client.post("/api/hub/join", json={"team_id": "inconnu", "folder": str(elsewhere)}, headers=h)
        assert missing.status_code == 404
    app.state.store.close()


def test_an_admin_turns_a_jarvis_project_into_a_team_project(data_dir, tmp_path, monkeypatch):
    app, client, hub, shared = _team_client(data_dir, monkeypatch)
    folder = tmp_path / "Études"
    folder.mkdir()
    h = {"X-Console-Token": app.state.auth.token}
    with client:
        app.state.engine.save_project({"folder": str(folder), "name": "Études SSI", "color": "#abcdef"})
        r = client.post("/api/hub/create", json={"folder": str(folder)}, headers=h)
        assert r.status_code == 200, r.text
        assert hub.projects["etudes-ssi"]["syncthing_folder"] == "etudes-ssi"
        assert hub.projects["etudes-ssi"]["color"] == "#abcdef"
        proj = next(p for p in app.state.engine.cfg.projects if p.name == "Études SSI")
        assert proj.team_id == "etudes-ssi"
        again = client.post("/api/hub/create", json={"folder": str(folder)}, headers=h)
        assert again.status_code == 409
    app.state.store.close()


def test_the_poste_shares_a_folder_once_and_refuses_a_second_path(tmp_path, monkeypatch):
    import pytest

    from console import syncbox

    devices, folders = {}, {}

    def rest(data_dir, method, path, body=None):
        kind, key = path.split("/")[-2:]
        store = devices if kind == "devices" else folders
        if method == "GET":
            return store.get(key)
        if method == "PUT":
            store[key] = body
        else:
            store[key].update(body)
        return None

    monkeypatch.setattr(syncbox, "start", lambda d: None)
    monkeypatch.setattr(syncbox, "_rest", rest)
    syncbox.share(tmp_path, str(tmp_path / "Offres"), "offre", SERVER, label="Offres", host="10.20.10.249")
    syncbox.share(tmp_path, str(tmp_path / "Offres"), "offre", SERVER, label="Offres", host="10.20.10.249")
    assert devices[SERVER]["introducer"] is True
    assert devices[SERVER]["addresses"] == ["tcp://10.20.10.249:22000", "dynamic"]
    assert folders["offre"]["devices"] == [{"deviceID": SERVER}]
    with pytest.raises(ValueError, match="autre dossier"):
        syncbox.share(tmp_path, str(tmp_path / "Autre"), "offre", SERVER)


def test_a_team_id_is_a_plain_slug():
    from console.equipe import slug

    assert slug("Études SSI — Lot 2") == "etudes-ssi-lot-2"
    assert len(slug("x" * 90)) == 40


def test_a_team_project_is_shared_with_the_chosen_colleagues_only(data_dir, tmp_path, monkeypatch):
    app, client, hub, shared = _team_client(data_dir, monkeypatch)
    folder = tmp_path / "Études"
    folder.mkdir()
    h = {"X-Console-Token": app.state.auth.token}
    with client:
        app.state.engine.save_project({"folder": str(folder), "name": "Études SSI"})
        r = client.post("/api/hub/create", headers=h, json={"folder": str(folder), "name": "Études", "members": [
            {"id": "paul", "role": "administrateur"}, {"id": "zoe", "role": "chef"}, "max", {"id": ""}]})
        assert r.status_code == 200, r.text
        assert hub.projects["etudes"]["members"] == [
            {"id": "paul", "role": "administrateur"}, {"id": "zoe", "role": "membre"}, {"id": "max", "role": "membre"}]
    app.state.store.close()


def test_leaving_detaches_the_folder_and_keeps_the_files(data_dir, tmp_path, monkeypatch):
    from console import equipe

    app, client, hub, shared = _team_client(data_dir, monkeypatch)
    unshared = []
    monkeypatch.setattr(equipe.syncbox, "unshare", lambda d, f: unshared.append(f) or 1)
    hub.projects["offre"] = {"id": "offre", "name": "Offres", "color": "#123456", "syncthing_folder": "offre"}
    folder = tmp_path / "Offres"
    folder.mkdir()
    (folder / "devis.txt").write_text("à garder", encoding="utf-8")
    h = {"X-Console-Token": app.state.auth.token}
    with client:
        client.post("/api/hub/join", json={"team_id": "offre", "folder": str(folder)}, headers=h)
        r = client.post("/api/hub/leave", json={"folder": str(folder)}, headers=h)
        assert r.status_code == 200, r.text
        assert hub.left == ["offre"] and unshared == [str(folder)]
        assert not (folder / ".jarvis-projet").exists() and (folder / "devis.txt").is_file()
        proj = next(p for p in app.state.engine.cfg.projects if p.name == "Offres")
        assert proj.team_id == ""
        none = client.post("/api/hub/leave", json={"folder": str(folder)}, headers=h)
        assert none.status_code == 409
    app.state.store.close()


def test_a_removed_member_detaches_from_the_inbox(data_dir, tmp_path, monkeypatch):
    from console import equipe

    app, client, hub, shared = _team_client(data_dir, monkeypatch)
    monkeypatch.setattr(equipe.syncbox, "unshare", lambda d, f: 1)
    hub.projects["offre"] = {"id": "offre", "name": "Offres", "color": "#123456", "syncthing_folder": "offre"}
    folder = tmp_path / "Offres"
    folder.mkdir()
    h = {"X-Console-Token": app.state.auth.token}
    feed = app.state.team_feed
    call = lambda method, path: hub("", "", method, path)  # noqa: E731
    linked = lambda: {p.team_id: {"folder": p.folder, "name": p.name, "color": p.color}  # noqa: E731
                      for p in app.state.engine.cfg.projects if p.team_id}
    me = {"id": "roger", "name": "Roger"}
    with client:
        client.post("/api/hub/join", json={"team_id": "offre", "folder": str(folder)}, headers=h)
        feed.poll(call, me, linked())
        hub.messages["offre"] = [{"id": 1, "author": "paul", "text": "@roger regarde", "ts": 9e9}]
        feed.poll(call, me, linked())
        inbox = client.get("/api/inbox", headers=h).json()
        mention = next(e for e in inbox["entries"] if e["kind"] == "team_mention")
        assert mention["folder"] == str(folder)
        assert client.post("/api/hub/seen", json={"project": "offre", "msg": 9e9}, headers=h).status_code == 200
        assert not any(e["kind"].startswith("team_") for e in client.get("/api/inbox", headers=h).json()["entries"])
        del hub.projects["offre"]
        feed.poll(call, me, linked())
        gone = next(e for e in client.get("/api/inbox", headers=h).json()["entries"] if e["kind"] == "team_removed")
        r = client.post("/api/hub/unlink", json={"folder": gone["folder"]}, headers=h)
        assert r.status_code == 200, r.text
        feed.poll(call, me, linked())
        assert not any(e["kind"].startswith("team_") for e in client.get("/api/inbox", headers=h).json()["entries"])
        hub.projects["autre"] = {"id": "autre", "name": "Autre", "added_by": "paul"}
        feed.poll(call, me, linked())
        invite = next(e for e in client.get("/api/inbox", headers=h).json()["entries"] if e["kind"] == "team_invite")
        assert client.post("/api/inbox/dismiss", json={"ids": [invite["id"]]}, headers=h).json()["marked"] == 1
        assert not any(e["kind"] == "team_invite" for e in client.get("/api/inbox", headers=h).json()["entries"])
    app.state.store.close()


def test_a_new_poste_registers_on_the_built_in_hub(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from console import equipe
    from console.app import create_app
    from console.config import ConfigStore
    from console.hub_client import HUB_FINGERPRINT, HUB_URL, REGISTER_KEY
    from tests.conftest import FAKE, PORT

    monkeypatch.setattr(equipe.getpass, "getuser", lambda: "paul.martin")
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)
    hub = app.state.hub
    probed, sent = [], []
    monkeypatch.setattr(hub, "probe", lambda url, timeout=30: probed.append((url, timeout)) or HUB_FINGERPRINT)

    def exchange(url, method, path, *, token, fingerprint, body=None, **kwargs):
        sent.append((url, method, path, token, fingerprint, body))
        if body["id"] == "pris":
            raise HubError("pseudo déjà pris", 409)
        return {"user": {"id": body["id"], "name": body["name"]}, "token": "T" * 43}

    monkeypatch.setattr(hub, "_exchange", exchange)
    client = TestClient(app, headers={"X-Console-Token": app.state.auth.token})
    state = client.get("/api/hub/signup").json()
    assert state == {"needed": True, "reachable": True, "url": HUB_URL,
                     "suggest": {"id": "paul-martin", "name": "Paul Martin"}}
    assert probed == [(HUB_URL, 4)]
    assert client.post("/api/hub/signup", json={"id": "Paul Martin", "name": "Paul"}).status_code == 422
    taken = client.post("/api/hub/signup", json={"id": "pris", "name": "Paul"})
    assert taken.status_code == 409 and "« pris »" in taken.json()["detail"] and hub.token() == ""
    made = client.post("/api/hub/signup", json={"id": "paul", "name": " Paul   Martin ", "color": "#7DFFB3"})
    assert made.json()["user"]["id"] == "paul"
    assert sent[-1] == (HUB_URL, "POST", "/api/v1/register", "", HUB_FINGERPRINT,
                        {"key": REGISTER_KEY, "id": "paul", "name": "Paul Martin", "color": "#7dffb3"})
    assert hub.token() == "T" * 43
    cfg = ConfigStore(data_dir).config.hub
    assert (cfg.enabled, cfg.url, cfg.fingerprint) == (True, HUB_URL, HUB_FINGERPRINT)
    assert client.get("/api/hub/signup").json() == {"needed": False}
    assert client.post("/api/hub/signup", json={"id": "paul2", "name": "P"}).status_code == 409


def test_out_of_reach_the_window_waits_for_the_next_start(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from console.app import create_app
    from tests.conftest import FAKE, PORT

    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)

    def unreachable(url, timeout=30):
        raise HubError("Hub injoignable (TimeoutError).")

    monkeypatch.setattr(app.state.hub, "probe", unreachable)
    state = TestClient(app, headers={"X-Console-Token": app.state.auth.token}).get("/api/hub/signup").json()
    assert state["needed"] is True and state["reachable"] is False
