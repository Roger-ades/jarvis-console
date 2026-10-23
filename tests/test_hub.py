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


def test_health_needs_no_token(api):
    assert api.get("/health").json() == {"api": 1, "ok": True}
    assert api.get("/api/v1/me").status_code == 401
