"""Ctrl+K search: console discussions (title, request, answer) and Claude Code sessions."""
from console import library

from .conftest import task_status, wait_for
from .test_move import write_session
from .test_security import client  # noqa: F401 - fixture


def done(engine, tid):
    return wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def test_snippet():
    assert library.snippet("aaa " * 40 + "le devis Dupont " + "bbb " * 40, "dupont").startswith("…")
    assert "devis Dupont" in library.snippet("aaa le devis Dupont bbb", "DUPONT")


def test_finds_discussions_by_title_and_by_answer(engine):
    a = engine.create_task("Tarifs Visiotech 2026", profile="work")
    b = engine.create_task("bonjour", profile="work")  # the fake answers « écho:bonjour »
    done(engine, a["id"])
    done(engine, b["id"])
    r = engine.search("visiotech")
    assert [t["id"] for t in r["tasks"]] == [a["id"]] and "Visiotech" in r["tasks"][0]["snippet"]
    r = engine.search("écho:bon")  # only in Claude's answer
    assert b["id"] in [t["id"] for t in r["tasks"]]
    assert engine.search("x")["tasks"] == []  # at least two characters


def test_finds_sessions_by_content(engine):
    prof = engine.cfg.profile("work")
    sid = write_session(prof, prof.workdir, messages=("Prépare le devis Dupont.", "Remise de 7 % appliquée."))
    r = engine.search("remise de 7")
    assert [s["id"] for s in r["sessions"]] == [sid] and "Remise de 7 %" in r["sessions"][0]["snippet"]
    assert r["sessions"][0]["profile_name"] == prof.name


def test_search_api(client):  # noqa: F811
    assert client.get("/api/search", params={"q": "devis"}).status_code == 401
    r = client.get("/api/search", params={"q": "devis"}, headers={"X-Console-Token": client.token})
    assert r.status_code == 200 and r.json() == {"tasks": [], "sessions": []}
