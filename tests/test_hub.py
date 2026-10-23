"""Hub : jetons, projets visibles seulement par leurs membres."""
import pytest
from fastapi.testclient import TestClient

from hub.__main__ import main
from hub.app import create_app
from hub.db import HubDB, hash_token


@pytest.fixture()
def db(tmp_path):
    store = HubDB(tmp_path / "hub.db")
    yield store
    store.close()


@pytest.fixture()
def api(db):
    with TestClient(create_app(db)) as client:
        yield client


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_first_user_is_admin_and_the_token_is_only_a_hash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("JARVIS_HUB_ENV", str(tmp_path / "absent.env"))
    main(["user", "add", "roger"])
    token = capsys.readouterr().out.strip()
    assert token
    store = HubDB(tmp_path / "hub.db")
    try:
        assert store.plaintext_token(token) is False
        user = store.user_by_token(token)
        assert user["admin"] is True and user["id"] == "roger"
    finally:
        store.close()
    with pytest.raises(SystemExit):
        main(["user", "add", "roger"])


def test_revoked_token_is_refused_and_the_last_admin_stays(db, api):
    admin = db.add_user("roger")
    other = db.add_user("paul")
    assert api.get("/api/v1/me", headers=_auth(other)).status_code == 200
    db.revoke_user("paul")
    refused = api.get("/api/v1/me", headers=_auth(other))
    assert refused.status_code == 401 and refused.json()["api"] == 1
    with pytest.raises(ValueError, match="dernier"):
        db.revoke_user("roger")
    assert api.get("/api/v1/me", headers=_auth(admin)).status_code == 200


def test_a_member_sees_only_their_projects(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    created = api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres", "syncthing_folder": "essai", "preset": "assiste",
    })
    assert created.status_code == 201
    assert created.json()["api"] == 1
    assert created.json()["project"]["role"] == "administrateur"
    assert api.get("/api/v1/projects", headers=_auth(paul)).json()["projects"] == []
    denied = api.put("/api/v1/projects/offre", headers=_auth(paul), json={"name": "Autre"})
    assert denied.status_code == 404
    api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [
            {"id": "roger", "role": "administrateur"},
            {"id": "paul", "role": "membre"},
        ],
    })
    visible = api.get("/api/v1/projects", headers=_auth(paul)).json()["projects"]
    assert [p["id"] for p in visible] == ["offre"]
    assert visible[0]["syncthing_folder"] == "essai"
    still = api.put("/api/v1/projects/offre", headers=_auth(paul), json={"name": "Autre"})
    assert still.status_code == 403


def test_member_list_keeps_one_project_admin(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    empty = api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [{"id": "paul", "role": "membre"}],
    })
    assert empty.status_code == 422
    unknown = api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [{"id": "zoe", "role": "administrateur"}],
    })
    assert unknown.status_code == 400


def test_audit_is_for_the_admin(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    assert api.get("/api/v1/audit", headers=_auth(paul)).status_code == 403
    kinds = [e["kind"] for e in api.get("/api/v1/audit", headers=_auth(roger)).json()["entries"]]
    assert "project.create" in kinds and "user.add" in kinds
    assert roger not in str(kinds)
    assert hash_token(roger) not in api.get("/api/v1/audit", headers=_auth(roger)).text


def test_messages_and_presence_stay_inside_the_project(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [{"id": "roger", "role": "administrateur"}, {"id": "paul", "role": "membre"}],
    })
    sent = api.post("/api/v1/projects/offre/messages", headers=_auth(paul), json={
        "text": "Le devis est prêt", "refs": [{"kind": "file", "path": "Offres/devis.docx"}],
    })
    assert sent.status_code == 201 and sent.json()["api"] == 1
    listed = api.get("/api/v1/projects/offre/messages", headers=_auth(roger)).json()["messages"]
    assert listed[-1]["text"] == "Le devis est prêt"
    api.post("/api/v1/presence", headers=_auth(paul), json={"projects": [
        {"id": "offre", "sessions": 1, "files": ["Offres/devis.docx"]},
        {"id": "secret", "sessions": 1, "files": ["cache.txt"]},
    ]})
    here = api.get("/api/v1/presence", headers=_auth(roger)).json()["presence"]
    assert [p["project"] for p in here] == ["offre"]
    assert here[0]["files"] == ["Offres/devis.docx"]


def test_a_shared_conversation_is_a_zip_the_author_can_revise(db, api, monkeypatch):
    import hub.app as hub_app
    monkeypatch.setattr(hub_app, "MAX_ARCHIVE", 8)
    roger = db.add_user("roger")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    heavy = api.post("/api/v1/projects/offre/conversations?title=Longue&session=s1&mode=stripped",
                     headers=_auth(roger), content=b"x" * 9)
    assert heavy.status_code == 413
    made = api.post("/api/v1/projects/offre/conversations?title=Point&session=s1&mode=stripped",
                    headers=_auth(roger), content=b"archive")
    assert made.status_code == 201
    conv_id = made.json()["conversation"]["id"]
    archive = api.get(f"/api/v1/conversations/{conv_id}/archive", headers=_auth(roger))
    assert archive.content == b"archive"
    revised = api.put(f"/api/v1/conversations/{conv_id}?title=Point&mode=complete",
                      headers=_auth(roger), content=b"v2")
    assert revised.status_code == 200 and revised.json()["conversation"]["rev"] == 2
    assert api.get(f"/api/v1/conversations/{conv_id}/archive", headers=_auth(roger)).content == b"v2"


def test_a_poste_registers_on_the_project_folder(db, api, monkeypatch):
    token = db.add_user("roger")
    api.post("/api/v1/projects", headers=_auth(token), json={
        "id": "offre", "name": "Offres", "syncthing_folder": "essai",
    })
    bad = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": "nope"})
    assert bad.status_code == 422
    calls = []
    monkeypatch.setattr("hub.sync_link.subprocess.run", lambda cmd, **kwargs: calls.append(cmd))
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    ok = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": device})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert calls[0][-1] == device
    assert "essai" in calls[1]


def test_the_server_device_id_uses_the_syncthing_2_subcommand(db, api, monkeypatch):
    import subprocess

    token = db.add_user("roger")
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    calls = []

    def answer(cmd, **kwargs):
        calls.append(cmd)
        return device + "\n"

    monkeypatch.setattr("hub.sync_link.subprocess.check_output", answer)
    ok = api.get("/api/v1/syncthing", headers=_auth(token))
    assert ok.status_code == 200 and ok.json()["device_id"] == device
    assert calls[0][1:] == ["device-id"]

    def refuse(cmd, **kwargs):
        raise subprocess.CalledProcessError(80, cmd)

    monkeypatch.setattr("hub.sync_link.subprocess.check_output", refuse)
    down = api.get("/api/v1/syncthing", headers=_auth(token))
    assert down.status_code == 503 and "Syncthing" in down.json()["error"]


def test_health_needs_no_token(api):
    assert api.get("/health").json() == {"api": 1, "ok": True}
    assert api.get("/api/v1/me").status_code == 401
