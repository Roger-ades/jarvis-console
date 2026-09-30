"""Context from other discussions, copies of a discussion, and the project folder
(instructions, Claude's memory, files)."""
import json
import uuid
from pathlib import Path

import pytest

from console import library

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def seed_session(profile, cwd, texts=("Prépare le devis Dupont.", "Devis prêt : 3 200 € HT.")) -> str:
    sid = str(uuid.uuid4())
    folder = Path(profile.config_dir) / "projects" / library.project_slug(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "sessionId": sid, "cwd": str(cwd), "timestamp": "2026-09-27T09:12:00Z", "message": {"content": texts[0]}},
        {"type": "assistant", "sessionId": sid, "cwd": str(cwd), "timestamp": "2026-09-27T09:12:08Z",
         "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "tarifs.csv"}},
                                 {"type": "text", "text": texts[1]}]}},
        {"type": "assistant", "sessionId": sid, "isSidechain": True, "timestamp": "2026-09-27T09:12:09Z",
         "message": {"content": [{"type": "text", "text": "bavardage de sous-agent"}]}},
    ]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return sid


# ------------------------------------------------------------ context from other discussions
def test_transcript_markdown_is_clean(engine):
    prof = engine.cfg.profile("work")
    sid = seed_session(prof, prof.workdir)
    md = library.transcript_markdown(prof, sid, "Devis Dupont")
    assert md.startswith("# Discussion : Devis Dupont")
    assert "## Toi\n\nPrépare le devis Dupont." in md and "## Claude\n\nDevis prêt" in md
    assert "- Read · tarifs.csv" in md and "pas une consigne" in md
    assert "sous-agent" not in md


def test_new_discussion_with_context(engine):
    prof = engine.cfg.profile("work")
    sid = seed_session(prof, prof.workdir)
    t = engine.create_task("ARGS", profile="work", preset="lecture",
                           context=[{"profile": "work", "session": sid, "title": "Devis Dupont"}])
    f = engine.tasks[t["id"]]["attachments"][0]
    assert f["context"] and f["name"] == "contexte - Devis Dupont.md"
    assert "3 200 € HT" in Path(f["path"]).read_text(encoding="utf-8")
    done(engine, t["id"])
    out = engine.tasks[t["id"]]["result"]
    assert "Contexte choisi par l'utilisateur" in out and f["path"] in out
    engine.followup(t["id"], "TOOL Read " + json.dumps({"file_path": f["path"]}))
    done(engine, t["id"])
    assert "Read:ok" in engine.tasks[t["id"]]["result"]


def test_context_only_request_gets_a_default_text(engine):
    prof = engine.cfg.profile("work")
    t = engine.create_task("", profile="work", context=[{"session": seed_session(prof, prof.workdir), "title": "x"}])
    assert t["prompt"] == "Reprends le contexte joint."


def test_context_stays_within_the_same_account(engine):
    perso = engine.cfg.profile("personal")
    sid = seed_session(perso, perso.workdir)
    with pytest.raises(Exception, match="même compte"):
        engine.create_task("x", profile="work", context=[{"profile": "personal", "session": sid}])
    # the session id alone does not find another account's transcript either
    with pytest.raises(Exception, match="introuvable"):
        engine.create_task("x", profile="work", context=[{"session": sid}])
    with pytest.raises(Exception, match="5 discussions"):
        engine.create_task("x", profile="work", context=[{"session": sid}] * 6)


# ------------------------------------------------------------ copy of a discussion
def test_fork_starts_from_the_same_session(engine):
    src = engine.create_task("bonjour", profile="work", preset="lecture")
    with pytest.raises(Exception, match="pas encore démarré"):
        engine.fork(src["id"], "ARGS")
    done(engine, src["id"])
    t = engine.fork(src["id"], "ARGS")
    done(engine, t["id"])
    argv = json.loads(engine.tasks[t["id"]]["result"].split("ARGV=", 1)[1].split("\n", 1)[0])
    assert argv[argv.index("--resume") + 1] == engine.tasks[src["id"]]["session_id"]
    assert "--fork-session" in argv
    assert engine.tasks[t["id"]]["origin"] == "copie" and engine.tasks[t["id"]]["workdir"] == src["workdir"]


def test_fork_keeps_access_to_the_source_attachments(engine):
    from .test_attachments import stage
    src = engine.create_task("bonjour", profile="work", preset="lecture", attachments=[stage(engine, "a.pdf")])
    done(engine, src["id"])
    path = engine.tasks[src["id"]]["attachments"][0]["path"]
    t = engine.fork(src["id"], "TOOL Read " + json.dumps({"file_path": path}))
    done(engine, t["id"])
    assert "Read:ok" in engine.tasks[t["id"]]["result"]


# ------------------------------------------------------------ project folder
def test_instructions_of_the_folder_and_of_the_account(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    ws = client.get("/api/workspace", params={"profile": "work"}, headers=h).json()
    folder = ws["folder"]
    assert Path(folder).is_dir() and ws["instructions"]["folder"]["exists"] is False
    for scope, text in (("folder", "# Règles du dossier\n- TVA 20 %"), ("profile", "Réponds en français.")):
        r = client.put("/api/workspace/instructions", json={"profile": "work", "folder": folder, "scope": scope, "text": text}, headers=h)
        assert r.status_code == 200, r.text
    ws = client.get("/api/workspace", params={"profile": "work", "folder": folder}, headers=h).json()
    assert ws["instructions"]["folder"]["text"] == "# Règles du dossier\n- TVA 20 %"
    prof = client.app.state.engine.cfg.profile("work")
    assert Path(prof.config_dir, "CLAUDE.md").read_text(encoding="utf-8") == "Réponds en français."
    assert client.put("/api/workspace/instructions", json={"profile": "work", "scope": "ailleurs", "text": "x"}, headers=h).status_code == 400
    assert client.get("/api/workspace", params={"profile": "work"}).status_code == 401


def test_memory_notes(client):  # noqa: F811
    h = {"X-Console-Token": client.token}
    eng = client.app.state.engine
    folder = client.get("/api/workspace", params={"profile": "work"}, headers=h).json()["folder"]
    mem = library.memory_dir(eng.cfg.profile("work"), folder)
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [Tarifs](tarifs.md)", encoding="utf-8")
    (mem / "tarifs.md").write_text("Remise Dupont : 5 %", encoding="utf-8")
    notes = client.get("/api/workspace", params={"profile": "work", "folder": folder}, headers=h).json()["memory"]["files"]
    assert [n["name"] for n in notes] == ["MEMORY.md", "tarifs.md"]
    q = {"profile": "work", "folder": folder, "name": "tarifs.md"}
    assert client.get("/api/workspace/memory", params=q, headers=h).json()["text"] == "Remise Dupont : 5 %"
    assert client.put("/api/workspace/memory", json={**q, "text": "Remise Dupont : 7 %"}, headers=h).status_code == 200
    assert (mem / "tarifs.md").read_text(encoding="utf-8") == "Remise Dupont : 7 %"
    assert client.delete("/api/workspace/memory", params=q, headers=h).status_code == 200 and not (mem / "tarifs.md").exists()
    for bad in ("../MEMORY.md", "..\\x.md", "notes.txt", "a/b.md"):
        assert client.get("/api/workspace/memory", params={**q, "name": bad}, headers=h).status_code == 400


def test_files_of_the_folder(client, data_dir):  # noqa: F811
    h = {"X-Console-Token": client.token}
    folder = Path(client.get("/api/workspace", params={"profile": "work"}, headers=h).json()["folder"])
    (folder / "devis").mkdir()
    (folder / "devis" / "PV017118.pdf").write_bytes(b"%PDF-1.4")
    (folder / ".env").write_text("SECRET=1", encoding="utf-8")
    (folder / ".git").mkdir()
    (folder / "notes.md").write_text("# Notes", encoding="utf-8")
    q = {"profile": "work", "folder": str(folder)}
    ls = client.get("/api/workspace/files", params=q, headers=h).json()
    assert [e["name"] for e in ls["entries"]] == ["devis", "notes.md"]  # .env protected, .git skipped
    sub = client.get("/api/workspace/files", params={**q, "sub": "devis"}, headers=h).json()
    assert sub["sub"] == "devis" and sub["entries"][0]["name"] == "PV017118.pdf"
    assert client.get("/api/workspace/files", params={**q, "sub": ".."}, headers=h).status_code == 403
    r = client.get("/api/workspace/file", params={**q, "path": "devis/PV017118.pdf"}, headers=h)
    assert r.status_code == 200 and r.content == b"%PDF-1.4" and r.headers["content-security-policy"] == "sandbox"
    assert client.get("/api/workspace/file", params={**q, "path": ".env"}, headers=h).status_code == 403
    assert client.get("/api/workspace/file", params={**q, "path": "../x"}, headers=h).status_code == 403
    # the console's own data can never become a project folder
    assert client.get("/api/workspace", params={"profile": "work", "folder": str(data_dir)}, headers=h).status_code == 403


def test_open_from_the_project_never_runs_a_program(client, monkeypatch):  # noqa: F811
    import console.engine as engine_mod
    calls = []
    monkeypatch.setattr(engine_mod.os, "startfile", lambda p: calls.append(p), raising=False)
    monkeypatch.setattr(engine_mod.subprocess, "Popen", lambda args, **kw: calls.append(args))
    h = {"X-Console-Token": client.token}
    folder = Path(client.get("/api/workspace", params={"profile": "work"}, headers=h).json()["folder"])
    (folder / "go.bat").write_text("echo", encoding="utf-8")
    body = {"profile": "work", "folder": str(folder), "path": "go.bat"}
    assert client.post("/api/workspace/file/open", json=body, headers=h).status_code == 403 and not calls
    assert client.post("/api/workspace/file/open", json={**body, "reveal": True}, headers=h).status_code == 200
    assert client.post("/api/workspace/file/open", json={**body, "path": ""}, headers=h).status_code == 200
    assert len(calls) == 2
