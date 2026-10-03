"""Notes: general or of a project, each with an account and an optional reminder."""
from .test_security import client  # noqa: F401 - fixture


def h(client):  # noqa: F811
    return {"X-Console-Token": client.token}


def make_project(client, tmp_path, profile="work"):  # noqa: F811
    folder = tmp_path / "Chantier"
    folder.mkdir()
    r = client.put("/api/projects", json={"folder": str(folder), "name": "Chantier", "color": "#ffb347",
                                          "profile": profile}, headers=h(client))
    assert r.status_code == 200, r.text
    return r.json()["folder"]


def test_general_and_project_notes(client, tmp_path):  # noqa: F811
    folder = make_project(client, tmp_path)
    r = client.post("/api/notes", json={"text": "  Appeler le fournisseur\nAvant jeudi  "}, headers=h(client))
    assert r.status_code == 200, r.text
    general = r.json()
    assert general["text"] == "Appeler le fournisseur\nAvant jeudi" and general["folder"] == ""
    assert general["profile"]  # the first account by default
    r = client.post("/api/notes", json={"text": "Relire le devis", "folder": folder}, headers=h(client))
    proj = r.json()
    assert proj["folder"] == folder and proj["profile"] == "work"  # follows the project's account
    rows = client.get("/api/notes", headers=h(client)).json()["notes"]
    assert [n["id"] for n in rows] == [proj["id"], general["id"]]  # newest first
    only = client.get("/api/notes", params={"folder": folder}, headers=h(client)).json()["notes"]
    assert [n["id"] for n in only] == [proj["id"]]
    only = client.get("/api/notes", params={"folder": ""}, headers=h(client)).json()["notes"]
    assert [n["id"] for n in only] == [general["id"]]
    assert client.get("/api/notes").status_code == 401


def test_reminder_snooze_and_delete(client):  # noqa: F811
    note = client.post("/api/notes", json={"text": "Réunion", "remind_at": 1_900_000_000}, headers=h(client)).json()
    assert note["remind_at"] == 1_900_000_000 and note["reminded"] is False
    r = client.patch(f"/api/notes/{note['id']}", json={"reminded": True}, headers=h(client))
    assert r.json()["reminded"] is True and r.json()["text"] == "Réunion"
    r = client.patch(f"/api/notes/{note['id']}", json={"remind_at": 1_900_000_600}, headers=h(client))
    assert r.json()["remind_at"] == 1_900_000_600 and r.json()["reminded"] is False  # a new date rings again
    r = client.patch(f"/api/notes/{note['id']}", json={"remind_at": None}, headers=h(client))
    assert r.json()["remind_at"] is None
    assert client.delete(f"/api/notes/{note['id']}", headers=h(client)).status_code == 200
    assert client.delete(f"/api/notes/{note['id']}", headers=h(client)).status_code == 404
    assert client.patch(f"/api/notes/{note['id']}", json={"text": "x"}, headers=h(client)).status_code == 404


def test_invalid_notes(client, tmp_path):  # noqa: F811
    for bad in ({"text": "   "}, {"text": "x", "folder": str(tmp_path)}, {"text": "x", "profile": "inconnu"},
                {"text": "x", "remind_at": "demain"}, {"text": "x" * 20001}):
        r = client.post("/api/notes", json=bad, headers=h(client))
        assert r.status_code == 400, bad
    assert client.get("/api/notes", headers=h(client)).json()["notes"] == []
