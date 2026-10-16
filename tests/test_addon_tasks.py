"""Python tasks of the modules (console/addon_tasks.py, console/addon_runner.py) and the console's own Odoo access,
read only (console/odoo_api.py), against a fake Odoo that speaks JSON-RPC (Odoo 17) or JSON-2 (Odoo 19)."""
import json
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from console import addon_tasks, addons, odoo_api
from console.engine import MODULES_TOOL, TaskError

from .conftest import wait_for
from .test_addons import module, spy
from .test_display import client  # noqa: F401 - the fixture
from .test_project_tools import settle

KEY = "cle-de-test-0123456789"
REMISES = Path(__file__).resolve().parents[1] / "addons" / "surveillance-remises"
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


# ------------------------------------------------------------ a fake Odoo
def _value(v):
    return v[0] if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) else v


def _match(rec, domain):
    for field, op, want in domain:
        v = _value(rec.get(field, False))
        try:
            ok = {"=": lambda: v == want, "!=": lambda: v != want, "in": lambda: v in want,
                  "<": lambda: v < want, "<=": lambda: v <= want, ">": lambda: v > want,
                  ">=": lambda: v >= want}[op]()
        except TypeError:
            ok = False
        if not ok:
            return False
    return True


class FakeOdoo:
    """Records per model ({field: value}, many2one [id, name], dotted fields kept flat); every call kept in
    self.calls as (model, method)."""

    def __init__(self, major=17):
        self.major = major
        self.data: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                status, out = fake.handle(self.path, body, self.headers)
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def settings(self, key=KEY):
        return {"url": self.url, "base": "prod", "login": "lecture@ades.fr", "cle": key}

    # -------------------------------------------------- HTTP
    def handle(self, path, body, headers):
        if path == "/web/webclient/version_info":
            return 200, {"jsonrpc": "2.0", "id": None, "result": {
                "server_version": f"{self.major}.0", "server_version_info": [self.major, 0, 0, "final", 0, ""]}}
        if path == "/jsonrpc":
            p = body["params"]
            try:
                if p["service"] == "common" and p["method"] == "authenticate":
                    db, login, key, _ = p["args"]
                    return 200, {"jsonrpc": "2.0", "id": 1, "result": 7 if key == KEY and db == "prod" else False}
                db, uid, key, model, method, args, kw = p["args"]
                if key != KEY or uid != 7:
                    raise ValueError("Access Denied")
                return 200, {"jsonrpc": "2.0", "id": 1, "result": self.run(model, method, args[0] if args else None, kw)}
            except ValueError as exc:
                return 200, {"jsonrpc": "2.0", "id": 1, "error": {"code": 200, "message": "Odoo Server Error",
                                                                  "data": {"message": str(exc)}}}
        m = re.fullmatch(r"/json/2/([a-z0-9_.]+)/([a-z_]+)", path)
        if m and self.major >= 19:
            if headers.get("Authorization") != f"bearer {KEY}":
                return 401, {"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"}
            body = dict(body)
            ids = body.pop("ids", None)
            try:
                return 200, self.run(m[1], m[2], ids, body)
            except ValueError as exc:
                return 422, {"name": "odoo.exceptions.UserError", "message": str(exc)}
        return 404, {"message": "Not Found"} if m else None

    # -------------------------------------------------- the methods
    def run(self, model, method, ids, kw):
        self.calls.append((model, method))
        recs = self.data.get(model, [])
        if method == "search_count":
            return sum(_match(r, kw.get("domain") or []) for r in recs)
        if method == "read":
            return [self._fields(r, kw.get("fields")) for r in recs if r["id"] in ids]
        if method == "search_read":
            got = [r for r in recs if _match(r, kw.get("domain") or [])]
            key, _, way = (kw.get("order") or "id").partition(" ")
            got.sort(key=lambda r: r.get(key) or "", reverse=way.strip() == "desc")
            off = kw.get("offset") or 0
            return [self._fields(r, kw.get("fields")) for r in got[off:off + kw["limit"] if kw.get("limit") else None]]
        if method == "read_group" and self.major < 19:
            return self._group(recs, kw["domain"], kw["groupby"], kw["fields"], plain=True)
        if method == "formatted_read_group" and self.major >= 19:
            return self._group(recs, kw["domain"], kw["groupby"], kw["aggregates"], plain=False)
        raise ValueError(f"The method '{model}.{method}' does not exist")

    @staticmethod
    def _fields(r, fields):
        return {f: r.get(f, False) for f in (fields or list(r))} | {"id": r["id"]}

    def _group(self, recs, domain, groupby, aggregates, plain):
        groups: dict = {}
        for r in recs:
            if not _match(r, domain):
                continue
            key = []
            for g in groupby:
                field, _, gran = g.partition(":")
                v = r.get(field, False)
                key.append(v[:7] if gran == "month" and v else json.dumps(v))
            groups.setdefault(tuple(key), []).append(r)
        rows = []
        for key, rs in sorted(groups.items()):
            row = {"__count": len(rs)}
            for g, k in zip(groupby, key):
                field, _, gran = g.partition(":")
                if gran == "month":
                    label = f"{MONTHS[int(k[5:7]) - 1]} {k[:4]}"
                    if plain:  # read_group: the label, the bounds in __range
                        row[g] = label
                        row.setdefault("__range", {})[g] = {"from": f"{k}-01 00:00:00", "to": "…"}
                    else:      # formatted_read_group: [first day, label]
                        row[g] = [f"{k}-01", label]
                else:
                    row[g] = json.loads(k)
            for a in [a for a in aggregates if a != "__count"]:
                field, op = a.split(":")
                vals = [float(r.get(field) or 0) for r in rs]
                row[field if plain else a] = sum(vals) / len(vals) if op == "avg" else sum(vals)
            rows.append(row)
        return rows


@pytest.fixture(params=[17, 19], ids=["odoo17-jsonrpc", "odoo19-json2"])
def odoo(request):
    o = FakeOdoo(request.param)
    yield o
    o.close()


@pytest.fixture
def odoo17():
    o = FakeOdoo(17)
    yield o
    o.close()


def lines(n, **extra):
    return [{"id": i, "state": "sale", "display_type": False, "create_date": f"2025-0{1 + i % 3}-1{i % 9} 08:00:00",
             "price_subtotal": 100.0 * i, "margin": 10.0 * i - 50, "discount": (i * 7) % 80,
             "salesman_id": [3, "Alice"] if i % 2 else False, "product_id": [9, "[V9] Vis"],
             "order_id": [100 + i, f"S{100 + i}"], **extra} for i in range(1, n + 1)]


# ------------------------------------------------------------ the settings
def test_settings_keep_the_key_out_of_sight(tmp_path, client):  # noqa: F811
    s = odoo_api.save(tmp_path, "ades-erp.odoo.com/", " prod ", "lecture@ades.fr", KEY)
    assert s["url"] == "https://ades-erp.odoo.com" and s["base"] == "prod" and s["cle"] == KEY
    pub = odoo_api.public(odoo_api.load(tmp_path))
    assert pub == {"url": "https://ades-erp.odoo.com", "base": "prod", "login": "lecture@ades.fr", "cle": True, "pret": True}
    assert odoo_api.save(tmp_path, "https://ades-erp.odoo.com", "prod", "x", None)["cle"] == KEY   # kept
    assert odoo_api.save(tmp_path, "https://ades-erp.odoo.com", "prod", "x", "-")["cle"] == ""     # removed
    assert odoo_api.clean_url("http://localhost:8069/odoo") == "http://localhost:8069"
    for bad in ("http://ades-erp.odoo.com", "ftp://x.fr", "https://moi:secret@x.fr"):
        with pytest.raises(odoo_api.OdooError):
            odoo_api.clean_url(bad)
    with pytest.raises(odoo_api.OdooError, match="non configuré"):
        odoo_api.Client(odoo_api.load(tmp_path))
    # the page: never the key, nor in the log
    h = {"X-Console-Token": client.token}
    r = client.put("/api/odoo-api", json={"url": "ades-erp.odoo.com", "base": "prod", "login": "l", "cle": KEY}, headers=h)
    assert r.status_code == 200 and r.json()["cle"] is True and KEY not in r.text
    assert KEY not in client.get("/api/odoo-api", headers=h).text
    assert json.loads((client.engine.data_dir / odoo_api.FILE).read_text("utf-8"))["cle"] == KEY
    assert client.put("/api/odoo-api", json={"url": "http://x.fr", "cle": KEY}, headers=h).status_code == 400


def test_only_reading_methods_reach_odoo(odoo):
    c = odoo_api.Client(odoo.settings())
    for method in ("write", "create", "unlink", "action_confirm", "message_post"):
        with pytest.raises(odoo_api.OdooError, match="Méthode refusée"):
            c.call("sale.order", method, [1], vals={"state": "cancel"})
    with pytest.raises(odoo_api.OdooError, match="Modèle invalide"):
        c.call("../res.users", "read", [1])
    assert odoo.calls == []  # refused before anything was sent
    assert c.test()["protocole"] == ("JSON-2" if odoo.major >= 19 else "JSON-RPC")
    with pytest.raises(odoo_api.OdooError, match="refus|Access Denied"):
        odoo_api.Client(odoo.settings("mauvaise-cle")).call("res.partner", "search_count", domain=[])


def test_groups_and_pages_have_one_shape_on_every_version(odoo):
    odoo.data["sale.order.line"] = lines(12)
    c = odoo_api.Client(odoo.settings())
    by_month = c.group("sale.order.line", [["state", "=", "sale"]], ["create_date:month"],
                       ["price_subtotal:sum", "margin:sum"])
    assert [g["create_date:month"] for g in by_month] == ["2025-01-01", "2025-02-01", "2025-03-01"]
    assert sum(g["__count"] for g in by_month) == 12 and by_month[0]["price_subtotal:sum"] == 100.0 * (3 + 6 + 9 + 12)
    by_comm = c.group("sale.order.line", [], "salesman_id", ["discount:avg"])
    assert {json.dumps(g["salesman_id"]): g["__count"] for g in by_comm} == {"false": 6, '[3, "Alice"]': 6}
    assert all(isinstance(g["discount:avg"], float) for g in by_comm)
    rows = c.search_all("sale.order.line", [["price_subtotal", ">", 250]], ["id", "order_id"], page=4)
    assert [r["id"] for r in rows] == list(range(3, 13)) and rows[0]["order_id"] == [103, "S103"]
    assert odoo.calls.count(("sale.order.line", "search_read")) == 3  # 4 + 4 + 2


# ------------------------------------------------------------ the runner
def execution(tmp_path, script, permissions=("python",), timeout=30, params=None, data_dir=None):
    d = module(tmp_path, "tache", None, None, **{"t.py": script})
    a = {"id": "tache", "folder": str(d), "permissions": list(permissions),
         "taches": {"t": {"script": "t.py", "duree_max": timeout}}}
    ex = addon_tasks.Execution("r1", a, "t", params, data_dir or tmp_path, lambda e: None)
    ex.start()
    wait_for(lambda: ex.fin is not None, timeout=timeout + 15)
    return ex


def test_a_task_speaks_to_the_console_and_prints_to_its_log(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ne-doit-pas-passer")
    ex = execution(tmp_path, "import jarvis, os, pathlib\n"
                             "print('début', jarvis.tache, jarvis.params['n'])\n"
                             "jarvis.progres('à moitié')\n"
                             "pathlib.Path('sortie.txt').write_text('ok')\n"
                             "jarvis.resultat({'n': jarvis.params['n'] * 2, 'cwd': os.getcwd(),"
                             " 'secret': os.environ.get('ANTHROPIC_API_KEY')})\n", params={"n": 21})
    assert ex.etat == "terminee" and ex.progres == "à moitié" and "début t 21" in ex.public()["sortie"]
    assert ex.resultat == {"n": 42, "cwd": ex.a["folder"], "secret": None}
    assert (Path(ex.a["folder"]) / "sortie.txt").read_text() == "ok"
    assert "terminée" in ex.summary() and '"n": 42' in ex.summary()


def test_failures_end_the_task_with_their_reason(tmp_path, odoo17):
    ex = execution(tmp_path / "a", "print('avant')\nraise RuntimeError('pas de données')\n")
    assert ex.etat == "echec" and "pas de données" in ex.erreur and "avant" in ex.summary()
    ex = execution(tmp_path / "b", "import time\ntime.sleep(60)\n", timeout=1)
    assert ex.etat == "arretee" and "plus de 1 s" in ex.erreur and ex.fin - ex.debut < 15
    # without the permission "odoo", the console answers a refusal the script can catch
    ex = execution(tmp_path / "c", "import jarvis\ntry:\n    jarvis.odoo.compter('res.partner')\n"
                                   "except jarvis.JarvisErreur as e:\n    jarvis.resultat(str(e))\n")
    assert ex.etat == "terminee" and "permission « odoo »" in ex.resultat and odoo17.calls == []
    # with it: Odoo errors and writing methods come back the same way
    odoo_api.save(tmp_path, odoo17.url, "prod", "lecture@ades.fr", KEY)
    ex = execution(tmp_path / "d", "import jarvis\nout = [jarvis.odoo.compter('res.partner')]\n"
                                   "for m in ('write', 'inconnue'):\n    try:\n"
                                   "        jarvis.odoo.appeler('res.partner', m, [1])\n"
                                   "    except jarvis.JarvisErreur as e:\n        out.append(str(e))\n"
                                   "jarvis.resultat(out)\n", permissions=("python", "odoo"), data_dir=tmp_path)
    assert ex.etat == "terminee" and ex.resultat[0] == 0 and "Méthode refusée : write" in ex.resultat[1]
    assert "Méthode refusée : inconnue" in ex.resultat[2] and ex.requetes == 3
    assert odoo17.calls == [("res.partner", "search_count")]


# ------------------------------------------------------------ through the console
TASK = {"nom": "Tâche", "permissions": ["python"], "taches": {"go": {"script": "go.py", "duree_max": 30}}}


def finished(engine, rid):
    return wait_for(lambda: engine.addon_run_status(rid)["fin"] and engine.addon_run_status(rid), timeout=30)


def test_the_grant_goes_with_the_code(engine):
    d = module(engine.addons_dir, "tache", TASK, **{"go.py": "import jarvis, time\ntime.sleep(1)\njarvis.resultat(1)\n"})
    with pytest.raises(TaskError) as e:
        engine.addon_run("tache", "go")
    assert e.value.status == 403
    assert engine.addon_grant("tache", True)["autorise"]
    seen = spy(engine)
    run = engine.addon_run("tache", "go")
    with pytest.raises(TaskError) as e:  # one at a time per module
        engine.addon_run("tache", "go")
    assert e.value.status == 429
    assert finished(engine, run["id"])["resultat"] == 1
    events = [x for k, x in seen if k == "addon_run"]
    assert events[0]["etat"] == "en cours" and "resultat" not in events[0] and events[-1]["etat"] == "terminee"
    with pytest.raises(TaskError) as e:
        engine.addon_run("tache", "absente")
    assert e.value.status == 404
    # a .py changed (even a helper): asked again
    (d / "aide.py").write_text("X = 1\n", "utf-8")
    pub = next(a for a in engine.list_addons()["modules"] if a["id"] == "tache")
    assert not pub["autorise"] and pub["code_change"] and pub["scripts"] == ["aide.py", "go.py"]
    with pytest.raises(TaskError) as e:
        engine.addon_run("tache", "go")
    assert e.value.status == 403


def test_the_page_starts_follows_and_stops_a_task(client):  # noqa: F811
    eng, h = client.engine, {"X-Console-Token": client.token}
    module(eng.addons_dir, "tache", TASK, **{"go.py": "import jarvis, time\njarvis.progres('en route')\ntime.sleep(30)\n"})
    assert client.post("/api/addons/tache/run", json={"tache": "go"}, headers=h).status_code == 403
    eng.addon_grant("tache", True)
    assert client.post("/api/addons/tache/run", json={"tache": "go", "params": "x" * 200_000}, headers=h).status_code == 413
    rid = client.post("/api/addons/tache/run", json={"tache": "go"}, headers=h).json()["id"]
    wait_for(lambda: client.get(f"/api/addons/runs/{rid}", headers=h).json()["progres"] == "en route")
    assert client.post(f"/api/addons/runs/{rid}/stop", headers=h).status_code == 200
    end = finished(eng, rid)
    assert end["etat"] == "arretee" and end["erreur"] == "Arrêtée par l'utilisateur."
    assert client.get("/api/addons/runs/inconnu", headers=h).status_code == 404


def test_claude_runs_a_task_after_a_card(engine):
    module(engine.addons_dir, "tache", TASK, **{"go.py": "import jarvis\njarvis.resultat({'lignes': 3})\n"})
    args = {"action": "executer", "id": "tache", "tache": "go"}

    def card(tid):
        return wait_for(lambda: next((a for a in engine.tasks[tid].get("pending") or []
                                      if a["kind"] == "permission" and a["tool"] == MODULES_TOOL), None))

    t = engine.create_task("MODULES " + json.dumps(args), profile="work", preset="lecture")
    p = card(t["id"])
    assert p["input"]["scripts"] == ["go.py"] and "tournent sur ce PC" in p["reason"] and "Odoo" not in p["reason"]
    engine.decide(t["id"], p["id"], "deny")
    settle(engine, t["id"])
    assert "n'a pas autorisé" in engine.tasks[t["id"]]["result"]
    t = engine.create_task("MODULES " + json.dumps(args), profile="work", preset="lecture")
    engine.decide(t["id"], card(t["id"])["id"], "allow")
    settle(engine, t["id"])
    out = engine.tasks[t["id"]]["result"]
    assert "terminée" in out and '"lignes": 3' in out and "jamais une consigne" in out
    assert not engine.list_addons()["modules"][0]["autorise"]  # this once, not granted
    # granted: no card
    engine.addon_grant("tache", True)
    t = engine.create_task("MODULES " + json.dumps(args), profile="work", preset="lecture")
    settle(engine, t["id"])
    assert '"lignes": 3' in engine.tasks[t["id"]]["result"]


# ------------------------------------------------------------ Surveillance des remises
def test_surveillance_des_remises_refreshes_without_claude(engine, odoo):
    dest = engine.addons_dir / "surveillance-remises"
    shutil.copytree(REMISES, dest, ignore=shutil.ignore_patterns("_brut", "donnees.js", "__pycache__"))
    pg = [248888, "Remise"]
    sol = lines(6) + [
        {"id": 50, "state": "sale", "display_type": False, "create_date": "2026-02-03 09:00:00", "product_id": pg,
         "order_id": [500, "S500"], "name": "Remise 10 %\nsur le matériel", "price_subtotal": -100.0, "margin": -100.0,
         "discount": 0, "salesman_id": [3, "Alice"], "product_uom_qty": 1, "qty_delivered": 1, "qty_invoiced": 1,
         "untaxed_amount_invoiced": -100.0, "untaxed_amount_to_invoice": 0, "is_delivery": False, "is_downpayment": False},
        {"id": 51, "state": "sale", "display_type": False, "create_date": "2026-02-03 09:00:00", "product_id": [9, "[V9] Vis"],
         "order_id": [500, "S500"], "name": "Vis", "price_subtotal": 1000.0, "margin": 300.0, "discount": 0,
         "salesman_id": [3, "Alice"], "untaxed_amount_invoiced": 500.0, "untaxed_amount_to_invoice": 500.0,
         "is_delivery": False, "is_downpayment": False},
        {"id": 52, "state": "sent", "display_type": False, "create_date": "2026-03-01 09:00:00", "product_id": pg,
         "order_id": [600, "S600"], "name": "Remise 5 %", "price_subtotal": -50.0},
        {"id": 53, "state": "sale", "display_type": False, "create_date": "2026-03-05 09:00:00", "product_id": [9, "[V9] Vis"],
         "order_id": [700, "S700"], "name": "Vis", "price_subtotal": 80.0, "margin": -20.0, "discount": 60,
         "salesman_id": False, "order_partner_id": [8, "Client"], "price_unit": 200, "purchase_price": 100,
         "product_uom_qty": 1, "is_delivery": False, "is_downpayment": False},
    ]
    odoo.data = {
        "sale.order.line": sol,
        "sale.order": [
            {"id": 500, "name": "S500", "state": "sale", "partner_id": [8, "Client"], "user_id": [3, "Alice"],
             "date_order": "2026-02-03 09:00:00", "amount_untaxed": 900.0, "margin": 200.0, "invoice_status": "to invoice",
             "pricelist_id": [1, "Public"]},
            {"id": 600, "name": "S600", "state": "sent", "partner_id": [8, "Client"], "user_id": False,
             "date_order": "2026-03-01 09:00:00", "amount_untaxed": 950.0, "margin": 100.0, "validity_date": "2026-04-01"},
            {"id": 700, "name": "S700", "state": "sale", "partner_id": [8, "Client"], "user_id": False,
             "date_order": "2026-03-05 09:00:00", "amount_untaxed": 80.0, "margin": -20.0, "invoice_status": "no"},
        ],
        "account.move.line": [
            {"id": 1, "product_id": pg, "parent_state": "posted", "move_id.move_type": "out_invoice",
             "date": "2026-02-10", "price_subtotal": -60.0},
            {"id": 2, "product_id": pg, "parent_state": "posted", "move_id.move_type": "out_refund",
             "date": "2026-02-20", "price_subtotal": -10.0},
        ],
    }
    odoo_api.save(engine.data_dir, odoo.url, "prod", "lecture@ades.fr", KEY)
    a = next(x for x in engine.list_addons()["modules"] if x["id"] == "surveillance-remises")
    assert not a["problemes"] and a["scripts"] == ["actualiser.py", "construire.py"]
    engine.addon_grant("surveillance-remises", True)
    run = engine.addon_run("surveillance-remises", "actualiser")
    end = finished(engine, run["id"])
    assert end["etat"] == "terminee", end["erreur"] + "\n" + end["sortie"]
    assert end["progres"] == "19/19 · calcul des écarts (construire.py)"
    req = end["resultat"]["requetes"]
    assert req["rg_lignes"] == 1 and req["devis_cmd"] == 1 and req["neg_lignes"] == 1 and req["mois_rg_fact"] == 2
    assert any("remise globale : 1 commandes" in l for l in end["resultat"]["resume"])
    txt = (dest / "donnees.js").read_text("utf-8")
    data = json.loads(txt[txt.index("{"):txt.rindex(";")])
    rg = data["rg"][0]
    assert rg["cmd"] == "S500" and rg["base"] == 1000.0 and rg["remise"] == -100.0 and rg["ratio"] == 0.5
    assert rg["attendu"] == -50.0 and rg["ecart"] == -50.0 and rg["pct_lib"] == 10.0
    feb = next(m for m in data["mensuel"] if m["mois"] == "2026-02")
    assert feb["rg_fact"] == -50.0 and feb["rg_nb"] == 1
    assert {m["mois"] for m in data["mensuel"]} >= {"2025-01", "2025-02", "2025-03", "2026-02"}
    assert data["devis"][0]["cmd"] == "S600" and data["neg"][0]["lid"] == 53 and data["neg"][0]["ref"] == "V9"
    assert {m for _, m in odoo.calls} <= {"search_read", "read_group", "formatted_read_group", "search_count"}
    assert sorted(p.name for p in (dest / "_brut").iterdir()) == sorted(f"{n}.json" for n in req)
    assert "jarvis.executer(\"actualiser\"" in (dest / "app.js").read_text("utf-8")
