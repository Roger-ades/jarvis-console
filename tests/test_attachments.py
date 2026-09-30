"""Attachments: uploaded, filed in the task's own folder, readable by Claude and nobody else's."""
import asyncio
import json
from pathlib import Path

import pytest

from console import attachments as att

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def stage(engine, name, data=b"%PDF-1.4 devis"):
    async def chunks():
        yield data
    return asyncio.run(att.receive(engine.attachments_root(), name, chunks()))["id"]


def result(engine, tid):
    return engine.tasks[tid]["result"]


# ------------------------------------------------------------ names and staging
@pytest.mark.parametrize("given, safe", [
    ("devis.pdf", "devis.pdf"), ("../../secret.txt", "secret.txt"), ("C:\\x\\y\\photo.png", "photo.png"),
    ("CON.txt", "_CON.txt"), ('a<b>:"c".pdf', "a_b___c_.pdf"), ("", "fichier"), ("...", "fichier"),
    (" rapport final.docx ", "rapport final.docx"),
])
def test_safe_name(given, safe):
    assert att.safe_name(given) == safe


def test_same_name_twice_is_kept_apart(tmp_path):
    root = tmp_path / "pj"

    async def one(name, data):
        async def chunks():
            yield data
        return (await att.receive(root, name, chunks()))["id"]

    ids = [asyncio.run(one("a.pdf", b"1")), asyncio.run(one("a.pdf", b"2"))]
    files = att.claim(root, ids, tmp_path / "tache")
    assert [f["name"] for f in files] == ["a.pdf", "a (2).pdf"]
    assert (tmp_path / "tache" / "a (2).pdf").read_bytes() == b"2"
    assert not any((root / att.STAGING).iterdir())


def test_unknown_or_forged_upload_id_is_refused(tmp_path):
    for uid in ("0" * 24, "../../data", ""):
        with pytest.raises(att.AttachmentError):
            att.claim(tmp_path, [uid], tmp_path / "t")


# ------------------------------------------------------------ HTTP
def test_upload_needs_the_token_and_respects_the_size_limit(client, monkeypatch):  # noqa: F811
    assert client.post("/api/uploads?name=a.txt", content=b"x").status_code == 401
    h = {"X-Console-Token": client.token}
    r = client.post("/api/uploads?name=../note.txt", content="bonjour".encode(), headers=h)
    assert r.status_code == 200 and r.json()["name"] == "note.txt" and r.json()["size"] == 7
    monkeypatch.setattr(att, "MAX_FILE", 10)
    r = client.post("/api/uploads?name=gros.bin", content=b"x" * 50, headers=h)
    assert r.status_code == 413
    root = client.app.state.engine.attachments_root()
    assert len(list((root / att.STAGING).iterdir())) == 1  # the refused upload left nothing behind


def test_task_with_attachments(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    up = client.post("/api/uploads?name=devis PV017118.pdf", content=b"%PDF-1.4", headers=h).json()
    r = client.post("/api/tasks", json={"prompt": "", "profile": "work", "attachments": [up["id"]]}, headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    f = t["attachments"][0]
    assert f["name"] == "devis PV017118.pdf" and Path(f["path"]).read_bytes() == b"%PDF-1.4"
    assert Path(f["path"]).parent == Path(t["attachments_dir"])
    assert t["prompt"] == "Voici des pièces jointes."
    eng = client.app.state.engine
    sent = eng.tasks[t["id"]]["queued_messages"][0]
    assert f["path"] in sent and "Read" in sent
    ev = [e for e in eng.store.events(t["id"]) if e["kind"] == "user"][0]["data"]
    assert ev["files"][0]["path"] == f["path"]
    # previewable, and a staged id is single use
    assert client.get(f"/api/tasks/{t['id']}/file", params={"path": f["path"]}, headers=h).status_code == 200
    r = client.post("/api/tasks", json={"prompt": "x", "attachments": [up["id"]]}, headers=h)
    assert r.status_code == 400 and "introuvable" in r.json()["detail"]


def test_discard_an_upload(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    up = client.post("/api/uploads?name=a.txt", content=b"a", headers=h).json()
    assert client.delete(f"/api/uploads/{up['id']}", headers=h).status_code == 200
    r = client.post("/api/tasks", json={"prompt": "x", "attachments": [up["id"]]}, headers=h)
    assert r.status_code == 400


def test_attachments_folder_inside_console_data_is_refused(client, data_dir):  # noqa: F811
    eng = client.app.state.engine
    eng.cfg.general.attachments_dir = str(data_dir / "pj")
    r = client.post("/api/uploads?name=a.txt", content=b"a", headers={"X-Console-Token": client.token})
    assert r.status_code == 400 and "données de la console" in r.json()["detail"]


# ------------------------------------------------------------ with the (fake) CLI
def test_claude_gets_the_folder_and_may_read_the_file(engine):
    t = engine.create_task("ARGS", profile="work", preset="lecture", attachments=[stage(engine, "devis.pdf")])
    done(engine, t["id"])
    path = engine.tasks[t["id"]]["attachments"][0]["path"]
    out = result(engine, t["id"])
    argv = json.loads(out.split("ARGV=", 1)[1].split("\n", 1)[0])
    assert t["attachments_dir"] in [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert f"écho:- {path}" in out
    # "Lecture seule" confines reads to the task's folders: the attachment folder is one of them
    engine.followup(t["id"], "TOOL Read " + json.dumps({"file_path": path}))
    done(engine, t["id"])
    assert "Read:ok" in result(engine, t["id"])


def test_attachment_sent_in_a_follow_up(engine):
    t = engine.create_task("bonjour", profile="work", preset="lecture")
    done(engine, t["id"])
    assert not Path(t["attachments_dir"]).exists()  # no empty folder left for a task without files
    engine.followup(t["id"], "", [stage(engine, "photo.png", b"\x89PNG")])
    done(engine, t["id"])
    path = engine.tasks[t["id"]]["attachments"][0]["path"]
    assert Path(path).read_bytes() == b"\x89PNG"
    assert f"écho:- {path}" in result(engine, t["id"])
    engine.followup(t["id"], "TOOL Read " + json.dumps({"file_path": path}))
    done(engine, t["id"])
    assert "Read:ok" in result(engine, t["id"])


def test_another_task_cannot_read_those_attachments(engine):
    a = engine.create_task("bonjour", profile="work", preset="lecture", attachments=[stage(engine, "a.pdf")])
    done(engine, a["id"])
    path = engine.tasks[a["id"]]["attachments"][0]["path"]
    b = engine.create_task("TOOL Read " + json.dumps({"file_path": path}), profile="work", preset="lecture")
    done(engine, b["id"])
    assert "Read:refus" in result(engine, b["id"])


def test_retry_keeps_the_attachments(engine):
    t = engine.create_task("bonjour", profile="work", attachments=[stage(engine, "a.pdf", b"A")])
    done(engine, t["id"])
    r = engine.retry(t["id"])
    f = engine.tasks[r["id"]]["attachments"][0]
    assert Path(f["path"]).read_bytes() == b"A" and Path(f["path"]).parent == Path(r["attachments_dir"])
    assert Path(engine.tasks[t["id"]]["attachments"][0]["path"]).exists()  # the original stays
