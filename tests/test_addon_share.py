"""Sharing a module between two JARVIS through OneDrive (console/addon_share.py): a zip in the sender's drive, its
link for a mail draft, found and installed on the recipient's side after a click."""
import json
import zipfile
from pathlib import Path

import pytest

from console import addon_share, addons
from console.engine import MODULES_TOOL

from .conftest import wait_for
from .test_addons import module, tool
from .test_project_tools import settle

URL = "https://ades69-my.sharepoint.com/personal/roger_ades-securite_com/Documents/"
MANIFEST = {"nom": "Surveillance des remises", "version": "1.1", "permissions": ["claude"]}


def zipped(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return path


@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A OneDrive synchronised in tmp_path/OneDrive, and an empty home (Downloads, Desktop)."""
    root = tmp_path / "OneDrive - ADES"
    root.mkdir()
    monkeypatch.setattr(addon_share, "drive_roots", lambda: [{"path": str(root), "url": URL, "personal": True}])
    monkeypatch.setattr(addon_share, "account", lambda: "roger@ades-securite.com")
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return root


# ------------------------------------------------------------ the zip
def test_a_module_goes_and_comes_back_with_its_files(tmp_path):
    src = module(tmp_path / "a", "surveillance-remises", MANIFEST, **{"app.js": "1", "_brut__x.json": "{}"})
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    roots = [{"path": str(tmp_path / "od"), "url": URL, "personal": True}]
    path = addon_share.export(addons.read(src), addon_share.default_dir(roots), "roger@ades-securite.com")
    assert path.name == "surveillance-remises-v1.1.jarvis.zip"
    assert addon_share.web_url(path, roots) == URL + "JARVIS%20-%20Modules%20partag%C3%A9s/surveillance-remises-v1.1.jarvis.zip"
    assert addon_share.web_url(tmp_path / "ailleurs.zip", roots) == ""
    info = addon_share.inspect(path)
    assert (info["id"], info["nom"], info["version"], info["de"], info["fichiers"]) == \
        ("surveillance-remises", "Surveillance des remises", "1.1", "roger@ades-securite.com", 4)
    assert "__pycache__/x.pyc" not in " ".join(zipfile.ZipFile(path).namelist())
    # installed elsewhere; installed again: the first one is kept aside, out of the list of modules
    dest = tmp_path / "addons"
    done = addon_share.install(path, dest)
    assert not done["module"]["problemes"] and (dest / "surveillance-remises" / "_brut" / "x.json").is_file()
    assert done["ancien"] == ""
    again = addon_share.install(path, dest)
    assert Path(again["ancien"]).parent == dest / addon_share.OLD and Path(again["ancien"], "app.js").is_file()
    assert [a["id"] for a in addons.scan(dest)] == ["surveillance-remises"]


@pytest.mark.parametrize("entries, why", [
    ({"x/addon.json": "{}", "x/../../evil.txt": "1"}, "nom de fichier refusé"),
    ({"x/addon.json": "{}", "/etc/passwd": "1"}, "nom de fichier refusé"),
    ({"x/addon.json": "{}", "C:/x.txt": "1"}, "nom de fichier refusé"),
    ({"ab/addon.json": "{}", "cd/addon.json": "{}"}, "un seul dossier"),
    ({"addon.json": "{}"}, "un seul dossier"),
    ({"Mauvais_Nom/addon.json": "{}"}, "nom de module invalide"),
    ({"ab/index.html": "x"}, "addon.json manquant"),
    ({"ab/addon.json": "{x"}, "illisible"),
])
def test_a_zip_that_would_write_outside_its_module_is_refused(tmp_path, entries, why):
    path = zipped(tmp_path / "m.jarvis.zip", entries)
    with pytest.raises(addon_share.ShareError, match=why):
        addon_share.inspect(path)
    with pytest.raises(addon_share.ShareError):
        addon_share.install(path, tmp_path / "addons")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "addons").exists()


def test_received_zips_are_found_by_their_name_most_recent_first(tmp_path):
    (tmp_path / "od" / "Partagé" / "JARVIS").mkdir(parents=True)
    (tmp_path / "od" / "a" / "b" / "c" / "d" / "e").mkdir(parents=True)
    old = zipped(tmp_path / "od" / "Partagé" / "JARVIS" / "x-v1.jarvis.zip", {})
    new = zipped(tmp_path / "od" / "y.jarvis (1).zip", {})
    zipped(tmp_path / "od" / "autre.zip", {})
    zipped(tmp_path / "od" / "a" / "b" / "c" / "d" / "e" / "trop-loin.jarvis.zip", {})
    import os
    os.utime(old, (1, 1))
    assert addon_share.find([(tmp_path / "od", 4)]) == [new, old]


# ------------------------------------------------------------ through Claude
def card(engine, tid):
    return wait_for(lambda: next((a for a in engine.tasks[tid].get("pending") or []
                                  if a["kind"] == "permission" and a["tool"] == MODULES_TOOL), None))


def test_claude_shares_a_module_then_a_colleague_imports_it(engine, drive):
    module(engine.addons_dir, "surveillance-remises", MANIFEST, **{"app.js": "v1.1"})
    out = tool(engine, {"action": "partager", "id": "surveillance-remises"})
    zip_path = drive / addon_share.SHARE_FOLDER / "surveillance-remises-v1.1.jarvis.zip"
    assert zip_path.is_file() and URL + "JARVIS%20-%20Modules%20partag%C3%A9s/" in out
    assert "brouillon" in out and "N'envoie pas" in out and "partagé avec lui" in out
    assert "Module introuvable" in tool(engine, {"action": "partager", "id": "absent"})

    # the colleague's side: an older version installed, the zip downloaded
    (engine.addons_dir / "surveillance-remises" / "addon.json").write_text(json.dumps({**MANIFEST, "version": "1.0"}), "utf-8")
    (engine.addons_dir / "surveillance-remises" / "app.js").write_text("v1.0", "utf-8")
    listing = tool(engine, {"action": "recus"})
    assert str(zip_path) in listing and "de roger@ades-securite.com" in listing and "(v1.0)" in listing

    args = {"action": "importer"}
    t = engine.create_task("MODULES " + json.dumps(args), profile="work", preset="lecture")
    p = card(engine, t["id"])
    assert p["input"]["fichier"] == str(zip_path) and "reçu de roger@ades-securite.com" in p["reason"]
    assert "remplacera le module installé (v1.0)" in p["reason"] and "claude" in p["reason"]
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert "n'a pas voulu" in engine.tasks[t["id"]]["result"]
    assert (engine.addons_dir / "surveillance-remises" / "app.js").read_text("utf-8") == "v1.0"

    t = engine.create_task("MODULES " + json.dumps(args), profile="work", preset="lecture")
    engine.decide(t["id"], card(engine, t["id"])["id"], "allow")
    settle(engine, t["id"])
    out = engine.tasks[t["id"]]["result"]
    assert "installé" in out and "ouvrir" in out
    assert (engine.addons_dir / "surveillance-remises" / "app.js").read_text("utf-8") == "v1.1"
    kept = list((engine.addons_dir / addon_share.OLD).iterdir())
    assert len(kept) == 1 and (kept[0] / "app.js").read_text("utf-8") == "v1.0"
    assert "déjà installée" in tool(engine, {"action": "recus"})


def test_import_asks_which_module_when_several_were_received(engine, drive):
    for aid in ("aa", "bb"):
        zipped(drive / f"{aid}.jarvis.zip", {f"{aid}/addon.json": "{}", f"{aid}/index.html": "x"})
    assert "Plusieurs modules reçus" in tool(engine, {"action": "importer"})
    t = engine.create_task("MODULES " + json.dumps({"action": "importer", "id": "bb"}), profile="work", preset="lecture")
    assert card(engine, t["id"])["input"]["id"] == "bb"
    engine.decide(t["id"], card(engine, t["id"])["id"], "deny")
    settle(engine, t["id"])
    zipped(drive / "casse.jarvis.zip", {"../x": "1"})
    assert "pas un module installable" in tool(engine, {"action": "importer", "fichier": str(drive / "casse.jarvis.zip")})
    assert "Zip introuvable" in tool(engine, {"action": "importer", "fichier": str(drive / "absent.zip")})


def test_nothing_received_says_where_it_looked(engine, drive):
    out = tool(engine, {"action": "importer"})
    assert "Aucun module reçu" in out and str(drive) in out and "Ajouter un raccourci" in out
