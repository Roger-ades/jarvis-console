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
    assert "incendie : Dossiers de sécurité incendie" in engine.tasks[t["id"]]["result"]
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


def test_a_validated_account_action_stays_validated(engine):
    prof = engine.cfg.profile("work")
    prof.account_actions = True
    skill(engine)
    a = next(x for x in engine.account_actions(prof.id) if x["name"] == "incendie")
    engine.approve_account_action(prof.id, "incendie", a["hash"])
    t = call(engine, {"action": "proposer", "competence": "incendie", "fichier": "lecons.md", "texte": "- Une leçon."})
    p = proposal(engine, t["id"])
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    a = next(x for x in engine.account_actions(prof.id) if x["name"] == "incendie")
    assert a["status"] == "ok"
