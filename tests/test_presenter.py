"""Displays composed by Claude (tool "presenter"): typed blocks checked by the console, drawn by the UI,
and the user's clicks sent back to the session."""
import json
import os

import pytest

from console import display
from console.engine import CONSOLE_MCP, PRESENT_SPEC, TaskError

from .conftest import task_status, wait_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def run(engine, prompt, wd):
    t = engine.create_task(prompt, profile="work", preset="lecture", workdir=str(wd))
    settle(engine, t["id"])
    return engine.tasks[t["id"]], engine.store.events(t["id"])


def settle(engine, tid):
    wait_for(lambda: task_status(engine, tid) in ("done", "error") and tid not in engine.runs)


def present(args) -> str:
    return "PRESENT " + json.dumps(args, ensure_ascii=False)


def displays(events):
    return [e["data"] for e in events if e["kind"] == "display"]


def test_a_display_is_checked_then_sent_to_the_interface(engine, tmp_path):
    wd = tmp_path / "projet"
    (wd / "images").mkdir(parents=True)
    (wd / "images" / "photo.png").write_bytes(PNG)
    (wd / ".env").write_text("SECRET=1", encoding="utf-8")
    task, events = run(engine, present({"titre": "Recherche", "ou": "modale", "blocs": [
        {"type": "images", "images": [{"source": "photo.png", "legende": "Locale"},
                                      {"source": "https://images.example.com/a.jpg"},
                                      {"source": "http://site.test/b.png"},
                                      {"source": ".env"}]},
        {"type": "resultats", "elements": [{"titre": "Page", "url": "https://example.com/p", "extrait": "x"},
                                           {"titre": "Piège", "url": "javascript:alert(1)"}]},
        {"type": "inconnu"},
        {"type": "graphique", "forme": "barres", "etiquettes": ["a", "b"], "series": [{"nom": "S", "valeurs": [1, "2,5"]}]},
    ]}), wd)
    [d] = displays(events)
    assert d["titre"] == "Recherche" and d["ou"] == "modale" and d["rev"] == 1
    imgs = d["blocs"][0]["images"]
    assert imgs[0] == {"path": os.path.realpath(wd / "images" / "photo.png"), "legende": "Locale"}
    assert imgs[1]["web"] == "https://images.example.com/a.jpg" and imgs[1]["trusted"] is False
    assert len(imgs) == 2  # http:// and the protected file are dropped
    res = d["blocs"][1]["elements"]
    assert len(res) == 2 and res[0]["lien"]["url"] == "https://example.com/p" and "lien" not in res[1]
    assert [b["type"] for b in d["blocs"]] == ["images", "resultats", "graphique"]
    assert d["blocs"][2]["series"][0]["valeurs"] == [1.0, 2.5]
    out = task["result"]
    assert "au premier plan" in out and f"Identifiant : {d['key']}" in out
    assert "1 image(s) du web" in out and "Ignoré" in out and "protégé" in out and "type inconnu" in out
    # allowed without asking, even in read-only mode
    assert not [e for e in events if e["kind"] in ("approval", "policy")]


def test_approved_domains_load_web_images_at_once(engine, tmp_path):
    cfg = engine.cfg_store.config.model_copy(deep=True)
    cfg.security.trusted_domains = ["example.com"]
    engine.cfg_store.save(cfg, "tests")
    task, events = run(engine, present({"titre": "T", "blocs": [
        {"type": "images", "images": ["https://cdn.example.com/a.png"]}]}), tmp_path)
    assert displays(events)[0]["blocs"][0]["images"][0]["trusted"] is True
    assert "du web" not in task["result"]


def test_nothing_usable_is_an_error_for_claude(engine, tmp_path):
    task, events = run(engine, present({"titre": "Vide", "blocs": [{"type": "texte", "texte": ""}]}), tmp_path)
    assert not displays(events)
    assert task["result"].startswith("affichage refusé : Rien d'affiché") and "texte vide" in task["result"]


def test_updating_a_display_keeps_its_place(engine, tmp_path):
    first = present({"titre": "Import", "ou": "fenetre", "id": "import", "blocs": [{"type": "progression", "valeur": 20}]})
    second = present({"titre": "Import", "id": "import", "blocs": [{"type": "progression", "valeur": 140, "texte": "fini"}]})
    task, events = run(engine, first + "\n" + second, tmp_path)
    a, b = displays(events)
    assert a["key"] == b["key"] == "import" and (a["rev"], b["rev"]) == (1, 2)
    assert b["ou"] == "fenetre"  # inherited from the first call
    assert b["blocs"][0]["valeur"] == 100
    assert "Mis à jour dans une fenêtre" in task["result"]


def test_a_choice_is_sent_back_to_the_session(engine, tmp_path):
    task, events = run(engine, present({"titre": "Offre", "blocs": [
        {"type": "choix", "question": "Laquelle ?", "options": ["Standard", "Premium"]},
        {"type": "actions", "boutons": [{"libelle": "Devis", "message": "Prépare le devis."},
                                        {"libelle": "Site", "url": "https://example.com"}]},
        {"type": "texte", "texte": "info"}]}), tmp_path)
    tid, key = task["id"], displays(events)[0]["key"]
    assert "nouveau message" in task["result"]
    engine.display_answer(tid, key, 0, choice=[1, 0])  # one answer only: the first picked
    settle(engine, tid)
    assert engine.tasks[tid]["result"] == "écho:[Affichage « Offre »] Laquelle ? → Premium"
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 0, choice=[0])
    assert e.value.status == 409
    for bad in ({"index": 1, "button": 1}, {"index": 2}, {"index": 9}, {"index": 1}):
        with pytest.raises(TaskError):
            engine.display_answer(tid, key, bad["index"], button=bad.get("button"))
    engine.display_answer(tid, key, 1, button=0)
    settle(engine, tid)
    assert engine.tasks[tid]["result"] == "écho:[Affichage « Offre »] Prépare le devis."
    answers = [e["data"] for e in engine.store.events(tid) if e["kind"] == "display_answer"]
    assert [a["labels"] for a in answers] == [["Premium"], ["Devis"]]
    # after a restart, the display and its answers come back from the stored events
    engine._displays.clear()
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 1, button=0)
    assert e.value.status == 409
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, "inconnu", 0, choice=[0])
    assert e.value.status == 404


def test_free_answer_and_multiple_choice():
    doc = {"titre": "T", "blocs": [{"type": "choix", "question": "", "options": ["a", "b", "c"], "multiple": True}]}
    assert display.answer_text(doc, 0, [2, 0, 2], "autre chose", None) == ("[Affichage « T »] → c ; a ; autre chose",
                                                                         ["c", "a", "autre chose"], None)
    with pytest.raises(ValueError):
        display.answer_text(doc, 0, [], "  ", None)


def test_a_form_is_checked_then_sent_back(engine, tmp_path):
    task, events = run(engine, present({"titre": "Devis", "blocs": [
        {"type": "formulaire", "titre": "Devis Dupont", "texte": "Corrige avant d'envoyer.", "bouton": "Valider le devis",
         "champs": [
             {"id": "client", "libelle": "Client", "valeur": "Dupont", "requis": True},
             {"libelle": "Corps", "type": "zone", "valeur": "Bonjour,\nvoici le devis."},
             {"id": "montant", "libelle": "Montant", "type": "nombre", "valeur": "1 200,50"},
             {"id": "echeance", "libelle": "Échéance", "type": "date", "valeur": "demain"},
             {"id": "tva", "libelle": "TVA", "type": "liste", "options": ["20 %", "10 %"], "valeur": "autre"},
             {"id": "tva", "libelle": "Doublon", "type": "liste"},
             {"id": "urgent", "libelle": "Urgent", "type": "case", "valeur": True, "requis": True},
         ]}]}), tmp_path)
    doc = displays(events)[0]
    fields = {f["id"]: f for f in doc["blocs"][0]["champs"]}
    assert list(fields) == ["client", "corps", "montant", "echeance", "tva", "urgent"]
    assert fields["client"]["requis"] is True and fields["corps"]["type"] == "zone"
    assert fields["corps"]["valeur"] == "Bonjour,\nvoici le devis."
    assert fields["montant"]["valeur"] == "1200.5" and fields["echeance"]["valeur"] == ""
    assert fields["tva"]["valeur"] == "" and fields["urgent"]["valeur"] == "oui"
    assert doc["blocs"][0]["bouton"] == "Valider le devis"
    assert "validation habituelle" in task["result"] and "formulaire (6 champs)" in task["result"]
    tid, key = task["id"], doc["key"]
    assert any(d.get("key") == key for d in task.get("waiting_displays") or [])
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 0, valeurs={"client": "  ", "urgent": "oui"})
    assert e.value.status == 400 and "requis" in str(e.value)
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 0, valeurs={"client": "Martin", "tva": "nope", "urgent": "oui"})
    assert e.value.status == 400 and "invalide" in str(e.value)
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 0, valeurs={"client": "Martin", "urgent": "non"})
    assert e.value.status == 400 and "Urgent" in str(e.value)
    engine.display_answer(tid, key, 0, valeurs={
        "client": "Martin", "corps": "Bonjour Martin,\nle devis suit.", "montant": "99",
        "echeance": "2026-11-02", "tva": "10 %", "urgent": "oui",
    })
    settle(engine, tid)
    sent = next(ev["data"]["text"] for ev in reversed(engine.store.events(tid))
                if ev["kind"] == "user" and ev["data"].get("text", "").startswith("[Affichage"))
    assert sent.startswith("[Affichage « Devis »] Formulaire « Devis Dupont » :")
    assert "Client : Martin" in sent and "Corps :\nBonjour Martin,\nle devis suit." in sent
    assert "Montant : 99" in sent and "Échéance : 2026-11-02" in sent and "TVA : 10 %" in sent and "Urgent : oui" in sent
    answers = [ev["data"] for ev in engine.store.events(tid) if ev["kind"] == "display_answer" and not ev["data"].get("annule")]
    assert answers[-1]["valeurs"]["client"] == "Martin" and answers[-1]["valeurs"]["urgent"] == "oui"
    with pytest.raises(TaskError) as e:
        engine.display_answer(tid, key, 0, valeurs={"client": "Martin", "urgent": "oui"})
    assert e.value.status == 409
    assert not engine.tasks[tid].get("waiting_displays")


def check(*blocks, trusted=()):
    def file(p):
        raise TaskError("Fichier introuvable.", 404)
    return display.check({"titre": "T", "blocs": list(blocks)}, file, list(trusted))


def test_blocks_are_normalized():
    doc, problems, _ = check(
        {"type": "tableau", "colonnes": ["a"], "lignes": [[1, True, None], {"a": 2}]},
        {"type": "graphique", "forme": "secteurs", "etiquettes": list("abcdefghi"),
         "series": [{"nom": "x", "valeurs": [9, 8, 7, 6, 5, 4, 3, 2, -1]}, {"nom": "ignorée", "valeurs": [1]}]},
        {"type": "graphique", "forme": "courbe", "series": [{"valeurs": [1, "n/a", float("nan")]}]},
        {"type": "schema", "svg": "<svg viewBox='0 0 1 1'><rect/></svg>"},
        {"type": "schema", "svg": "<script>alert(1)</script>"},
        {"type": "actions", "boutons": [{"libelle": "Go", "url": "javascript:alert(1)"}, {"libelle": "Ok"}]},
        {"type": "chiffres", "elements": [{"libelle": "CA", "valeur": 12.5}, {"libelle": "vide"}]},
        {"type": "fiche", "champs": {"Nom": "Dupont", "Actif": True}},
        {"type": "progression", "valeur": "-5"},
    )
    t, pie, line, svg, act, kpi, card, prog = doc["blocs"]
    assert t["colonnes"] == ["a", "", ""] and t["lignes"] == [[1, "oui", ""], [2, "", ""]]
    assert pie["etiquettes"] == ["a", "b", "c", "d", "e", "f", "Autres"] and pie["series"] == [{"nom": "x", "valeurs": [9, 8, 7, 6, 5, 4, 5]}]
    assert line["etiquettes"] == ["1", "2", "3"] and line["series"][0]["valeurs"] == [1.0, None, None]
    assert svg["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox')
    assert act["boutons"] == [{"libelle": "Ok", "message": "Ok"}]
    assert kpi["elements"] == [{"libelle": "CA", "valeur": "12.5", "evolution": "", "detail": ""}]
    assert card["champs"] == [{"libelle": "Nom", "valeur": "Dupont"}, {"libelle": "Actif", "valeur": "oui"}]
    assert prog["valeur"] == 0
    assert any("<svg>" in p for p in problems) and any("https://" in p for p in problems)


def test_size_limits():
    doc, problems, _ = check({"type": "tableau", "colonnes": ["x"], "lignes": [["y" * 900]] * 1000})
    assert doc is None and "trop volumineux" in problems[-1]
    doc, problems, _ = check(*[{"type": "texte", "texte": "a"}] * 25)
    assert len(doc["blocs"]) == 20 and "au-delà de 20" in problems[0]
    assert display.key_of("  mon id / 2 ") == "mon-id-2" and display.key_of("").startswith("d-")


def test_cards_run_without_another_turn(engine, tmp_path):
    folder = tmp_path / "reseau"
    folder.mkdir()
    engine.save_project({"folder": str(folder), "name": "Network", "profile": "work"})
    task, events = run(engine, present({"titre": "Brief", "blocs": [{"type": "cartes", "elements": [
        {"titre": "Etimia", "texte": "Pas de réponse sur les accès admin.", "boutons": [
            {"libelle": "Ouvrir", "ouvrir": {"outil": "mail", "contient": "accès admin"}},
            {"libelle": "Retenir", "suivi": "En attente Etimia, accès admin"},
            {"libelle": "Terminée", "terminee": {"ligne": "Tâche accès admin terminée"}},
        ]},
        {"titre": "Suite", "boutons": [
            {"libelle": "Routine", "routine": {"nom": "Relance Etimia", "description": "Relancer le support",
                                               "consigne": "Vérifie si Etimia a répondu.",
                                               "planification": {"type": "quotidienne", "heure": "09:00"}}},
            {"libelle": "Consigne", "consigne": {"texte": "Prévenir si Etimia n'a pas répondu sur les accès admin.",
                                                 "description": "Attente Etimia"}},
            {"libelle": "Mauvais", "routine": {"nom": "X", "consigne": "y", "planification": {"jours": ["jamais"]}}},
        ]},
    ]}]}), folder)
    shown = displays(events)[0]
    cards = shown["blocs"][0]["elements"]
    assert [b["faire"] for b in cards[0]["boutons"]] == ["ouvrir", "suivi", "terminee"]
    assert [b["faire"] for b in cards[1]["boutons"]] == ["routine", "consigne"]
    assert "planification" in json.dumps([e["data"] for e in events if e["kind"] == "tool_result"], ensure_ascii=False)
    tid, key = task["id"], shown["key"]
    users = lambda: [e for e in engine.store.events(tid) if e["kind"] == "user"]
    before = len(users())
    with pytest.raises(TaskError, match="Aucun résultat"):
        engine.display_act(tid, key, 0, 0, 0)
    assert len(users()) == before
    kept = engine.display_act(tid, key, 0, 0, 1)
    assert kept["note"].startswith("Ajouté")
    memo = (folder / "BRIEF.md").read_text(encoding="utf-8")
    assert "En attente Etimia, accès admin" in memo
    with pytest.raises(TaskError, match="Déjà fait"):
        engine.display_act(tid, key, 0, 0, 1)
    done = engine.display_act(tid, key, 0, 0, 2)
    assert "lecture seule" in done["note"]
    assert "Tâche accès admin terminée" in (folder / "BRIEF.md").read_text(encoding="utf-8")
    assert len(users()) == before
    offered = engine.display_act(tid, key, 0, 1, 0)
    assert not engine.routines
    prop = offered["proposition"]
    assert prop["kind"] == "proposal" and prop["input"]["quoi"] == "routine"
    engine.decide(tid, prop["id"], "allow", answers={"activer": "non"})
    saved = next(iter(engine.routines.values()))
    assert saved.name == "Relance Etimia" and saved.enabled is False and saved.preset == "lecture"
    assert engine._project(str(folder)).mails.instructions == ""
    consigne = engine.display_act(tid, key, 0, 1, 1)
    engine.decide(tid, consigne["proposition"]["id"], "allow")
    assert engine._project(str(folder)).mails.instructions == "Prévenir si Etimia n'a pas répondu sur les accès admin."
    assert len(users()) == before


def test_server_lists_the_tool(engine):
    tools = engine._console_mcp("x", CONSOLE_MCP, {"id": 1, "method": "tools/list"})["result"]["tools"]
    assert PRESENT_SPEC in tools and PRESENT_SPEC["name"] == "presenter"
    json.dumps(PRESENT_SPEC)


def test_a_dashboard_keeps_each_fact_for_the_page_to_filter():
    doc, problems, waiting = display.check({"titre": "CA septembre", "blocs": [{
        "type": "tableau_de_bord",
        "colonnes": [
            {"id": "jour", "libelle": "Jour", "type": "date"},
            {"libelle": "Client", "type": "texte"},
            {"id": "ca", "libelle": "CA", "type": "nombre", "unite": "€", "role": "montant"},
            {"id": "marge", "libelle": "Marge", "type": "nombre", "unite": "€", "agregat": "somme", "role": "marge"},
            {"id": "autre", "libelle": "Autre marge", "type": "nombre", "role": "marge"},
        ],
        "lignes": [
            {"jour": "2026-09-01", "client": "Dupont", "ca": "1 200,50", "marge": 400},
            ["03/09/2026", "Martin", 800, 200, 10],
            {"jour": "", "client": "", "ca": None, "marge": None},
        ],
        "grouper": "Client",
        "forme": "barres",
        "mesures": ["CA", "marge"],
    }]}, str, [])
    assert waiting == 0
    assert doc["ou"] == "fenetre"  # a report opens beside the discussion when « ou » is left out
    b = doc["blocs"][0]
    assert b["lignes"] == [
        ["2026-09-01", "Dupont", 1200.5, 400.0, None],
        ["2026-09-03", "Martin", 800.0, 200.0, 10.0],
    ]
    assert [c["id"] for c in b["colonnes"]] == ["jour", "client", "ca", "marge", "autre"]
    assert b["colonnes"][2]["role"] == "montant" and b["colonnes"][3]["role"] == "marge"
    assert "role" not in b["colonnes"][4]
    assert b["grouper"] == "client" and b["forme"] == "barres" and b["mesures"] == ["ca", "marge"]
    assert "periode" not in b
    assert any("rôle « marge » déjà utilisé" in p for p in problems)
    assert display.summary(doc) == "tableau de bord (2 lignes)"


def test_a_dashboard_infers_columns_and_a_daily_curve():
    doc, problems, _ = display.check({"titre": "Septembre", "blocs": [{
        "type": "rapport",
        "lignes": [
            {"jour": "2026-09-01", "client": "Dupont", "ca": 10},
            {"jour": "2026-09-02", "client": "Martin", "ca": 12},
        ],
        "periode": "mois",
    }]}, str, [])
    assert not problems
    b = doc["blocs"][0]
    assert b["type"] == "tableau_de_bord"
    assert [(c["id"], c["type"]) for c in b["colonnes"]] == [("jour", "date"), ("client", "texte"), ("ca", "nombre")]
    assert b["grouper"] == "jour" and b["forme"] == "courbe" and b["periode"] == "mois" and b["mesures"] == ["ca"]
    assert b["colonnes"][2]["agregat"] == "somme"


def test_a_dashboard_without_rows_is_refused_and_a_long_one_is_cut():
    doc, problems, _ = display.check({"titre": "Vide", "blocs": [{"type": "tableau_de_bord", "lignes": []}]}, str, [])
    assert doc is None and "aucune ligne" in problems[0]
    rows = [{"client": "A", "ca": i} for i in range(2001)]
    doc, problems, _ = display.check({"titre": "Gros", "ou": "conversation", "blocs": [
        {"type": "tableau_de_bord", "lignes": rows}]}, str, [])
    b = doc["blocs"][0]
    assert doc["ou"] == "conversation" and len(b["lignes"]) == 2000 and b["tronque"] is True
    assert any("2 000" in p or "2000" in p for p in problems)


def test_a_call_without_blocks_says_how_to_write_them():
    doc, problems, _ = display.check({"titre": "Devis", "kind": "fiche", "donnees": "[]"}, str, [])
    assert doc is None and "passe « blocs »" in problems[0] and "fiche" in problems[0]
