"""Previews: files of a task's folders only, never a protected file, never run a program."""
from pathlib import Path

import pytest

import console.engine as engine_mod

from .test_security import client  # noqa: F401 - fixture

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def task(client):  # noqa: F811
    r = client.post("/api/tasks", json={"prompt": "SLEEP 30\nx", "profile": "work"},
                    headers={"X-Console-Token": client.token})
    assert r.status_code == 200, r.text
    t = r.json()["task"] if "task" in r.json() else r.json()
    wd = Path(t["workdir"])
    (wd / "img").mkdir(exist_ok=True)
    (wd / "img" / "photo.png").write_bytes(PNG)
    (wd / "rapport.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (wd / ".env").write_text("SECRET=1", encoding="utf-8")
    (wd / "lancer.bat").write_text("echo x", encoding="utf-8")
    return t


def get(client, tid, path, auth=True):  # noqa: F811
    headers = {"X-Console-Token": client.token} if auth else {}
    return client.get(f"/api/tasks/{tid}/file", params={"path": path}, headers=headers)


def test_file_of_the_task_folder_is_served_inline_and_sandboxed(client, task):  # noqa: F811
    r = get(client, task["id"], str(Path(task["workdir"]) / "img" / "photo.png"))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    # relative to the task folder too
    assert get(client, task["id"], "img/photo.png").content == PNG
    html = get(client, task["id"], "rapport.html")
    assert html.status_code == 200 and html.headers["content-security-policy"] == "sandbox"


def test_file_preview_needs_the_token(client, task):  # noqa: F811
    assert get(client, task["id"], "img/photo.png", auth=False).status_code == 401


def test_file_outside_the_task_folders_is_refused(client, task, tmp_path):  # noqa: F811
    outside = tmp_path / "ailleurs.txt"
    outside.write_text("non", encoding="utf-8")
    assert get(client, task["id"], str(outside)).status_code == 403
    assert get(client, task["id"], "../../ailleurs.txt").status_code == 403
    assert get(client, task["id"], "..\\..\\ailleurs.txt").status_code == 403
    # the console's own data (token, history) is never inside a task folder
    assert get(client, task["id"], str(tmp_path / "data" / "token")).status_code == 403


def test_file_cited_by_the_task_is_allowed_elsewhere(client, task, tmp_path):  # noqa: F811
    """Claude copied a PDF outside the task folder and named it in its answer."""
    eng = client.app.state.engine
    elsewhere = tmp_path / "ailleurs" / "PV017118.pdf"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"%PDF-1.4 test")
    other = tmp_path / "ailleurs" / "autre.pdf"
    other.write_bytes(b"%PDF-1.4 autre")
    secret = tmp_path / "ailleurs" / ".env"
    secret.write_text("SECRET=1", encoding="utf-8")
    assert get(client, task["id"], str(elsewhere)).status_code == 403
    eng.store.add_event(task["id"], 9001, 0, "text", {"text": f"Je l'ai copié dans **{elsewhere}**. Voir aussi {secret}"})
    r = get(client, task["id"], str(elsewhere))
    assert r.status_code == 200 and r.content == b"%PDF-1.4 test"
    assert get(client, task["id"], str(other)).status_code == 403      # same folder, not cited
    assert get(client, task["id"], str(secret)).status_code == 403     # cited but protected
    # a relative spelling never counts as a citation
    eng.store.add_event(task["id"], 9002, 0, "text", {"text": "voir ../../ailleurs/autre.pdf"})
    assert get(client, task["id"], "../../ailleurs/autre.pdf").status_code == 403


def test_profile_folder_is_allowed_for_a_task_run_elsewhere(client, tmp_path):  # noqa: F811
    h = {"X-Console-Token": client.token}
    wd = tmp_path / "projet"
    wd.mkdir()
    t = client.post("/api/tasks", json={"prompt": "x", "profile": "work", "workdir": str(wd)}, headers=h).json()
    assert t["workdir"] == str(wd.resolve())
    prof_dir = tmp_path / "work" / "work"
    prof_dir.mkdir(parents=True, exist_ok=True)
    (prof_dir / "devis.pdf").write_bytes(b"%PDF-1.4 profil")
    assert get(client, t["id"], str(prof_dir / "devis.pdf")).content == b"%PDF-1.4 profil"


def test_protected_file_inside_the_folder_is_refused(client, task):  # noqa: F811
    r = get(client, task["id"], ".env")
    assert r.status_code == 403 and "protégé" in r.json()["detail"]


def test_missing_file_and_unknown_task(client, task):  # noqa: F811
    assert get(client, task["id"], "absent.png").status_code == 404
    assert get(client, "inconnue", "img/photo.png").status_code == 404


def test_open_with_the_default_application(client, task, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(engine_mod.os, "startfile", lambda p: calls.append(("start", p)), raising=False)
    monkeypatch.setattr(engine_mod.subprocess, "Popen", lambda args, **kw: calls.append(("popen", args)))
    h = {"X-Console-Token": client.token}
    url = f"/api/tasks/{task['id']}/file/open"
    assert client.post(url, json={"path": "img/photo.png"}, headers=h).status_code == 200
    assert calls and "photo.png" in str(calls[-1])
    # a program is never launched from the console, only shown in its folder
    calls.clear()
    r = client.post(url, json={"path": "lancer.bat"}, headers=h)
    assert r.status_code == 403 and not calls
    assert client.post(url, json={"path": "lancer.bat", "reveal": True}, headers=h).status_code == 200
    assert calls and calls[-1][0] == "popen"
    assert client.post(url, json={"path": ".env", "reveal": True}, headers=h).status_code == 403
