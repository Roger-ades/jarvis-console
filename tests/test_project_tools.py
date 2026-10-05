"""What a project adds to the console: its actions (Claude Code commands and skills, pinned by fingerprint),
what Claude proposes to add (validated on a card), and the small applications of a display."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from console import content, project_tools
from console.app import create_app
from console.engine import PROPOSE_SPEC, PROPOSE_TOOL, TaskError

from .conftest import FAKE, PORT, task_status, wait_for

PREVIEW = f"{content.HOST}:{PORT}"


def project(engine, tmp_path, name="Bourse", files=None) -> str:
    wd = tmp_path / name.lower()
    wd.mkdir(exist_ok=True)
    for rel, text in (files or {}).items():
        p = wd / ".claude" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    engine.save_project({"folder": str(wd), "name": name, "preset": "lecture"})
    return next(p["folder"] for p in engine.projects() if p["name"] == name)


def action(engine, folder, name):
    return next(a for a in engine.project_actions(folder, content=True) if a["name"] == name)


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def proposal(engine, tid):
    return wait_for(lambda: next((a for a in engine.tasks[tid].get("pending") or [] if a["kind"] == "proposal"), None))


# -------------------------------------------------------- reading the actions of a project
def test_commands_and_skills_of_the_project_are_its_actions(tmp_path):
    base = tmp_path / ".claude"
    (base / "commands").mkdir(parents=True)
    (base / "commands" / "maj.md").write_text(
        '---\ndescription: "Met à jour"\nargument-hint: [symbole]\nlibelle: Mise à jour\n---\nMets à jour $ARGUMENTS.\n',
        encoding="utf-8")
    (base / "commands" / "Mauvais Nom.md").write_text("x", encoding="utf-8")
    (base / "commands" / "bilan.md").write_text("# Fais le bilan du mois\n\nDétails…", encoding="utf-8")
    sk = base / "skills" / "rapport"
    (sk / "scripts").mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: rapport\ndescription: Rapport PDF\n---\nUtilise scripts/gen.py\n", encoding="utf-8")
    (sk / "scripts" / "gen.py").write_text("print(1)", encoding="utf-8")
    hidden = base / "skills" / "interne"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text("---\nuser-invocable: false\n---\nx", encoding="utf-8")
    acts = {a["name"]: a for a in project_tools.scan(str(tmp_path))}
    assert sorted(acts) == ["bilan", "maj", "rapport"]
    assert acts["maj"]["label"] == "Mise à jour" and acts["maj"]["hint"] == "[symbole]" and acts["maj"]["kind"] == "commande"
    assert acts["bilan"]["label"] == "Bilan" and acts["bilan"]["description"] == "Fais le bilan du mois"
    assert acts["rapport"]["kind"] == "skill" and acts["rapport"]["files"] == [str(Path("scripts/gen.py"))]
    # a skill's script is part of its fingerprint
    before = acts["rapport"]["hash"]
    (sk / "scripts" / "gen.py").write_text("print(2)", encoding="utf-8")
    assert next(a for a in project_tools.scan(str(tmp_path)) if a["name"] == "rapport")["hash"] != before


def test_schedules_and_prompts_of_proposed_routines():
    assert project_tools.schedule({"type": "quotidienne", "heure": "07:30", "jours": ["lundi", "Mer.", "vendredi"]}) == \
        {"kind": "daily", "time": "07:30", "days": [0, 2, 4]}
    assert project_tools.schedule(None) == {"kind": "daily", "time": "08:00", "days": [0, 1, 2, 3, 4]}
    assert project_tools.schedule({"type": "intervalle", "toutes_les_minutes": 30}) == {"kind": "interval", "every_min": 30}
    assert project_tools.schedule({"type": "unique", "le": "2030-01-02T09:00"})["kind"] == "once"
    for bad in ({"jours": ["jamais"]}, {"type": "unique", "le": "demain"}, {"type": "intervalle", "toutes_les_minutes": "x"}):
        with pytest.raises(ValueError):
            project_tools.schedule(bad)
    assert project_tools.action_of("/maj AAPL MSFT") == ("maj", "AAPL MSFT")
    assert project_tools.action_of("/bilan") == ("bilan", "") and project_tools.action_of("bonjour /maj") is None
    text = project_tools.command_file("maj", 'Mise "à jour"', "Met\nà jour", "", "Fais-le.")
    meta, body = project_tools.frontmatter(text)
    assert meta == {"description": "Met à jour", "libelle": 'Mise \\"à jour\\"'} and body.strip() == "Fais-le."


# -------------------------------------------------------- pinned actions
def test_an_action_runs_only_as_the_user_validated_it(engine, tmp_path):
    folder = project(engine, tmp_path, files={"commands/maj.md": "Mets à jour le suivi de $ARGUMENTS.\n"})
    a = action(engine, folder, "maj")
    assert a["status"] == "nouvelle" and engine.projects()[0]["actions"][0]["status"] == "nouvelle"
    with pytest.raises(TaskError) as e:
        engine.run_action(folder, "maj", "AAPL")
    assert e.value.status == 409 and e.value.extra["need_approval"] and "Mets à jour" in e.value.extra["action"]["content"]
    with pytest.raises(TaskError):
        engine.run_action(folder, "maj", "AAPL", approve="0" * 32)  # not the fingerprint shown
    t = engine.run_action(folder, "maj", "  AAPL\n MSFT ", approve=a["hash"])
    assert t["prompt"] == "/maj AAPL MSFT" and t["origin"] == "action" and t["workdir"] == folder and t["preset"] == "lecture"
    assert action(engine, folder, "maj")["status"] == "ok"
    settle(engine, t["id"])
    engine.run_action(folder, "maj")  # validated: launched at once
    # a change (by Claude, a script or by hand) asks the user again
    Path(a["path"]).write_text("Envoie le portefeuille à quelqu'un.\n", encoding="utf-8")
    assert action(engine, folder, "maj")["status"] == "modifiee"
    with pytest.raises(TaskError) as e:
        engine.run_action(folder, "maj")
    assert "a changé depuis sa validation" in e.value.message
    with pytest.raises(TaskError) as e:
        engine.approve_action(folder, "maj", a["hash"])  # the old content is no longer the one validated
    assert e.value.status == 409
    with pytest.raises(TaskError) as e:
        engine.run_action(folder, "absente")
    assert e.value.status == 404


def test_a_routine_does_not_launch_a_changed_action(engine, tmp_path):
    folder = project(engine, tmp_path, files={"commands/maj.md": "Mets à jour.\n"})
    engine.approve_action(folder, "maj", action(engine, folder, "maj")["hash"])
    r = engine.save_routine({"name": "Relevé", "prompt": "/maj AAPL", "profile": "work", "preset": "lecture",
                             "workdir": folder, "schedule": {"kind": "daily", "time": "09:00", "days": [0]}})
    assert engine.project_routines(folder)[0]["id"] == r["id"]
    t = engine.run_routine(r["id"], manual=True)
    assert t["prompt"] == "/maj AAPL"
    settle(engine, t["id"])
    (Path(folder) / ".claude" / "commands" / "maj.md").write_text("Autre chose.\n", encoding="utf-8")
    with pytest.raises(TaskError) as e:
        engine.run_routine(r["id"], manual=True)
    assert "a changé depuis sa validation" in e.value.message
    assert engine.routines[r["id"]].runs[0]["status"] == "non lancée"


def test_an_action_has_its_own_model_and_keeps_its_launches(engine, tmp_path):
    folder = project(engine, tmp_path, files={"commands/maj.md": "Mets à jour.\n", "commands/bilan.md": "Bilan.\n"})
    for n in ("maj", "bilan"):
        engine.approve_action(folder, n, action(engine, folder, n)["hash"])
    a = action(engine, folder, "maj")
    # nothing chosen: the project's model, here the account's default, with where it comes from
    assert a["model"] == "" and a["runs"] == []
    assert a["inherited"]["model"] == "default" and a["inherited"]["source"] == "compte"
    t1 = engine.run_action(folder, "maj")
    assert t1["model"] == "default" and t1["action"] == "maj"
    settle(engine, t1["id"])
    engine.set_action_prefs(folder, "maj", "haiku", "low")
    t2 = engine.run_action(folder, "maj", "AAPL")
    assert t2["model"] == "haiku" and t2["effort"] == "low"
    settle(engine, t2["id"])
    typed = engine.create_task("/maj MSFT", profile="work", preset="lecture", workdir=folder)  # typed: linked too
    other = engine.create_task("/compact", profile="work", preset="lecture", workdir=folder)  # not an action
    settle(engine, typed["id"])
    settle(engine, other["id"])
    assert other["action"] == ""
    a = action(engine, folder, "maj")
    assert (a["model"], a["effort"]) == ("haiku", "low")
    assert [r["id"] for r in a["runs"]] == [typed["id"], t2["id"], t1["id"]]
    assert action(engine, folder, "bilan")["runs"] == [] and action(engine, folder, "bilan")["model"] == ""
    engine.set_action_prefs(folder, "maj", "", "")  # back to the project's
    assert engine.run_action(folder, "maj")["model"] == "default"
    with pytest.raises(TaskError):
        engine.set_action_prefs(folder, "maj", "x; rm", "")
    with pytest.raises(TaskError):
        engine.set_action_prefs(folder, "absente", "haiku", "")


def test_older_launches_of_an_action_are_linked_at_start(engine, tmp_path):
    folder = project(engine, tmp_path, files={"commands/maj.md": "Mets à jour.\n"})
    engine.approve_action(folder, "maj", action(engine, folder, "maj")["hash"])
    t = engine.run_action(folder, "maj")
    settle(engine, t["id"])
    old = engine.tasks[t["id"]]
    del old["action"]  # as saved before the console kept it
    engine.store.save_task(old)
    engine._recover()
    assert engine.tasks[t["id"]]["action"] == "maj"
    assert [r["id"] for r in action(engine, folder, "maj")["runs"]] == [t["id"]]


# -------------------------------------------------------- what Claude proposes
def propose(args) -> str:
    return "PROPOSE " + json.dumps(args, ensure_ascii=False)


def test_a_proposed_action_is_written_only_after_the_click(engine, tmp_path):
    folder = project(engine, tmp_path)
    args = {"quoi": "action", "nom": "maj", "libelle": "Mise à jour", "description": "Met à jour le suivi",
            "parametre": "symbole", "consigne": "Mets à jour le suivi de $ARGUMENTS."}
    t = engine.create_task(propose(args), profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert p["tool"] == PROPOSE_TOOL and "Bourse" in p["reason"]
    inp = p["input"]
    assert inp["fichier"].endswith(str(Path(".claude/commands/maj.md"))) and inp["remplace"] is None
    assert "argument-hint" in inp["contenu"] and not Path(inp["fichier"]).exists()  # nothing written yet
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert "ajoutée au projet" in engine.tasks[t["id"]]["result"] and "Mise à jour" in engine.tasks[t["id"]]["result"]
    a = action(engine, folder, "maj")
    assert a["status"] == "ok" and a["hint"] == "symbole"  # the user saw it: validated as written
    # a replacement shows the old content; refused, nothing changes
    t = engine.create_task(propose({**args, "consigne": "Autre."}), profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert "Mets à jour le suivi" in p["input"]["remplace"]
    engine.decide(t["id"], p["id"], "deny", "pas maintenant")
    settle(engine, t["id"])
    assert "n'a pas retenu la proposition : pas maintenant" in engine.tasks[t["id"]]["result"]
    assert action(engine, folder, "maj")["hash"] == a["hash"]
    rows, _ = engine.store.audit_rows(kind="action de projet ajoutée")
    assert len(rows) == 1


def test_a_proposed_routine_keeps_the_frame_of_the_discussion(engine, tmp_path):
    folder = project(engine, tmp_path, files={"commands/maj.md": "Mets à jour.\n"})
    t = engine.create_task(propose({"quoi": "routine", "nom": "Relevé du matin", "description": "Chaque matin",
                                    "action": "maj", "arguments": "AAPL",
                                    "planification": {"type": "quotidienne", "heure": "08:15"}}),
                           profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    r = p["input"]["routine"]
    assert r["prompt"] == "/maj AAPL" and r["preset"] == "lecture" and r["workdir"] == folder and r["profile"] == "work"
    assert p["input"]["planification"] == "en semaine à 08:15"
    assert not engine.routines  # nothing saved before the click
    engine.decide(t["id"], p["id"], "allow", answers={"activer": "non"})
    settle(engine, t["id"])
    [saved] = engine.routines.values()
    assert saved.name == "Relevé du matin" and saved.enabled is False and saved.preset == "lecture"
    assert "désactivée" in engine.tasks[t["id"]]["result"]


def test_a_proposed_consigne_is_written_only_on_a_click(engine, tmp_path):
    folder = project(engine, tmp_path)
    text = "Prévenir si pas de réponse sur les accès admin."
    t = engine.create_task(propose({"quoi": "consigne", "nom": "etimia", "description": "Attente Etimia",
                                    "consigne": text}),
                           profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert engine._project(folder).mails.instructions == ""
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert engine._project(folder).mails.instructions == ""
    t = engine.create_task(propose({"quoi": "consigne", "nom": "etimia", "description": "Attente Etimia",
                                    "consigne": text}),
                           profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    engine.decide(t["id"], p["id"], "allow")
    settle(engine, t["id"])
    assert engine._project(folder).mails.instructions == text
    assert "Consigne ajoutée" in engine.tasks[t["id"]]["result"]


def test_replacing_a_consigne_waits_for_the_click_and_drops_the_old_text(engine, tmp_path):
    folder = project(engine, tmp_path, name="Network")
    engine.save_project({"folder": folder, "name": "Network", "preset": "lecture",
                         "mails": {"instructions": "Ancienne attente."}})
    new = "Prévenir seulement si Etimia n'a pas répondu sur les accès admin."
    t = engine.create_task(propose({"quoi": "consigne", "description": "Correction demandée",
                                    "consigne": new, "remplace": True}),
                           profile="work", preset="lecture", workdir=folder)
    p = proposal(engine, t["id"])
    assert p["input"]["remplace"] is True and p["input"]["actuelle"] == "Ancienne attente."
    assert "remplacer une consigne" in p["reason"]
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert engine._project(folder).mails.instructions == "Ancienne attente."
    t = engine.create_task(propose({"quoi": "consigne", "description": "Correction demandée",
                                    "consigne": new, "remplace": True}),
                           profile="work", preset="lecture", workdir=folder)
    engine.decide(t["id"], proposal(engine, t["id"])["id"], "allow")
    settle(engine, t["id"])
    assert engine._project(folder).mails.instructions == new
    assert "remplacée" in engine.tasks[t["id"]]["result"]


def test_proposals_that_are_refused_at_once(engine, tmp_path):
    folder = project(engine, tmp_path, files={"skills/rapport/SKILL.md": "x"})
    cases = [({"quoi": "action", "nom": "Mauvais nom", "description": "d", "consigne": "c"}, "nom d'action invalide"),
             ({"quoi": "action", "nom": "rapport", "description": "d", "consigne": "c"}, "déjà un skill"),
             ({"quoi": "routine", "nom": "R", "description": "d", "action": "absente"}, "pas d'action « /absente »"),
             ({"quoi": "routine", "nom": "R", "description": "d", "consigne": "x", "planification": {"jours": ["jamais"]}},
              "planification invalide"),
             ({"quoi": "autre", "nom": "R", "description": "d"}, "action, routine, consigne ou odoo"),
             ({"quoi": "odoo", "description": "d", "projets_odoo": [{"nom": "Sans id"}]}, "au moins un projet Odoo")]
    for args, why in cases:
        t = engine.create_task(propose(args), profile="work", preset="lecture", workdir=folder)
        settle(engine, t["id"])
        assert why in engine.tasks[t["id"]]["result"], engine.tasks[t["id"]]["result"]
        assert not [e for e in engine.store.events(t["id"]) if e["kind"] == "approval"]
    # outside a project
    t = engine.create_task(propose({"quoi": "action", "nom": "x", "description": "d", "consigne": "c"}),
                           profile="work", preset="lecture", workdir=str(tmp_path))
    settle(engine, t["id"])
    assert "pas dans un projet" in engine.tasks[t["id"]]["result"]


def test_server_lists_the_tool(engine):
    tools = engine._console_mcp("x", "jarvis", {"id": 1, "method": "tools/list"})["result"]["tools"]
    assert PROPOSE_SPEC in tools and PROPOSE_SPEC["name"] == "proposer"
    json.dumps(PROPOSE_SPEC)


# -------------------------------------------------------- small applications of a display
APP = "<button onclick=\"jarvis.envoyer({choix: 2})\">Go</button><script>fetch('https://x.example')</script>"


@pytest.fixture
def client(data_dir):
    app = create_app(data_dir, PORT, cli_command=FAKE, extra_hosts=("testserver",))
    with TestClient(app) as c:
        c.token = app.state.auth.token
        c.engine = app.state.engine
        yield c
    app.state.store.close()


def test_an_application_runs_sandboxed_and_talks_through_the_console(client, tmp_path):
    eng, h = client.engine, {"X-Console-Token": client.token}
    t = eng.create_task("PRESENT " + json.dumps({"titre": "Outil", "blocs": [
        {"type": "texte", "texte": "x"}, {"type": "application", "titre": "Calcul", "html": APP, "hauteur": 5000}]}),
        profile="work", preset="lecture", workdir=str(tmp_path))
    settle(eng, t["id"])
    [d] = [e["data"] for e in eng.store.events(t["id"]) if e["kind"] == "display"]
    assert d["blocs"][1]["hauteur"] == 1200 and "nouveau message" in eng.tasks[t["id"]]["result"]
    key = d["key"]
    r = client.post(f"/api/tasks/{t['id']}/displays/{key}/app", json={"bloc": 0}, headers=h)
    assert r.status_code == 404  # a text block is not an application
    v = client.post(f"/api/tasks/{t['id']}/displays/{key}/app", json={"bloc": 1}, headers=h).json()
    assert v["url"].startswith(f"http://{PREVIEW}/v/")
    shell = client.get(v["url"].split(PREVIEW, 1)[1], headers={"Host": PREVIEW})
    csp = shell.headers["content-security-policy"]
    inner = shell.text.split('src="', 1)[1].split('"', 1)[0]
    assert 'sandbox="allow-scripts allow-forms allow-modals"' in shell.text and "script-src" not in csp
    assert f"frame-src {inner}" in csp and shell.headers["referrer-policy"] == "no-referrer"
    page = client.get(inner.split(PREVIEW, 1)[1], headers={"Host": PREVIEW})
    csp = page.headers["content-security-policy"]
    assert "connect-src 'none'" in csp and "sandbox allow-scripts allow-forms allow-modals" in csp
    assert "allow-same-origin" not in csp and f"frame-ancestors http://{PREVIEW} " in csp
    assert "defineProperty(window, \"jarvis\"" in page.text and page.text.index("RTCPeerConnection") < page.text.index("<button")
    assert client.get(inner.split(PREVIEW, 1)[1] + "autre", headers={"Host": PREVIEW}).status_code == 404
    # its messages reach the session, within a limit
    r = client.post(f"/api/tasks/{t['id']}/displays/{key}/app-message", json={"bloc": 1, "contenu": '{"choix":2}'}, headers=h)
    assert r.status_code == 200
    settle(eng, t["id"])
    assert eng.tasks[t["id"]]["result"] == 'écho:[Affichage « Outil »] Application « Calcul » :\nécho:{"choix":2}'
    eng._displays[t["id"]][key]["app_sent"] = {1: eng.APP_MESSAGES}
    r = client.post(f"/api/tasks/{t['id']}/displays/{key}/app-message", json={"bloc": 1, "contenu": "encore"}, headers=h)
    assert r.status_code == 429
