"""Named projects: a folder with its defaults (account, permissions, model), pinned or not."""
from pathlib import Path

from .test_security import client  # noqa: F401 - fixture


def h(client):  # noqa: F811
    return {"X-Console-Token": client.token}


def test_create_update_and_list(client, tmp_path):  # noqa: F811
    folder = tmp_path / "Visiotech"
    folder.mkdir()
    body = {"folder": str(folder), "name": "Visiotech", "color": "#ffb347", "profile": "work", "preset": "edition",
            "model": "sonnet", "effort": "high"}
    r = client.put("/api/projects", json=body, headers=h(client))
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == str(folder.resolve())
    created = r.json()["created"]
    r = client.put("/api/projects", json={**body, "name": "Tarifs Visiotech", "pinned": False}, headers=h(client))
    assert r.json()["created"] == created  # updated, not duplicated
    rows = client.get("/api/projects", headers=h(client)).json()["projects"]
    assert len(rows) == 1 and rows[0]["name"] == "Tarifs Visiotech" and rows[0]["exists"] and rows[0]["discussions"] == 0
    assert client.get("/api/projects").status_code == 401


def test_invalid_projects(client, tmp_path, data_dir):  # noqa: F811
    folder = tmp_path / "P"
    folder.mkdir()
    for bad in ({"name": ""}, {"color": "rouge"}, {"profile": "inconnu"}, {"preset": "inconnu"}, {"effort": "énorme"}):
        r = client.put("/api/projects", json={"folder": str(folder), "name": "P", **bad}, headers=h(client))
        assert r.status_code in (400, 404), bad
    r = client.put("/api/projects", json={"folder": str(data_dir), "name": "Données"}, headers=h(client))
    assert r.status_code == 403  # the console's data is never a project


def test_remove_keeps_the_folder(client, tmp_path):  # noqa: F811
    folder = tmp_path / "Garder"
    folder.mkdir()
    (folder / "important.txt").write_text("x", encoding="utf-8")
    client.put("/api/projects", json={"folder": str(folder), "name": "Garder"}, headers=h(client))
    assert client.delete("/api/projects", params={"folder": str(folder.resolve())}, headers=h(client)).status_code == 200
    assert (folder / "important.txt").exists()
    assert client.get("/api/projects", headers=h(client)).json()["projects"] == []
    assert client.delete("/api/projects", params={"folder": str(folder)}, headers=h(client)).status_code == 404


def test_activity_counts_the_discussions(engine, tmp_path):
    folder = tmp_path / "Devis"
    folder.mkdir()
    engine.save_project({"folder": str(folder), "name": "Devis"})
    t = engine.create_task("bonjour", profile="work", workdir=str(folder))
    row = engine.projects()[0]
    assert row["discussions"] == 1 and row["last"] == engine.tasks[t["id"]]["created"]
    assert Path(row["folder"]) == folder.resolve()
