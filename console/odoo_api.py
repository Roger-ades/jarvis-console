"""The console's own Odoo access, read only, for the Python tasks of the modules (console/addon_tasks.py).

Claude reads Odoo through its MCP server: each record it reads goes through the model, which costs tokens.
A module that refreshes a table from Odoo every day does not need a model: its Python task asks the console,
which calls Odoo itself with an API key the user gave in the settings (Intégrations → Odoo pour les modules).

The key stays in the console (data/odoo-api.json, a folder no agent may read) and is not given to the module's
process: the task sends its requests to the console, which only runs reading methods (READ_METHODS). A task is
still a program of the PC (permission "python", granted for its code as it is): the surest is an API key of an
Odoo user whose rights are only reading.

Two protocols, the first that answers: JSON-2 (/json/2/<model>/<method>, Odoo 19 and later, bearer key) and
the external JSON-RPC (/jsonrpc, execute_kw, the key in place of the password; needs the database and login).
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

FILE = "odoo-api.json"
READ_METHODS = {"search_read", "search", "search_count", "read", "fields_get", "read_group",
                "formatted_read_group", "name_search"}
MODEL_RX = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
TIMEOUT = 120
MAX_BODY = 64 * 1024 * 1024  # an answer of Odoo


class OdooError(Exception):
    pass


# ---------------------------------------------------------------- settings (data/odoo-api.json)
def load(data_dir: Path) -> dict:
    try:
        raw = json.loads((Path(data_dir) / FILE).read_text("utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    return {k: str(raw.get(k) or "").strip() for k in ("url", "base", "login", "cle")}


def public(s: dict) -> dict:
    """What the settings page sees: never the key, only whether there is one."""
    return {"url": s["url"], "base": s["base"], "login": s["login"], "cle": bool(s["cle"]),
            "pret": bool(s["url"] and s["cle"])}


def clean_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        u = urlsplit(url)
    except ValueError:
        raise OdooError("Adresse Odoo invalide.") from None
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or (u.scheme == "http" and not local):
        raise OdooError("Adresse Odoo invalide : https://votre-base.odoo.com (http seulement sur ce poste).")
    return f"{u.scheme}://{u.netloc}"


def save(data_dir: Path, url: str, base: str, login: str, cle: str | None) -> dict:
    """cle None or empty keeps the key already saved; "-" removes it."""
    s = load(data_dir)
    s.update(url=clean_url(url), base=str(base or "").strip()[:200], login=str(login or "").strip()[:200])
    if cle == "-":
        s["cle"] = ""
    elif cle:
        s["cle"] = str(cle).strip()[:500]
    path = Path(data_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)
    return s


# ---------------------------------------------------------------- the client
class Client:
    def __init__(self, s: dict, timeout: float = TIMEOUT):
        if not s.get("url") or not s.get("cle"):
            raise OdooError("Accès Odoo des modules non configuré : Réglages → Intégrations → Odoo pour les modules "
                            "(adresse et clé API).")
        self.s = s
        self.timeout = timeout
        self.mode: str | None = None   # json2 | jsonrpc
        self.uid: int | None = None
        self._version: dict | None = None
        self._grouping: str | None = None  # formatted_read_group | read_group
        self._lock = threading.Lock()

    # -------------------------------------------------- HTTP
    def _post(self, path: str, body: dict, headers: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.s["url"] + path, data=data, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
            "User-Agent": "JARVIS-Console", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read(MAX_BODY) or b"null")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read(MAX_BODY) or b"null")
            except ValueError:
                payload = None
            return e.code, payload
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise OdooError(f"Odoo injoignable ({self.s['url']}) : {getattr(e, 'reason', e)}") from None

    def _rpc(self, service: str, method: str, args: list):
        status, payload = self._post("/jsonrpc", {"jsonrpc": "2.0", "method": "call", "id": 1,
                                                  "params": {"service": service, "method": method, "args": args}})
        if not isinstance(payload, dict):
            raise OdooError(f"Réponse inattendue d'Odoo (HTTP {status}).")
        if payload.get("error"):
            err = payload["error"]
            data = err.get("data") or {}
            raise OdooError(str(data.get("message") or err.get("message") or "Erreur Odoo")[:1000])
        return payload.get("result")

    def version(self) -> dict:
        if self._version is None:
            status, payload = self._post("/web/webclient/version_info", {"jsonrpc": "2.0", "method": "call", "params": {}})
            res = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(res, dict):
                raise OdooError(f"Ce n'est pas un Odoo qui répond à {self.s['url']} (HTTP {status}).")
            info = res.get("server_version_info") or []
            self._version = {"version": str(res.get("server_version") or ""),
                             "majeure": int(info[0]) if info and str(info[0]).isdigit() else 0}
        return self._version

    def _json2(self, model: str, method: str, body: dict):
        headers = {"Authorization": f"bearer {self.s['cle']}"}
        if self.s.get("base"):
            headers["X-Odoo-Database"] = self.s["base"]
        status, payload = self._post(f"/json/2/{model}/{method}", body, headers)
        if status == 200:
            return payload
        if isinstance(payload, dict) and (payload.get("message") or payload.get("name")):
            raise OdooError(str(payload.get("message") or payload.get("name"))[:1000])
        if status in (401, 403):
            raise OdooError("Clé API Odoo refusée.")
        raise OdooError(f"Odoo a répondu HTTP {status}.")

    def _connect(self):
        """JSON-2 when the server has it (Odoo 19+), else JSON-RPC with the database and login."""
        if self.mode:
            return
        v = self.version()
        if v["majeure"] >= 19 or not v["majeure"]:
            status, _ = self._post("/json/2/res.users/search_count", {"domain": [["id", "=", 0]]},
                                   {"Authorization": f"bearer {self.s['cle']}",
                                    **({"X-Odoo-Database": self.s["base"]} if self.s.get("base") else {})})
            if status == 200:
                self.mode = "json2"
                return
            if status in (401, 403):
                raise OdooError("Clé API Odoo refusée.")
        if not self.s.get("base") or not self.s.get("login"):
            raise OdooError(f"Odoo {v['version'] or '?'} : indique la base de données et l'identifiant (login) qui "
                            "vont avec la clé API.")
        uid = self._rpc("common", "authenticate", [self.s["base"], self.s["login"], self.s["cle"], {}])
        if not uid:
            raise OdooError("Odoo refuse l'identifiant ou la clé API.")
        self.uid, self.mode = int(uid), "jsonrpc"

    def call(self, model: str, method: str, ids: list | None = None, **kwargs):
        """One reading method of a model. Every other method is refused here, whatever asks."""
        model, method = str(model or ""), str(method or "")
        if method not in READ_METHODS:
            raise OdooError(f"Méthode refusée : {method} (lecture seule : {', '.join(sorted(READ_METHODS))}).")
        if not MODEL_RX.match(model):
            raise OdooError(f"Modèle invalide : {model}")
        if ids is not None:
            ids = [int(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids])]
        with self._lock:
            self._connect()
        if self.mode == "json2":
            return self._json2(model, method, {**({"ids": ids} if ids is not None else {}), **kwargs})
        return self._rpc("object", "execute_kw", [self.s["base"], self.uid, self.s["cle"], model, method,
                                                  [ids] if ids is not None else [], kwargs])

    # -------------------------------------------------- helpers
    def search_all(self, model: str, domain=None, fields=None, order=None, limit=None, offset=0, page=500) -> list:
        """search_read, page after page when no limit is given."""
        domain = domain or []
        if limit:
            return self.call(model, "search_read", domain=domain, fields=fields or [], order=order, limit=int(limit),
                             offset=int(offset or 0)) or []
        out, off = [], int(offset or 0)
        while True:
            got = self.call(model, "search_read", domain=domain, fields=fields or [], order=order or "id",
                            limit=page, offset=off) or []
            out += got
            if len(got) < page:
                return out
            off += page

    def group(self, model: str, domain=None, groupby=None, aggregates=None, order=None, limit=None) -> list[dict]:
        """Totals per group, the same shape whatever the version:
        [{"<groupby>": value, "__count": n, "<field>:<op>": value}]. A date grouping (create_date:month) gives the
        first day of its period (AAAA-MM-JJ), a many2one [id, name]."""
        domain = domain or []
        groupby = [groupby] if isinstance(groupby, str) else list(groupby or [])
        aggregates = [a for a in ([aggregates] if isinstance(aggregates, str) else list(aggregates or [])) if a != "__count"]
        if self._grouping is None:
            try:
                major = self.version()["majeure"]
            except OdooError:
                major = 0
            self._grouping = "formatted_read_group" if major >= 19 else "read_group"
        if self._grouping == "formatted_read_group":
            try:
                rows = self.call(model, "formatted_read_group", domain=domain, groupby=groupby,
                                 aggregates=[*aggregates, "__count"], order=order, limit=limit) or []
                return [_norm_group(r, groupby, aggregates, plain=False) for r in rows]
            except OdooError as exc:
                if "formatted_read_group" not in str(exc) and "has no attribute" not in str(exc):
                    raise
                self._grouping = "read_group"
        rows = self.call(model, "read_group", domain=domain, fields=aggregates, groupby=groupby, lazy=False,
                         orderby=order or False, limit=limit or None) or []
        return [_norm_group(r, groupby, aggregates, plain=True) for r in rows]

    def test(self) -> dict:
        """Settings page: the version, the protocol and a first reading."""
        v = self.version()
        self._connect()
        who = ""
        try:
            if self.mode == "jsonrpc":
                rows = self.call("res.users", "read", [self.uid], fields=["name"])
            else:
                rows = self.call("res.company", "search_read", domain=[], fields=["name"], limit=1)
            who = str((rows or [{}])[0].get("name") or "")
        except OdooError:
            pass
        return {"version": v["version"], "protocole": "JSON-2" if self.mode == "json2" else "JSON-RPC", "nom": who}


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _norm_group(row: dict, groupby: list[str], aggregates: list[str], plain: bool) -> dict:
    out = {"__count": int(row.get("__count") or row.get(f"{groupby[0]}_count") or 0) if groupby else int(row.get("__count") or 0)}
    ranges = row.get("__range") if isinstance(row.get("__range"), dict) else {}
    for g in groupby:
        v = row.get(g)
        rng = ranges.get(g)
        if isinstance(rng, dict) and rng.get("from"):
            v = str(rng["from"])[:10]
        elif isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str) and _DATE.match(v[0]):
            v = v[0][:10]
        elif isinstance(v, str) and _DATE.match(v):
            v = v[:10]
        elif isinstance(v, tuple):
            v = list(v)
        out[g] = v
    for a in aggregates:
        name = a.split(":")[0]
        out[a] = row.get(name if plain else a, row.get(a))
    return out
