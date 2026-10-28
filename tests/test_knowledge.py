"""The tool "retenir": what the user knows for all his projects, in the skills of his account, written only
after his click on the differences."""
import json
from pathlib import Path

import pytest

from console import knowledge
from console.engine import KNOWLEDGE_SPEC, KNOWLEDGE_TOOL

from .conftest import task_status, wait_for

SKILL = "---\nname: incendie\ndescription: Dossiers de sécurité incendie\n---\n\n# Méthode\n\n- Lister les hypothèses.\n"


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def proposal(engine, tid):
    return wait_for(lambda: next((a for a in engine.tasks[tid].get("pending") or [] if a["kind"] == "proposal"), None))


def call(engine, args) -> dict:
    return engine.create_task("RETENIR " + json.dumps(args, ensure_ascii=False), profile="work", preset="lecture")


def base(engine) -> Path:
    return Path(engine.cfg.profile("work").config_dir)


def skill(engine, files=None) -> Path:
    d = base(engine) / "skills" / "incendie"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in {"SKILL.md": SKILL, "lecons.md": "# Leçons\n", **(files or {})}.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


# ------------------------------------------------------------ the module alone
def test_proposals_are_checked(tmp_path):
    d = tmp_path / "skills" / "incendie"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    for bad, why in (({"competence": "synced", "texte": "x"}, "synced"),
                     ({"competence": "../x", "texte": "x"}, "invalide"),
                     ({"competence": "incendie", "fichier": "../a.md", "texte": "x"}, "fichier invalide"),
                     ({"competence": "incendie", "fichier": "a.py", "texte": "x"}, "fichier invalide"),
                     ({"competence": "incendie"}, "texte"),
                     ({"competence": "incendie", "texte": "Lister les hypothèses."}, "déjà"),
                     ({"competence": "incendie", "mode": "remplacer", "texte": "# Sans en-tête"}, "en-tête"),
                     ({"competence": "neuve", "texte": "x"}, "description"),
                     ({"competence": "neuve", "fichier": "a.md", "texte": "x", "description": "d"}, "SKILL.md")):
        with pytest.raises(knowledge.KnowledgeError, match=why):
            knowledge.proposal(tmp_path, bad)
    prop, after = knowledge.proposal(tmp_path, {"competence": "incendie", "texte": "- Citer l'édition des normes."})
    assert after.endswith("- Lister les hypothèses.\n\n- Citer l'édition des normes.\n")
    assert prop["ajouts"] >= 1 and prop["retraits"] == 0 and not prop["nouvelle"]
    assert any(r["k"] == "+" and "édition" in r["t"] for r in prop["diff"]["lines"])
    # a new skill gets its header
    prop, after = knowledge.proposal(tmp_path, {"competence": "neuve", "texte": "# Règles", "description": "Quand x"})
    assert prop["nouvelle"] and after.startswith('---\nname: neuve\ndescription: "Quand x"\n---')
    # a long file: the card stays small
    big = "\n".join(f"ligne {n} " + "x" * 80 for n in range(200))
    prop, _ = knowledge.proposal(tmp_path, {"competence": "incendie", "fichier": "gros.md", "texte": big})
    assert prop["diff"]["truncated"] and len(json.dumps(prop, ensure_ascii=False)) < 20000 and prop["ajouts"] == 200


# ------------------------------------------------------------ through a discussion
def test_server_lists_the_tool(engine):
    tools = engine._console_mcp("x", "jarvis", {"id": 1, "method": "tools/list"})["result"]["tools"]
    assert KNOWLEDGE_SPEC in tools
    json.dumps(KNOWLEDGE_SPEC)


def test_claude_lists_and_reads_the_skills_of_the_account(engine):
    t = call(engine, {"action": "lister"})
    settle(engine, t["id"])
    assert "Aucune compétence" in engine.tasks[t["id"]]["result"]
    skill(engine)
    t = call(engine, {"action": "lister"})
    settle(engine, t["id"])
    assert "incendie [hors groupe] : Dossiers de sécurité incendie" in engine.tasks[t["id"]]["result"]
    assert "lecons.md" in engine.tasks[t["id"]]["result"]
    t = call(engine, {"action": "lire", "competence": "incendie", "fichier": "lecons.md"})
    settle(engine, t["id"])
    assert "# Leçons" in engine.tasks[t["id"]]["result"]
    t = call(engine, {"action": "lire", "competence": "incendie", "fichier": "absent.md"})
    settle(engine, t["id"])
    assert "lecons.md" in engine.tasks[t["id"]]["result"] and "Pas de fichier" in engine.tasks[t["id"]]["result"]


def test_a_lesson_is_written_only_after_the_click(engine):
    d = skill(engine)
    args = {"action": "proposer", "competence": "incendie", "fichier": "lecons.md",
            "texte": "## 2026-10-13 — Faux plafonds\n- Règle retenue : vérifier les plénums.",
            "raison": "Roger a demandé de retenir la vérification des plénums."}
    t = call(engine, args)
    p = proposal(engine, t["id"])
    assert p["tool"] == KNOWLEDGE_TOOL and "tous les projets" in p["reason"]
    assert p["input"]["quoi"] == "savoir" and p["input"]["fichier"] == "lecons.md"
    assert (d / "lecons.md").read_text(encoding="utf-8") == "# Leçons\n"  # nothing written yet
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "complété" in engine.tasks[t["id"]]["result"]
    assert "vérifier les plénums" in (d / "lecons.md").read_text(encoding="utf-8")
    rows, _ = engine.store.audit_rows(kind="savoir du compte")
    assert len(rows) == 1
    # refused: nothing changes
    t = call(engine, {**args, "texte": "Autre leçon."})
    p = proposal(engine, t["id"])
    engine.decide(t["id"], p["id"], "deny", "pas utile")
    settle(engine, t["id"])
    assert "n'a pas retenu la proposition : pas utile" in engine.tasks[t["id"]]["result"]
    assert "Autre leçon" not in (d / "lecons.md").read_text(encoding="utf-8")


def test_a_file_changed_meanwhile_is_not_overwritten(engine):
    d = skill(engine)
    t = call(engine, {"action": "proposer", "competence": "incendie", "texte": "- Nouvelle règle."})
    p = proposal(engine, t["id"])
    (d / "SKILL.md").write_text(SKILL + "- Ajout à la main.\n", encoding="utf-8")
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "a changé pendant la proposition" in engine.tasks[t["id"]]["result"]
    assert "Nouvelle règle" not in (d / "SKILL.md").read_text(encoding="utf-8")


def test_a_new_skill_is_created(engine):
    t = call(engine, {"action": "proposer", "competence": "desenfumage", "description": "Désenfumage des ERP",
                      "texte": "# Méthode\n\n- Vérifier les amenées d'air."})
    p = proposal(engine, t["id"])
    assert p["input"]["nouvelle"]
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    text = (base(engine) / "skills" / "desenfumage" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: desenfumage\n") and "amenées d'air" in text


def test_the_skills_of_the_account_are_not_its_actions(engine):
    prof = engine.cfg.profile("work")
    prof.account_actions = True
    skill(engine)
    assert engine.account_actions(prof.id) == []
    assert [s["name"] for s in engine.skills(profile=prof.id)["compte"]] == ["incendie"]


# ------------------------------------------------------------ the page Compétences and its discussion
def project(engine, tmp_path) -> Path:
    folder = tmp_path / "work" / "work" / "Bourse"
    folder.mkdir(parents=True)
    engine.save_project({"folder": str(folder), "name": "Bourse", "profile": "work"})
    return Path(engine._project(str(folder)).folder)


def test_the_workshop_of_a_project_writes_its_skills(engine, tmp_path):
    folder = project(engine, tmp_path)
    args = {"action": "proposer", "competence": "cotations", "description": "Cotations de la bourse",
            "texte": "# Méthode\n\n- Citer la source."}
    t = engine.skills_workshop("RETENIR " + json.dumps(args, ensure_ascii=False), folder=str(folder),
                               competence="cotations")
    assert t["origin"] == "atelier" and t["title"] == "Compétence « cotations »"
    assert t["atelier"] == {"cible": "projet", "dossier": str(folder), "competence": "cotations"}
    p = proposal(engine, t["id"])
    assert "du projet « Bourse »" in p["reason"] and "tous les projets" not in p["reason"]
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "dans ce projet" in engine.tasks[t["id"]]["result"]
    assert (folder / ".claude" / "skills" / "cotations" / "SKILL.md").is_file()
    assert not (base(engine) / "skills" / "cotations").exists()
    assert [s["name"] for s in engine.skills(str(folder))["projet"]] == ["cotations"]


def test_the_workshop_never_goes_to_haiku_by_itself(engine):
    engine.cfg.general.auto_model = True
    engine.cfg.profile("work").default_model = "opus"
    short = "Relis la compétence et dis-moi ce que tu améliorerais."
    assert engine.create_task(short, profile="work", preset="lecture")["model"] == "haiku"  # elsewhere, yes
    t = engine.skills_workshop(short, profile="work")
    assert t["model"] == "opus"
    t = engine.skills_workshop(short, profile="work", model="sonnet")
    assert t["model"] == "sonnet"
    engine.cfg.profile("work").default_model = "default"
    assert engine.skills_workshop(short, profile="work", model="auto")["model"] == "default"


def test_the_workshop_of_the_account(engine):
    skill(engine)
    t = engine.skills_workshop("RETENIR " + json.dumps({"action": "lister"}), profile="work")
    settle(engine, t["id"])
    assert t["atelier"]["cible"] == "compte" and t["title"] == "Nouvelle compétence"
    assert "Compétences du compte Travail" in engine.tasks[t["id"]]["result"] or \
        "Compétences du compte" in engine.tasks[t["id"]]["result"]
    block = knowledge.workshop("du compte Travail", base(engine), "incendie")
    assert "« incendie »" in block and str(base(engine) / "skills") in block
    with pytest.raises(Exception, match="Projet introuvable"):
        engine.skills_workshop("x", folder=str(base(engine)))


def test_a_file_of_a_skill_is_read_inside_it_only(engine):
    skill(engine, {"normes.md": "# Normes\n"})
    assert engine.skill_file("incendie", "normes.md", profile="work")["text"].strip() == "# Normes"
    for bad in ("../incendie/SKILL.md/..", "../../settings.json", "absent.md"):
        with pytest.raises(Exception, match="introuvable"):
            engine.skill_file("incendie", bad, profile="work")
    with pytest.raises(Exception, match="Compétence introuvable"):
        engine.skill_file("autre", "SKILL.md", profile="work")
    entry = engine.skills(profile="work")["compte"][0]
    assert entry["folder"] == "incendie" and entry["modified"] > 0 and "normes.md" in entry["files"]


# ------------------------------------------------------------ the groups of the page Compétences
def test_skills_are_put_in_groups_without_touching_them(engine, tmp_path):
    d = skill(engine)
    before = (d / "SKILL.md").read_text(encoding="utf-8")
    assert engine.group_path(" Métier /  Incendie › Normes\\x ") == "Métier/Incendie/Normes/x"
    with pytest.raises(Exception, match="Quatre niveaux"):
        engine.group_path("a/b/c/d/e")
    g = engine.classify_skill("incendie", "Métier / Incendie", profile="work")
    assert g["classement"] == {"incendie": "Métier/Incendie"} and g["groupes"] == ["Métier", "Métier/Incendie"]
    sk = engine.skills(profile="work")
    assert sk["compte"][0]["groupe"] == "Métier/Incendie" and sk["groupes"]["compte"] == ["Métier", "Métier/Incendie"]
    assert (d / "SKILL.md").read_text(encoding="utf-8") == before and not (base(engine) / "skills" / "Métier").exists()
    with pytest.raises(Exception, match="introuvable"):
        engine.classify_skill("absente", "x", profile="work")
    # an empty group is kept; a group moves with what it holds; dissolved, its content goes up one level
    engine.edit_skill_group("creer", "Bureau", profile="work")
    g = engine.edit_skill_group("renommer", "Métier", "Bureau/Métier", profile="work")
    assert g["classement"]["incendie"] == "Bureau/Métier/Incendie" and "Métier" not in g["groupes"]
    with pytest.raises(Exception, match="lui-même"):
        engine.edit_skill_group("renommer", "Bureau", "Bureau/Sous", profile="work")
    g = engine.edit_skill_group("dissoudre", "Bureau/Métier", profile="work")
    assert g["classement"]["incendie"] == "Bureau/Incendie" and g["groupes"] == ["Bureau", "Bureau/Incendie"]
    # the groups of a project are its own
    folder = project(engine, tmp_path)
    assert engine.skills(str(folder))["groupes"]["projet"] == []
    g = engine.classify_skill("incendie", "", profile="work")
    assert g["classement"] == {} and g["groupes"] == ["Bureau", "Bureau/Incendie"]


def test_claude_sees_the_groups_and_proposes_one(engine):
    d = skill(engine)
    engine.classify_skill("incendie", "Métier", profile="work")
    t = call(engine, {"action": "lister"})
    settle(engine, t["id"])
    assert "incendie [groupe Métier]" in engine.tasks[t["id"]]["result"]
    assert "Groupes existants : Métier." in engine.tasks[t["id"]]["result"]
    # a new skill with its group: written and put in it on the same click
    t = call(engine, {"action": "proposer", "competence": "desenfumage", "description": "Désenfumage des ERP",
                      "texte": "# Méthode", "groupe": "Métier / Incendie"})
    p = proposal(engine, t["id"])
    assert p["input"]["groupe"] == "Métier/Incendie" and p["input"]["groupe_avant"] == ""
    assert engine.skills(profile="work")["groupes"]["compte"] == ["Métier"]  # nothing before the click
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "rangée dans le groupe Métier/Incendie" in engine.tasks[t["id"]]["result"]
    assert engine._skill_groups("compte:work")["classement"] == {"incendie": "Métier", "desenfumage": "Métier/Incendie"}
    # the group it already has, or none given: the card says nothing about it
    for extra in ({"groupe": "Métier"}, {}):
        t = call(engine, {"action": "proposer", "competence": "incendie", "texte": f"- Règle {len(extra)}.", **extra})
        assert "groupe" not in proposal(engine, t["id"])["input"]
    assert (d / "SKILL.md").is_file()


def test_claude_proposes_to_sort_the_skills(engine):
    skill(engine)
    (base(engine) / "skills" / "mails").mkdir()
    (base(engine) / "skills" / "mails" / "SKILL.md").write_text(
        "---\nname: mails\ndescription: Mails\n---\n", encoding="utf-8")
    for bad, why in (({"classement": []}, "requis"),
                     ({"classement": [{"competence": "absente", "groupe": "x"}]}, "pas de compétence"),
                     ({"classement": [{"competence": "mails", "groupe": "a/b/c/d/e"}]}, "quatre niveaux"),
                     ({"classement": [{"competence": "mails", "groupe": ""}]}, "déjà")):
        t = call(engine, {"action": "classer", **bad})
        settle(engine, t["id"])
        assert why in engine.tasks[t["id"]]["result"]
    args = {"action": "classer", "raison": "Ranger par métier.",
            "classement": [{"competence": "incendie", "groupe": "Métier › Incendie"},
                           {"competence": "mails", "groupe": "Bureau"}]}
    t = call(engine, args)
    p = proposal(engine, t["id"])
    x = p["input"]
    assert x["quoi"] == "classement" and x["nouveaux_groupes"] == ["Bureau", "Métier/Incendie"]
    assert x["lignes"] == [{"competence": "mails", "avant": "", "apres": "Bureau"},
                           {"competence": "incendie", "avant": "", "apres": "Métier/Incendie"}]
    assert engine._skill_groups("compte:work")["classement"] == {}  # nothing before the click
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "2 compétences rangées" in engine.tasks[t["id"]]["result"]
    assert engine._skill_groups("compte:work")["classement"] == {"incendie": "Métier/Incendie", "mails": "Bureau"}
    assert len(engine.store.audit_rows(kind="classement des compétences")[0]) == 1
    # refused: nothing moves
    t = call(engine, {"action": "classer", "classement": [{"competence": "mails", "groupe": ""}]})
    p = proposal(engine, t["id"])
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert engine._skill_groups("compte:work")["classement"]["mails"] == "Bureau"


def test_the_workshop_of_a_project_sorts_its_own_skills(engine, tmp_path):
    folder = project(engine, tmp_path)
    d = folder / ".claude" / "skills" / "cotations"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: cotations\ndescription: Cotations\n---\n", encoding="utf-8")
    args = {"action": "classer", "classement": [{"competence": "cotations", "groupe": "Bourse"}]}
    t = engine.skills_workshop("RETENIR " + json.dumps(args, ensure_ascii=False), folder=str(folder))
    p = proposal(engine, t["id"])
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    sk = engine.skills(str(folder))
    assert sk["projet"][0]["groupe"] == "Bourse" and sk["groupes"]["compte"] == []
