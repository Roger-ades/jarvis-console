"""Folder picker: any folder of the disk can become a project, never a protected one."""
from pathlib import Path

from .test_security import client  # noqa: F401 - fixture


def h(client):  # noqa: F811
    return {"X-Console-Token": client.token}


def test_places(client):  # noqa: F811
    eng = client.app.state.engine
    Path(eng.cfg.profile("work").workdir).mkdir(parents=True, exist_ok=True)
    places = client.get("/api/fs/places", headers=h(client)).json()["places"]
    paths = {p["path"] for p in places}
    assert str(Path(eng.cfg.profile("work").workdir).resolve()) in paths
    assert str(Path.home().resolve()) in paths
    assert any(p["kind"] == "account" for p in places)
    assert client.get("/api/fs/places").status_code == 401


def test_browse_folders(client, tmp_path, data_dir):  # noqa: F811
    root = tmp_path / "Clients"
    for d in ("Dupont", "martin", ".cache", "Zeta"):
        (root / d).mkdir(parents=True)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    r = client.get("/api/fs/dirs", params={"path": str(root)}, headers=h(client)).json()
    assert r["dirs"] == ["Dupont", "martin", "Zeta"]  # folders only, sorted, no hidden one
    assert r["path"] == str(root.resolve()) and r["parent"] == str(tmp_path.resolve())
    assert client.get("/api/fs/dirs", params={"path": "Clients"}, headers=h(client)).status_code == 400
    assert client.get("/api/fs/dirs", params={"path": str(root / "absent")}, headers=h(client)).status_code == 404
    # the console's own data folder is never offered
    assert client.get("/api/fs/dirs", params={"path": str(data_dir)}, headers=h(client)).status_code == 403
    listing = client.get("/api/fs/dirs", params={"path": str(data_dir.parent)}, headers=h(client)).json()
    assert data_dir.name not in listing["dirs"]


def test_create_a_folder(client, tmp_path):  # noqa: F811
    base = tmp_path / "Projets"
    base.mkdir()
    r = client.post("/api/fs/mkdir", json={"path": str(base), "name": "Devis 2026"}, headers=h(client))
    assert r.status_code == 200 and Path(r.json()["path"]).is_dir() and Path(r.json()["path"]).parent == base.resolve()
    assert client.post("/api/fs/mkdir", json={"path": str(base), "name": "Devis 2026"}, headers=h(client)).status_code == 409
    r = client.post("/api/fs/mkdir", json={"path": str(base), "name": "..\\..\\evasion"}, headers=h(client))
    assert r.status_code == 200 and Path(r.json()["path"]).parent == base.resolve()  # stays inside
    assert client.post("/api/fs/mkdir", json={"path": str(base), "name": "  "}, headers=h(client)).status_code == 400
    assert client.post("/api/fs/mkdir", json={"path": str(base), "name": "x"}).status_code == 401


def test_a_picked_folder_works_as_a_project(client, tmp_path):  # noqa: F811
    folder = tmp_path / "Visiotech"
    folder.mkdir()
    ws = client.get("/api/workspace", params={"profile": "work", "folder": str(folder)}, headers=h(client)).json()
    assert ws["folder"] == str(folder.resolve())
