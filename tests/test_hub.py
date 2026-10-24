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


class FakeSyncthing:
    """L'API REST de Syncthing réduite à ses appareils et ses dossiers."""

    def __init__(self):
        self.devices, self.folders, self.calls = {}, {}, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        kind, key = path.split("/")[-2:]
        store = self.devices if kind == "devices" else self.folders
        if method == "GET":
            return store.get(key)
        if method == "PUT":
            store[key] = body
        elif method == "PATCH":
            store[key].update(body)
        return None


def test_a_poste_registers_on_the_project_folder(db, api, monkeypatch, tmp_path):
    token = db.add_user("roger")
    api.post("/api/v1/projects", headers=_auth(token), json={
        "id": "offre", "name": "Offres", "syncthing_folder": "essai",
    })
    bad = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": "nope"})
    assert bad.status_code == 422
    fake = FakeSyncthing()
    monkeypatch.setattr("hub.sync_link._rest", fake)
    monkeypatch.setattr("hub.sync_link.PROJECTS", tmp_path / "projets")
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    other = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"
    ok = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": device})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    folder = fake.folders["essai"]
    assert folder["label"] == "Offres" and folder["versioning"]["type"] == "staggered"
    assert (tmp_path / "projets" / "essai").is_dir()
    assert device in fake.devices
    again = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": device})
    assert again.status_code == 200 and len(folder["devices"]) == 1
    api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": other})
    assert [d["deviceID"] for d in fake.folders["essai"]["devices"]] == [device, other]


def test_a_syncthing_refusal_reaches_the_poste(db, api, monkeypatch):
    token = db.add_user("roger")
    api.post("/api/v1/projects", headers=_auth(token), json={"id": "offre", "name": "Offres"})

    def down(method, path, body=None):
        raise ValueError("Syncthing injoignable sur le serveur")

    monkeypatch.setattr("hub.sync_link._rest", down)
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    failed = api.post("/api/v1/projects/offre/sync", headers=_auth(token), json={"device_id": device})
    assert failed.status_code == 422 and "injoignable" in failed.json()["error"]


def test_members_and_users_are_listed_without_tokens(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    users = api.get("/api/v1/users", headers=_auth(paul)).json()["users"]
    assert [u["id"] for u in users] == ["paul", "roger"] and "token_hash" not in users[0]
    assert api.get("/api/v1/projects/offre/members", headers=_auth(paul)).status_code == 404
    members = api.get("/api/v1/projects/offre/members", headers=_auth(roger)).json()["members"]
    assert [(m["id"], m["role"]) for m in members] == [("roger", "administrateur")]


def test_an_empty_edit_keeps_the_message(db, api):
    roger = db.add_user("roger")
    api.post("/api/v1/projects", headers=_auth(roger), json={"id": "offre", "name": "Offres"})
    sent = api.post("/api/v1/projects/offre/messages", headers=_auth(roger), json={"text": "Bonjour"})
    msg_id = sent.json()["message"]["id"]
    assert api.patch(f"/api/v1/messages/{msg_id}", headers=_auth(roger), json={}).status_code == 422
    listed = api.get("/api/v1/projects/offre/messages", headers=_auth(roger)).json()["messages"]
    assert listed[-1]["text"] == "Bonjour"


def test_a_conversation_is_revised_only_inside_its_project(db, api):
    roger = db.add_user("roger")
    for pid in ("offre", "autre"):
        api.post("/api/v1/projects", headers=_auth(roger), json={"id": pid, "name": pid})
    made = api.post("/api/v1/projects/offre/conversations?title=Point&session=s1&mode=stripped",
                    headers=_auth(roger), content=b"v1")
    conv_id = made.json()["conversation"]["id"]
    moved = api.post(f"/api/v1/projects/autre/conversations?title=Point&session=s1&mode=stripped&parent={conv_id}",
                     headers=_auth(roger), content=b"v2")
    assert moved.status_code == 404


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


def test_colleagues_are_invited_at_creation(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    db.add_user("zoe")
    created = api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres",
        "members": [{"id": "paul", "role": "membre"}, {"id": "roger", "role": "membre"}, {"id": "paul", "role": "administrateur"}],
    })
    assert created.status_code == 201, created.text
    members = api.get("/api/v1/projects/offre/members", headers=_auth(roger)).json()["members"]
    assert [(m["id"], m["role"], m["added_by"]) for m in members] == [
        ("paul", "membre", "roger"), ("roger", "administrateur", "roger")]
    assert members[0]["name"] == "paul" and members[0]["color"]
    mine = api.get("/api/v1/projects", headers=_auth(paul)).json()["projects"]
    assert mine[0]["added_by"] == "roger" and mine[0]["member_count"] == 2
    assert [p["id"] for p in api.get("/api/v1/projects", headers=_auth(db.add_user("max"))).json()["projects"]] == []
    unknown = api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "autre", "name": "Autre", "members": [{"id": "inconnu", "role": "membre"}]})
    assert unknown.status_code == 400 and unknown.json()["users"] == ["inconnu"]
    assert api.get("/api/v1/projects", headers=_auth(roger)).json()["projects"][0]["id"] == "offre"


def test_a_kept_member_keeps_their_arrival(db, api):
    roger = db.add_user("roger")
    db.add_user("paul")
    db.add_user("zoe")
    api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres", "members": [{"id": "paul", "role": "membre"}]})
    first = {m["id"]: m for m in api.get("/api/v1/projects/offre/members", headers=_auth(roger)).json()["members"]}
    api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={"members": [
        {"id": "roger", "role": "administrateur"}, {"id": "paul", "role": "administrateur"}, {"id": "zoe", "role": "membre"}]})
    after = {m["id"]: m for m in api.get("/api/v1/projects/offre/members", headers=_auth(roger)).json()["members"]}
    assert after["paul"]["joined"] == first["paul"]["joined"] and after["paul"]["role"] == "administrateur"
    assert after["zoe"]["added_by"] == "roger" and after["zoe"]["joined"] >= first["paul"]["joined"]


def test_a_member_leaves_and_the_last_admin_hands_over_first(db, api):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres", "members": [{"id": "paul", "role": "membre"}]})
    stuck = api.delete("/api/v1/projects/offre/members/me", headers=_auth(roger))
    assert stuck.status_code == 409 and "administrateur" in stuck.json()["error"]
    assert api.delete("/api/v1/projects/offre/members/me", headers=_auth(paul)).status_code == 200
    assert api.get("/api/v1/projects", headers=_auth(paul)).json()["projects"] == []
    assert api.delete("/api/v1/projects/offre/members/me", headers=_auth(paul)).status_code == 404


def test_a_removed_member_s_postes_leave_the_folder(db, api, monkeypatch, tmp_path):
    roger = db.add_user("roger")
    paul = db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres", "syncthing_folder": "essai", "members": [{"id": "paul", "role": "membre"}]})
    fake = FakeSyncthing()
    monkeypatch.setattr("hub.sync_link._rest", fake)
    monkeypatch.setattr("hub.sync_link.PROJECTS", tmp_path / "projets")
    mine = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    his = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"
    api.post("/api/v1/projects/offre/sync", headers=_auth(roger), json={"device_id": mine})
    api.post("/api/v1/projects/offre/sync", headers=_auth(paul), json={"device_id": his})
    assert [d["deviceID"] for d in fake.folders["essai"]["devices"]] == [mine, his]
    out = api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [{"id": "roger", "role": "administrateur"}]})
    assert out.status_code == 200 and "sync_errors" not in out.json()
    assert [d["deviceID"] for d in fake.folders["essai"]["devices"]] == [mine]

    def down(method, path, body=None):
        raise ValueError("Syncthing injoignable sur le serveur")

    api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={"members": [
        {"id": "roger", "role": "administrateur"}, {"id": "paul", "role": "membre"}]})
    api.post("/api/v1/projects/offre/sync", headers=_auth(paul), json={"device_id": his})
    monkeypatch.setattr("hub.sync_link._rest", down)
    failed = api.put("/api/v1/projects/offre/members", headers=_auth(roger), json={
        "members": [{"id": "roger", "role": "administrateur"}]})
    assert failed.status_code == 200 and "injoignable" in failed.json()["sync_errors"][0]
    assert [m["id"] for m in api.get("/api/v1/projects/offre/members", headers=_auth(roger)).json()["members"]] == ["roger"]


def test_an_older_hub_gains_the_added_by_column(tmp_path):
    import sqlite3

    path = tmp_path / "hub.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE members (project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, "
                "joined REAL NOT NULL, PRIMARY KEY (project_id, user_id))")
    old.commit()
    old.close()
    store = HubDB(path)
    try:
        cols = {r["name"] for r in store.db.execute("PRAGMA table_info(members)")}
        assert "added_by" in cols
    finally:
        store.close()


KEY = "1arQQ8m8R0f_5mnfh0KOgGkFPz0MnbWVzFMtNdqodzY"


def test_jarvis_registers_with_the_built_in_key(db, api):
    from console.hub_client import REGISTER_KEY

    assert REGISTER_KEY == KEY   # la clé de JARVIS est celle dont le hub garde l'empreinte
    db.add_user("roger")
    made = api.post("/api/v1/register", json={"key": KEY, "id": "paul", "name": "Paul  Martin", "color": "#7DFFB3"})
    assert made.status_code == 201
    body = made.json()
    assert body["user"] == {**body["user"], "id": "paul", "name": "Paul Martin", "color": "#7dffb3", "admin": False}
    assert db.plaintext_token(body["token"]) is False
    assert api.get("/api/v1/me", headers=_auth(body["token"])).json()["user"]["id"] == "paul"
    taken = api.post("/api/v1/register", json={"key": KEY, "id": "paul", "name": "Autre"})
    assert taken.status_code == 409 and "pris" in taken.json()["error"]
    assert api.post("/api/v1/register", json={"key": KEY, "id": "me", "name": "Moi"}).status_code == 422
    assert api.post("/api/v1/register", json={"key": KEY, "id": "Paul!", "name": "P"}).status_code == 422


def test_a_wrong_key_creates_nothing_and_is_throttled(db, api):
    for _ in range(10):
        assert api.post("/api/v1/register", json={"key": "devine", "id": "intrus", "name": "X"}).status_code == 403
    assert api.post("/api/v1/register", json={"key": KEY, "id": "paul", "name": "Paul"}).status_code == 429
    assert db.user_row("intrus") is None and db.user_row("paul") is None


def test_a_revoked_pseudo_cannot_register_again(db, api):
    roger = db.add_user("roger")
    paul = api.post("/api/v1/register", json={"key": KEY, "id": "paul", "name": "Paul"}).json()["token"]
    assert api.delete("/api/v1/users/roger", headers=_auth(paul)).status_code == 403
    assert api.delete("/api/v1/users/roger", headers=_auth(roger)).status_code == 400
    assert api.delete("/api/v1/users/paul", headers=_auth(roger)).json()["ok"] is True
    assert api.get("/api/v1/me", headers=_auth(paul)).status_code == 401
    assert api.post("/api/v1/register", json={"key": KEY, "id": "paul", "name": "Paul"}).status_code == 409
    assert api.delete("/api/v1/users/paul", headers=_auth(roger)).status_code == 409
    kinds = [e["kind"] for e in api.get("/api/v1/audit", headers=_auth(roger)).json()["entries"]]
    assert "user.register" in kinds and "user.revoke" in kinds


def test_a_revoked_account_s_postes_leave_every_folder(db, api, monkeypatch):
    from hub import sync_link

    roger = db.add_user("roger")
    db.add_user("paul")
    api.post("/api/v1/projects", headers=_auth(roger), json={
        "id": "offre", "name": "Offres", "members": [{"id": "paul", "role": "membre"}]})
    db.add_device("paul", "offre", "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH", "offre")
    gone = []
    monkeypatch.setattr(sync_link, "unregister", lambda device, folder: gone.append((device, folder)))
    assert api.delete("/api/v1/users/paul", headers=_auth(roger)).status_code == 200
    assert gone == [("AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH", "offre")]
