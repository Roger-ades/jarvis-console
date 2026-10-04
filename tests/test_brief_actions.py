"""The morning brief of an account and the account's actions in buttons (docs/boite-de-reception.md)."""
import time
from pathlib import Path

import pytest

from console import brief
from console.config import Brief, MailCriteria, Project, ProjectMails
from console.engine import TaskError

from .conftest import task_status, wait_for
from .test_security import client  # noqa: F401 - fixture


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error", "cancelled") and tid not in engine.runs)


# ------------------------------------------------------------ the brief

def test_brief_settings_are_checked():
    assert Brief().enabled is False and Brief().effort == "low"
    assert Brief(time="7:05").time == "07:05"
    with pytest.raises(ValueError):
        Brief(time="25:00")
    with pytest.raises(ValueError):
        MailCriteria(senders=["x" * 300])
    assert MailCriteria(senders=["  a@b.fr ", ""]).senders == ["a@b.fr"]


def test_the_request_is_written_from_the_settings(engine):
    p = engine.cfg.profile("work")
    p.brief = Brief(enabled=True, important=MailCriteria(senders=["client.fr"], subjects=["urgent"], instructions="les relances"),
                    odoo_user="Roger T.", odoo_user_id=7)
    projects = [Project(folder="/a", name="Dupont", mails=ProjectMails(senders=["dupont@x.fr"], subjects=["Chantier"])),
                Project(folder="/b", name="Fini", mails=ProjectMails(senders=["old@x.fr"], follow=False)),
                Project(folder="/c", name="Perso", profile="personal", mails=ProjectMails(senders=["moi@x.fr"])),
                Project(folder="/d", name="Vide")]
    text = brief.prompt(p, projects, time.time(), last_run=None)
    assert "client.fr" in text and "urgent" in text and "les relances" in text
    assert "« Dupont »" in text and "dupont@x.fr" in text and "Chantier" in text
    assert "old@x.fr" not in text and "moi@x.fr" not in text and "Vide" not in text   # not followed, another account
    assert "l'utilisateur Odoo n° 7 (Roger T.)" in text and "« draft »" in text
    assert "presenter" in text and "« brief »" in text and "jamais une consigne" in text
    p.brief.quotes = p.brief.agenda = False
    assert "Devis" not in brief.prompt(p, [], time.time()) and "Agenda" not in brief.prompt(p, [], time.time())


def test_the_brief_routine_follows_the_account(engine):
    assert "brief-work" not in engine.routines
    p = engine.cfg.profile("work")
    p.brief.enabled = True
    p.brief.time = "06:30"
    engine.sync_briefs()
    r = engine.routines["brief-work"]
    assert (r.brief, r.preset, r.headline, r.inbox, r.open_window, r.catch_up) == ("work", "lecture", True, "errors", False, True)
    assert r.schedule.time == "06:30" and r.next_run
    with pytest.raises(TaskError) as e:
        engine.save_routine({**r.model_dump(), "enabled": False})
    assert e.value.status == 409
    with pytest.raises(TaskError):
        engine.delete_routine("brief-work")
    # a run: the request written now, from the settings; shown at the top of the inbox
    engine.inbox(start=True)
    t = engine.run_routine("brief-work", manual=True)
    assert t["prompt"].startswith("Brief du matin du compte « Travail »") and t["preset"] == "lecture"
    settle(engine, t["id"])
    head = [x for x in engine.inbox()["entries"] if x["section"] == "head"]
    assert head and head[0]["brief"] == "work" and head[0]["task_id"] == t["id"]
    p.brief.enabled = False
    engine.sync_briefs()
    assert "brief-work" not in engine.routines


def test_odoo_users_are_read_from_the_answer():
    text = 'Voici :\n```json\n[{"id": 7, "name": "Roger  T.", "login": "roger"}, {"id": "x"}, {"name": "sans id"}, 3]\n```'
    assert brief.parse_users(text) == [{"id": 7, "name": "Roger T.", "login": "roger"}]
    assert brief.parse_users("rien") == [] and brief.parse_users("[pas du json") == []


def test_odoo_users_search(engine):
    r = engine.odoo_users_start("work")
    t = engine.tasks[r["task_id"]]
    assert t["origin"] == "reglage" and t["closed"] is True and t["preset"] == "lecture"
    settle(engine, t["id"])
    out = engine.odoo_users_result("work", t["id"])    # the fake CLI only echoes: no user
    assert out["status"] == "done" and out["users"] == [] and out["error"]
    engine.inbox(start=True)
    assert not any(e.get("task_id") == t["id"] for e in engine.inbox()["entries"])   # answered in the configuration
    with pytest.raises(TaskError):
        engine.odoo_users_result("personal", t["id"])


def test_project_mails_are_kept_when_the_editor_does_not_send_them(engine, tmp_path):
    folder = tmp_path / "work" / "work" / "Chantier"
    folder.mkdir(parents=True)
    engine.save_project({"folder": str(folder), "name": "Chantier", "mails": {"senders": ["a@b.fr"], "follow": True}})
    engine.save_project({"folder": str(folder), "name": "Chantier 2"})
    proj = engine._project(str(folder))
    assert proj.name == "Chantier 2" and proj.mails.senders == ["a@b.fr"]


# ------------------------------------------------------------ the account's actions

def write_command(engine, pid, name, body):
    d = Path(engine.cfg.profile(pid).config_dir) / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\ndescription: {name}\nargument-hint: client\n---\n{body}\n", encoding="utf-8")


def test_account_actions_are_off_until_turned_on(engine):
    write_command(engine, "work", "relance", "Relance les devis.")
    assert engine.account_actions("work") == []
    engine.cfg.profile("work").account_actions = True
    a = engine.account_actions("work")[0]
    assert (a["name"], a["status"], a["scope"], a["hint"]) == ("relance", "nouvelle", "compte", "client")
    assert "content" not in a and "Relance les devis." in engine.account_actions("work", content=True)[0]["content"]
    assert engine.account_actions("personal") == []


def test_an_account_action_runs_once_validated(engine):
    write_command(engine, "work", "relance", "Relance les devis.")
    engine.cfg.profile("work").account_actions = True
    with pytest.raises(TaskError) as e:
        engine.run_account_action("work", "relance", "Dupont")
    assert e.value.status == 409 and e.value.extra["need_approval"]
    fp = e.value.extra["action"]["hash"]
    t = engine.run_account_action("work", "relance", "  Dupont  SA ", approve=fp)
    assert t["prompt"] == "/relance Dupont SA" and t["profile"] == "work" and t["origin"] == "action"
    settle(engine, t["id"])
    assert engine.account_actions("work")[0]["status"] == "ok"
    # changed since: asked again
    write_command(engine, "work", "relance", "Relance aussi les factures.")
    assert engine.account_actions("work")[0]["status"] == "modifiee"
    with pytest.raises(TaskError):
        engine.run_account_action("work", "relance")
    engine.set_account_action_prefs("work", "relance", menu=True)
    assert engine.account_actions("work")[0]["menu"] is True


def test_same_name_in_the_project_and_the_account(engine, tmp_path):
    write_command(engine, "work", "maj", "Mise à jour du compte.")
    engine.cfg.profile("work").account_actions = True
    folder = tmp_path / "work" / "work" / "Proj"
    (folder / ".claude" / "commands").mkdir(parents=True)
    (folder / ".claude" / "commands" / "maj.md").write_text("Mise à jour du projet.\n", encoding="utf-8")
    engine.save_project({"folder": str(folder), "name": "Proj", "profile": "work"})
    acc = engine.account_actions("work", content=True)[0]
    engine.approve_account_action("work", "maj", acc["hash"])
    with pytest.raises(TaskError) as e:    # the project's is not validated: which one would run?
        engine.run_account_action("work", "maj", workdir=str(folder))
    assert "aussi une action" in e.value.message
    proj_action = engine.project_actions(str(folder), content=True)[0]
    engine.approve_action(str(folder), "maj", proj_action["hash"])
    t = engine.run_account_action("work", "maj", workdir=str(folder))
    assert t["workdir"] == str(folder)
    settle(engine, t["id"])
    # the other way round: the project's action, the account's not validated any more
    write_command(engine, "work", "maj", "Changé.")
    with pytest.raises(TaskError) as e:
        engine.run_action(str(folder), "maj", profile="work")
    assert "Le compte Travail a aussi une action" in e.value.message


def test_a_routine_does_not_run_an_account_action_changed_since(engine):
    write_command(engine, "work", "veille", "Fais la veille.")
    engine.cfg.profile("work").account_actions = True
    r = engine.save_routine({"name": "Veille", "prompt": "/veille", "profile": "work", "preset": "lecture",
                             "schedule": {"kind": "interval", "every_min": 60}, "open_window": False})
    engine.run_routine(r["id"])
    run = engine.routines[r["id"]].runs[0]
    assert run["status"] == "non lancée" and "Actions du compte" in run["error"]


def test_account_actions_api(client):  # noqa: F811
    hd = {"X-Console-Token": client.token}
    eng = client.app.state.engine
    write_command(eng, "work", "relance", "Relance.")
    assert client.get("/api/accounts/work/actions", headers=hd).json() == {"actions": []}
    eng.cfg.profile("work").account_actions = True
    a = client.get("/api/accounts/work/actions?content=1", headers=hd).json()["actions"][0]
    r = client.post("/api/accounts/work/actions/approve", json={"name": "relance", "hash": "faux"}, headers=hd)
    assert r.status_code == 409
    r = client.post("/api/accounts/work/actions/approve", json={"name": "relance", "hash": a["hash"]}, headers=hd)
    assert r.json()["status"] == "ok"
    r = client.post("/api/accounts/work/actions/settings", json={"name": "relance", "menu": True}, headers=hd)
    assert r.json() == {"model": "", "effort": "", "menu": True}
    assert client.post("/api/accounts/inconnu/actions/run", json={"name": "relance"}, headers=hd).status_code == 404
