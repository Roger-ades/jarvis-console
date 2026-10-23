"""Le jeton d'équipe ne passe ni dans la configuration, ni dans un mauvais dossier."""
import json

from console.config import default_config, dump
from console.hub_client import HubClient, normalize_fingerprint


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

    def __call__(self, url, fingerprint, method, path, body=None, **kwargs):
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
