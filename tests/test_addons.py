"""Modules (console/addons.py): tools and panels Claude develops for JARVIS in addons/, checked and opened by the
tool "modules", served sandboxed on the preview origin, with their own storage and questions to Claude."""
import json
from pathlib import Path

import pytest

from console import addons, presence
from console.engine import CONSOLE_MCP, MODULES_TOOL, TaskError

from .conftest import wait_for
from .test_display import PREVIEW, client  # noqa: F401 - the fixture
from .test_project_tools import settle


def module(root: Path, aid: str, manifest: dict | None = None, page: str = "<h1>Bonjour</h1>", **files) -> Path:
    d = root / aid
    d.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (d / "addon.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    if page is not None:
        (d / "index.html").write_text(page, "utf-8")
    for name, text in files.items():
        (d / name.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
        (d / name.replace("__", "/")).write_text(text, "utf-8")
    return d


def tool(engine, args) -> str:
    t = engine.create_task("MODULES " + json.dumps(args, ensure_ascii=False), profile="work", preset="lecture")
    settle(engine, t["id"])
    return engine.tasks[t["id"]]["result"]


def spy(engine) -> list:
    seen = []
    engine.bus.publish = (lambda pub: lambda kind, data: (seen.append((kind, data)), pub(kind, data))[1])(engine.bus.publish)
    return seen


# ------------------------------------------------------------ reading a module
def test_manifest_is_read_with_defaults_and_bounds(tmp_path):
    d = module(tmp_path, "suivi-devis", {"nom": "Suivi  des\ndevis", "icone": "📋", "largeur": 99999, "hauteur": "x",
                                         "permissions": ["claude", "claude"]}, **{"app.js": "1", "img__a.png": "x"})
    a = addons.read(d)
    assert a["nom"] == "Suivi des devis" and a["icone"] == "📋" and a["entree"] == "index.html"
    assert a["largeur"] == 1800 and a["hauteur"] == 640 and a["permissions"] == ["claude"]
    assert a["fichiers"] == 4 and not a["problemes"] and not a["avertissements"]
    assert addons.permission_key(a) == "suivi-devis|claude"


def test_what_prevents_a_module_from_opening(tmp_path):
    assert any("addon.json manquant" in p for p in addons.read(module(tmp_path, "sans-manifeste")).get("problemes"))
    bad = module(tmp_path, "Mauvais_Nom", {})
    assert any("nom de dossier" in p for p in addons.read(bad)["problemes"])
    d = module(tmp_path, "perm", {"permissions": ["reseau"], "entree": "absent.html"})
    probs = " ".join(addons.read(d)["problemes"])
    assert "permissions inconnues : reseau" in probs and "page d'entrée introuvable" in probs
    (tmp_path / "json-casse").mkdir()
    (tmp_path / "json-casse" / "addon.json").write_text("{nom:", "utf-8")
    assert any("illisible" in p for p in addons.read(tmp_path / "json-casse")["problemes"])


def test_web_resources_are_flagged(tmp_path):
    d = module(tmp_path, "web", {}, page='<script src="https://cdn.example.com/lib.js"></script>',
               **{"app.js": 'import x from "https://esm.sh/x"; fetch("//api.example.com")', "ok.js": 'import "./lib.js"'})
    warns = addons.read(d)["avertissements"]
    assert len(warns) == 2 and all("depuis le web" in w for w in warns)


def test_scan_skips_drafts_and_resolve_stays_inside(tmp_path):
    module(tmp_path, "b-module", {"nom": "Zèbre"})
    module(tmp_path, "a-module", {"nom": "Alpha"})
    module(tmp_path, "_brouillon", {})
    module(tmp_path, ".cache", {})
    assert [a["id"] for a in addons.scan(tmp_path)] == ["a-module", "b-module"]
    d = module(tmp_path, "c-module", {}, **{"js__app.js": "1", "node_modules__x.js": "1"})
    (tmp_path / "secret.txt").write_text("non", "utf-8")
    assert addons.resolve(d, "js/app.js?v=2") == (d / "js" / "app.js").resolve()
    for bad in ("../secret.txt", "/etc/passwd", "C:/x", "js/../../secret.txt", "node_modules/x.js", ""):
        assert addons.resolve(d, bad) is None, bad


def test_answers_in_json_tolerate_a_code_fence():
    assert addons.answer('```json\n{"a": 1}\n```', "json") == {"a": 1}
    assert addons.answer('Voici : [1, 2] et voilà', "json") == [1, 2]
    assert addons.answer("  bonjour ", "texte") == "bonjour"
    with pytest.raises(ValueError):
        addons.answer("pas de json", "json")


# ------------------------------------------------------------ the console side
def test_addons_folder_is_open_to_discussions_and_named_in_the_prompt(engine):
    root = engine.addons_dir
    assert root == engine.data_dir.parent / "addons" and (root / "README.md").is_file()
    assert "window.jarvis" in (root / "README.md").read_text("utf-8")
    t = engine.create_task("bonjour", profile="work", preset="lecture")
    settle(engine, t["id"])
    assert str(root) in engine.tasks[t["id"]]["add_dirs"]
    text = presence.prompt(engine.tasks[t["id"]], engine.cfg.profile("work"), engine.cfg.preset("lecture"),
                           addons=str(root))
    assert "Modules de JARVIS" in text and str(root) in text and "README.md" in text
    listed = engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})
    assert "modules" in [x["name"] for x in listed["result"]["tools"]] and MODULES_TOOL == f"mcp__{CONSOLE_MCP}__modules"


def test_claude_lists_checks_and_opens_a_module(engine):
    seen = spy(engine)
    assert "Aucun module" in tool(engine, {"action": "lister"})
    module(engine.addons_dir, "minuteur", {"nom": "Minuteur", "description": "Pomodoro", "permissions": ["claude"]})
    module(engine.addons_dir, "casse", {"permissions": ["reseau"]})
    out = tool(engine, {"action": "lister"})
    assert "minuteur : « Minuteur » — Pomodoro" in out and "permissions inconnues" in out
    assert "peut s'ouvrir" in tool(engine, {"action": "verifier", "id": "minuteur"})
    out = tool(engine, {"action": "ouvrir", "id": "casse"})
    assert "ne peut pas s'ouvrir" in out and not any(k == "addon_open" for k, _ in seen)
    out = tool(engine, {"action": "ouvrir", "id": "minuteur"})
    assert "ouvert" in out and "l'utilisateur doit l'autoriser" in out
    assert any(k == "addon_open" and d["id"] == "minuteur" for k, d in seen)
    assert "Module introuvable" in tool(engine, {"action": "ouvrir", "id": "../data"})
    engine.addon_data_set("minuteur", "durée", 25)
    assert '"durée": 25' in tool(engine, {"action": "donnees", "id": "minuteur"})


def test_storage_per_module_with_a_limit(engine):
    module(engine.addons_dir, "a-mod", {})
    module(engine.addons_dir, "b-mod", {})
    engine.addon_data_set("a-mod", "liste", [1, 2])
    engine.addon_data_set("a-mod", "x", "y")
    engine.addon_data_set("a-mod", "x", None)
    assert engine.addon_data("a-mod") == {"liste": [1, 2]} and engine.addon_data("b-mod") == {}
    with pytest.raises(TaskError) as e:
        engine.addon_data_set("a-mod", "gros", "x" * (addons.MAX_DATA + 1))
    assert e.value.status == 413 and engine.addon_data("a-mod") == {"liste": [1, 2]}
    with pytest.raises(TaskError):
        engine.addon_data_set("inconnu", "k", 1)


def test_a_question_to_claude_needs_the_permission_and_the_grant(engine):
    seen = spy(engine)
    module(engine.addons_dir, "sans-perm", {})
    module(engine.addons_dir, "avec-perm", {"permissions": ["claude"]})
    with pytest.raises(TaskError) as e:
        engine.addon_ask("sans-perm", "bonjour")
    assert e.value.status == 403 and "ne déclare pas" in e.value.message
    with pytest.raises(TaskError) as e:
        engine.addon_ask("avec-perm", "bonjour")
    assert e.value.status == 403 and "autorise-le" in e.value.message
    assert engine.addon_grant("avec-perm", True)["autorise"]
    assert engine.list_addons()["modules"][0]["autorise"]
    tid = engine.addon_ask("avec-perm", '{"total": 3}', fmt="json", profile="work")["task_id"]
    with pytest.raises(TaskError) as e:  # one question at a time
        engine.addon_ask("avec-perm", "encore")
    assert e.value.status == 429
    got = wait_for(lambda: next((d for k, d in seen if k == "addon_answer" and d["task_id"] == tid), None))
    assert got == {"module": "avec-perm", "task_id": tid, "ok": True, "valeur": {"total": 3}}
    t = engine.tasks.get(tid)
    assert t is None or (t.get("closed") and t.get("ephemeral"))
    # a manifest asking for more powers needs a new grant
    (engine.addons_dir / "avec-perm" / "addon.json").write_text(json.dumps({"permissions": []}), "utf-8")
    assert engine.list_addons()["modules"][0]["autorise"]  # nothing to grant
    engine.addon_grant("avec-perm", False)
    (engine.addons_dir / "avec-perm" / "addon.json").write_text(json.dumps({"permissions": ["claude"]}), "utf-8")
    assert not engine.list_addons()["modules"][0]["autorise"]


# ------------------------------------------------------------ served on the preview origin
def test_a_module_is_served_sandboxed_with_its_files_and_the_bridge(client):  # noqa: F811
    eng = client.engine
    module(eng.addons_dir, "outil", {"nom": "Outil"}, page="<!doctype html><p>Salut</p><script src=app.js></script>",
           **{"app.js": "console.log(1)", "autre.html": "<p>2</p>", "data__d.json": "{}"})
    (eng.addons_dir / "secret.txt").write_text("non", "utf-8")
    h = {"X-Console-Token": client.token}
    assert client.get("/api/addons", headers=h).json()["modules"][0]["id"] == "outil"
    r = client.post("/api/addons/outil/open", headers=h).json()
    shell = r["url"].split(PREVIEW, 1)[1]
    s = client.get(shell, headers={"Host": PREVIEW})
    assert "<iframe sandbox=\"allow-scripts allow-forms allow-modals\"" in s.text and "<script" not in s.text.split("<iframe")[0]
    app_url = s.text.split('src="', 1)[1].split('"', 1)[0]
    base = app_url.split(PREVIEW, 1)[1]
    page = client.get(base, headers={"Host": PREVIEW})
    csp = page.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-forms allow-modals" in csp and f"connect-src {app_url};" in csp
    assert "allow-same-origin" not in csp and "https:" not in csp
    assert page.text.startswith("<!doctype html>") and 'module: "outil"' in page.text and "<p>Salut</p>" in page.text
    js = client.get(base + "app.js", headers={"Host": PREVIEW})
    assert js.headers["content-type"].startswith("text/javascript") and js.headers["access-control-allow-origin"] == "*"
    assert 'module: "outil"' in client.get(base + "autre.html", headers={"Host": PREVIEW}).text
    assert client.get(base + "data/d.json", headers={"Host": PREVIEW}).status_code == 200
    for bad in ("../secret.txt", "addon.json/../../secret.txt", "%2e%2e/secret.txt"):
        assert client.get(base + bad, headers={"Host": PREVIEW}).status_code == 404, bad
    # the API stays on the console's origin only
    assert client.post("/api/addons/outil/open", headers={"Host": PREVIEW, **h}).status_code == 404
    assert client.put("/api/addons/outil/data", json={"cle": "k", "valeur": [1]}, headers=h).json() == {"ok": True}
    assert client.get("/api/addons/outil/data", headers=h).json() == {"donnees": {"k": [1]}}
    assert client.post("/api/addons/outil/ask", json={"consigne": "x"}, headers=h).status_code == 403
    module(eng.addons_dir, "casse", {"permissions": ["reseau"]})
    assert client.post("/api/addons/casse/open", headers=h).status_code == 409


# ------------------------------------------------------------ Claude proposes a module nobody asked for
def test_a_module_proposed_by_claude_waits_for_the_click(engine):
    from .test_project_tools import proposal
    args = {"action": "proposer", "id": "marges", "nom": "Calcul des marges", "icone": "📈",
            "description": "Saisis prix d'achat et de vente, il donne la marge.", "raison": "tu la recalcules à chaque devis"}
    t = engine.create_task("MODULES " + json.dumps(args, ensure_ascii=False), profile="work", preset="lecture")
    p = proposal(engine, t["id"])
    assert p["tool"] == MODULES_TOOL and "créer un module « Calcul des marges »" in p["reason"]
    assert p["input"] | {"dossier": ""} == {"quoi": "module", "id": "marges", "nom": "Calcul des marges", "icone": "📈",
                                          "description": args["description"], "raison": args["raison"],
                                          "existe": False, "dossier": ""}
    engine.decide(t["id"], p["id"], "allow", "en euros HT")
    settle(engine, t["id"])
    out = engine.tasks[t["id"]]["result"]
    assert "Accepté" in out and "en euros HT" in out and str(engine.addons_dir / "marges") in out
    assert not (engine.addons_dir / "marges").exists()  # Claude writes it, not the console
    # refused: Claude is told not to insist; an existing module is an improvement
    module(engine.addons_dir, "marges", {"nom": "Marges"})
    t = engine.create_task("MODULES " + json.dumps({**args, "nom": ""}), profile="work", preset="lecture")
    p = proposal(engine, t["id"])
    assert p["input"]["existe"] and p["input"]["nom"] == "Marges" and "améliorer le module" in p["reason"]
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert "ne le repropose pas" in engine.tasks[t["id"]]["result"]
    # checked before any card
    assert "Rien de proposé" in tool(engine, {"action": "proposer", "id": "Mauvais Nom", "description": "x"})
    assert "description" in tool(engine, {"action": "proposer", "id": "ok-id"})
    text = presence.prompt(engine.tasks[t["id"]], engine.cfg.profile("work"), engine.cfg.preset("lecture"),
                           addons=str(engine.addons_dir))
    assert "action proposer" in text and "a accepté ta carte" in text
