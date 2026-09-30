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
