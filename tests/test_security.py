import pytest
from fastapi.testclient import TestClient

from console.app import create_app

from .conftest import FAKE, PORT


@pytest.fixture
def client(data_dir):
    # TestClient talks to "testserver"; allow it as an extra local host for the tests only.
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",), start_threads=False)
    with TestClient(app) as c:
        c.token = app.state.auth.token
        c.console_auth = app.state.auth
        yield c
    app.state.store.close()


def test_api_without_token_is_rejected(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/tasks", json={"prompt": "x"}).status_code == 401
    assert client.get("/api/state", headers={"X-Console-Token": "faux"}).status_code == 401


def test_api_with_token_works(client):
    r = client.get("/api/state", headers={"X-Console-Token": client.token})
    assert r.status_code == 200 and "emergency_stop" in r.json()


def test_foreign_origin_is_rejected(client):
    h = {"X-Console-Token": client.token, "Origin": "https://evil.example"}
    assert client.get("/api/state", headers=h).status_code == 403
    assert client.post("/api/emergency", json={"on": True}, headers=h).status_code == 403
    ok = {"X-Console-Token": client.token, "Origin": f"http://127.0.0.1:{PORT}"}
    assert client.get("/api/state", headers=ok).status_code == 200


def test_dns_rebinding_host_is_rejected(client):
    r = client.get("/", headers={"Host": "attacker.example"})
    assert r.status_code == 403
    r = client.get("/api/ping", headers={"Host": f"evil.example:{PORT}"})
    assert r.status_code == 403


def test_one_time_code_exchange(client):
    code = client.console_auth.new_code()
    r = client.post("/api/auth/exchange", json={"code": code})
    assert r.status_code == 200 and r.json()["token"] == client.token
    assert client.post("/api/auth/exchange", json={"code": code}).status_code == 401


def test_index_has_strict_csp_and_no_token(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.token not in r.text


def test_installable_app_files(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone" and m.json()["start_url"] == "/"
    assert any(i["sizes"] == "512x512" for i in m.json()["icons"])
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/"
    assert client.get("/static/offline.html").status_code == 200


def test_shutdown_from_the_ui(client):
    h = {"X-Console-Token": client.token}
    assert client.post("/api/system/shutdown").status_code == 401
    assert client.post("/api/system/shutdown", headers=h).status_code == 409  # not started by the launcher

    class FakeServer:
        should_exit = False
    srv = FakeServer()
    client.app.state.server = srv
    assert client.get("/api/system", headers=h).json()["stoppable"] is True
    assert client.post("/api/system/shutdown", headers=h).status_code == 200
    import time
    time.sleep(0.9)
    assert srv.should_exit is True


def test_rejections_are_audited(client):
    client.get("/api/state")
    rows = client.get("/api/audit", headers={"X-Console-Token": client.token}).json()["rows"]
    assert any(r["kind"] == "appel refusé" for r in rows)


def test_config_validation_errors_are_reported(client):
    h = {"X-Console-Token": client.token}
    cfg = client.get("/api/config", headers=h).json()["config"]
    cfg["profiles"][0]["color"] = "rouge"
    r = client.put("/api/config", json=cfg, headers=h)
    assert r.status_code == 422 and r.json()["errors"]
