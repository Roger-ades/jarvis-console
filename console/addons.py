"""Modules (addons): tools, applications and panels that Claude develops for JARVIS itself.

A module is a folder of addons/ (next to data/): an addon.json manifest, an HTML page and what it loads
next to it (scripts, styles, images, data files). The console lists them (button Modules, Ctrl+K) and opens
each one in a window of its own. Claude writes them with its usual file tools, under the usual permissions,
then checks and opens them with the tool "modules" of the jarvis server.

A module runs like the "application" block of presenter (content.py): on the preview origin, in a sandboxed
frame (opaque origin) without network, inside a shell whose only allowed frame is the module. It can load its
own files and nothing else. What it does beyond its window goes through window.jarvis, answered by the console
page (static/js/addons.js):
- jarvis.donnees: its own storage, kept by the console (store, one entry per module);
- jarvis.envoyer: puts a text in the command bar, the user sends it or not;
- jarvis.demander: a question to Claude, answered in the module (permission "claude" in the manifest, granted
  once by the user; a short discussion of the account, with its preset and validations, then deleted);
- jarvis.ouvrir: an https page, proposed to the user like a link of an answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MANIFEST = "addon.json"
ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PERMISSIONS = {"claude": "demander à Claude (une courte discussion du compte, avec ses autorisations)"}
MAX_FILES = 300
MAX_FILE = 5 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_DATA = 1024 * 1024        # JSON of a module's storage
MAX_ASK = 20_000              # characters of a question to Claude
ASK_MODELS = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,80}$")
SKIP = {".git", "node_modules", "__pycache__", ".DS_Store"}
TEXT = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".md"}
_REMOTE = re.compile(r"""(?:\bsrc\s*=\s*["']?|<link\b[^>]*\bhref\s*=\s*["']?|url\(\s*["']?|@import\s+["']?|\bfrom\s+["']|\bimport\s*\(\s*["']|\bfetch\s*\(\s*["'])(?:https?:)?//""", re.I)

README = "README.md"


def kv_key(aid: str) -> str:
    return f"addon_data:{aid}"


def _s(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def files(folder: Path) -> list[Path]:
    """The module's files (no link leaving it, nothing hidden by SKIP), sorted."""
    out = []
    root = folder.resolve()
    for p in sorted(folder.rglob("*")):
        if any(part in SKIP for part in p.relative_to(folder).parts) or not p.is_file():
            continue
        try:
            p.resolve().relative_to(root)
        except ValueError:
            continue
        out.append(p)
    return out


def read(folder: Path) -> dict:
    """One module as the console sees it: its manifest's fields, and what is wrong with it ("problemes")."""
    aid = folder.name
    problems: list[str] = []
    warnings: list[str] = []
    raw: dict = {}
    if not ID_RX.match(aid):
        problems.append("nom de dossier invalide : minuscules, chiffres et tirets (2 à 41 caractères)")
    mf = folder / MANIFEST
    try:
        raw = json.loads(mf.read_text("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("un objet JSON est attendu")
    except FileNotFoundError:
        problems.append(f"{MANIFEST} manquant")
        raw = {}
    except (ValueError, OSError) as exc:
        problems.append(f"{MANIFEST} illisible : {exc}")
        raw = {}
    entry = str(raw.get("entree") or "index.html").replace("\\", "/").lstrip("/")
    perms = raw.get("permissions") or []
    perms = [perms] if isinstance(perms, str) else perms if isinstance(perms, list) else []
    unknown = [str(p) for p in perms if p not in PERMISSIONS]
    if unknown:
        problems.append("permissions inconnues : " + ", ".join(unknown) + f" (possibles : {', '.join(PERMISSIONS)})")
    perms = sorted({p for p in perms if p in PERMISSIONS})
    size, count, sig = 0, 0, hashlib.sha256()
    if folder.is_dir():
        for p in files(folder):
            count += 1
            st = p.stat()
            size += st.st_size
            rel = p.relative_to(folder).as_posix()
            sig.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode())
            if st.st_size > MAX_FILE:
                problems.append(f"{rel} : plus de {MAX_FILE // 1024 // 1024} Mo")
            elif p.suffix.lower() in TEXT and st.st_size < 2 * 1024 * 1024:
                try:
                    if _REMOTE.search(p.read_text("utf-8", "replace")):
                        warnings.append(f"{rel} charge quelque chose depuis le web : ce sera bloqué (pas de réseau), "
                                        "mets le fichier dans le module")
                except OSError:
                    pass
    if count > MAX_FILES:
        problems.append(f"{count} fichiers : {MAX_FILES} au plus")
    if size > MAX_TOTAL:
        problems.append(f"{size // 1024 // 1024} Mo : {MAX_TOTAL // 1024 // 1024} Mo au plus")
    if not resolve(folder, entry) or Path(entry).suffix.lower() not in (".html", ".htm"):
        problems.append(f"page d'entrée introuvable : {entry} (une page .html du module)")

    def dim(k, lo, hi, default):
        try:
            return int(max(lo, min(hi, float(raw.get(k)))))
        except (TypeError, ValueError):
            return default

    return {
        "id": aid,
        "nom": _s(raw.get("nom"), 80) or aid,
        "description": _s(raw.get("description"), 400),
        "version": _s(raw.get("version"), 20),
        "icone": _s(raw.get("icone"), 4),
        "entree": entry,
        "largeur": dim("largeur", 320, 1800, 820),
        "hauteur": dim("hauteur", 240, 1400, 640),
        "permissions": perms,
        "fichiers": count,
        "taille": size,
        "signature": sig.hexdigest()[:16],
        "problemes": problems,
        "avertissements": warnings[:10],
        "folder": str(folder),
    }


def scan(root: Path) -> list[dict]:
    """Every module of the folder, by name."""
    if not root.is_dir():
        return []
    out = [read(d) for d in root.iterdir() if d.is_dir() and d.name not in SKIP and not d.name.startswith((".", "_"))]
    return sorted(out, key=lambda a: (a["nom"].lower(), a["id"]))


def resolve(folder: Path, rel: str) -> Path | None:
    """A file of the module by its relative path, never outside it, never the manifest's neighbours in SKIP."""
    rel = str(rel or "").replace("\\", "/").split("?")[0].split("#")[0]
    if not rel or rel.startswith("/") or ":" in rel or any(p in ("..", "") or p in SKIP for p in rel.split("/")):
        return None
    try:
        root = folder.resolve()
        p = (folder / rel).resolve()
        p.relative_to(root)
    except (OSError, ValueError):
        return None
    return p if p.is_file() else None


def permission_key(a: dict) -> str:
    """What the user grants: this module with these permissions (its code may change, its powers may not)."""
    return f"{a['id']}|{','.join(a['permissions'])}"


# ---------------------------------------------------------------- pages
# The bridge goes in front of each HTML page of the module. It speaks to the console page (window.top) by
# messages; the console checks the frame is one of its module windows before answering.
BRIDGE = """<script>(function(){
for (const k of ["RTCPeerConnection","webkitRTCPeerConnection","RTCDataChannel","RTCSessionDescription",
                 "RTCIceCandidate","RTCRtpSender","RTCRtpReceiver"]) { try { delete window[k]; } catch (e) {} }
let seq = 0;
const waiting = new Map();
window.addEventListener("message", (ev) => {
  const d = ev.data;
  if (!d || d.jarvisAddon !== 1 || ev.source !== window.top || !waiting.has(d.id)) return;
  const w = waiting.get(d.id);
  waiting.delete(d.id);
  if (d.ok) w.resolve(d.valeur); else w.reject(new Error(d.erreur || "Refusé par la console."));
});
const call = (type, data) => new Promise((resolve, reject) => {
  const id = ++seq;
  waiting.set(id, {resolve, reject});
  window.top.postMessage(Object.assign({jarvisAddon: 1, id: id, type: type}, data || {}), "*");
});
const text = (v) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
Object.defineProperty(window, "jarvis", {value: Object.freeze({
  module: __ID__,
  donnees: Object.freeze({
    lire(cle) { return call("lire", {cle: String(cle)}); },
    ecrire(cle, valeur) { return call("ecrire", {cle: String(cle), valeur: valeur === undefined ? null : valeur}); },
    supprimer(cle) { return call("ecrire", {cle: String(cle), valeur: null}); },
    cles() { return call("cles"); },
  }),
  envoyer(contenu) { return call("envoyer", {contenu: text(contenu)}); },
  demander(consigne, options) {
    const o = options || {};
    return call("demander", {consigne: text(consigne), format: o.format === "json" ? "json" : "texte",
                             modele: o.modele ? String(o.modele) : ""});
  },
  ouvrir(url) { return call("ouvrir", {url: String(url || "")}); },
  notifier(texte) { return call("notifier", {texte: String(texte || "")}); },
}), writable: false, configurable: false});
})();</script>"""
STYLE = ("<style>html{color-scheme:light dark}body{margin:12px 14px;font:14px/1.45 'Segoe UI',system-ui,"
         "sans-serif}</style>")
_DOCTYPE = re.compile(r"^\s*(?:﻿)?\s*<!doctype\b[^>]*>", re.I)
_REFRESH = re.compile(r"<meta\b[^>]*\bhttp-equiv\s*=\s*['\"]?\s*refresh\b[^>]*>", re.I)


def page(html: str, aid: str) -> str:
    """An HTML page of the module, after the bridge and a base style (the module's own style wins)."""
    html = _DOCTYPE.sub("", _REFRESH.sub("", html))
    return f'<!doctype html><meta charset="utf-8">{BRIDGE.replace("__ID__", json.dumps(aid))}{STYLE}' + html


def policy(base: str, ancestors: str) -> str:
    """Its own files only (base: the module's address, a folder), no network, no frame, sandboxed."""
    return (f"default-src 'none'; script-src {base} 'unsafe-inline' 'unsafe-eval'; style-src {base} 'unsafe-inline'; "
            f"img-src {base} data: blob:; font-src {base} data:; media-src {base} data: blob:; connect-src {base}; "
            "frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; form-action 'none'; "
            f"base-uri 'none'; frame-ancestors {ancestors}; sandbox allow-scripts allow-forms allow-modals")


# ---------------------------------------------------------------- the tool "modules" (jarvis server)
SPEC = {
    "name": "modules",
    "description": (
        "Les modules de JARVIS : des outils, applications et panneaux que tu développes pour la console elle-même, "
        "chacun dans un dossier du répertoire des modules (indiqué dans ton prompt système, lis son README.md avant "
        "d'en écrire un). Un module = addon.json (nom, description, icone, entree, largeur, hauteur, permissions) + une "
        "page HTML et ses fichiers (JS, CSS, images, données JSON). Il s'ouvre dans une fenêtre de la console, isolé et "
        "sans réseau ; window.jarvis lui donne un stockage (donnees.lire/ecrire), envoyer (texte dans la barre), "
        "demander (une question à Claude, permission \"claude\"), ouvrir (une page https), notifier. "
        "action = lister : les modules et leurs problèmes. action = verifier (id) : ce qui empêche le module de "
        "s'ouvrir ou sera bloqué. action = ouvrir (id) : l'ouvre (ou le recharge) dans une fenêtre, après "
        "vérification. action = donnees (id) : lit son stockage, pour déboguer. action = proposer (id, nom, description, "
        "raison) : quand l'utilisateur n'a rien demandé mais qu'un module l'aiderait (un besoin qui revient, un suivi à "
        "garder, un calcul refait à la main), propose-le-lui sur une carte ; son clic vaut demande, et tu l'écris alors "
        "dans ce même tour. Écris les fichiers avec tes outils habituels ; après chaque modification, ouvre-le pour que "
        "l'utilisateur voie le résultat."),
    "inputSchema": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["lister", "verifier", "ouvrir", "donnees", "proposer"]},
            "id": {"type": "string", "description": "verifier, ouvrir, donnees : le nom du dossier du module. proposer : "
                                                    "celui du module à créer (minuscules, chiffres, tirets), ou d'un "
                                                    "module existant à améliorer."},
            "nom": {"type": "string", "description": "proposer : le nom affiché du module."},
            "icone": {"type": "string", "description": "proposer : un emoji."},
            "description": {"type": "string", "description": "proposer : ce que fera le module, en une ou deux phrases "
                                                            "concrètes (ce qu'on y voit, ce qu'on y fait)."},
            "raison": {"type": "string", "description": "proposer : pourquoi maintenant, tiré de la conversation "
                                                       "(ex. « tu recalcules ces marges à chaque devis »)."},
        },
    },
}


def proposal(args: dict, root: Path) -> dict:
    """What Claude proposes to build (action proposer), checked; raises ValueError with the reason."""
    aid = str(args.get("id") or "").strip().lower()
    if not ID_RX.match(aid):
        raise ValueError("« id » : minuscules, chiffres et tirets (2 à 41 caractères), le nom du dossier du module.")
    nom, desc, why = _s(args.get("nom"), 60), _s(args.get("description"), 400), _s(args.get("raison"), 300)
    if not desc:
        raise ValueError("« description » est requise : ce que fera le module.")
    current = read(root / aid) if (root / aid).is_dir() else None
    return {"quoi": "module", "id": aid, "nom": nom or (current or {}).get("nom") or aid,
            "icone": _s(args.get("icone"), 4) or (current or {}).get("icone") or "🧩", "description": desc,
            "raison": why, "existe": current is not None, "dossier": str(root / aid)}


def describe(a: dict) -> str:
    perms = ", ".join(a["permissions"]) or "aucune"
    head = (f"- {a['id']} : « {a['nom']} »" + (f" v{a['version']}" if a["version"] else "")
            + (f" — {a['description']}" if a["description"] else "")
            + f" ({a['fichiers']} fichier{'s' if a['fichiers'] > 1 else ''}, {max(1, a['taille'] // 1024)} Ko, "
              f"entrée {a['entree']}, permissions : {perms})")
    out = [head]
    out += [f"  problème : {p}" for p in a["problemes"]]
    out += [f"  attention : {w}" for w in a["avertissements"]]
    return "\n".join(out)


def ask_prompt(a: dict, question: str, fmt: str) -> str:
    """The prompt of a question asked by a module (jarvis.demander)."""
    form = ("Réponds uniquement par un JSON valide, sans texte autour ni bloc de code : le module le lira tel quel."
            if fmt == "json" else "Réponds directement par le texte demandé, sans préambule : il est affiché dans le module.")
    return (f"[Module « {a['nom']} » ({a['id']})] Le module te pose cette question, après un clic de l'utilisateur. "
            "Tu n'as pas de fenêtre : ta réponse finale revient au module, qui l'affiche lui-même. Utilise tes outils "
            "si besoin (lecture avant tout ; une action qui écrit ou envoie reste soumise aux validations habituelles), "
            f"sans les outils « presenter » ni « afficher ». {form}\n\n{question}")


def answer(text: str, fmt: str):
    """The final answer, as the module asked it: text, or parsed JSON (a code fence around it is tolerated)."""
    text = str(text or "").strip()
    if fmt != "json":
        return text
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    body = m.group(1) if m else text
    try:
        return json.loads(body)
    except ValueError:
        for m in re.finditer(r"[\[{]", body):  # the first JSON value inside a sentence
            try:
                return json.JSONDecoder().raw_decode(body[m.start():])[0]
            except ValueError:
                continue
    raise ValueError("Claude n'a pas répondu par un JSON lisible.")
