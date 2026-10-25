"""One-click update from the Git repository, on a throwaway remote and clone."""
import subprocess

import pytest

from console import updater

from .test_security import client  # noqa: F401 - fixture


def run(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    remote, work, local = tmp_path / "remote.git", tmp_path / "work", tmp_path / "local"
    run(tmp_path, "init", "--bare", "-b", "main", str(remote))
    run(tmp_path, "clone", str(remote), str(work))
    for r in (work,):
        run(r, "config", "user.email", "t@example.com")
        run(r, "config", "user.name", "Test")
    (work / "README.md").write_text("v1\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-m", "Première version")
    run(work, "push", "-u", "origin", "main")
    run(tmp_path, "clone", str(remote), str(local))
    monkeypatch.setattr(updater, "ROOT", local)
    return work, local


def publish(work, text, message):
    (work / "README.md").write_text(text, encoding="utf-8")
    run(work, "commit", "-am", message)
    run(work, "push")


def test_status_and_update(repos):
    work, local = repos
    st = updater.status()
    assert st["git"] and st["branch"] == "main" and st["behind"] == 0 and not st["error"]
    publish(work, "v2\n", "Limites des comptes")
    st = updater.status()
    assert st["behind"] == 1 and st["commits"] == ["Limites des comptes"] and not st["dirty"]
    res = updater.update()
    assert res == {"updated": True, "requirements": False, "commits": ["Limites des comptes"]}
    assert (local / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert updater.update()["updated"] is False  # already up to date


def test_local_changes_are_never_overwritten(repos):
    work, local = repos
    publish(work, "v2\n", "Nouveautés")
    (local / "README.md").write_text("modifié ici\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="modifiés sur ce poste"):
        updater.update()
    assert (local / "README.md").read_text(encoding="utf-8") == "modifié ici\n"


def test_not_a_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    st = updater.status()
    assert not st["git"] and "dépôt Git" in st["error"]


def test_update_api_needs_a_restartable_console(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    assert client.post("/api/system/update").status_code == 401
    assert client.post("/api/system/update", headers=h).status_code == 409
    assert "behind" in client.get("/api/system/update", headers=h).json()


@pytest.fixture
def hub_server(tmp_path, monkeypatch):
    """The hub over plain HTTP on a free port, serving a bare repository as the team's server."""
    import socket
    import threading
    import time

    import uvicorn

    from hub.app import create_app
    from hub.db import HubDB

    server_repo = tmp_path / "srv" / "jarvis-console.git"
    server_repo.parent.mkdir()
    monkeypatch.setenv("JARVIS_HUB_GIT_REPO", str(server_repo))
    db = HubDB(tmp_path / "hub.db")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(db), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    monkeypatch.setattr(updater, "SERVER_REPO", f"http://127.0.0.1:{port}/git/jarvis-console.git")
    yield db, server_repo
    server.should_exit = True
    thread.join(5)
    db.close()


@pytest.fixture
def from_github(tmp_path, monkeypatch, hub_server):
    """A poste cloned from GitHub; the server's repository holds the same history, then moves ahead."""
    db, server_repo = hub_server
    github, work, local = tmp_path / "github.com" / "jarvis.git", tmp_path / "work", tmp_path / "local"
    github.parent.mkdir()
    run(tmp_path, "init", "--bare", "-b", "main", str(github))
    run(tmp_path, "clone", str(github), str(work))
    run(work, "config", "user.email", "t@example.com")
    run(work, "config", "user.name", "Test")
    (work / "README.md").write_text("v1\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-m", "Première version")
    run(work, "push", "-u", "origin", "main")
    run(tmp_path, "clone", "--bare", str(github), str(server_repo))
    run(work, "remote", "add", "server", str(server_repo))
    run(tmp_path, "clone", str(github), str(local))
    monkeypatch.setattr(updater, "ROOT", local)
    data = tmp_path / "data"
    data.mkdir()
    return db, work, local, data


def test_a_github_copy_moves_to_the_server_once_it_has_a_hub_token(from_github):
    db, work, local, data = from_github
    assert updater.status(data_dir=data)["migrated"] is False  # no token yet: stays on GitHub
    assert "github.com" in subprocess.run(["git", "remote", "get-url", "origin"], cwd=local,
                                          capture_output=True, text=True).stdout
    (data / "hub-token").write_text(db.add_user("paul"), encoding="utf-8")
    (work / "README.md").write_text("v2\n", encoding="utf-8")
    run(work, "commit", "-am", "Seulement sur le serveur")
    run(work, "push", "server", "main")
    st = updater.status(data_dir=data)
    assert st["migrated"] and not st["error"] and st["commits"] == ["Seulement sur le serveur"]
    remotes = subprocess.run(["git", "remote", "-v"], cwd=local, capture_output=True, text=True).stdout
    assert updater.SERVER_REPO in remotes and "github" in remotes
    assert "Bearer" not in (local / ".git" / "config").read_text(encoding="utf-8")  # the token is never stored
    assert updater.update(data)["updated"]
    assert (local / "README.md").read_text(encoding="utf-8") == "v2\n"


def test_a_refused_token_leaves_the_copy_on_github(from_github):
    db, _work, local, data = from_github
    db.add_user("roger")
    (data / "hub-token").write_text(db.add_user("paul"), encoding="utf-8")
    db.revoke_user("paul")
    st = updater.status(data_dir=data)
    assert st["migrated"] is False and not st["error"]
    remotes = subprocess.run(["git", "remote"], cwd=local, capture_output=True, text=True).stdout.split()
    assert remotes == ["origin"]


def test_the_hub_repository_is_read_only_and_needs_a_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from hub.app import create_app
    from hub.db import HubDB

    repo = tmp_path / "jarvis-console.git"
    run(tmp_path, "init", "--bare", "-b", "main", str(repo))
    monkeypatch.setenv("JARVIS_HUB_GIT_REPO", str(repo))
    db = HubDB(tmp_path / "hub.db")
    token = db.add_user("paul")
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(db)) as api:
        refs = "/git/jarvis-console.git/info/refs"
        assert api.get(refs, params={"service": "git-upload-pack"}).status_code == 401
        ok = api.get(refs, params={"service": "git-upload-pack"}, headers=auth)
        assert ok.status_code == 200 and ok.headers["content-type"] == "application/x-git-upload-pack-advertisement"
        assert api.get(refs, params={"service": "git-receive-pack"}, headers=auth).status_code == 403
        assert api.post("/git/jarvis-console.git/git-receive-pack", headers=auth).status_code == 403
        assert api.get("/git/jarvis-console.git/HEAD", headers=auth).status_code == 403
    db.close()
