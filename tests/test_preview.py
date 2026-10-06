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


def test_bare_file_name_is_found_in_the_task_folders(client, task):  # noqa: F811
    """Claude lists « photo.png » while the file sits in img/: the most recent match wins."""
    import os
    from urllib.parse import unquote
    wd = Path(task["workdir"])
    r = get(client, task["id"], "photo.png")
    assert r.content == PNG
    assert unquote(r.headers["x-file-path"]) == os.path.realpath(wd / "img" / "photo.png")  # shown in the preview
    (wd / "ancien").mkdir()
    (wd / "ancien" / "photo.png").write_bytes(b"ancien")
    os.utime(wd / "ancien" / "photo.png", (1_000_000, 1_000_000))
    assert get(client, task["id"], "photo.png").content == PNG
    assert get(client, task["id"], "./img/photo.png").content == PNG
    # hidden and tool folders are not searched, protected files stay refused
    (wd / "node_modules").mkdir()
    (wd / "node_modules" / "cache.png").write_bytes(PNG)
    assert get(client, task["id"], "cache.png").status_code == 404
    (wd / "img" / ".env").write_text("SECRET=2", encoding="utf-8")
    assert get(client, task["id"], "img/.env").status_code == 403


def test_bare_file_name_written_by_the_task_elsewhere(client, task, tmp_path):  # noqa: F811
    """The image was written outside the task folder: its own tool call names it in full."""
    eng = client.app.state.engine
    out = tmp_path / "rendus" / "banniere.png"
    out.parent.mkdir()
    out.write_bytes(PNG)
    assert get(client, task["id"], "banniere.png").status_code == 404
    eng.store.add_event(task["id"], 9003, 0, "tool", {"name": "Write", "input": {"file_path": str(out), "content": "…"}})
    assert get(client, task["id"], "banniere.png").content == PNG
    assert get(client, task["id"], "rendus/banniere.png").content == PNG
    assert get(client, task["id"], "autre/banniere.png").status_code == 404


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


def test_a_text_file_can_be_edited_from_its_preview(client, task, tmp_path):  # noqa: F811
    """Markdown, CSV and the other plain-text types save in place. Newlines and a BOM stay.
    A protected file, a program, a binary or a file outside the task is never written."""
    h = {"X-Console-Token": client.token}
    url = f"/api/tasks/{task['id']}/file"
    wd = Path(task["workdir"])
    notes = wd / "notes.md"
    notes.write_bytes("\ufeff# Bonjour\r\n\r\nligne\r\n".encode("utf-8"))
    opened = client.get(url, params={"path": "notes.md"}, headers=h)
    stamp = opened.headers["x-file-stamp"]
    assert client.put(url, json={"path": "notes.md", "text": "# Bonjour\n\nligne changée\n", "stamp": stamp}).status_code == 401
    saved = client.put(url, json={"path": "notes.md", "text": "# Bonjour\n\nligne changée\n", "stamp": stamp}, headers=h)
    assert saved.status_code == 200, saved.text
    raw = notes.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.endswith("changée\r\n".encode("utf-8")) and b"\n" not in raw.replace(b"\r\n", b"")
    same = client.put(url, json={"path": "notes.md", "text": "# Bonjour\n\nligne changée\n", "stamp": saved.json()["stamp"]}, headers=h)
    assert same.status_code == 200 and same.json()["stamp"] == saved.json()["stamp"] and notes.read_bytes() == raw
    notes.write_text("autre\n", encoding="utf-8")
    conflict = client.put(url, json={"path": "notes.md", "text": "nouveau\n", "stamp": saved.json()["stamp"]}, headers=h)
    assert conflict.status_code == 409 and conflict.json()["conflict"] is True and notes.read_text(encoding="utf-8") == "autre\n"
    forced = client.put(url, json={"path": "notes.md", "text": "forcé\n", "stamp": saved.json()["stamp"], "force": True}, headers=h)
    assert forced.status_code == 200 and notes.read_text(encoding="utf-8") == "forcé\n"
    csv = wd / "tarifs.csv"
    csv.write_text("a;b\n1;2\n", encoding="utf-8")
    csv_stamp = client.get(url, params={"path": "tarifs.csv"}, headers=h).headers["x-file-stamp"]
    assert client.put(url, json={"path": "tarifs.csv", "text": "a;b\n1;3\n", "stamp": csv_stamp}, headers=h).status_code == 200
    assert csv.read_text(encoding="utf-8") == "a;b\n1;3\n"
    assert client.put(url, json={"path": "notes.md", "stamp": forced.json()["stamp"]}, headers=h).status_code == 400
    assert client.put(url, json={"path": "notes.md", "text": "x" * 2_000_001, "stamp": forced.json()["stamp"]}, headers=h).status_code == 413
    assert client.put(url, json={"path": "img/photo.png", "text": "x", "stamp": "1"}, headers=h).status_code == 415
    assert client.put(url, json={"path": "lancer.bat", "text": "x", "stamp": "1"}, headers=h).status_code == 415
    assert client.put(url, json={"path": ".env", "text": "x", "stamp": "1"}, headers=h).status_code == 403
    assert notes.read_text(encoding="utf-8") == "forcé\n"
    outside = tmp_path / "ailleurs.txt"
    outside.write_text("non", encoding="utf-8")
    assert client.put(url, json={"path": str(outside), "text": "oui", "stamp": "1", "force": True}, headers=h).status_code == 403
    assert outside.read_text(encoding="utf-8") == "non"
    latin = wd / "latin.txt"
    latin.write_bytes("café".encode("latin-1"))
    assert client.put(url, json={"path": "latin.txt", "text": "cafe", "stamp": "1", "force": True}, headers=h).status_code == 415
    assert latin.read_bytes() == "café".encode("latin-1")
    huge = wd / "gros.txt"
    huge.write_text("x" * 2_000_001, encoding="utf-8")
    assert client.put(url, json={"path": "gros.txt", "text": "y", "stamp": "1", "force": True}, headers=h).status_code == 413
    assert huge.read_text(encoding="utf-8").startswith("x")
