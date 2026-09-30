"""After an update: the page sees that the server's code is older, and can restart it."""
import socket
import threading
import time
from types import SimpleNamespace

import console.app as app_mod
from console import __main__ as launcher
from console import winsys

from .test_security import client  # noqa: F401 - fixture


def test_version_reports_an_update_waiting_for_a_restart(client, monkeypatch):  # noqa: F811
    h = {"X-Console-Token": client.token}
    v = client.get("/api/system/version", headers=h).json()
    assert v["stale"] is False and v["restartable"] is False and v["boot"]
    assert client.get("/api/ping").json()["boot"] == v["boot"]
    monkeypatch.setattr(app_mod, "code_stamp", lambda: time.time() + 3600)
    assert client.get("/api/system/version", headers=h).json()["stale"] is True
    assert client.get("/api/system/version").status_code == 401


def test_restart_relaunches_then_stops(client, monkeypatch, data_dir):  # noqa: F811
    h = {"X-Console-Token": client.token}
    assert client.post("/api/system/restart", headers=h).status_code == 409  # not started by start.bat
    calls = []
    monkeypatch.setattr(winsys, "relaunch", lambda port, d: calls.append((port, d)))
    server = SimpleNamespace(should_exit=False)
    client.app.state.server = server
    r = client.post("/api/system/restart", headers=h)
    assert r.status_code == 200 and r.json()["boot"]
    assert calls == [(client.app.state.engine.port, data_dir.resolve())]
    time.sleep(1)
    assert server.should_exit is True
    assert client.post("/api/system/restart").status_code == 401


def test_the_new_console_waits_for_the_port(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    threading.Timer(0.8, s.close).start()
    t0 = time.time()
    launcher._wait_port_free(port, timeout=10)
    assert 0.6 < time.time() - t0 < 5
