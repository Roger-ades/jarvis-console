"""The team's shared skills (console/skill_library.py, hub/db.py library): one library for every account of the hub,
copied into each JARVIS's accounts; whoever changes a skill sends it, the others receive it. Claude takes no part."""
from pathlib import Path

from console import skill_library, skill_share
from hub.db import HubDB

from .test_addon_share import team  # noqa: F401 - the fixture
from .test_skill_share import skill


class Kv:
    def __init__(self):
        self.d = {}

    def kv_get(self, key, default=None):
        return self.d.get(key, default)

    def kv_set(self, key, value):
        self.d[key] = value


def jarvis(tmp_path, bridge, who):
    """One JARVIS of `who`, one account folder."""
    base = tmp_path / who / ".claude"
    base.mkdir(parents=True)

    def call(method, path, **kw):
        bridge.who = who
        return bridge(None, None, method, path, **kw)
    lib = skill_library.Library(Kv(), tmp_path / who, lambda: [("travail", base)], lambda: None)
    return lib, call, base


def edit(base, name, text):
    (base / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {text}\n---\n{text}\n", "utf-8")


def text(base, name):
    return (base / "skills" / name / "SKILL.md").read_text("utf-8")


def test_a_shared_skill_follows_whoever_changes_it(team, tmp_path):
    _, _, bridge, db = team
    roger, rcall, rbase = jarvis(tmp_path, bridge, "roger")
    paul, pcall, pbase = jarvis(tmp_path, bridge, "paul")
    skill(rbase, "incendie", "Règles incendie", **{"ref__table.md": "t"})
    skill(pbase, "incendie", "la mienne, personnelle")

    # Roger drags it to the shared part: nobody to pick, every account of the hub gets it
    assert roger.publish(rcall, rbase, ["incendie"], {"incendie": "Métier/Incendie"}, account=True) == ["incendie"]
    lib = db.library()
    assert [(c["nom"], c["groupe"], c["rev"], c["par"]) for c in lib["competences"]] == [("incendie", "Métier/Incendie", 1, "roger")]
    assert lib["groupes"] == ["Métier", "Métier/Incendie"]

    # Paul's JARVIS, at its next sync: installed; his own skill of the same name kept aside, and said
    view = paul.sync(pcall)
    assert "Règles incendie" in text(pbase, "incendie") and (pbase / "skills" / "incendie" / "ref" / "table.md").is_file()
    old, = (pbase / skill_share.OLD).iterdir()
    assert "la mienne" in (old / "SKILL.md").read_text("utf-8")
    assert "personnelle du même nom" in view["notes"][0]["texte"]
    assert paul.held(pbase)["incendie"]["rev"] == 1

    # Paul improves it: it leaves at his next sync, Roger receives it at his, nothing kept aside for a plain update
    edit(pbase, "incendie", "version de Paul")
    paul.sync(pcall)
    assert db.library()["competences"][0]["rev"] == 2
    roger.sync(rcall)
    assert "version de Paul" in text(rbase, "incendie") and not (rbase / skill_share.OLD).exists()
    assert paul.sync(pcall)["notes"] == view["notes"]     # nothing new: no round trip

    # both change it before a sync: the first to send wins, the other's version is kept aside, and said
    edit(rbase, "incendie", "version de Roger")
    edit(pbase, "incendie", "autre version de Paul")
    roger.sync(rcall)
    view = paul.sync(pcall)      # the hub is ahead: its version is installed, Paul's kept aside
    assert "version de Roger" in text(pbase, "incendie")
    assert any("en même temps" in n["texte"] for n in view["notes"])
    assert any("autre version de Paul" in (p / "SKILL.md").read_text("utf-8") for p in (pbase / skill_share.OLD).iterdir())

    # a shared group, renamed by anyone: the same for all
    db.library_groups("paul", "renommer", "Métier", "Métiers")
    assert roger.sync(rcall)["competences"][0]["groupe"] == "Métiers/Incendie"

    # Roger takes it back to his personal skills: it leaves the library; he keeps it, Paul puts it aside
    roger.unshare(rcall, rbase, "incendie")
    roger.sync(rcall)
    paul.sync(pcall)
    assert (rbase / "skills" / "incendie").is_dir() and not (pbase / "skills" / "incendie").exists()
    assert roger.held(rbase) == {} and paul.held(pbase) == {}
    assert db.library()["competences"][0]["retiree"] and not list((db.files / "bibliotheque").glob("*.zip"))
    # and can share it again later
    assert roger.publish(rcall, rbase, ["incendie"], {}, account=True) == ["incendie"]
    paul.sync(pcall)
    assert "version de Roger" in text(pbase, "incendie")


def test_the_library_refuses_a_name_already_shared(team, tmp_path):
    _, _, bridge, db = team
    roger, rcall, rbase = jarvis(tmp_path, bridge, "roger")
    paul, pcall, pbase = jarvis(tmp_path, bridge, "paul")
    skill(rbase, "mails", "Rédaction")
    skill(pbase, "mails", "Autre")
    roger.publish(rcall, rbase, ["mails"], {}, account=True)
    try:
        paul.publish(pcall, pbase, ["mails"], {}, account=True)
    except skill_library.HubError as exc:
        assert "déjà" in exc.message
    else:
        raise AssertionError("refus attendu")
    assert db.library()["competences"][0]["par"] == "roger"


def test_the_page_splits_personal_and_shared(team):
    app, console, bridge, db = team
    engine = app.state.engine
    prof = engine.cfg.profiles[0]
    base = engine._account_dir(prof)
    skill(base, "incendie", "Règles incendie")
    skill(base, "mails", "Rédaction")
    engine.classify_skill("incendie", "Métier", profile=prof.id)

    r = console.post("/api/hub/library/publish", json={"competences": ["incendie"], "groupes": {"incendie": "Équipe/Incendie"},
                                                        "profile": prof.id})
    assert r.status_code == 200, r.text
    assert r.json()["partagees"] == ["incendie"] and r.json()["bibliotheque"]["groupes"] == ["Équipe", "Équipe/Incendie"]
    sk = engine.skills(profile=prof.id)
    listed = {s["name"]: (s.get("partagee", False), s["groupe"]) for s in sk["compte"]}
    assert listed == {"incendie": (True, "Équipe/Incendie"), "mails": (False, "")}
    assert [c["nom"] for c in sk["bibliotheque"]["competences"]] == ["incendie"] and sk["bibliotheque"]["actif"]
    # already shared: refused
    assert console.post("/api/hub/library/publish", json={"competences": ["incendie"], "profile": prof.id}).status_code == 409

    # moved to another shared group, then a group made and dissolved
    assert console.post("/api/hub/library/classement", json={"competence": "incendie", "groupe": "Bureau"}).json()["groupes"] == ["Bureau"]
    assert console.post("/api/hub/library/groupes", json={"action": "creer", "groupe": "Vide"}).json()["groupes"] == ["Bureau", "Vide"]
    assert console.post("/api/hub/library/groupes", json={"action": "dissoudre", "groupe": "Vide"}).json()["groupes"] == ["Bureau"]

    # dragged back to the personal part, into a group: personal again, here
    r = console.post("/api/hub/library/unshare", json={"competence": "incendie", "groupe": "Métier", "profile": prof.id})
    assert r.status_code == 200, r.text and r.json()["bibliotheque"]["competences"] == []
    listed = {s["name"]: (s.get("partagee", False), s["groupe"]) for s in engine.skills(profile=prof.id)["compte"]}
    assert listed == {"incendie": (False, "Métier"), "mails": (False, "")}
    assert (Path(base) / "skills" / "incendie" / "SKILL.md").is_file()


def test_the_hub_library_alone(tmp_path):
    db = HubDB(tmp_path / "hub.db")
    row = db.library_put("roger", "x1", "A/B", "d", b"zip1", 0)
    assert row["rev"] == 1
    try:
        db.library_put("paul", "x1", "", "d", b"zip2", 0)
    except ValueError as exc:
        assert exc.args[0] == 1
    else:
        raise AssertionError("conflit attendu")
    assert db.library_put("paul", "x1", "A/B", "d", b"zip2", 1)["rev"] == 2
    assert db.library_zip("x1").read_bytes() == b"zip2" and len(list((db.files / "bibliotheque").glob("*.zip"))) == 1
    db.library_remove("anne", "x1")
    assert db.library_zip("x1") is None and db.library()["groupes"] == []
    assert db.library_put("anne", "x1", "", "d", b"zip3", 0)["rev"] == 4
    db.close()


def test_only_the_work_account_receives_them_unless_configured(team, tmp_path):
    _, _, bridge, db = team
    roger, rcall, rbase = jarvis(tmp_path, bridge, "roger")
    skill(rbase, "incendie", "Règles incendie")
    skill(rbase, "mails", "Rédaction")
    roger.publish(rcall, rbase, ["incendie", "mails"], {}, account=True)

    # Paul: two accounts, only the work one gets them
    work, perso = tmp_path / "paul" / "work", tmp_path / "paul" / "perso"
    work.mkdir(parents=True)
    perso.mkdir(parents=True)
    every = {"on": False}

    def call(method, path, **kw):
        bridge.who = "paul"
        return bridge(None, None, method, path, **kw)
    paul = skill_library.Library(Kv(), tmp_path / "paul", lambda: [("work", work), ("personal", perso)], lambda: None,
                                 lambda pid: every["on"] or pid == "work")
    paul.sync(call)
    assert sorted(paul.held(work)) == ["incendie", "mails"] and not (perso / "skills").exists()
    assert paul.covers(work) and not paul.covers(perso)

    # the option on: the other account too
    every["on"] = True
    paul.sync(call)
    assert sorted(paul.held(perso)) == ["incendie", "mails"]

    # off again: its copies go, the one changed there kept aside, and said
    edit(perso, "mails", "à ma façon")
    every["on"] = False
    view = paul.sync(call)
    assert not (perso / "skills" / "incendie").exists() and not (perso / "skills" / "mails").exists()
    old, = (perso / skill_share.OLD).iterdir()
    assert "à ma façon" in (old / "SKILL.md").read_text("utf-8")
    assert view["notes"][0]["texte"].startswith("Compte personal : il ne reçoit plus") and paul.held(perso) == {}
    assert sorted(paul.held(work)) == ["incendie", "mails"]


def test_the_page_of_an_account_that_does_not_receive_them(team):
    app, console, bridge, db = team
    engine = app.state.engine
    perso = engine.cfg.profile("personal")
    base = engine._account_dir(perso)
    base.mkdir(parents=True, exist_ok=True)
    skill(base, "devis", "Devis")
    lib = engine.skills(profile="personal")["bibliotheque"]
    assert lib["ici"] is False and lib["comptes"] == ["Travail"]
    r = console.post("/api/hub/library/publish", json={"competences": ["devis"], "profile": "personal"})
    assert r.status_code == 409 and "ne reçoit pas" in r.text
    engine._account_dir(engine.cfg.profile("work")).mkdir(parents=True, exist_ok=True)
    assert engine.skills(profile="work")["bibliotheque"]["ici"] is True
