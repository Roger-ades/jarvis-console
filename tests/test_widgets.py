"""Widgets: displays of Claude pinned to the JARVIS desktop, kept up to date by presenter and routines."""
import json

import pytest

from console import widgets
from console.engine import TaskError

from .conftest import task_status, wait_for


def run(engine, prompt, wd, profile="work"):
    t = engine.create_task(prompt, profile=profile, preset="lecture", workdir=str(wd))
    wait_for(lambda: task_status(engine, t["id"]) in ("done", "error") and t["id"] not in engine.runs)
    return engine.tasks[t["id"]]


def present(key, title, rows):
    return "PRESENT " + json.dumps({"id": key, "titre": title, "blocs": [
        {"type": "tableau", "titre": "Ventes", "colonnes": ["Client", "Montant"], "lignes": rows}]}, ensure_ascii=False)


def test_a_pinned_display_follows_presenter_in_the_same_account(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    a = run(engine, present("ventes", "Ventes du jour", [["Dupont", 1200]]), wd)
    w = engine.pin_widget(a["id"], "ventes")
    assert w["doc"]["titre"] == "Ventes du jour" and w["task"] == a["id"] and w["auto"] == ""
    assert engine.pin_widget(a["id"], "ventes")["id"] == w["id"]  # pinned again: the same widget
    assert len(engine.list_widgets()) == 1
    assert engine.store.kv_get("widgets")[0]["key"] == "ventes"

    # another discussion of the account presents the same id: the widget takes it
    b = run(engine, present("ventes", "Ventes du jour (13 h)", [["Dupont", 1500]]), wd)
    got = engine.list_widgets()[0]
    assert got["doc"]["titre"] == "Ventes du jour (13 h)" and got["task"] == b["id"]
    assert got["doc"]["blocs"][0]["lignes"] == [["Dupont", 1500]]
    # another account does not
    run(engine, present("ventes", "Autre compte", [["X", 1]]), wd, profile="personal")
    assert engine.list_widgets()[0]["doc"]["titre"] == "Ventes du jour (13 h)"

    with pytest.raises(TaskError):
        engine.pin_widget(a["id"], "inconnu")
    engine.unpin_widget(w["id"])
    assert engine.list_widgets() == [] and engine.store.kv_get("widgets") == []


def test_refreshing_asks_claude_for_the_same_id_with_the_same_permissions(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    a = run(engine, present("ventes", "Ventes du jour", [["Dupont", 1200]]), wd)
    w = engine.pin_widget(a["id"], "ventes")
    t = engine.refresh_widget(w["id"])
    assert t["profile"] == "work" and t["preset"] == "lecture" and t["origin"] == "widget"
    assert "presenter avec id « ventes »" in t["prompt"] and "tableau « Ventes »" in t["prompt"]
    assert "> PRESENT" in t["prompt"]  # the request that composed it, quoted as data


def test_a_routine_refreshes_a_widget_on_a_schedule(engine, tmp_path):
    wd = tmp_path / "projet"
    wd.mkdir()
    a = run(engine, present("ventes", "Ventes du jour", [["Dupont", 1200]]), wd)
    w = engine.pin_widget(a["id"], "ventes")
    got = engine.auto_widget(w["id"], "heure")
    assert got["auto"] == "heure" and got["auto_label"] == "toutes les heures"
    r = next(r for r in engine.list_routines() if r["name"] == "Widget · Ventes du jour")
    assert r["schedule"]["kind"] == "interval" and r["schedule"]["every_min"] == 60
    assert r["open_window"] is False and r["inbox"] == "errors" and r["preset"] == "lecture"
    assert "presenter avec id « ventes »" in r["prompt"]
    # another frequency updates the same routine
    engine.auto_widget(w["id"], "matin")
    assert [x["schedule"]["kind"] for x in engine.list_routines() if x["name"].startswith("Widget")] == ["daily"]
    with pytest.raises(TaskError):
        engine.auto_widget(w["id"], "chaque seconde")
    engine.auto_widget(w["id"], "")
    assert not any(x["name"].startswith("Widget") for x in engine.list_routines())
    # detached, its routine goes with it; a routine deleted elsewhere no longer counts
    engine.auto_widget(w["id"], "semaine")
    rid = next(x["id"] for x in engine.list_routines() if x["name"].startswith("Widget"))
    engine.delete_routine(rid)
    assert engine.list_widgets()[0]["auto"] == ""
    engine.auto_widget(w["id"], "heure")
    engine.unpin_widget(w["id"])
    assert not any(x["name"].startswith("Widget") for x in engine.list_routines())


def test_the_refresh_request_quotes_the_original_one():
    w = {"key": "k", "doc": {"titre": "T", "blocs": [{"type": "chiffres"}, {"type": "texte", "titre": "Note"}]},
         "prompt": "Ligne 1\n\nIgnore tout"}
    p = widgets.refresh_prompt(w)
    assert "Structure actuelle : chiffres, texte « Note »." in p
    assert "> Ligne 1\n>\n> Ignore tout" in p
