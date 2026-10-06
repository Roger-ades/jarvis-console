"""What Claude composes for the user (the console's tool "presenter"): typed blocks, not free HTML.

Claude says what to show — images, search results, a table, a chart, a record, a timeline, key
figures, choices and buttons — and the console decides how to draw it, always the same way.
Everything is checked here before it reaches the page: local files like a preview (task folders,
protected paths), web addresses in https only, sizes clipped. A web image outside the approved
domains is only loaded after a click of the user (an image address written by a model could carry
data out). A choice or a button sends the user's answer back to the session as a new message.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import unicodedata
from typing import Callable
from urllib.parse import urlsplit

from .config import trusted_url, web_domain

WHERE = ("conversation", "fenetre", "modale")
KINDS = ("texte", "images", "resultats", "tableau", "graphique", "tableau_de_bord", "fiche", "chronologie",
         "chiffres", "progression", "schema", "fichiers", "choix", "actions", "cartes", "formulaire", "application")
FIELD_TYPES = ("texte", "zone", "nombre", "date", "liste", "case")
CHARTS = ("barres", "courbe", "secteurs")
MAX_BLOCKS = 20
MAX_JSON = 600 * 1024
MAX_SVG = 300 * 1024
MAX_APP = 300 * 1024
IMG_EXT = re.compile(r"\.(png|jpe?g|gif|webp|svg|bmp)$", re.I)
_KEY = re.compile(r"[^\w.-]+")

TOOL_SPEC = {
    "name": "presenter",
    "description": (
        "Compose un affichage pour l'utilisateur dans la console JARVIS : images ou galerie, résultats de "
        "recherche en cartes, tableau, graphique, tableau de bord, fiche (un client, un devis, un contact…), chronologie ou agenda, "
        "chiffres clés, progression, schéma SVG, fichiers, des choix ou boutons auxquels il répond d'un clic, "
        "un formulaire prérempli (un mail, un devis) qu'il corrige puis valide, "
        "et des cartes dont les boutons sont exécutés par la console (ouvrir un résultat déjà lu, retenir une ligne, "
        "noter une tâche terminée, proposer une routine ou une consigne). "
        "Un rapport, un chiffre d'affaires, des marges, une comparaison ou un tableau de bord — dans une session "
        "comme dans une routine — est un bloc tableau_de_bord avec ou = fenetre. Envoie le détail, une ligne par "
        "fait (un jour, une facture, un produit, un client), et des colonnes typées (texte, nombre, date). "
        "N'agrège pas toi-même et n'envoie pas un simple tableau : la console filtre, regroupe, calcule les totaux "
        "et le taux de marge, et trace le graphique. "
        "La console dessine chaque bloc : donne les données, pas de mise en forme. À utiliser dès que l'utilisateur "
        "demande de montrer, d'afficher, de comparer ou de visualiser quelque chose, quand un affichage est plus "
        "clair qu'un long texte, et pour faire corriger un texte déjà rédigé (un formulaire plutôt qu'une suite de "
        "questions). Une réponse à un choix, à un bouton ou à un formulaire t'arrive comme un nouveau message de "
        "l'utilisateur, préfixé par « [Affichage …] ». Le formulaire ne crée ni n'envoie rien : une action qui suit "
        "reste soumise à la validation habituelle. Pour faire évoluer un affichage (progression, tableau qui se "
        "remplit), rappelle l'outil avec le même id. Pour un fichier seul, utilise plutôt « afficher » ; pour le "
        "résultat brut d'un outil (un mail…), « afficher_resultat »."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "titre": {"type": "string", "description": "Titre de l'affichage."},
            "ou": {"type": "string", "enum": list(WHERE),
                   "description": "conversation (par défaut) : dans la discussion. fenetre : une fenêtre à part, pour ce "
                                  "qui reste ouvert pendant que l'utilisateur travaille. modale : au premier plan, "
                                  "seulement si l'utilisateur l'a demandé ou pour une décision qui bloque la suite."},
            "id": {"type": "string", "description": "Identifiant d'un affichage déjà montré (rendu par l'outil la première "
                                                    "fois). Pour le modifier (données corrigées, ligne ajoutée, étape "
                                                    "suivante), réutilise-le : l'affichage est redessiné là où il est au "
                                                    "lieu d'en ouvrir un nouveau."},
            "blocs": {
                "type": "array", "minItems": 1, "maxItems": MAX_BLOCKS,
                "description": "Les blocs, dans l'ordre. Chacun a un « type » et les champs de ce type.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(KINDS)},
                        "titre": {"type": "string", "description": "Intertitre facultatif du bloc."},
                        "texte": {"type": "string", "description": "texte : Markdown. progression : légende."},
                        "images": {"type": "array", "description": "images : [{source, legende}] ; source = chemin "
                                   "d'un fichier ou adresse https:// d'une image.", "items": {"type": "object"}},
                        "elements": {"type": "array", "items": {"type": "object"}, "description":
                                     "resultats : [{titre, url, extrait, source, image}] ; "
                                     "chronologie : [{quand, titre, texte}] (quand : date ISO ou texte) ; "
                                     "chiffres : [{libelle, valeur, evolution, detail}] ; "
                                     "cartes : [{titre, texte, boutons}]. Chaque bouton a exactement une action : "
                                     "{ouvrir: {outil, contient, id, rang}} ouvre un résultat d'outil déjà reçu "
                                     "(contient doit y figurer tel quel) ; {suivi: \"ligne\"} l'ajoute au fichier de suivi ; "
                                     "{terminee: {ligne, message}} note la tâche terminée (et, si le brief peut écrire, "
                                     "redemande de la clôturer) ; {routine: {nom, consigne, description, planification}} "
                                     "et {consigne: {texte, description}} ouvrent une carte de validation, rien n'est "
                                     "enregistré avant ; {message: \"…\"} renvoie ce texte à la discussion. "
                                     "Huit cartes, quatre boutons chacune."},
                        "colonnes": {"type": "array", "description":
                                     "tableau : en-têtes (textes). tableau_de_bord : [{id, libelle, type, unite, agregat, role}]. "
                                     "type : texte, nombre ou date. agregat (nombre) : somme (défaut), moyenne, min, max. "
                                     "role : montant ou marge, un de chaque au plus : la console en tire le taux de marge. "
                                     "Sans colonnes, elles sont déduites des lignes."},
                        "lignes": {"type": "array", "description":
                                   "tableau : lignes de cellules. tableau_de_bord : une ligne par fait, objet {id: valeur} "
                                   "ou liste dans l'ordre des colonnes, 2 000 au plus. Dates en AAAA-MM-JJ ou JJ/MM/AAAA. "
                                   "Nombres tels quels (1200.5 ou « 1 200,50 »)."},
                        "grouper": {"type": "string", "description":
                                    "tableau_de_bord : id de la colonne de regroupement (un texte ou une date)."},
                        "periode": {"type": "string", "enum": ["jour", "semaine", "mois"],
                                    "description": "tableau_de_bord : découpage quand le regroupement est une date."},
                        "mesures": {"type": "array", "items": {"type": "string"},
                                    "description": "tableau_de_bord : ids des colonnes numériques à tracer (8 au plus). Défaut : toutes."},
                        "forme": {"type": "string", "enum": list(CHARTS),
                                  "description": "graphique ou tableau_de_bord : barres, courbe ou secteurs."},
                        "etiquettes": {"type": "array", "items": {"type": "string"}, "description": "graphique : axe des catégories."},
                        "series": {"type": "array", "items": {"type": "object"},
                                   "description": "graphique : [{nom, valeurs: [nombres]}], 8 au plus (secteurs : une)."},
                        "unite": {"type": "string", "description": "graphique : unité des valeurs (€, h, %…)."},
                        "champs": {"type": "array", "items": {"type": "object"}, "description":
                                   "fiche : [{libelle, valeur}]. formulaire : [{id, libelle, type, valeur, requis, aide, options}]. "
                                   "type : texte, zone (texte long, un mail), nombre, date (AAAA-MM-JJ), liste (avec options), "
                                   "case. valeur préremplit le champ. requis : il doit être rempli."},
                        "bouton": {"type": "string", "description": "formulaire : libellé du bouton (Valider par défaut)."},
                        "lien": {"type": "string", "description": "fiche : adresse https:// de l'enregistrement."},
                        "image": {"type": "string", "description": "fiche : image (chemin ou https://)."},
                        "valeur": {"type": "number", "description": "progression : 0 à 100."},
                        "svg": {"type": "string", "description": "schema : un document <svg> complet (sans script)."},
                        "fichiers": {"type": "array", "items": {"type": "string"}, "description": "fichiers : chemins."},
                        "question": {"type": "string", "description": "choix : la question posée."},
                        "options": {"type": "array", "items": {"type": "string"}, "description": "choix : les réponses proposées."},
                        "multiple": {"type": "boolean", "description": "choix : plusieurs réponses possibles."},
                        "boutons": {"type": "array", "items": {"type": "object"}, "description":
                                    "actions : [{libelle, message}] (le message t'est renvoyé au clic) ou [{libelle, url}]."},
                        "html": {"type": "string", "description": (
                            "application : une petite page HTML complète (CSS et JavaScript en ligne) : calculateur, "
                            "simulateur, tri, saisie… Elle tourne isolée, sans réseau ni accès aux fichiers : mets-y les "
                            "données dont elle a besoin. jarvis.envoyer(texte ou objet) t'envoie un message ; "
                            "jarvis.action(nom, arguments) propose de lancer une action du projet. Chaque envoi part au "
                            "clic de l'utilisateur.")},
                        "hauteur": {"type": "integer", "minimum": 120, "maximum": 1200,
                                    "description": "application : hauteur en pixels (400 par défaut)."},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["titre", "blocs"],
    },
}


class Problems(list):
    def add(self, where: str, why: str):
        self.append(f"{where} : {why}")


def _s(v, n: int = 500) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    s = str(v).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _cell(v):
    if isinstance(v, bool) or v is None:
        return "" if v is None else ("oui" if v else "non")
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else ""
    return _s(v, 1000)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        t = v.strip().replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
        try:
            f = float(t)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _list(v, n: int) -> list:
    return list(v)[:n] if isinstance(v, (list, tuple)) else []


def _field_id(raw, j: int) -> str:
    s = unicodedata.normalize("NFKD", _s(raw, 40).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-")[:40]
    return s or f"c{j + 1}"


def _field_prefill(kind: str, value, options: list[str]) -> str:
    """The value Claude wrote, kept only when it matches the field type."""
    if kind == "case":
        return "oui" if value in (True, 1, "1", "oui", "true", "vrai") else ""
    if kind == "nombre":
        n = _num(value)
        if n is None:
            return ""
        return str(int(n)) if n == int(n) else str(n)
    if kind == "date":
        s = _s(value, 10)
        return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""
    if kind == "liste":
        s = _s(value, 200)
        return s if s in options else ""
    return _s(value, 8000 if kind == "zone" else 2000)


def _field_answer(kind: str, raw, options: list[str]) -> str | None:
    """The value the user sent, or None when it does not match the type."""
    if kind == "case":
        return "oui" if raw in (True, 1, "1", "oui", "true", "vrai") else "non"
    if isinstance(raw, (dict, list)) or raw is None:
        raw = ""
    if kind == "nombre":
        if str(raw).strip() == "":
            return ""
        n = _num(raw)
        if n is None:
            return None
        return str(int(n)) if n == int(n) else str(n)
    if kind == "date":
        s = _s(raw, 10)
        if s and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return None
        return s
    if kind == "liste":
        s = _s(raw, 200)
        if s and s not in options:
            return None
        return s
    return _s(raw, 8000 if kind == "zone" else 2000)


_DASH_ROWS = 2000
_DASH_COLS = 12
_DASH_TYPES = ("texte", "nombre", "date")
_DASH_KIND = {"text": "texte", "string": "texte", "number": "nombre", "num": "nombre", "float": "nombre",
              "date": "date", "nombre": "nombre", "texte": "texte"}
_DASH_AGG = {"sum": "somme", "avg": "moyenne", "average": "moyenne", "mean": "moyenne",
             "somme": "somme", "moyenne": "moyenne", "min": "min", "max": "max"}


def _dash_ident(raw, used: set[str]) -> str:
    base = _KEY.sub("-", _s(raw, 48)).strip("-.").lower() or "c"
    base = base[:40]
    ident, n = base, 2
    while ident in used:
        suffix = f"-{n}"
        ident = f"{base[:40 - len(suffix)]}{suffix}"
        n += 1
    used.add(ident)
    return ident


def _dash_date(v) -> str:
    if v is None or isinstance(v, (bool, int, float, dict, list)):
        return ""
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = (int(m.group(i)) for i in (1, 2, 3))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if not m:
            return ""
        d, mo, y = (int(m.group(i)) for i in (1, 2, 3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _infer_kind(values) -> str:
    kinds = set()
    for v in values:
        if v is None or v == "":
            continue
        if _dash_date(v):
            kinds.add("date")
        elif _num(v) is not None and not (isinstance(v, str) and re.search(r"[A-Za-zÀ-ÿ]", v)):
            kinds.add("nombre")
        else:
            kinds.add("texte")
    if kinds == {"date"}:
        return "date"
    if kinds == {"nombre"}:
        return "nombre"
    return "texte"


def _dash_value(v, kind: str):
    if kind == "nombre":
        return _num(v)
    if kind == "date":
        return _dash_date(v)
    return _s(v, 200)


def key_of(raw) -> str:
    k = _KEY.sub("-", _s(raw, 60)).strip("-.")
    return k or f"d-{secrets.token_hex(4)}"


class Checker:
    """Normalizes one call of "presenter". file(path) → absolute path or raises (task folders, protected
    paths); trusted: the approved web domains."""

    def __init__(self, file: Callable[[str], str], trusted: list[str]):
        self.file, self.trusted, self.problems = file, trusted, Problems()
        self.web_images = 0

    def web(self, url: str, where: str) -> dict | None:
        url = _s(url, 2000)
        if not re.match(r"^https://", url, re.I):
            self.problems.add(where, f"{url[:80] or 'adresse vide'} — seules les adresses https:// sont acceptées")
            return None
        host = web_domain(url)
        if not host:
            self.problems.add(where, f"adresse invalide : {url[:80]}")
            return None
        return {"url": url, "host": host, "trusted": trusted_url(url, self.trusted)}

    def image(self, src, where: str) -> dict | None:
        src = _s(src, 2000)
        if not src:
            return None
        if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) and not re.match(r"^[a-z]:[\\/]", src, re.I):
            w = self.web(src, where)
            if w and not w["trusted"]:
                self.web_images += 1
            return {"web": w["url"], "host": w["host"], "trusted": w["trusted"]} if w else None
        try:
            path = self.file(src)
        except Exception as exc:  # noqa: BLE001 - TaskError, OSError: told to Claude, never shown
            self.problems.add(where, f"{src} — {getattr(exc, 'message', exc)}")
            return None
        if not IMG_EXT.search(path):
            self.problems.add(where, f"{src} — pas une image (utilise le bloc fichiers)")
            return None
        return {"path": path}

    def block(self, b, i: int) -> dict | None:
        where = f"bloc {i + 1}"
        if not isinstance(b, dict):
            self.problems.add(where, "doit être un objet")
            return None
        kind = _s(b.get("type"), 40).lower().replace(" ", "_").replace("-", "_")
        if kind in ("dashboard", "rapport"):
            kind = "tableau_de_bord"
        if kind not in KINDS:
            self.problems.add(where, f"type inconnu « {kind} » (types : {', '.join(KINDS)})")
            return None
        out = {"type": kind, **({"titre": _s(b["titre"], 200)} if _s(b.get("titre")) else {})}
        body = getattr(self, f"_{kind}")(b, f"{where} ({kind})")
        if body is None:
            return None
        return {**out, **body}

    # ---- one method per type: the normalized fields, or None (with a problem) when nothing is left
    def _texte(self, b, where):
        text = _s(b.get("texte"), 60_000)
        if not text:
            self.problems.add(where, "texte vide")
            return None
        return {"texte": text}

    def _images(self, b, where):
        items = []
        for j, it in enumerate(_list(b.get("images"), 48)):
            it = it if isinstance(it, dict) else {"source": it}
            img = self.image(it.get("source") or it.get("src") or it.get("url"), f"{where}, image {j + 1}")
            if img:
                items.append({**img, "legende": _s(it.get("legende") or it.get("titre"), 300)})
        if not items:
            self.problems.add(where, "aucune image utilisable")
            return None
        return {"images": items}

    def _resultats(self, b, where):
        items = []
        for j, it in enumerate(_list(b.get("elements"), 50)):
            if not isinstance(it, dict):
                continue
            w = self.web(it.get("url"), f"{where}, élément {j + 1}") if _s(it.get("url")) else None
            title = _s(it.get("titre"), 300)
            if not title and not w:
                continue
            img = self.image(it.get("image"), f"{where}, image {j + 1}") if _s(it.get("image")) else None
            items.append({"titre": title or w["host"], "extrait": _s(it.get("extrait") or it.get("texte"), 1200),
                          "source": _s(it.get("source"), 100), **({"lien": w} if w else {}),
                          **({"image": img} if img else {})})
        if not items:
            self.problems.add(where, "aucun élément")
            return None
        return {"elements": items}

    def _tableau(self, b, where):
        cols = [_s(c, 200) for c in _list(b.get("colonnes"), 40)]
        rows = []
        for r in _list(b.get("lignes"), 1000):
            if isinstance(r, dict):
                r = [r.get(c) for c in cols] if cols else list(r.values())
            if isinstance(r, (list, tuple)):
                rows.append([_cell(c) for c in list(r)[:40]])
        if not rows and not cols:
            self.problems.add(where, "tableau vide")
            return None
        width = max([len(cols), *(len(r) for r in rows)])
        cols += [""] * (width - len(cols))
        rows = [r + [""] * (width - len(r)) for r in rows]
        more = len(b.get("lignes") or []) > 1000 if isinstance(b.get("lignes"), list) else False
        return {"colonnes": cols, "lignes": rows, **({"tronque": True} if more else {})}

    def _tableau_de_bord(self, b, where):
        """One row per fact. The page filters, groups and charts: nothing is aggregated here."""
        raw_rows = b.get("lignes")
        if isinstance(raw_rows, dict):
            raw_rows = [raw_rows]
        if not isinstance(raw_rows, list) or not raw_rows:
            self.problems.add(where, "aucune ligne : une ligne par fait (jour, facture, produit…)")
            return None
        cut = len(raw_rows) > _DASH_ROWS
        raw_rows = [r for r in raw_rows[:_DASH_ROWS] if isinstance(r, (dict, list, tuple))]
        specs = self._dash_columns(b.get("colonnes"), raw_rows, where)
        if not specs:
            return None
        rows = []
        for r in raw_rows:
            if isinstance(r, dict):
                folded = {str(k).strip().casefold(): v for k, v in r.items()}
                cells = []
                for spec in specs:
                    raw = folded.get(spec["id"].casefold())
                    if raw is None:
                        raw = folded.get(spec["libelle"].casefold())
                    cells.append(_dash_value(raw, spec["type"]))
            else:
                seq = list(r)
                cells = [_dash_value(seq[j] if j < len(seq) else None, spec["type"]) for j, spec in enumerate(specs)]
            if any(c not in ("", None) for c in cells):
                rows.append(cells)
        if not rows:
            self.problems.add(where, "aucune ligne exploitable")
            return None
        by_name = {c["id"].casefold(): c for c in specs}
        for c in specs:
            by_name.setdefault(c["libelle"].casefold(), c)
        chosen = by_name.get(_s(b.get("grouper"), 80).casefold())
        if chosen is None or chosen["type"] == "nombre":
            chosen = next((c for c in specs if c["type"] == "date"), None) or next((c for c in specs if c["type"] == "texte"), None)
        grouper = chosen["id"] if chosen else ""
        forme = _s(b.get("forme"), 20).lower() or ("courbe" if chosen and chosen["type"] == "date" else "barres")
        if forme not in CHARTS:
            forme = "barres"
        numbers = [c for c in specs if c["type"] == "nombre"]
        wanted = b.get("mesures") if isinstance(b.get("mesures"), list) and b.get("mesures") else [c["id"] for c in numbers]
        mesures = []
        for raw in wanted:
            token = _s(raw, 80).casefold()
            hit = next((c["id"] for c in numbers if token in (c["id"].casefold(), c["libelle"].casefold())), "")
            if hit and hit not in mesures:
                mesures.append(hit)
            if len(mesures) >= 8:
                break
        out = {"colonnes": specs, "lignes": rows, "grouper": grouper, "forme": forme, "mesures": mesures}
        if chosen and chosen["type"] == "date":
            periode = _s(b.get("periode"), 20).lower()
            out["periode"] = periode if periode in ("jour", "semaine", "mois") else "jour"
        if cut:
            out["tronque"] = True
            self.problems.add(where, f"au-delà de {_DASH_ROWS} lignes, la suite est ignorée")
        return out

    def _dash_columns(self, raw, rows, where):
        used: set[str] = set()
        items = raw if isinstance(raw, list) else []
        if not items:
            keys = []
            for r in rows[:50]:
                if isinstance(r, dict):
                    for k in r:
                        ks = _s(k, 80)
                        if ks and ks not in keys:
                            keys.append(ks)
            if keys:
                items = [{"id": k, "libelle": k} for k in keys[:_DASH_COLS]]
            else:
                width = max((len(r) for r in rows if isinstance(r, (list, tuple))), default=0)
                items = [{"libelle": f"Colonne {i + 1}"} for i in range(min(width, _DASH_COLS))]
        specs = []
        roles: set[str] = set()
        for c in items[:_DASH_COLS]:
            if isinstance(c, str):
                c = {"libelle": c}
            if not isinstance(c, dict):
                continue
            label = _s(c.get("libelle") or c.get("nom") or c.get("id"), 80) or f"Colonne {len(specs) + 1}"
            ident = _dash_ident(c.get("id") or label, used)
            samples = []
            for r in rows[:80]:
                if isinstance(r, dict):
                    folded = {str(k).strip().casefold(): v for k, v in r.items()}
                    samples.append(folded.get(str(c.get("id") or "").strip().casefold(), folded.get(label.casefold())))
                elif isinstance(r, (list, tuple)) and len(specs) < len(r):
                    samples.append(r[len(specs)])
            kind = _DASH_KIND.get(_s(c.get("type"), 20).lower(), "")
            if kind not in _DASH_TYPES:
                kind = _infer_kind(samples)
            spec = {"id": ident, "libelle": label, "type": kind}
            if kind == "nombre":
                agg = _DASH_AGG.get(_s(c.get("agregat"), 20).lower(), "")
                spec["agregat"] = agg or "somme"
                unit = _s(c.get("unite"), 12)
                if unit:
                    spec["unite"] = unit
                role = _s(c.get("role"), 20).lower()
                if role in ("montant", "marge") and role not in roles:
                    spec["role"] = role
                    roles.add(role)
                elif role in ("montant", "marge"):
                    self.problems.add(where, f"rôle « {role} » déjà utilisé, ignoré")
            specs.append(spec)
        if isinstance(items, list) and len(raw if isinstance(raw, list) else []) > _DASH_COLS:
            self.problems.add(where, f"au-delà de {_DASH_COLS} colonnes, la suite est ignorée")
        if not specs:
            self.problems.add(where, "aucune colonne")
            return None
        return specs

    def _graphique(self, b, where):
        form = _s(b.get("forme"), 20).lower() or "barres"
        if form not in CHARTS:
            self.problems.add(where, f"forme inconnue « {form} » (barres, courbe, secteurs)")
            return None
        limit = 500 if form == "courbe" else 60
        labels = [_s(x, 80) for x in _list(b.get("etiquettes"), limit)]
        series = []
        for s in _list(b.get("series"), 8):
            if not isinstance(s, dict):
                continue
            vals = [_num(v) for v in _list(s.get("valeurs"), limit)]
            if any(v is not None for v in vals):
                series.append({"nom": _s(s.get("nom"), 80), "valeurs": vals})
        if not series:
            self.problems.add(where, "aucune série de nombres")
            return None
        n = max(len(s["valeurs"]) for s in series)
        labels += [str(k + 1) for k in range(len(labels), n)]
        labels = labels[:n]
        for s in series:
            s["valeurs"] += [None] * (n - len(s["valeurs"]))
        if form == "secteurs":
            s = series[0]
            pairs = [(lab, v) for lab, v in zip(labels, s["valeurs"]) if v is not None and v > 0]
            if not pairs:
                self.problems.add(where, "secteurs : aucune valeur positive")
                return None
            if len(pairs) > 7:  # past seven slices nobody reads them: the rest becomes « Autres »
                pairs = sorted(pairs, key=lambda p: -p[1])
                pairs = pairs[:6] + [("Autres", sum(v for _, v in pairs[6:]))]
            labels, series = [p[0] for p in pairs], [{"nom": s["nom"], "valeurs": [p[1] for p in pairs]}]
        return {"forme": form, "etiquettes": labels, "series": series, "unite": _s(b.get("unite"), 20)}

    def _fiche(self, b, where):
        fields = []
        for f in _list(b.get("champs"), 60):
            if isinstance(f, dict) and (_s(f.get("libelle")) or _s(f.get("valeur"), 4000)):
                fields.append({"libelle": _s(f.get("libelle"), 120), "valeur": _s(_cell(f.get("valeur")), 4000)})
        if isinstance(b.get("champs"), dict):
            fields = [{"libelle": _s(k, 120), "valeur": _s(_cell(v), 4000)} for k, v in list(b["champs"].items())[:60]]
        link = self.web(b.get("lien"), where) if _s(b.get("lien")) else None
        img = self.image(b.get("image"), where) if _s(b.get("image")) else None
        if not fields and not link:
            self.problems.add(where, "fiche sans champ")
            return None
        return {"champs": fields, **({"lien": link} if link else {}), **({"image": img} if img else {})}

    def _formulaire(self, b, where):
        """A prefilled sheet the user corrects. Nothing is sent or created: the values come back as a message."""
        raw = b.get("champs")
        if isinstance(raw, dict):
            items = [{**(v if isinstance(v, dict) else {"valeur": v}), "id": k} for k, v in list(raw.items())[:24]]
        else:
            items = _list(raw, 24)
        fields, seen = [], set()
        for j, f in enumerate(items):
            if not isinstance(f, dict):
                continue
            label = _s(f.get("libelle") or f.get("id"), 120)
            if not label:
                continue
            kind = _s(f.get("type") or f.get("sorte"), 20).lower()
            if kind not in FIELD_TYPES:
                kind = "zone" if len(_s(f.get("valeur"), 8000)) > 200 else "texte"
            fid = _field_id(f.get("id") or label, j)
            if fid in seen:
                fid = f"{fid}-{j + 1}"[:40]
            seen.add(fid)
            options = [_s(o, 200) for o in _list(f.get("options"), 30)]
            options = [o for o in options if o]
            if kind == "liste" and not options:
                self.problems.add(where, f"« {label} » : une liste sans option")
                continue
            value = _field_prefill(kind, f.get("valeur"), options)
            fields.append({"id": fid, "libelle": label, "type": kind, "valeur": value, "requis": bool(f.get("requis")),
                           **({"options": options} if kind == "liste" else {}),
                           **({"aide": _s(f.get("aide"), 300)} if _s(f.get("aide")) else {})})
        if not fields:
            self.problems.add(where, "formulaire sans champ")
            return None
        intro = _s(b.get("texte"), 2000)
        return {"champs": fields, "bouton": _s(b.get("bouton"), 40) or "Valider", **({"texte": intro} if intro else {})}

    def _chronologie(self, b, where):
        items = [{"quand": _s(it.get("quand") or it.get("date"), 60), "titre": _s(it.get("titre"), 300),
                  "texte": _s(it.get("texte"), 1500)}
                 for it in _list(b.get("elements"), 200) if isinstance(it, dict) and (_s(it.get("titre")) or _s(it.get("texte")))]
        if not items:
            self.problems.add(where, "aucun élément")
            return None
        return {"elements": items}

    def _chiffres(self, b, where):
        items = [{"libelle": _s(it.get("libelle"), 80), "valeur": _s(_cell(it.get("valeur")), 40),
                  "evolution": _s(it.get("evolution"), 40), "detail": _s(it.get("detail"), 160)}
                 for it in _list(b.get("elements"), 12) if isinstance(it, dict) and _s(_cell(it.get("valeur")), 40)]
        if not items:
            self.problems.add(where, "aucun chiffre")
            return None
        return {"elements": items}

    def _progression(self, b, where):
        v = _num(b.get("valeur"))
        if v is None:
            self.problems.add(where, "valeur manquante (0 à 100)")
            return None
        return {"valeur": max(0.0, min(100.0, v)), "texte": _s(b.get("texte"), 300)}

    def _schema(self, b, where):
        svg = str(b.get("svg") or "").strip()
        if not re.match(r"^(<\?xml[^>]*>\s*)?<svg[\s>]", svg, re.I):
            self.problems.add(where, "le schéma doit être un document <svg>")
            return None
        if len(svg.encode("utf-8")) > MAX_SVG:
            self.problems.add(where, "schéma trop volumineux (300 Ko au plus)")
            return None
        if not re.search(r"\bxmlns\s*=", svg[:2000]):
            svg = re.sub(r"^(<\?xml[^>]*>\s*)?<svg", r'\1<svg xmlns="http://www.w3.org/2000/svg"', svg, count=1, flags=re.I)
        # drawn as an image (never in the page): no script runs and nothing loads from the web
        return {"svg": svg}

    def _application(self, b, where):
        html = str(b.get("html") or "").strip()
        if not html:
            self.problems.add(where, "html vide")
            return None
        if len(html.encode("utf-8")) > MAX_APP:
            self.problems.add(where, "application trop volumineuse (300 Ko au plus)")
            return None
        h = _num(b.get("hauteur"))
        # run by the UI in a sandboxed frame of the preview origin, with no network (see content.app_page)
        return {"html": html, "hauteur": int(max(120, min(1200, h))) if h else 400}

    def _fichiers(self, b, where):
        paths = []
        for p in _list(b.get("fichiers"), 40):
            p = _s(p, 2000)
            try:
                paths.append(self.file(p))
            except Exception as exc:  # noqa: BLE001
                self.problems.add(where, f"{p} — {getattr(exc, 'message', exc)}")
        if not paths:
            self.problems.add(where, "aucun fichier affichable")
            return None
        return {"fichiers": paths}

    def _choix(self, b, where):
        opts = [_s(o.get("libelle") if isinstance(o, dict) else o, 200) for o in _list(b.get("options"), 12)]
        opts = [o for o in opts if o]
        if not opts:
            self.problems.add(where, "aucune option")
            return None
        return {"question": _s(b.get("question"), 500), "options": opts, "multiple": bool(b.get("multiple"))}

    def _actions(self, b, where):
        buttons = []
        for j, x in enumerate(_list(b.get("boutons"), 8)):
            if not isinstance(x, dict) or not _s(x.get("libelle"), 60):
                continue
            label = _s(x.get("libelle"), 60)
            if _s(x.get("url")):
                w = self.web(x.get("url"), f"{where}, bouton {j + 1}")
                if w:
                    buttons.append({"libelle": label, "lien": w})
            else:
                buttons.append({"libelle": label, "message": _s(x.get("message"), 2000) or label})
        if not buttons:
            self.problems.add(where, "aucun bouton")
            return None
        return {"boutons": buttons}

    def _cartes(self, b, where):
        """Cards whose buttons the console runs on a click. One action per button, taken from the stored card."""
        from . import project_tools
        cards = []
        for j, x in enumerate(_list(b.get("elements"), 8)):
            if not isinstance(x, dict):
                continue
            title = _s(x.get("titre"), 120)
            if not title:
                continue
            buttons = []
            for k, btn in enumerate(_list(x.get("boutons"), 4)):
                got = self._card_button(btn, f"{where}, carte {j + 1}, bouton {k + 1}", project_tools.schedule)
                if got:
                    buttons.append(got)
            if not buttons:
                self.problems.add(f"{where}, carte {j + 1}", "aucun bouton")
                continue
            card = {"titre": title, "boutons": buttons}
            text = _s(x.get("texte"), 500)
            if text:
                card["texte"] = text
            cards.append(card)
        if not cards:
            self.problems.add(where, "aucune carte")
            return None
        return {"elements": cards}

    def _card_button(self, btn, where: str, schedule) -> dict | None:
        if not isinstance(btn, dict):
            return None
        label = _s(btn.get("libelle"), 60)
        if not label:
            return None
        kinds = [k for k in ("ouvrir", "message", "suivi", "terminee", "routine", "consigne")
                 if btn.get(k) not in (None, "", {})]
        if len(kinds) != 1:
            self.problems.add(where, "un bouton a exactement une action : ouvrir, message, suivi, terminee, routine ou consigne")
            return None
        kind, raw = kinds[0], btn.get(kinds[0])
        if kind == "ouvrir":
            if not isinstance(raw, dict):
                self.problems.add(where, "ouvrir est un objet")
                return None
            spec = {}
            for key, limit in (("outil", 80), ("contient", 200), ("id", 80)):
                v = _s(raw.get(key), limit)
                if v:
                    spec[key] = v
            if raw.get("rang") not in (None, ""):
                try:
                    spec["rang"] = max(1, min(50, int(raw.get("rang"))))
                except (TypeError, ValueError):
                    self.problems.add(where, "rang invalide")
                    return None
            if not any(k in spec for k in ("outil", "contient", "id")):
                self.problems.add(where, "ouvrir : indique l'outil, un extrait (contient) ou l'identifiant")
                return None
            return {"libelle": label, "faire": "ouvrir", "ouvrir": spec}
        if kind == "message":
            msg = _s(raw, 2000) if isinstance(raw, str) else ""
            if not msg:
                self.problems.add(where, "message vide")
                return None
            return {"libelle": label, "faire": "message", "message": msg}
        if kind == "suivi":
            line = _s(raw, 200) if isinstance(raw, str) else ""
            if not line:
                self.problems.add(where, "ligne vide")
                return None
            return {"libelle": label, "faire": "suivi", "ligne": line}
        if kind == "terminee":
            if isinstance(raw, str):
                line, msg = _s(raw, 200), ""
            elif isinstance(raw, dict):
                line, msg = _s(raw.get("ligne"), 200), _s(raw.get("message"), 2000)
            else:
                line, msg = "", ""
            if not line:
                self.problems.add(where, "ligne vide")
                return None
            out = {"libelle": label, "faire": "terminee", "ligne": line}
            if msg:
                out["message"] = msg
            return out
        if kind == "routine":
            if not isinstance(raw, dict):
                self.problems.add(where, "routine est un objet")
                return None
            name, body = _s(raw.get("nom"), 80), _s(raw.get("consigne"), 4000)
            if not name or not body:
                self.problems.add(where, "routine : nom et consigne requis")
                return None
            plan = raw.get("planification") if isinstance(raw.get("planification"), dict) else {}
            try:
                schedule(plan)
            except ValueError as exc:
                self.problems.add(where, f"planification : {exc}")
                return None
            return {"libelle": label, "faire": "routine", "routine": {
                "nom": name, "consigne": body, "description": _s(raw.get("description"), 300) or name,
                "planification": plan}}
        if isinstance(raw, str):
            text, description = _s(raw, 2000), ""
        elif isinstance(raw, dict):
            text = _s(raw.get("texte") or raw.get("consigne"), 2000)
            description = _s(raw.get("description"), 300)
        else:
            text, description = "", ""
        if not text:
            self.problems.add(where, "consigne vide")
            return None
        return {"libelle": label, "faire": "consigne",
                "consigne": {"texte": text, "description": description or _s(text, 120)}}


def check(args: dict, file: Callable[[str], str], trusted: list[str], previous: dict | None = None) -> tuple[dict | None, list[str], int]:
    """(display, problems, web images waiting for a click) of one call of the tool."""
    args = args if isinstance(args, dict) else {}
    c = Checker(file, trusted)
    raw = args.get("blocs")
    if isinstance(raw, dict):
        raw = [raw]
    blocks = [x for x in (c.block(b, i) for i, b in enumerate(_list(raw, MAX_BLOCKS))) if x]
    if isinstance(raw, list) and len(raw) > MAX_BLOCKS:
        c.problems.append(f"{len(raw) - MAX_BLOCKS} bloc(s) au-delà de {MAX_BLOCKS} ignoré(s)")
    if not blocks:
        return None, list(c.problems) or [
            "aucun bloc : passe « blocs », une liste d'objets avec un « type » (" + ", ".join(KINDS) + ") et ses "
            "champs, ex. [{\"type\": \"fiche\", \"champs\": [{\"libelle\": \"Client\", \"valeur\": \"…\"}]}]"], 0
    asked = _s(args.get("ou"), 20).lower().replace("ê", "e")
    if asked in WHERE:
        where = asked
    elif (previous or {}).get("ou") in WHERE:
        where = previous["ou"]
    elif any(b["type"] == "tableau_de_bord" for b in blocks):
        where = "fenetre"  # a report is looked at beside the discussion, unless the call says otherwise
    else:
        where = "conversation"
    doc = {"titre": _s(args.get("titre"), 200) or (previous or {}).get("titre") or "Affichage", "ou": where, "blocs": blocks}
    if len(json.dumps(doc, ensure_ascii=False)) > MAX_JSON:
        return None, [*c.problems, "affichage trop volumineux (600 Ko au plus) : réduis le tableau ou découpe-le"], 0
    return doc, list(c.problems), c.web_images


def summary(doc: dict) -> str:
    """« 2 images, un tableau (12 lignes) » for Claude."""
    parts = []
    for b in doc["blocs"]:
        k = b["type"]
        if k == "images":
            n = len(b["images"])
            parts.append(f"{n} image{'s' if n > 1 else ''}")
        elif k == "tableau":
            parts.append(f"tableau ({len(b['lignes'])} lignes)")
        elif k == "resultats":
            parts.append(f"{len(b['elements'])} résultat(s)")
        elif k == "graphique":
            parts.append(f"graphique en {b['forme']}")
        elif k == "tableau_de_bord":
            parts.append(f"tableau de bord ({len(b['lignes'])} lignes)")
        elif k == "formulaire":
            parts.append(f"formulaire ({len(b['champs'])} champs)")
        elif k == "cartes":
            parts.append(f"{len(b['elements'])} carte(s)")
        elif k == "application":
            parts.append(f"application ({len(b['html']) // 1024 + 1} Ko)")
        else:
            parts.append(k)
    return ", ".join(parts)


def answer_text(doc: dict, index: int, choice: list[int] | None, other: str, button: int | None,
                valeurs: dict | None = None) -> tuple[str, list[str], dict | None]:
    """The message sent to the session for a click in a display, the labels shown as answered,
    and, for a form, the values the user kept."""
    blocks = doc.get("blocs") or []
    if not 0 <= index < len(blocks):
        raise ValueError("Bloc introuvable.")
    b = blocks[index]
    head = f"[Affichage « {doc.get('titre') or 'Affichage'} »]"
    if b["type"] == "formulaire":
        return _form_answer(head, b, valeurs if isinstance(valeurs, dict) else {})
    if b["type"] == "choix":
        picked = [b["options"][i] for i in dict.fromkeys(choice or []) if isinstance(i, int) and 0 <= i < len(b["options"])]
        if not b.get("multiple"):
            picked = picked[:1]
        other = _s(other, 1000)
        labels = picked + ([other] if other else [])
        if not labels:
            raise ValueError("Aucune réponse choisie.")
        q = f" {b['question']}" if b.get("question") else ""
        return f"{head}{q} → {' ; '.join(labels)}", labels, None
    if b["type"] == "actions":
        btns = b["boutons"]
        if button is None or not 0 <= button < len(btns) or "message" not in btns[button]:
            raise ValueError("Bouton introuvable.")
        return f"{head} {btns[button]['message']}", [btns[button]["libelle"]], None
    raise ValueError("Ce bloc n'attend pas de réponse.")


def _form_answer(head: str, b: dict, got: dict) -> tuple[str, list[str], dict]:
    lines, labels, clean = [], [], {}
    for f in b.get("champs") or []:
        text = _field_answer(f["type"], got.get(f["id"]), f.get("options") or [])
        if text is None:
            raise ValueError(f"« {f['libelle']} » : valeur invalide.")
        if f.get("requis") and text in ("", "non") and f["type"] == "case":
            raise ValueError(f"Le champ « {f['libelle']} » est requis.")
        if f.get("requis") and f["type"] != "case" and not text:
            raise ValueError(f"Le champ « {f['libelle']} » est requis.")
        clean[f["id"]] = text
        shown = text if len(text) <= 80 else text[:79].rstrip() + "…"
        labels.append(f"{f['libelle']} : {shown or '—'}")
        if f["type"] == "zone" and "\n" in text:
            lines.append(f"{f['libelle']} :\n{text}")
        else:
            lines.append(f"{f['libelle']} : {text}")
    name = b.get("titre") or "Formulaire"
    return f"{head} Formulaire « {name} » :\n" + "\n".join(lines), labels, clean


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""
