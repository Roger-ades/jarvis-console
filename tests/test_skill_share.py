"""Sharing skills between two JARVIS through the team's hub (console/skill_share.py, equipe.py, hub/): from the page
Compétences, one skill, several or whole groups go to colleagues, each with its group; the colleague's inbox shows
them, they pick the account, the skills land in the same groups. Claude takes no part."""
import json
import zipfile
from pathlib import Path

import pytest

from console import skill_share
from hub.db import HubDB

from .test_addon_share import Bridge, team, zipped  # noqa: F401 - team: the fixture

GROUPS = {"incendie": "Métier/Incendie", "desenfumage": "Métier/Incendie/Désenfumage", "mails": "Bureau"}


def skill(base: Path, name: str, desc: str = "", **files) -> Path:
    d = base / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc or name}\n---\n# {name}\n", "utf-8")
    for f, text in files.items():
        (d / f.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
        (d / f.replace("__", "/")).write_text(text, "utf-8")
    return d


# ------------------------------------------------------------ the zip
def test_skills_go_and_come_back_with_their_groups(tmp_path):
    src = tmp_path / "a"
    skill(src, "incendie", "Règles incendie", **{"lecons.md": "x", "ref__table.md": "t"})
    skill(src, "mails", "Rédaction")
    (src / "skills" / "incendie" / "__pycache__").mkdir()
    (src / "skills" / "incendie" / "__pycache__" / "x.pyc").write_bytes(b"x")
    path, sent = skill_share.export(src, ["incendie", "Mails"], GROUPS, tmp_path / "out", "Roger Martin")
    assert [(s["nom"], s["groupe"]) for s in sent] == [("incendie", "Métier/Incendie"), ("mails", "Bureau")]
    assert "__pycache__" not in " ".join(zipfile.ZipFile(path).namelist())
    info = skill_share.inspect(path)
    assert info["de"] == "Roger Martin"
    assert [(c["nom"], c["groupe"], c["fichiers"]) for c in info["competences"]] == \
        [("incendie", "Métier/Incendie", 3), ("mails", "Bureau", 1)]
    # installed elsewhere (only one of them), then again: the first one is kept aside, out of skills/
    dest = tmp_path / "b"
    done = skill_share.install(path, dest, ["incendie"])
    assert [c["nom"] for c in done["installees"]] == ["incendie"] and done["installees"][0]["ancien"] == ""
    assert (dest / "skills" / "incendie" / "ref" / "table.md").is_file() and not (dest / "skills" / "mails").exists()
    again = skill_share.install(path, dest)
    old = Path(again["installees"][0]["ancien"])
    assert old.parent == dest / skill_share.OLD and (old / "lecons.md").is_file()
    assert sorted(p.name for p in (dest / "skills").iterdir()) == ["incendie", "mails"]
    assert not list(dest.glob(".import-competences-*"))


def test_export_refuses_what_is_not_there(tmp_path):
    skill(tmp_path, "incendie")
    with pytest.raises(skill_share.ShareError, match="introuvable"):
        skill_share.export(tmp_path, ["absente"], {}, tmp_path / "out")
    with pytest.raises(skill_share.ShareError, match="au moins une"):
        skill_share.export(tmp_path, [], {}, tmp_path / "out")


def info(*names, groupe=""):
    return json.dumps({"type": "competences", "competences": [{"nom": n, "groupe": groupe} for n in names]})


@pytest.mark.parametrize("entries, why", [
    ({"partage.json": info("x1"), "x1/SKILL.md": "a", "x1/../../evil.txt": "1"}, "nom de fichier refusé"),
    ({"partage.json": info("x1"), "x1/SKILL.md": "a", "/etc/passwd": "1"}, "nom de fichier refusé"),
    ({"partage.json": info("x1"), "x1/SKILL.md": "a", "C:/x.txt": "1"}, "nom de fichier refusé"),
    ({"partage.json": info("x1"), "x1/SKILL.md": "a", "y2/SKILL.md": "b"}, "ne correspond pas"),
    ({"partage.json": info("x1"), "x1/SKILL.md": "a", "evil.txt": "1"}, "hors d'une compétence"),
    ({"partage.json": info("Mauvais"), "Mauvais/SKILL.md": "a"}, "nom de compétence invalide"),
    ({"partage.json": info("synced"), "synced/SKILL.md": "a"}, "nom de compétence invalide"),
    ({"partage.json": info("x1"), "x1/lecons.md": "a"}, "SKILL.md manquant"),
    ({"partage.json": "{x", "x1/SKILL.md": "a"}, "illisible"),
    ({"partage.json": json.dumps({"type": "module"}), "x1/SKILL.md": "a"}, "pas de compétences"),
    ({"x1/SKILL.md": "a"}, "manquant"),
])
def test_a_zip_that_would_write_outside_its_skills_is_refused(tmp_path, entries, why):
    path = zipped(tmp_path / "c.zip", entries)
    with pytest.raises(skill_share.ShareError, match=why):
        skill_share.inspect(path)
    with pytest.raises(skill_share.ShareError):
        skill_share.install(path, tmp_path / "dest")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "dest").exists()


def test_a_group_that_is_not_one_is_dropped(tmp_path):
    path = zipped(tmp_path / "c.zip", {"partage.json": info("x1", groupe="/".join("abcde")), "x1/SKILL.md": "a"})
    assert skill_share.inspect(path)["competences"][0]["groupe"] == ""


# ------------------------------------------------------------ console -> hub -> console
def test_a_group_sent_from_the_page_lands_in_the_same_groups(team):
    app, console, bridge, db = team
    engine, feed = app.state.engine, app.state.team_feed
    prof = engine.cfg.profiles[0]
    base = engine._account_dir(prof)
    for n in GROUPS:
        skill(base, n, f"À propos de {n}")
    for n, g in GROUPS.items():
        engine.classify_skill(n, g, profile=prof.id)
    # the group Métier, its sub-groups with it
    names = [n for n, g in GROUPS.items() if g.startswith("Métier")]
    r = console.post("/api/hub/skills/send", json={"competences": names, "to": ["paul", "anne"], "profile": prof.id})
    assert r.status_code == 200 and r.json() == {"ok": True, "sent": 2, "count": 2}
    assert console.post("/api/hub/skills/send", json={"competences": ["absente"], "to": ["paul"], "profile": prof.id}).status_code == 422
    assert console.post("/api/hub/skills/send", json={"competences": names, "to": [], "profile": prof.id}).status_code == 422
    assert console.post("/api/hub/skills/send", json={"competences": names, "to": ["inconnu"], "profile": prof.id}).status_code == 404

    # Paul's side (the same console here, another account): nothing classified yet, an old incendie already there
    other = engine.cfg.profiles[-1]
    there = engine._account_dir(other)
    skill(there, "incendie", "la mienne")
    feed.poll(bridge.as_("paul"), {"id": "paul", "name": "Paul Durand"}, {})
    entry, = [e for e in engine.inbox()["entries"] if e["kind"] == "team_skills"]
    assert entry["from"] == "Roger Martin" and entry["section"] == "todo"
    assert entry["summary"] == "Roger Martin t'envoie 2 compétences (groupes Métier › Incendie, Métier › Incendie › Désenfumage)."
    assert {c["nom"]: c["groupe"] for c in entry["skills"]} == {n: GROUPS[n] for n in names}

    r = console.post(f"/api/hub/skills/{entry['pack_id']}/install", json={"profile": other.id})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["profile"] == other.id and sorted(c["nom"] for c in got["installees"]) == sorted(names)
    assert Path(next(c["ancien"] for c in got["installees"] if c["nom"] == "incendie"), "SKILL.md").read_text("utf-8").count("la mienne")
    listed = {s["name"]: s["groupe"] for s in engine.skills(profile=other.id)["compte"]}
    assert listed == {n: GROUPS[n] for n in names}
    assert set(engine.skills(profile=other.id)["groupes"]["compte"]) >= {"Métier", "Métier/Incendie"}
    assert not [e for e in engine.inbox()["entries"] if e["kind"] == "team_skills"]
    assert db.skills_for("paul") == []
    # gone from the hub: installing again says so, nothing is written
    assert console.post(f"/api/hub/skills/{entry['pack_id']}/install", json={"profile": other.id}).status_code == 404

    # Anne puts hers away: the hub drops the zip once nobody waits for it
    feed.poll(bridge.as_("anne"), {"id": "anne", "name": "Anne Petit"}, {})
    entry, = [e for e in engine.inbox()["entries"] if e["kind"] == "team_skills"]
    assert console.post(f"/api/hub/skills/{entry['pack_id']}/dismiss").json() == {"ok": True}
    assert not [e for e in engine.inbox()["entries"] if e["kind"] == "team_skills"]
    assert not list((db.files / "competences").glob("*.zip"))


def test_the_hub_keeps_skills_to_their_recipient(tmp_path):
    db = HubDB(tmp_path / "hub.db")
    for u in ("roger", "paul", "anne"):
        db.add_user(u)
    blob = b"PK-pas-vraiment"
    pack, = db.send_skills("roger", ["paul"], [{"nom": "incendie", "groupe": "Métier"}], blob)
    assert db.skills_for("anne") == [] and db.skills_zip("anne", pack["id"]) is None
    assert db.skills_zip("paul", pack["id"]).read_bytes() == blob
    with pytest.raises(KeyError):
        db.close_skills("anne", pack["id"], "installé")
    db.close_skills("paul", pack["id"], "installé")
    assert db.skills_for("paul") == [] and not list((db.files / "competences").glob("*.zip"))
    with pytest.raises(KeyError):
        db.send_skills("roger", ["inconnu"], [{"nom": "x1", "groupe": ""}], blob)
    db.close()
