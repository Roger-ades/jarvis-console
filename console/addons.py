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
- jarvis.ouvrir: an https page, proposed to the user like a link of an answer;
- jarvis.executer: one of its Python tasks ("taches" of the manifest, permission "python"), run by the console
  without a model (console/addon_tasks.py), reading Odoo through the console's key if it asks "odoo".
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MANIFEST = "addon.json"
ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PERMISSIONS = {
    "claude": "demander à Claude (une courte discussion du compte, avec ses autorisations)",
    "python": "lancer ses scripts Python sur ce PC (ses « taches », avec tes droits, dans son dossier)",
    "odoo": "lire Odoo, en lecture seule, avec la clé API de la console (depuis ses scripts Python)",
}
TASK_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
TASK_TIME = (10, 300, 1800)   # s: least, default, most of a task's duree_max
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
    size, count, sig, code = 0, 0, hashlib.sha256(), hashlib.sha256()
    if folder.is_dir():
        for p in files(folder):
            count += 1
            st = p.stat()
            size += st.st_size
            rel = p.relative_to(folder).as_posix()
            sig.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode())
            if p.suffix.lower() == ".py":
                try:
                    code.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
                except OSError:
                    code.update(rel.encode() + b"\0?\0")
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

    tasks = _tasks(folder, raw.get("taches"), perms, problems, warnings)

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
        "taches": tasks,
        "code": code.hexdigest()[:16],
        "fichiers": count,
        "taille": size,
        "signature": sig.hexdigest()[:16],
        "problemes": problems,
        "avertissements": warnings[:10],
        "folder": str(folder),
    }


def _tasks(folder: Path, raw, perms: list[str], problems: list[str], warnings: list[str]) -> dict:
    """The manifest's "taches": {"nom": {"script": "x.py", "description": "...", "duree_max": 600}}."""
    if raw in (None, {}, []):
        if "python" in perms:
            warnings.append("permission « python » sans « taches » dans addon.json")
        return {}
    if not isinstance(raw, dict):
        problems.append("« taches » : un objet {nom: {script, description, duree_max}} est attendu")
        return {}
    out = {}
    for name, t in list(raw.items())[:20]:
        t = {"script": t} if isinstance(t, str) else t if isinstance(t, dict) else {}
        script = str(t.get("script") or "").replace("\\", "/").lstrip("/")
        if not TASK_RX.match(str(name)):
            problems.append(f"tâche « {name} » : nom en minuscules, chiffres, tirets ou _")
            continue
        if not script.lower().endswith(".py") or not resolve(folder, script):
            problems.append(f"tâche « {name} » : script introuvable ({script or 'vide'}, un fichier .py du module)")
            continue
        try:
            secs = int(max(TASK_TIME[0], min(TASK_TIME[2], float(t.get("duree_max") or TASK_TIME[1]))))
        except (TypeError, ValueError):
            secs = TASK_TIME[1]
        out[str(name)] = {"script": script, "description": _s(t.get("description"), 300), "duree_max": secs}
    if out and "python" not in perms:
        problems.append("des « taches » demandent la permission « python » dans addon.json")
    if "odoo" in perms and "python" not in perms:
        warnings.append("la permission « odoo » ne sert qu'aux scripts Python (permission « python »)")
    return out


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
    """What the user grants: this module with these permissions (its page may change, its powers may not); with
    "python", its Python code as it is too: a script changed is a new program, granted again."""
    key = f"{a['id']}|{','.join(a['permissions'])}"
    return key + f"|{a['code']}" if "python" in a["permissions"] else key


def python_files(a: dict) -> list[str]:
    """The module's Python files, what "python" grants (shown to the user before)."""
    folder = Path(a["folder"])
    return [p.relative_to(folder).as_posix() for p in files(folder) if p.suffix.lower() == ".py"][:50]


# ---------------------------------------------------------------- pages
# The bridge goes in front of each HTML page of the module. It speaks to the console page (window.top) by
# messages; the console checks the frame is one of its module windows before answering. Each request
# carries a MessagePort of its own and the answer comes back on it: never a message to the module's window,
# whose sender would be the console's page, which in the desktop app ("Intégré au bureau") is not the
# module's window.top but the hidden JARVIS window; and no other frame can answer in the console's place.
BRIDGE = """<script>(function(){
for (const k of ["RTCPeerConnection","webkitRTCPeerConnection","RTCDataChannel","RTCSessionDescription",
                 "RTCIceCandidate","RTCRtpSender","RTCRtpReceiver"]) { try { delete window[k]; } catch (e) {} }
let seq = 0, busy = 0;
// a task or a question to Claude under way: <html data-jarvis-occupe> (the running light at the top)
const occupe = (n) => { busy += n; try { document.documentElement.toggleAttribute("data-jarvis-occupe", busy > 0); } catch (e) {} };
const QUICK = 20000;  // ms: storage, bar, notification (a question to Claude or a confirmation may take longer)
const call = (type, data, ms, onProgress) => new Promise((resolve, reject) => {
  const id = ++seq, lourd = type === "executer" || type === "demander";
  const ch = new MessageChannel();
  let timer = 0;
  const done = () => { clearTimeout(timer); ch.port1.onmessage = null; ch.port1.close(); if (lourd) occupe(-1); };
  if (lourd) occupe(1);
  ch.port1.onmessage = (ev) => {
    const d = ev.data;
    if (!d || d.jarvisAddon !== 1 || d.id !== id) return;
    if (d.progres !== undefined) { try { onProgress && onProgress(d.progres); } catch (e) {} return; }
    done();
    if (d.ok) resolve(d.valeur); else reject(new Error(d.erreur || "Refusé par la console."));
  };
  if (ms) timer = setTimeout(() => { done(); reject(new Error("La console n'a pas répondu (" + type + ").")); }, ms);
  window.top.postMessage(Object.assign({jarvisAddon: 1, id: id, type: type}, data || {}), "*", [ch.port2]);
});
const text = (v) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
Object.defineProperty(window, "jarvis", {value: Object.freeze({
  module: __ID__,
  donnees: Object.freeze({
    lire(cle) { return call("lire", {cle: String(cle)}, QUICK); },
    ecrire(cle, valeur) { return call("ecrire", {cle: String(cle), valeur: valeur === undefined ? null : valeur}, QUICK); },
    supprimer(cle) { return call("ecrire", {cle: String(cle), valeur: null}, QUICK); },
    cles() { return call("cles", null, QUICK); },
  }),
  envoyer(contenu) { return call("envoyer", {contenu: text(contenu)}, QUICK); },
  demander(consigne, options) {
    const o = options || {};
    return call("demander", {consigne: text(consigne), format: o.format === "json" ? "json" : "texte",
                             modele: o.modele ? String(o.modele) : ""});
  },
  ouvrir(url) { return call("ouvrir", {url: String(url || "")}); },
  notifier(texte) { return call("notifier", {texte: String(texte || "")}, QUICK); },
  executer(tache, params, options) {
    const o = options || {};
    return call("executer", {tache: String(tache || ""), params: params === undefined ? null : params}, 0,
                typeof o.progres === "function" ? o.progres : null);
  },
}), writable: false, configurable: false});
// the console's look: its theme and the account's color, now and each time they change (on a port it keeps)
try {
  const look = new MessageChannel();
  look.port1.onmessage = (ev) => {
    const d = ev.data || {}, root = document.documentElement;
    if (typeof d.theme === "string") root.setAttribute("data-theme", d.theme);
    if (typeof d.couleur === "string" && /^#[0-9a-f]{3,8}$/i.test(d.couleur)) root.style.setProperty("--pc", d.couleur);
  };
  window.top.postMessage({jarvisAddon: 1, id: 0, type: "apparence"}, "*", [look.port2]);
} catch (e) {}
})();</script>"""
# The console's look, in a layer of its own: the module's styles (outside any layer) always win over it. Its
# variables are the console's (--surface, --text, --accent, --pc the account's color, --elev-n, --sink...),
# following the console's theme (data-theme on <html>, kept up to date by the bridge).
_DARK = ("--bg:#141a23;--bg-2:#171e28;--surface:#1c2430;--surface-2:#222b38;--surface-3:#2a3442;--surface-4:#343f4e;"
         "--border:rgba(160,186,214,.09);--border-2:rgba(160,186,214,.16);--border-3:rgba(160,186,214,.28);"
         "--text:#e7edf4;--text-2:#aab6c3;--text-3:#74828f;--text-4:#535f6d;"
         "--accent:#72c9ff;--accent-2:#3d9fe0;--accent-ink:#03121d;--accent-soft:rgba(114,201,255,.12);"
         "--ok:#4fd3a0;--warn:#f0b45c;--err:#f26b80;--info:#a3adff;--violet:#b69cff;"
         "--ok-soft:rgba(79,211,160,.1);--warn-soft:rgba(240,180,92,.1);--err-soft:rgba(242,107,128,.1);"
         "--code-bg:#171d26;--hi:rgba(255,255,255,.06);--sh:0,0,0;--k:1;color-scheme:dark")
_LIGHT = ("--bg:#eef1f5;--bg-2:#f6f8fa;--surface:#fff;--surface-2:#f6f8fa;--surface-3:#eef2f6;--surface-4:#e3e9ef;"
          "--border:rgba(20,40,60,.08);--border-2:rgba(20,40,60,.14);--border-3:rgba(20,40,60,.26);"
          "--text:#0f1b27;--text-2:#3f5163;--text-3:#6c7c8c;--text-4:#9aa7b3;"
          "--accent:#0a78b8;--accent-2:#06659c;--accent-ink:#fff;--accent-soft:rgba(10,120,184,.09);"
          "--ok:#0c8a5b;--warn:#b36b00;--err:#c9304a;--info:#4652c9;--violet:#6f4fd6;"
          "--ok-soft:rgba(12,138,91,.08);--warn-soft:rgba(179,107,0,.08);--err-soft:rgba(201,48,74,.07);"
          "--code-bg:#f3f6f9;--hi:rgba(255,255,255,.85);--sh:15,27,39;--k:.4;color-scheme:light")
# The motion of the modules (addon_kit.css, addon_kit.js): what appears comes in, figures count up, charts draw.
_KIT_CSS = Path(__file__).with_name("addon_kit.css").read_text("utf-8")
_KIT_JS = Path(__file__).with_name("addon_kit.js").read_text("utf-8")
assert "</" not in _KIT_CSS + _KIT_JS
STYLE = (
    "<style>@layer jarvis{"
    f":root{{{_DARK};--pc:var(--accent);"
    "--font:'Segoe UI Variable Text','Segoe UI',system-ui,-apple-system,sans-serif;"
    "--font-display:'Segoe UI Variable Display','Segoe UI Variable Text','Segoe UI',system-ui,sans-serif;"
    "--mono:'Cascadia Mono','Cascadia Code',Consolas,monospace;--r:8px;--r-lg:12px;"
    "--elev-1:inset 0 1px 0 var(--hi),0 1px 2px rgba(var(--sh),calc(.22*var(--k))),0 3px 8px -4px rgba(var(--sh),calc(.35*var(--k)));"
    "--elev-2:inset 0 1px 0 var(--hi),0 2px 4px rgba(var(--sh),calc(.2*var(--k))),0 12px 26px -12px rgba(var(--sh),calc(.55*var(--k)));"
    "--elev-3:0 10px 30px -14px rgba(var(--sh),calc(.6*var(--k))),0 3px 8px -4px rgba(var(--sh),calc(.35*var(--k)));"
    "--sink:inset 0 2px 4px -1px rgba(var(--sh),calc(.35*var(--k)))}"
    f":root[data-theme=clair]{{{_LIGHT}}}"
    f"@media (prefers-color-scheme:light){{:root[data-theme=systeme]{{{_LIGHT}}}}}"
    "*,::before,::after{box-sizing:border-box}"
    "body{margin:12px 14px;font:13px/1.5 var(--font);color:var(--text);background:var(--surface);"
    "-webkit-font-smoothing:antialiased}"
    "h1,h2,h3{font-family:var(--font-display);font-weight:600;color:var(--text)}"
    "a{color:var(--accent)}::selection{background:color-mix(in srgb,var(--accent) 30%,transparent)}"
    "button,input,select,textarea{font:inherit;color:var(--text)}"
    "button{cursor:pointer;padding:5px 12px;border-radius:var(--r);border:1px solid var(--border-2);background:var(--surface-2);"
    "box-shadow:inset 0 1px 0 var(--hi),0 1px 2px rgba(var(--sh),calc(.25*var(--k)));transition:background .15s,border-color .15s}"
    "button:hover{background:var(--surface-3);border-color:var(--border-3)}"
    "button:active{box-shadow:var(--sink)}button:disabled{opacity:.45;cursor:default}"
    "button.primary,button.primaire,button[type=submit]{background:var(--accent);border-color:transparent;color:var(--accent-ink);"
    "font-weight:600;box-shadow:inset 0 1px 0 rgba(255,255,255,.25),0 2px 6px -2px color-mix(in srgb,var(--accent) 50%,transparent)}"
    "button.primary:hover,button.primaire:hover,button[type=submit]:hover{background:color-mix(in srgb,var(--accent) 88%,white)}"
    "input:not([type=checkbox],[type=radio],[type=range],[type=color]),select,textarea{padding:6px 9px;border-radius:var(--r);"
    "border:1px solid var(--border-2);background:var(--bg-2);box-shadow:var(--sink)}"
    "select option{background:var(--surface);color:var(--text)}"
    ":is(input,select,textarea):hover{border-color:var(--border-3)}"
    ":focus-visible{outline:2px solid color-mix(in srgb,var(--accent) 70%,transparent);outline-offset:1px}"
    "input[type=checkbox],input[type=radio],input[type=range],progress{accent-color:var(--accent)}"
    "table{border-collapse:collapse}th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}"
    "th{color:var(--text-3);font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:var(--surface)}"
    "tbody tr:hover td{background:var(--surface-2)}"
    "pre,code,kbd{font-family:var(--mono);font-size:12px}pre{padding:10px 12px;border-radius:var(--r);background:var(--code-bg);"
    "box-shadow:var(--sink);overflow:auto}"
    "hr{border:0;border-top:1px solid var(--border)}"
    "dialog{color:var(--text);background:var(--surface);border:0;border-radius:14px;padding:16px 18px;"
    "box-shadow:var(--elev-3),0 0 0 1px var(--border-2),inset 0 1px 0 var(--hi)}"
    "dialog::backdrop{background:rgba(var(--sh),calc(.45*var(--k)+.1));backdrop-filter:blur(3px)}"
    "::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}"
    "::-webkit-scrollbar-thumb{background:var(--border-2);border-radius:9px;border:2px solid transparent;background-clip:padding-box}"
    "::-webkit-scrollbar-thumb:hover{background:var(--border-3);border:2px solid transparent;background-clip:padding-box}"
    + _KIT_CSS + "}</style><script>" + _KIT_JS + "</script>")
THEMES = {"sombre", "clair", "systeme"}
_DOCTYPE = re.compile(r"^\s*(?:﻿)?\s*<!doctype\b[^>]*>", re.I)
_REFRESH = re.compile(r"<meta\b[^>]*\bhttp-equiv\s*=\s*['\"]?\s*refresh\b[^>]*>", re.I)


def page(html: str, aid: str, theme: str = "sombre") -> str:
    """An HTML page of the module, after the bridge and the console's look (the module's own style wins), in the
    console's theme (the module's own <html> tag only adds its attributes to this one)."""
    html = _DOCTYPE.sub("", _REFRESH.sub("", html))
    theme = theme if theme in THEMES else "sombre"
    return (f'<!doctype html><html data-theme="{theme}"><meta charset="utf-8">'
            f'{BRIDGE.replace("__ID__", json.dumps(aid))}{STYLE}' + html)


def policy(base: str, ancestors: str) -> str:
    """Its own files only (base: the module's address, a folder), no network, no frame, sandboxed."""
    return (f"default-src 'none'; script-src {base} 'unsafe-inline' 'unsafe-eval'; style-src {base} 'unsafe-inline'; "
            f"img-src {base} data: blob:; font-src {base} data:; media-src {base} data: blob:; connect-src {base}; "
            "frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; form-action 'none'; "
            f"base-uri 'none'; frame-ancestors {ancestors}; sandbox allow-scripts allow-forms allow-modals")


# ---------------------------------------------------------------- the tool "modules" (jarvis server)
# modules partager / recus / importer (d'avant le hub) : ce que Claude répond
SHARE_BY_HUB = ("Le partage des modules ne passe plus par toi. Pour en envoyer un à un collègue, l'utilisateur ouvre le "
                "module et clique sur Partager (en haut de sa fenêtre) : il passe par le hub de l'équipe. Un module reçu "
                "arrive dans sa boîte de réception, où un clic l'installe. Dis-le-lui en une phrase.")

SPEC = {
    "name": "modules",
    "description": (
        "Les modules de JARVIS : des outils, applications et panneaux que tu développes pour la console elle-même, "
        "chacun dans un dossier du répertoire des modules (indiqué dans ton prompt système, lis son README.md avant "
        "d'en écrire un). Un module = addon.json (nom, description, icone, entree, largeur, hauteur, permissions) + une "
        "page HTML et ses fichiers (JS, CSS, images, données JSON). Il s'ouvre dans une fenêtre de la console, isolé et "
        "sans réseau ; window.jarvis lui donne un stockage (donnees.lire/ecrire), envoyer (texte dans la barre), "
        "demander (une question à Claude, permission \"claude\"), ouvrir (une page https), notifier, executer (une "
        "tâche Python du module, déclarée dans \"taches\", permission \"python\" ; avec \"odoo\", elle lit Odoo en lecture "
        "seule par la clé de la console). Pour des données à rafraîchir (Odoo surtout), écris une tâche Python plutôt "
        "que jarvis.demander : la console la lance sans modèle, rien ne passe par toi, zéro jeton. "
        "action = lister : les modules et leurs problèmes. action = verifier (id) : ce qui empêche le module de "
        "s'ouvrir ou sera bloqué. action = ouvrir (id) : l'ouvre (ou le recharge) dans une fenêtre, après "
        "vérification. action = donnees (id) : lit son stockage, pour déboguer. action = executer (id, tache, params) : "
        "lance une tâche Python du module et attend sa fin (son résultat et ses dernières lignes de sortie te "
        "reviennent) ; l'utilisateur valide sur une carte si son code n'est pas encore autorisé. "
        "Le partage d'un module avec un collègue ne passe pas par toi : bouton Partager de sa fenêtre, par le hub, et le "
        "collègue l'installe d'un clic depuis sa boîte de réception. "
        "action = proposer (id, nom, description, "
        "raison) : quand l'utilisateur n'a rien demandé mais qu'un module l'aiderait (un besoin qui revient, un suivi à "
        "garder, un calcul refait à la main), propose-le-lui sur une carte ; son clic vaut demande, et tu l'écris alors "
        "dans ce même tour. Écris les fichiers avec tes outils habituels ; après chaque modification, ouvre-le pour que "
        "l'utilisateur voie le résultat."),
    "inputSchema": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["lister", "verifier", "ouvrir", "donnees", "executer", "proposer"]},
            "tache": {"type": "string", "description": "executer : le nom de la tâche dans les « taches » de addon.json."},
            "params": {"type": "object", "description": "executer : les paramètres de la tâche (jarvis.params)."},
            "id": {"type": "string", "description": "verifier, ouvrir, donnees, executer : le nom du "
                                                    "dossier du module. proposer : "
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
    out += [f"  tâche {n} : {t['script']}, {t['duree_max']} s au plus" + (f" — {t['description']}" if t["description"] else "")
            for n, t in a.get("taches", {}).items()]
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
