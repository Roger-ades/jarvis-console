// Displays composed by Claude (tool mcp__jarvis__presenter): typed blocks checked by the server, drawn
// here always the same way. A display lives in the conversation, in a window or in a modal; every place
// that shows it is redrawn when Claude updates it (same id) or when the user answers a choice.
// Web images outside the approved domains are only loaded after a click (an address written by a model
// could carry data out); local files come through the console's own API, like previews.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { confirmDialog, copyText, h, toast } from "./util.js";
import { openPreview, fileBlob, pinButton, thumbnail } from "./viewer.js";
import { joinButton } from "./regard.js";
import { colorOf, paint } from "./tint.js";
import * as wm from "./wm.js";

const SVGNS = "http://www.w3.org/2000/svg";
const entries = new Map(); // `${taskId}|${key}` -> {taskId, key, doc, rev, answers: Map, mounts: Set, …}
let settings = { autoImages: () => false, appAction: null, pinToDesktop: null };
export function configure(s) { settings = { ...settings, ...s }; }

const icon = (name) => h("span", { class: "i", svg: name });
const num = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
const fmt = (v, unit = "") => (v == null ? "—" : `${num.format(v)}${unit ? ` ${unit}` : ""}`);
const baseName = (p) => String(p || "").split(/[\\/]/).pop();

function entry(taskId, key) { return entries.get(`${taskId}|${key}`); }

/** A "display" event: stored once per revision (live and replayed events both come here). */
export function setDoc(taskId, d) {
  const id = `${taskId}|${d.key}`;
  let e = entries.get(id);
  if (e && (d.rev || 1) <= e.rev) return e;
  const prevWhere = e?.doc.ou;
  if (!e) {
    e = { taskId, key: d.key, mounts: new Set(), blobs: new Map(), shown: new Set(), revealed: false, pending: new Set() };
    entries.set(id, e);
  }
  e.doc = { titre: d.titre, ou: d.ou, blocs: d.blocs || [] };
  e.rev = d.rev || 1;
  e.answers = new Map();
  e.filled = new Map();
  e.movedFrom = prevWhere && prevWhere !== d.ou ? prevWhere : null;
  redraw(e);
  return e;
}

/** A "display_answer" event: the block shows what the user picked (an "annule" one gives it back). */
export function setAnswer(taskId, d) {
  const e = entry(taskId, d.key);
  if (!e || (d.rev && d.rev !== e.rev)) return;
  if (d.annule) e.answers.delete(d.bloc);
  else {
    e.answers.set(d.bloc, d.labels || []);
    if (d.valeurs && typeof d.valeurs === "object") e.filled.set(d.bloc, d.valeurs);
  }
  e.pending.delete(d.bloc);
  redraw(e);
}

function redraw(e) {
  for (const m of [...e.mounts]) {
    // a place that was on the page and left it (window closed, conversation cleared) stops following
    if (m.root.isConnected) m.seen = true;
    else if (m.seen) { e.mounts.delete(m); continue; }
    if (m.update) m.update(); else m.root.replaceChildren(...content(e, m));
  }
}

/** The element that shows a display; it follows every update until it leaves the page. */
export function renderDisplay(taskId, key, opts = {}) {
  const e = entry(taskId, key);
  const root = paint(h("div", { class: `dsp dsp-${opts.mode || "conversation"}`, "data-task": taskId, "data-key": key }), opts.color);
  if (!e) return root;
  const m = { root, mode: opts.mode || "conversation", color: opts.color };
  e.mounts.add(m);
  root.replaceChildren(...content(e, m));
  return root;
}

export function unmount(root) {
  for (const e of entries.values()) for (const m of e.mounts) if (m.root === root) e.mounts.delete(m);
}

const desktopPin = (e) => (settings.pinToDesktop ? h("button", { type: "button", class: "icon-btn",
  title: "Épingler au bureau JARVIS : un widget qui reste et que Claude peut actualiser", "aria-label": "Épingler au bureau",
  svg: "gauge", on: { click: () => settings.pinToDesktop(e.taskId, e.key) } }) : null);

function content(e, m) {
  const { doc } = e;
  const out = [];
  if (m.mode === "conversation") {
    out.push(h("div", { class: "dsp-head" }, icon("sparkle"), h("span", { class: "dsp-title" }, doc.titre),
      desktopPin(e),
      h("button", { type: "button", class: "icon-btn", title: "Ouvrir dans une fenêtre", "aria-label": "Ouvrir dans une fenêtre",
        svg: "max", on: { click: () => openDisplayWindow(e.taskId, e.key, m.color) } })));
  }
  const waiting = webWaiting(e);
  if (waiting) {
    out.push(h("div", { class: "dsp-reveal" }, icon("image"),
      h("span", {}, `${waiting} image${waiting > 1 ? "s" : ""} du web, chargée${waiting > 1 ? "s" : ""} seulement si tu le demandes (le site verra la demande).`),
      h("button", { type: "button", class: "btn small", on: { click: () => { e.revealed = true; redraw(e); } } }, "Afficher les images")));
  }
  doc.blocs.forEach((b, i) => {
    let body;
    try { body = BLOCKS[b.type]?.(b, e, m, i); } catch (err) { body = h("div", { class: "line err" }, `Bloc illisible : ${err.message}`); }
    if (!body) return;
    out.push(h("section", { class: `dsp-block dsp-b-${b.type}` }, b.titre ? h("h4", { class: "dsp-bt" }, b.titre) : null, body));
  });
  return out;
}

// ---------------------------------------------------------------- images
const webAllowed = (e, ref) => ref.trusted || e.revealed || settings.autoImages() || e.shown.has(ref.web);

function webWaiting(e) {
  const refs = [];
  for (const b of e.doc.blocs) {
    if (b.type === "images") refs.push(...b.images);
    if (b.type === "resultats") refs.push(...b.elements.map((x) => x.image).filter(Boolean));
    if (b.type === "fiche" && b.image) refs.push(b.image);
  }
  return refs.filter((r) => r.web && !webAllowed(e, r)).length;
}

/** An image of a block: a local file (through the console) or a web address (approved, or after a click). */
function picture(e, m, ref, cls, alt = "") {
  if (ref.path) {
    const img = h("img", { class: cls, alt: alt || baseName(ref.path), title: "Cliquer pour agrandir" });
    if (!e.blobs.has(ref.path)) e.blobs.set(ref.path, fileBlob(e.taskId, ref.path).then((b) => URL.createObjectURL(b)));
    e.blobs.get(ref.path).then((u) => { img.src = u; }, () => img.replaceWith(h("span", { class: `${cls} dsp-missing` }, icon("alert"), baseName(ref.path))));
    img.addEventListener("click", () => openPreview({ taskId: e.taskId, path: ref.path, color: m.color }));
    return img;
  }
  if (!webAllowed(e, ref)) {
    return h("button", { type: "button", class: `${cls} dsp-ph`, title: `Charger l'image de ${ref.host}`,
      on: { click: () => { e.shown.add(ref.web); redraw(e); } } }, icon("image"), h("small", {}, ref.host));
  }
  const img = h("img", { class: cls, src: ref.web, alt, referrerpolicy: "no-referrer", title: "Cliquer pour agrandir" });
  img.addEventListener("click", () => openPreview({ url: ref.web, kind: "image", color: m.color }));
  img.addEventListener("error", () => img.replaceWith(h("span", { class: `${cls} dsp-missing` }, icon("alert"), ref.host)), { once: true });
  return img;
}

const openWeb = (m, url) => openPreview({ url, kind: "web", color: m.color });
const webLink = (m, lien, label, cls = "dsp-link") => h("a", { href: lien.url, class: cls, title: lien.url,
  on: { click: (ev) => { ev.preventDefault(); openWeb(m, lien.url); } } }, label);
const browserBtn = (url) => h("button", { type: "button", class: "icon-btn", title: "Ouvrir dans le navigateur", "aria-label": "Ouvrir dans le navigateur",
  svg: "external", on: { click: () => window.open(url, "_blank", "noopener,noreferrer") } });

// What the user can point at with a right click (regard.js): a label for the chip, a description for Claude.
const pickable = (label, ...lines) => ({ "data-pick-label": label, "data-pick": lines.filter(Boolean).join("\n") });

// ---------------------------------------------------------------- blocks
const BLOCKS = {
  texte: (b) => mdElement(b.texte),

  images: (b, e, m) => h("div", { class: `dsp-gallery${b.images.length === 1 ? " one" : ""}` },
    ...b.images.map((ref) => h("figure", pickable(`image « ${ref.legende || baseName(ref.path || ref.web)} »`,
      `Image : ${ref.path || ref.web}`, ref.legende ? `Légende : ${ref.legende}` : ""),
    picture(e, m, ref, "dsp-img", ref.legende), ref.legende ? h("figcaption", {}, ref.legende) : null))),

  resultats: (b, e, m) => h("ol", { class: "dsp-results" }, ...b.elements.map((r) => h("li",
    pickable(`résultat « ${r.titre} »`, `Résultat « ${r.titre} »`, r.lien?.url, r.source ? `Source : ${r.source}` : "", r.extrait),
    r.image ? picture(e, m, r.image, "dsp-rimg", r.titre) : null,
    h("div", { class: "dsp-rbody" },
      h("div", { class: "dsp-rsrc" }, r.lien ? h("span", { class: "dsp-host" }, r.lien.host) : null, r.source && r.source !== r.lien?.host ? ` · ${r.source}` : ""),
      h("div", { class: "dsp-rtitle" }, r.lien ? webLink(m, r.lien, r.titre) : r.titre, r.lien ? browserBtn(r.lien.url) : null),
      r.extrait ? h("p", {}, r.extrait) : null)))),

  tableau: (b) => table(b.colonnes, b.lignes, b.tronque),

  graphique: (b) => chart(b),

  tableau_de_bord: (b, e, m, i) => dashboard(b, e, i),

  fiche: (b, e, m) => h("div", { class: "dsp-card" },
    b.image ? picture(e, m, b.image, "dsp-cimg") : null,
    h("dl", {}, ...b.champs.flatMap((f) => {
      const at = pickable(`champ « ${f.libelle} »`, `${b.titre ? `Fiche « ${b.titre} », champ ` : "Champ "}${f.libelle} : ${f.valeur}`);
      return [h("dt", at, f.libelle), h("dd", at, f.valeur)];
    })),
    b.lien ? h("div", { class: "dsp-card-act" }, h("button", { type: "button", class: "btn small", on: { click: () => openWeb(m, b.lien.url) } },
      icon("globe"), `Ouvrir (${b.lien.host})`), browserBtn(b.lien.url)) : null),

  chronologie: (b) => h("ol", { class: "dsp-timeline" }, ...b.elements.map((x) => h("li",
    pickable(`étape « ${x.titre || when(x.quand)} »`, `Étape : ${when(x.quand)}${x.titre ? ` · ${x.titre}` : ""}`, x.texte),
    h("time", {}, when(x.quand)), h("div", {}, x.titre ? h("strong", {}, x.titre) : null, x.texte ? h("p", {}, x.texte) : null)))),

  chiffres: (b) => h("div", { class: "dsp-kpis" }, ...b.elements.map((k) => h("div", { class: "dsp-kpi",
    ...pickable(`chiffre « ${k.libelle} »`, `Chiffre clé ${k.libelle} : ${k.valeur}`, k.evolution ? `Évolution : ${k.evolution}` : "", k.detail) },
    h("span", { class: "dsp-kl" }, k.libelle), h("strong", {}, k.valeur),
    k.evolution ? h("span", { class: "dsp-ke" }, /^[-−–]/.test(k.evolution) ? "▼ " : /^\+/.test(k.evolution) ? "▲ " : "", k.evolution) : null,
    k.detail ? h("small", {}, k.detail) : null))),

  progression: (b) => h("div", { class: "dsp-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(b.valeur)) },
    h("div", { class: "dsp-pbar" }, h("i", { style: { width: `${b.valeur}%` } })),
    h("div", { class: "dsp-plabel" }, h("strong", {}, `${num.format(Math.round(b.valeur * 10) / 10)} %`), b.texte ? h("span", {}, b.texte) : null)),

  // drawn as an image: no script of the SVG runs and nothing it names is loaded
  schema: (b) => h("img", { class: "dsp-schema", alt: b.titre || "Schéma", src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(b.svg)}` }),

  fichiers: (b, e, m) => {
    const list = h("div", { class: "dsp-files" }, ...b.fichiers.map((p) => h("a", { href: "#", class: `fileref${/\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(p) ? " img" : ""}`, "data-path": p, title: `Aperçu : ${p}` }, baseName(p))));
    queueMicrotask(() => list.querySelectorAll(".fileref.img").forEach((a) => thumbnail(a, e.taskId, m.color)));
    return list;
  },

  choix: (b, e, m, i) => choice(b, e, i),
  actions: (b, e, m, i) => buttons(b, e, m, i),
  cartes: (b, e, m, i) => cards(b, e, m, i),
  formulaire: (b, e, m, i) => formBlock(b, e, i),
  application: (b, e, m, i) => application(b, e, m, i),
};

function when(s) {
  if (!/^\d{4}-\d{2}-\d{2}/.test(s || "")) return s || "";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const day = d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  return /T\d{2}:\d{2}/.test(s) ? `${day} · ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}` : day;
}

// ---------------------------------------------------------------- answers (sent back to the session)
async function answer(e, i, body) {
  if (e.pending.has(i)) return;
  e.pending.add(i);
  redraw(e);
  try {
    await api(`/api/tasks/${e.taskId}/displays/${encodeURIComponent(e.key)}/answer`, { method: "POST", body: { bloc: i, ...body } });
  } catch (err) {
    e.pending.delete(i);
    redraw(e);
    toast(err.message, "err");
  }
}

function choice(b, e, i) {
  const done = e.answers.get(i);
  const busy = e.pending.has(i);
  const picked = new Set();
  const other = h("input", { type: "text", class: "dsp-other", placeholder: "Autre réponse…", maxlength: "1000", disabled: !!(done || busy),
    on: { keydown: (ev) => { if (ev.key === "Enter" && other.value.trim()) { ev.preventDefault(); send(); } } } });
  const send = () => answer(e, i, { choix: [...picked], autre: other.value.trim() });
  const opts = b.options.map((o, j) => h("button", {
    type: "button", class: `dsp-opt${done?.includes(o) ? " on" : ""}`, disabled: !!(done || busy), "aria-pressed": done?.includes(o) ? "true" : "false",
    on: { click: (ev) => {
      if (!b.multiple) { picked.clear(); picked.add(j); send(); return; }
      if (picked.has(j)) picked.delete(j); else picked.add(j);
      ev.currentTarget.classList.toggle("on", picked.has(j));
      ev.currentTarget.setAttribute("aria-pressed", String(picked.has(j)));
    } } }, b.multiple ? h("span", { class: "dsp-box" }) : null, o));
  return h("div", { class: "dsp-choice" },
    b.question ? h("p", { class: "dsp-q" }, b.question) : null,
    h("div", { class: "dsp-opts" }, ...opts),
    done
      ? h("div", { class: "dsp-done" }, icon("check"), `Répondu : ${done.join(" ; ")}`)
      : h("div", { class: "dsp-free" }, other,
        h("button", { type: "button", class: "btn small primary", disabled: busy, on: { click: () => { if (picked.size || other.value.trim()) send(); else other.focus(); } } },
          busy ? "Envoi…" : "Envoyer")));
}

function formBlock(b, e, i) {
  const done = e.answers.has(i);
  const busy = e.pending.has(i);
  const locked = done || busy;
  const kept = e.filled?.get(i) || {};
  const val = (f) => (Object.prototype.hasOwnProperty.call(kept, f.id) ? kept[f.id] : (f.valeur || ""));
  const controls = b.champs.map((f) => {
    const v = String(val(f) ?? "");
    if (f.type === "case") {
      return h("label", { class: "fld case" },
        h("input", { type: "checkbox", name: f.id, checked: v === "oui" || undefined, disabled: locked || undefined, required: f.requis || undefined }),
        h("span", {}, f.libelle));
    }
    let ctrl;
    if (f.type === "zone") {
      ctrl = h("textarea", { name: f.id, rows: "6", maxlength: "8000", disabled: locked || undefined, required: f.requis || undefined }, v);
    } else if (f.type === "liste") {
      ctrl = h("select", { name: f.id, disabled: locked || undefined, required: f.requis || undefined },
        f.requis ? null : h("option", { value: "" }, "—"),
        ...f.options.map((o) => h("option", { value: o, selected: o === v || undefined }, o)));
    } else {
      ctrl = h("input", {
        type: f.type === "date" ? "date" : "text", name: f.id, value: v,
        maxlength: f.type === "nombre" ? "40" : "2000",
        inputmode: f.type === "nombre" ? "decimal" : undefined,
        disabled: locked || undefined, required: f.requis || undefined,
      });
    }
    return h("label", { class: "fld" }, h("span", {}, f.libelle), ctrl, f.aide ? h("small", { class: "aide" }, f.aide) : null);
  });
  const form = h("form", { class: "dsp-form", on: { submit: (ev) => {
    ev.preventDefault();
    if (done || e.pending.has(i)) return;
    const valeurs = {};
    for (const f of b.champs) {
      const el = form.elements.namedItem(f.id);
      valeurs[f.id] = f.type === "case" ? (el?.checked ? "oui" : "non") : (el?.value ?? "");
    }
    (e.filled ??= new Map()).set(i, valeurs);
    answer(e, i, { valeurs });
  } } },
  b.texte ? h("p", { class: "dsp-q" }, b.texte) : null,
  ...controls,
  done
    ? h("div", { class: "dsp-done" }, icon("check"), "Validé")
    : h("div", { class: "dsp-form-act" },
      h("button", { type: "submit", class: "btn small primary", disabled: busy || undefined }, busy ? "Envoi…" : (b.bouton || "Valider"))));
  return form;
}

function buttons(b, e, m, i) {
  const done = e.answers.get(i);
  const busy = e.pending.has(i);
  return h("div", { class: "dsp-actions" }, ...b.boutons.map((x, j) => x.lien
    ? h("button", { type: "button", class: "btn small", title: x.lien.url, on: { click: () => openWeb(m, x.lien.url) } }, icon("globe"), x.libelle)
    : h("button", { type: "button", class: `btn small${done?.includes(x.libelle) ? " primary" : ""}`, disabled: !!(done || busy),
      title: x.message, on: { click: () => answer(e, i, { bouton: j }) } }, done?.includes(x.libelle) ? icon("check") : null, x.libelle)),
  done ? h("span", { class: "dsp-done" }, "Envoyé à Claude") : null);
}

const CARD_ICON = { ouvrir: "mail", suivi: "pin", terminee: "check", routine: "clock", consigne: "mail", message: "send" };

async function act(e, i, carte, bouton) {
  const k = `${i}:${carte}:${bouton}`;
  e.cardBusy ??= new Set();
  if (e.cardBusy.has(k)) return;
  e.cardBusy.add(k);
  redraw(e);
  try {
    const r = await api(`/api/tasks/${e.taskId}/displays/${encodeURIComponent(e.key)}/act`, {
      method: "POST", body: { bloc: i, carte, bouton },
    });
    if (r?.note) toast(r.note, "ok");
    if (r?.proposition) settings.openTask?.(e.taskId);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    e.cardBusy.delete(k);
    redraw(e);
  }
}

function cards(b, e, m, i) {
  const done = new Set(e.answers.get(i) || []);
  return h("div", { class: "dsp-cards" }, ...b.elements.map((card, c) => h("article", { class: "dsp-card",
    ...pickable(`carte « ${card.titre} »`, `Carte « ${card.titre} »`, card.texte) },
    h("h3", {}, card.titre),
    card.texte ? h("p", {}, card.texte) : null,
    h("div", { class: "dsp-actions" }, ...card.boutons.map((x, j) => {
      const used = done.has(`${c}:${j}`);
      const busy = e.cardBusy?.has(`${i}:${c}:${j}`);
      const again = x.faire === "ouvrir";
      return h("button", {
        type: "button",
        class: `btn small${used && !again ? " primary" : ""}`,
        disabled: busy || (!again && used) || undefined,
        on: { click: () => act(e, i, c, j) },
      }, used && !again ? icon("check") : (CARD_ICON[x.faire] ? icon(CARD_ICON[x.faire]) : null), x.libelle);
    })))));
}

// ---------------------------------------------------------------- application (a small page written by Claude)
// It runs on the preview origin, inside a sandboxed frame without network (see content.py): the console only
// listens to that frame, only while the user is in it, and every launch of an action is confirmed here.
const apps = new Set(); // {frame, e, i, last}
const APP_URL_AGE = 11 * 3600e3; // the content store keeps a page 12 h
const APP_GAP = 1500;            // ms between two sends of one application

function appUrl(e, i) {
  e.apps ??= new Map();
  const k = `${e.rev}|${i}`;
  const got = e.apps.get(k);
  if (got && Date.now() - got.at < APP_URL_AGE) return got.res;
  const res = api(`/api/tasks/${e.taskId}/displays/${encodeURIComponent(e.key)}/app`, { method: "POST", body: { bloc: i } });
  e.apps.set(k, { at: Date.now(), res });
  res.catch(() => e.apps.delete(k));
  return res;
}

function application(b, e, m, i) {
  const frame = h("iframe", { class: "dsp-app-frame", title: b.titre || "Application", referrerpolicy: "no-referrer",
    style: { height: `${b.hauteur || 400}px` } });
  const box = h("div", { class: "dsp-app" }, frame,
    h("div", { class: "dsp-app-note muted" }, icon("app"), "Application isolée : sans réseau ni accès à tes fichiers. Ce qu'elle envoie à Claude part de tes clics."));
  appUrl(e, i).then((r) => { frame.src = r.url; if (r.hauteur) frame.style.height = `${r.hauteur}px`; },
    (err) => frame.replaceWith(h("div", { class: "line err" }, err.message)));
  for (const a of [...apps]) if (!a.frame.isConnected && a.seen) apps.delete(a);
  apps.add({ frame, e, i, last: 0, seen: false });
  return box;
}

// an application's frame talks to the window it is in: this page's, or a native window's (desktop app)
wm.onDocument((doc) => doc.defaultView.addEventListener("message", onAppMessage));
async function onAppMessage(ev) {
  const d = ev.data;
  if (!d || d.jarvisApp !== 1 || ev.origin !== "null") return;
  let app = null;
  for (const a of apps) {
    if (a.frame.isConnected) a.seen = true;
    else if (a.seen) { apps.delete(a); continue; }
    try { if (ev.source && ev.source === a.frame.contentWindow?.[0]) app = a; } catch { /* another origin: not ours */ }
  }
  if (!app) return;
  if (app.frame.ownerDocument.activeElement !== app.frame) return; // the user is not in this application: ignored
  const now = Date.now();
  if (now - app.last < APP_GAP) return;
  app.last = now;
  const { e, i } = app;
  const name = e.doc.blocs[i]?.titre || e.doc.titre;
  if (d.type === "message") {
    const contenu = String(d.contenu ?? "").slice(0, 8000);
    if (!contenu.trim()) return;
    try {
      await api(`/api/tasks/${e.taskId}/displays/${encodeURIComponent(e.key)}/app-message`, { method: "POST", body: { bloc: i, contenu } });
      toast(`« ${name} » : envoyé à Claude.`, "ok");
    } catch (err) { toast(err.message, "err"); }
  } else if (d.type === "action") {
    const nom = String(d.nom || "").replace(/^\//, "").trim().slice(0, 64);
    const args = String(d.arguments ?? "").slice(0, 4000);
    if (!/^[\w.-]+$/.test(nom) || !settings.appAction) return;
    const ok = await confirmDialog("Lancer une action du projet ?", h("div", {},
      h("p", {}, `L'application « ${name} » propose de lancer :`),
      h("pre", { class: "prop-code" }, `/${nom}${args ? ` ${args}` : ""}`),
      h("p", { class: "muted" }, "Une nouvelle discussion du projet, avec ses autorisations et ses validations habituelles.")), "Lancer");
    if (ok) settings.appAction(e.taskId, nom, args);
  }
}

// ---------------------------------------------------------------- dashboard (facts in, filters and chart here)
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const DASH_TOP = 24;

function dashState(e, i, b) {
  if (!e.dash) e.dash = new Map();
  const sig = b.colonnes.map((c) => `${c.id}:${c.type}:${c.role || ""}`).join("|");
  let s = e.dash.get(i);
  if (!s || s.sig !== sig) {
    const date = b.colonnes.find((c) => c.type === "date");
    const commercial = !!(date && b.colonnes.some((c) => c.role === "montant" || c.role === "marge"));
    let periode = b.periode || "jour";
    const months = new Set();
    if (commercial && date) {
      const j = b.colonnes.indexOf(date);
      for (const r of b.lignes) {
        const v = String(r[j] || "");
        if (/^\d{4}-\d{2}-\d{2}$/.test(v)) months.add(v.slice(0, 7));
      }
      if (months.size > 1) periode = "mois";
    }
    s = {
      sig, grouper: b.grouper || "", periode, forme: b.forme || "barres",
      compare: commercial ? "n1" : "", preset: "", linesOpen: b.lignes.length <= 40,
      mesures: new Set((b.mesures || []).slice(0, 8)),
      text: new Map(), date: new Map(), num: new Map(),
    };
    if (!s.mesures.size) b.colonnes.filter((c) => c.type === "nombre").slice(0, 4).forEach((c) => s.mesures.add(c.id));
    if (!s.mesures.size) s.mesures.add("_n");
    let dateFrom = "", dateTo = "";
    if (commercial && date && months.size) {
      const list = [...months].sort();
      const ym = list.at(-1);
      const [y, m] = ym.split("-").map(Number);
      dateTo = isoOf(new Date(y, m, 0));
      dateFrom = months.size > 1 ? `${addMonths(ym, -11)}-01` : `${ym}-01`;
      s.preset = months.size > 1 ? "12" : "mois";
      s.date.set(date.id, { from: dateFrom, to: dateTo });
    }
    s.defaults = {
      grouper: s.grouper, periode: s.periode, forme: s.forme, compare: s.compare, mesures: [...s.mesures],
      preset: s.preset, dateId: date?.id || "", dateFrom, dateTo,
    };
    e.dash.set(i, s);
  }
  return s;
}

function addMonths(ym, n) {
  const [y, m] = ym.split("-").map(Number);
  const total = y * 12 + (m - 1) + n;
  const ny = Math.floor(total / 12);
  const nm = total - ny * 12;
  return `${ny}-${String(nm + 1).padStart(2, "0")}`;
}

function shiftYear(key, delta) {
  const [y, ...rest] = String(key).split("-");
  if (!/^\d{4}$/.test(y)) return "";
  return [String(Number(y) + delta), ...rest].join("-");
}

function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + n);
  return isoOf(dt);
}

function daySpan(a, b) {
  const [y1, m1, d1] = a.split("-").map(Number);
  const [y2, m2, d2] = b.split("-").map(Number);
  return Math.round((new Date(y2, m2 - 1, d2) - new Date(y1, m1 - 1, d1)) / 86400000);
}

function isoOf(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function bucketDate(iso, grain) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || "")) return "";
  if (grain === "mois") return iso.slice(0, 7);
  if (grain !== "semaine") return iso;
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return isoOf(new Date(y, m - 1, d - ((dt.getDay() + 6) % 7)));
}

function dateLabel(key, grain) {
  if (grain === "mois" && /^\d{4}-\d{2}$/.test(key)) {
    const [y, m] = key.split("-").map(Number);
    return `${MONTHS[m - 1]} ${y}`;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
  const [, m, d] = key.split("-").map(Number);
  const day = `${d} ${MONTHS[m - 1]}`;
  return grain === "semaine" ? `sem. ${day}` : day;
}

function boundOf(s) {
  const t = String(s || "").trim().replace(/[\s\u202f\u00a0]/g, "").replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function aggregate(values, how) {
  const xs = values.filter((v) => typeof v === "number");
  if (!xs.length) return how === "somme" ? 0 : null;
  if (how === "somme") return xs.reduce((a, v) => a + v, 0);
  if (how === "min") return Math.min(...xs);
  if (how === "max") return Math.max(...xs);
  return xs.reduce((a, v) => a + v, 0) / xs.length;
}

function dashboard(b, e, i) {
  const s = dashState(e, i, b);
  const cols = b.colonnes;
  const numbers = cols.filter((c) => c.type === "nombre");
  const groupable = cols.filter((c) => c.type === "texte" || c.type === "date");
  const dateCol = cols.find((c) => c.type === "date");
  const montant = cols.find((c) => c.role === "montant");
  const marge = cols.find((c) => c.role === "marge");
  const commercial = !!(dateCol && (montant || marge));
  const dateInputs = new Map();
  const wrap = h("div", { class: "dsp-dash" });
  let pop = null;
  const shut = () => { pop?._off?.(); pop?.remove(); pop = null; };

  const sel = (label, value, options, onChange) => {
    const node = h("label", { class: "dsp-ctl" }, h("span", {}, label),
      h("select", { on: { change: () => onChange(node.querySelector("select").value) } },
        ...options.map((o) => h("option", { value: o.value, selected: o.value === value ? true : null }, o.label))));
    return node;
  };

  const groupSel = sel("Regrouper", s.grouper, [
    ...groupable.map((c) => ({ value: c.id, label: c.libelle })),
    ...(!groupable.length ? [{ value: "", label: "Tout" }] : []),
  ], (v) => { s.grouper = v; paint(); });
  const periodSel = sel("Période", s.periode, [
    { value: "jour", label: "Jour" }, { value: "semaine", label: "Semaine" }, { value: "mois", label: "Mois" },
  ], (v) => { s.periode = v; paint(); });
  const formSel = sel("Graphique", s.forme, [
    { value: "barres", label: "Barres" }, { value: "courbe", label: "Courbe" }, { value: "secteurs", label: "Secteurs" },
  ], (v) => { s.forme = v; paint(); });
  const compareSel = sel("Comparer", s.compare, [
    { value: "", label: "Aucune" },
    { value: "n1", label: "Même période N-1" },
    { value: "prec", label: "Période précédente" },
  ], (v) => { s.compare = v; paint(); });
  compareSel.hidden = !commercial;

  const PRESETS = [["mois", "Dernier mois"], ["6", "6 mois"], ["12", "12 mois"], ["annee", "Année"], ["tout", "Tout"]];
  const presetBtns = new Map();
  const presetBar = h("span", { class: "dsp-shortcuts" }, ...PRESETS.map(([id, label]) => {
    const btn = h("button", { type: "button", class: "btn small ghost", on: { click: () => applyPreset(id) } }, label);
    presetBtns.set(id, btn);
    return btn;
  }));
  presetBar.hidden = !dateCol;

  const reset = h("button", { type: "button", class: "btn small ghost", on: { click: () => {
    const d = s.defaults;
    s.grouper = d.grouper; s.periode = d.periode; s.forme = d.forme; s.compare = d.compare; s.preset = d.preset || "";
    s.mesures = new Set(d.mesures);
    s.text.clear(); s.date.clear(); s.num.clear();
    if (d.dateId && (d.dateFrom || d.dateTo)) s.date.set(d.dateId, { from: d.dateFrom, to: d.dateTo });
    wrap.querySelectorAll(".dsp-range input").forEach((n) => { n.value = ""; });
    const box = dateInputs.get(d.dateId);
    if (box) { box.from.value = d.dateFrom || ""; box.to.value = d.dateTo || ""; }
    shut();
    syncSelects();
    paint();
  } } }, "Réinitialiser");

  const filters = h("div", { class: "dsp-dash-filters" });
  const measures = h("div", { class: "dsp-measures" });
  const kpis = h("div", { class: "dsp-kpis" });
  const note = h("p", { class: "dsp-dash-note muted" });
  const stage = h("div", { class: "dsp-stage" });
  const topBar = h("div", { class: "dsp-dash-bar" });
  let primaryRange = null;

  const syncSelects = () => {
    groupSel.querySelector("select").value = s.grouper;
    periodSel.querySelector("select").value = s.periode;
    formSel.querySelector("select").value = s.forme;
    compareSel.querySelector("select").value = s.compare;
  };

  const passes = (row, skipId) => {
    for (const [j, c] of cols.entries()) {
      if (c.id === skipId) continue;
      const v = row[j];
      if (c.type === "texte") {
        const keep = s.text.get(c.id);
        if (keep && !keep.has(String(v || ""))) return false;
      } else if (c.type === "date") {
        const f = s.date.get(c.id);
        if (!f || (!f.from && !f.to)) continue;
        if (!v || (f.from && v < f.from) || (f.to && v > f.to)) return false;
      } else {
        const f = s.num.get(c.id);
        if (!f) continue;
        const lo = boundOf(f.min), hi = boundOf(f.max);
        if (lo == null && hi == null) continue;
        if (typeof v !== "number" || (lo != null && v < lo) || (hi != null && v > hi)) return false;
      }
    }
    return true;
  };

  const uniques = (j) => {
    const set = new Set();
    for (const r of b.lignes) set.add(String(r[j] || ""));
    return [...set].sort((a, x) => a.localeCompare(x, "fr", { sensitivity: "base" }));
  };

  function openValues(btn, c, j) {
    shut();
    const all = uniques(j);
    const panel = h("div", { class: "dsp-pop" });
    const q = h("input", { type: "search", placeholder: "Filtrer la liste", class: "dsp-pop-q" });
    const list = h("div", { class: "dsp-pop-list" });
    const kept = () => s.text.get(c.id);
    const checked = (v) => { const k = kept(); return !k || k.has(v); };
    const toggle = (v, on) => {
      let k = kept();
      if (!k) k = new Set(all);
      if (on) k.add(v); else k.delete(v);
      if (k.size === all.length) s.text.delete(c.id);
      else s.text.set(c.id, k);
      fill();
      paint();
    };
    const fill = () => {
      const needle = q.value.trim().toLocaleLowerCase("fr");
      const shown = all.filter((v) => !needle || (v || "(vide)").toLocaleLowerCase("fr").includes(needle)).slice(0, 120);
      list.replaceChildren(...shown.map((v) => h("label", {},
        h("input", { type: "checkbox", checked: checked(v) ? true : null, on: { change: (ev) => toggle(v, ev.target.checked) } }),
        v || "(vide)")));
      if (all.length > shown.length) list.append(h("p", { class: "muted dsp-pop-more" }, `${shown.length} affichés sur ${all.length}`));
    };
    q.addEventListener("input", fill);
    panel.append(
      h("div", { class: "dsp-pop-act" },
        h("button", { type: "button", class: "btn small ghost", on: { click: () => { s.text.delete(c.id); fill(); paint(); } } }, "Tout"),
        h("button", { type: "button", class: "btn small ghost", on: { click: () => { s.text.set(c.id, new Set()); fill(); paint(); } } }, "Aucun")),
      q, list);
    fill();
    btn.parentElement.append(panel);
    const doc = wrap.ownerDocument;
    const onDoc = (ev) => { if (!panel.contains(ev.target) && ev.target !== btn) shut(); };
    setTimeout(() => doc.addEventListener("pointerdown", onDoc, true), 0);
    panel._off = () => doc.removeEventListener("pointerdown", onDoc, true);
    pop = panel;
    q.focus();
  }

  cols.forEach((c, j) => {
    if (c.type === "texte") {
      const btn = h("button", { type: "button", class: "btn small dsp-ff", on: { click: (ev) => { ev.stopPropagation(); openValues(btn, c, j); } } }, c.libelle);
      btn.dataset.col = c.id;
      filters.append(h("span", { class: "dsp-ff" }, btn));
    } else if (c.type === "date") {
      const from = h("input", { type: "date", "aria-label": `${c.libelle}, du`, on: { change: () => { remember(); paint(); } } });
      const to = h("input", { type: "date", "aria-label": `${c.libelle}, au`, on: { change: () => { remember(); paint(); } } });
      const remember = () => {
        s.preset = "";
        if (!from.value && !to.value) s.date.delete(c.id);
        else s.date.set(c.id, { from: from.value, to: to.value });
      };
      const saved = s.date.get(c.id);
      if (saved) { from.value = saved.from || ""; to.value = saved.to || ""; }
      const node = h("span", { class: "dsp-range" }, c.libelle, from, "→", to);
      dateInputs.set(c.id, { from, to });
      if (c === dateCol) primaryRange = node;
      else filters.append(node);
    } else {
      const min = h("input", { type: "text", inputmode: "decimal", placeholder: "min", "aria-label": `${c.libelle}, minimum`, on: { change: () => { remember(); paint(); } } });
      const max = h("input", { type: "text", inputmode: "decimal", placeholder: "max", "aria-label": `${c.libelle}, maximum`, on: { change: () => { remember(); paint(); } } });
      const remember = () => {
        if (!min.value.trim() && !max.value.trim()) s.num.delete(c.id);
        else s.num.set(c.id, { min: min.value, max: max.value });
      };
      filters.append(h("span", { class: "dsp-range" }, c.libelle, min, "–", max));
    }
  });

  function monthEnd(ym) {
    const [y, m] = ym.split("-").map(Number);
    return isoOf(new Date(y, m, 0));
  }

  function applyPreset(which) {
    if (!dateCol) return;
    s.preset = which;
    const box = dateInputs.get(dateCol.id);
    const write = (from, to) => {
      if (!from && !to) s.date.delete(dateCol.id);
      else s.date.set(dateCol.id, { from, to });
      if (box) { box.from.value = from; box.to.value = to; }
    };
    if (which === "tout") { write("", ""); paint(); return; }
    const j = cols.indexOf(dateCol);
    const anchor = b.lignes.map((r) => r[j]).filter((v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "")).sort().at(-1);
    if (!anchor) return;
    const ym = anchor.slice(0, 7);
    const year = anchor.slice(0, 4);
    if (which === "mois") write(`${ym}-01`, monthEnd(ym));
    else if (which === "6") write(`${addMonths(ym, -5)}-01`, monthEnd(ym));
    else if (which === "12") write(`${addMonths(ym, -11)}-01`, monthEnd(ym));
    else if (which === "annee") write(`${year}-01-01`, `${year}-12-31`);
    paint();
  }

  function sumOf(rows, col) {
    if (!col) return null;
    return aggregate(rows.map((r) => r[cols.indexOf(col)]), "somme") || 0;
  }

  function rateOf(ca, mg) {
    if (ca == null || mg == null || !ca) return null;
    return mg / ca * 100;
  }

  function pctDelta(now, prev) {
    if (now == null || prev == null || !prev) return null;
    return (now - prev) / Math.abs(prev) * 100;
  }

  function shortDate(iso) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || "")) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return `${d} ${MONTHS[m - 1]} ${y}`;
  }

  function spanLabel(from, to) {
    if (!from || !to || from < "1000" || to > "9000") return "";
    return `${shortDate(from)} → ${shortDate(to)}`;
  }

  function boundsOf(rows) {
    const f = dateCol && s.date.get(dateCol.id);
    if (f && (f.from || f.to)) return [f.from || "0000-01-01", f.to || "9999-12-31"];
    const j = dateCol ? cols.indexOf(dateCol) : -1;
    const dates = j < 0 ? [] : rows.map((r) => r[j]).filter((v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "")).sort();
    if (!dates.length) return ["", ""];
    return [dates[0], dates[dates.length - 1]];
  }

  function inSpan(rows, from, to) {
    if (!dateCol || !from || !to) return rows;
    const j = cols.indexOf(dateCol);
    return rows.filter((r) => r[j] && r[j] >= from && r[j] <= to);
  }

  function compareWindow(from, to, mode) {
    if (!mode || !from || !to || from < "1000" || to > "9000" || from > to) return null;
    if (mode === "n1") return { from: shiftYear(from, -1), to: shiftYear(to, -1), label: "N-1" };
    const span = daySpan(from, to);
    const end = addDays(from, -1);
    return { from: addDays(end, -span), to: end, label: "Précédente" };
  }

  function keysBetween(from, to, grain) {
    if (!from || !to || from < "1000" || to > "9000" || from > to) return [];
    if (grain === "mois") {
      const out = [];
      let k = from.slice(0, 7);
      const end = to.slice(0, 7);
      while (k <= end && out.length < 60) { out.push(k); k = addMonths(k, 1); }
      return out;
    }
    if (grain === "jour") {
      const span = daySpan(from, to);
      if (span > 400) return [];
      const out = [];
      for (let n = 0; n <= span; n++) out.push(addDays(from, n));
      return out;
    }
    const out = [];
    let k = bucketDate(from, "semaine");
    const end = bucketDate(to, "semaine");
    if (!k || !end) return [];
    while (k <= end && out.length < 80) { out.push(k); k = addDays(k, 7); }
    return out;
  }

  function buckets(rows, grain) {
    const j = cols.indexOf(dateCol);
    const map = new Map();
    for (const r of rows) {
      const key = bucketDate(r[j], grain);
      if (!key) continue;
      let g = map.get(key);
      if (!g) { g = { key, rows: [] }; map.set(key, g); }
      g.rows.push(r);
    }
    return map;
  }

  function aligned(pFrom, pTo, cmp, pMap, cMap, grain) {
    const keys = keysBetween(pFrom, pTo, grain);
    const use = keys.length ? keys : [...pMap.keys()].sort();
    if (!cmp) return use.map((k) => ({ key: k, cur: pMap.get(k), prev: null }));
    if (s.compare === "n1") return use.map((k) => ({ key: k, cur: pMap.get(k), prev: cMap.get(shiftYear(k, -1)) }));
    const cKeys = keysBetween(cmp.from, cmp.to, grain);
    return use.map((k, i) => ({ key: k, cur: pMap.get(k), prev: cMap.get(cKeys[i]) }));
  }

  function bucketSum(g, col) {
    if (!col) return null;
    if (!g) return 0;
    return aggregate(g.rows.map((r) => r[cols.indexOf(col)]), "somme") || 0;
  }

  const measureOf = (id) => (id === "_n" ? { id: "_n", libelle: "Lignes", agregat: "compte" } : numbers.find((c) => c.id === id));
  const activeMeasures = () => [...s.mesures].map(measureOf).filter(Boolean).slice(0, 8);

  function groupsOf(rows) {
    const col = cols.find((c) => c.id === s.grouper);
    const j = col ? cols.indexOf(col) : -1;
    const isDate = col?.type === "date";
    const map = new Map();
    for (const r of rows) {
      const raw = j < 0 ? "" : (r[j] || "");
      const key = isDate ? (bucketDate(raw, s.periode) || "") : String(raw);
      let g = map.get(key);
      if (!g) { g = { key, rows: [] }; map.set(key, g); }
      g.rows.push(r);
    }
    return { col, isDate, list: [...map.values()] };
  }

  function seriesOf(list) {
    return activeMeasures().map((c) => {
      if (c.id === "_n") return { nom: "Lignes", unite: "", agregat: "compte", valeurs: list.map((g) => g.rows.length) };
      const j = cols.indexOf(c);
      return {
        nom: c.libelle, unite: c.unite || "", agregat: c.agregat || "somme",
        valeurs: list.map((g) => aggregate(g.rows.map((r) => r[j]), c.agregat || "somme")),
      };
    });
  }

  function deltaNode(pct, unit, versus) {
    if (pct == null || !Number.isFinite(pct)) return h("small", {}, "\u00a0");
    const rounded = Math.round(pct * 10) / 10;
    const cls = rounded > 0 ? "up" : rounded < 0 ? "down" : "";
    const sign = rounded > 0 ? "+" : "";
    return h("small", { class: cls || null }, `${sign}${num.format(rounded)} ${unit}${versus ? ` vs ${versus}` : ""}`);
  }

  function kpiCard(label, value, foot, tone) {
    return h("div", { class: tone ? `dsp-kpi ${tone}` : "dsp-kpi" },
      h("span", { class: "dsp-kl" }, label), h("strong", {}, value), foot || h("small", {}, "\u00a0"));
  }

  function cell(v, cls) { return cls ? { v, cls } : v; }

  function varCell(now, prev) {
    if (now == null || prev == null) return "—";
    const d = now - prev;
    if (!d) return 0;
    return cell(d, d > 0 ? "up" : "down");
  }

  function boardTable(headers, rows, opts = {}) {
    const val = (c) => (c && typeof c === "object" && "v" in c ? c.v : c);
    const data = opts.pinLast && rows.length > 1 ? rows.slice(0, -1) : rows.slice();
    const pinned = opts.pinLast && rows.length > 1 ? rows[rows.length - 1] : null;
    let sort = { col: -1, dir: 1 };
    const tbody = h("tbody");
    const heads = headers.map((c, j) => h("th", { scope: "col", on: { click: () => {
      sort = { col: j, dir: sort.col === j ? -sort.dir : -1 }; fill();
    } } }, c || " ", h("span", { class: "dsp-sort" })));
    const cellNode = (c) => {
      const v = val(c);
      const cls = [isNum(v) ? "num" : "", c && typeof c === "object" ? c.cls : ""].filter(Boolean).join(" ");
      return h("td", { class: cls || null }, isNum(v) ? num.format(v) : (v ?? ""));
    };
    const fill = () => {
      const list = [...data];
      if (sort.col >= 0) {
        const j = sort.col;
        list.sort((a, x) => {
          const p = val(a.cells[j]), q = val(x.cells[j]);
          if (isNum(p) && isNum(q)) return (p - q) * sort.dir;
          return String(p ?? "").localeCompare(String(q ?? ""), "fr", { numeric: true, sensitivity: "base" }) * sort.dir;
        });
      }
      heads.forEach((th, j) => { th.dataset.sort = sort.col === j ? (sort.dir > 0 ? "asc" : "desc") : ""; });
      const trOf = (row, extra) => {
        const attrs = { class: [extra, row.cls].filter(Boolean).join(" ") || null };
        if (row.on) attrs.on = { click: row.on };
        return h("tr", attrs, ...row.cells.map(cellNode));
      };
      tbody.replaceChildren(...list.map((row) => trOf(row)), ...(pinned ? [trOf(pinned, "total")] : []));
    };
    fill();
    const csv = () => [headers, ...rows.map((r) => r.cells.map((c) => val(c)))].map((r) => r.map((c) => {
      const t = isNum(c) ? String(c).replace(".", ",") : String(c ?? "");
      return /[;"\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
    }).join(";")).join("\n");
    return h("div", { class: "dsp-table" },
      h("div", { class: "dsp-tools" }, h("span", { class: "muted" }, opts.caption || ""),
        h("button", { type: "button", class: "btn small ghost", on: { click: () => copyText(csv()) } }, icon("copy"), "Copier (CSV)")),
      h("div", { class: "dsp-scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ...heads)), tbody)));
  }

  function chartCard(title, spec) {
    const box = h("section", { class: "dsp-panel dsp-chart-card" }, h("h3", { class: "dsp-card-t" }, title));
    box.append(spec ? chart(spec) : h("div", { class: "empty-row" }, "Rien à tracer."));
    return box;
  }

  function lineDetail(rows) {
    const head = cols.map((c) => c.libelle);
    const body = rows.map((r) => cols.map((c, j) => (c.type === "nombre" ? (typeof r[j] === "number" ? r[j] : "") : (r[j] || ""))));
    const node = h("details", {
      class: "dsp-panel dsp-lines", open: s.linesOpen ? true : null,
      on: { toggle: (ev) => { s.linesOpen = ev.currentTarget.open; } },
    }, h("summary", {}, `Détail des lignes · ${num.format(rows.length)}`));
    node.append(table(head, body, false));
    return node;
  }

  function ranking(rows) {
    const textCol = cols.find((c) => c.id === s.grouper && c.type === "texte") || cols.find((c) => c.type === "texte");
    if (!textCol) return null;
    const j = cols.indexOf(textCol);
    const map = new Map();
    for (const r of rows) {
      const key = String(r[j] || "") || "Non renseigné";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(r);
    }
    const scoreCol = montant || numbers[0];
    const total = scoreCol ? (sumOf(rows, scoreCol) || 0) : rows.length;
    const ranked = [...map.entries()].map(([key, list]) => ({
      key, list,
      ca: montant ? sumOf(list, montant) : null,
      mg: marge ? sumOf(list, marge) : null,
      score: scoreCol ? sumOf(list, scoreCol) : list.length,
    })).sort((a, x) => (x.score || 0) - (a.score || 0) || a.key.localeCompare(x.key, "fr"));
    const top = ranked.slice(0, 12);
    const active = s.text.get(textCol.id);
    const pick = (key) => {
      const value = key === "Non renseigné" ? "" : key;
      const cur = s.text.get(textCol.id);
      if (cur && cur.size === 1 && cur.has(value)) s.text.delete(textCol.id);
      else s.text.set(textCol.id, new Set([value]));
      paint();
    };
    const headers = [textCol.libelle];
    if (montant) headers.push(montant.libelle);
    if (marge) headers.push(marge.libelle);
    if (montant && marge) headers.push("Taux %");
    headers.push("Part %");
    const body = top.map((row) => {
      const value = row.key === "Non renseigné" ? "" : row.key;
      const on = active && active.size === 1 && active.has(value);
      const cells = [row.key];
      if (montant) cells.push(row.ca);
      if (marge) cells.push(row.mg);
      if (montant && marge) {
        const rate = rateOf(row.ca, row.mg);
        cells.push(rate == null ? "—" : rate);
      }
      cells.push(total ? (row.score || 0) / total * 100 : null);
      return { cells, cls: on ? "on" : "dsp-pick", on: () => pick(row.key) };
    });
    return h("section", { class: "dsp-panel" }, h("h3", { class: "dsp-card-t" }, textCol.libelle),
      body.length ? boardTable(headers, body, { caption: "Cliquer une ligne pour filtrer." }) : h("div", { class: "empty-row" }, "Rien à classer."));
  }

  function paintChrome() {
    for (const [id, btn] of presetBtns) btn.classList.toggle("on", s.preset === id);
    const gcol = cols.find((c) => c.id === s.grouper);
    periodSel.hidden = !(commercial || gcol?.type === "date");
    measures.hidden = commercial;
    filters.querySelectorAll(".dsp-ff > .btn").forEach((btn) => {
      const c = cols.find((x) => x.id === btn.dataset.col);
      const keep = c && s.text.get(c.id);
      btn.textContent = !keep ? c.libelle : keep.size ? `${c.libelle} · ${keep.size}` : `${c.libelle} · aucun`;
      btn.classList.toggle("on", !!keep);
    });
    if (!commercial) {
      measures.replaceChildren(...[...numbers, { id: "_n", libelle: "Lignes" }].map((c) => h("button", {
        type: "button", class: `dsp-opt${s.mesures.has(c.id) ? " on" : ""}`,
        on: { click: () => {
          if (s.mesures.has(c.id)) s.mesures.delete(c.id); else if (s.mesures.size < 8) s.mesures.add(c.id);
          paint();
        } },
      }, c.libelle)));
    }
  }

  function paintGeneric(rows, notes) {
    const cards = [kpiCard("Lignes", num.format(rows.length))];
    for (const c of numbers) cards.push(kpiCard(c.libelle, fmt(sumOf(rows, c), c.unite)));
    if (montant && marge) {
      const rate = rateOf(sumOf(rows, montant), sumOf(rows, marge));
      cards.push(kpiCard("Taux de marge", rate == null ? "—" : fmt(rate, "%"), null, rate != null && rate < 0 ? "bad" : "ok"));
    }
    kpis.replaceChildren(...cards);

    let { isDate, list } = groupsOf(rows);
    const scoreOf = (g) => {
      const v = seriesOf([g])[0]?.valeurs[0];
      return typeof v === "number" ? v : 0;
    };
    if (isDate) list.sort((a, x) => a.key.localeCompare(x.key));
    else list.sort((a, x) => scoreOf(x) - scoreOf(a) || a.key.localeCompare(x.key, "fr"));
    let forme = s.forme;
    if (!isDate && list.length > DASH_TOP && forme !== "courbe") {
      const rest = list.slice(DASH_TOP);
      const how = activeMeasures()[0]?.agregat;
      if (rest.length && (how === "somme" || how === "compte")) list = [...list.slice(0, DASH_TOP), { key: "Autres", rows: rest.flatMap((g) => g.rows) }];
      else if (rest.length) { list = list.slice(0, DASH_TOP); notes.push(`${num.format(rest.length)} groupes masqués.`); }
    }
    if (isDate && list.length > 366) {
      notes.push(`${num.format(list.length - 366)} points les plus anciens masqués. La période « mois » les regroupe.`);
      list = list.slice(-366);
    }
    let series = seriesOf(list);
    if (forme === "secteurs" && series.some((ser) => ser.valeurs.some((v) => typeof v === "number" && v < 0))) {
      forme = "barres";
      notes.push("Valeurs négatives : affichées en barres.");
    }
    if (forme === "secteurs" && series.length > 1) {
      notes.push(`Secteurs : ${series[0].nom}.`);
      series = series.slice(0, 1);
    }
    const units = new Set(series.map((ser) => ser.unite).filter(Boolean));
    const unite = units.size === 1 ? [...units][0] : "";
    if (units.size > 1) series = series.map((ser) => ({ ...ser, nom: ser.unite ? `${ser.nom} (${ser.unite})` : ser.nom }));
    const labels = list.map((g) => (g.key === "Autres" ? "Autres" : !g.key ? "Non renseigné" : isDate ? dateLabel(g.key, s.periode) : g.key));
    const spec = !rows.length || !series.length ? null : { forme, etiquettes: labels, series, unite, titre: b.titre || "" };
    const title = !rows.length ? "Aucune ligne pour ces filtres." : !series.length ? "Choisis au moins une mesure." : (b.titre || "Graphique");
    stage.replaceChildren(...[chartCard(title, spec), ranking(rows), lineDetail(rows)].filter(Boolean));
  }

  function paintCommercial(base, notes) {
    const [pFrom, pTo] = boundsOf(base);
    const shown = inSpan(base, pFrom, pTo);
    const cmp = compareWindow(pFrom, pTo, s.compare);
    const compareRows = cmp ? inSpan(base, cmp.from, cmp.to) : [];
    if (s.compare && !cmp) notes.push("Indique un début et une fin pour comparer.");
    else if (cmp && !compareRows.length) notes.push(`Aucune ligne sur la période ${cmp.label}.`);

    const ca = montant ? sumOf(shown, montant) : null;
    const mg = marge ? sumOf(shown, marge) : null;
    const rate = rateOf(ca, mg);
    const caP = cmp && montant ? sumOf(compareRows, montant) : null;
    const mgP = cmp && marge ? sumOf(compareRows, marge) : null;
    const rateP = rateOf(caP, mgP);
    const versus = cmp?.label || "";
    const cards = [];
    if (montant) cards.push(kpiCard(montant.libelle, fmt(ca, montant.unite), deltaNode(pctDelta(ca, caP), "%", versus)));
    if (montant && marge) {
      const pts = rate != null && rateP != null ? rate - rateP : null;
      cards.push(kpiCard("Taux de marge", rate == null ? "—" : fmt(rate, "%"), deltaNode(pts, "pts", versus), rate != null && rate < 0 ? "bad" : "ok"));
    }
    if (marge) cards.push(kpiCard(marge.libelle, fmt(mg, marge.unite), deltaNode(pctDelta(mg, mgP), "%", versus), mg != null && mg < 0 ? "bad" : "ok"));
    if (cmp) {
      const main = montant || marge;
      const prev = montant ? caP : mgP;
      cards.push(kpiCard(cmp.label, fmt(prev, main.unite), h("small", {}, spanLabel(cmp.from, cmp.to) || "\u00a0"), "warn"));
    } else cards.push(kpiCard("Période", num.format(shown.length), h("small", {}, spanLabel(pFrom, pTo) || "lignes"), "warn"));
    kpis.replaceChildren(...cards);

    const grain = s.periode || "mois";
    const grainWord = grain === "jour" ? "jour" : grain === "semaine" ? "semaine" : "mois";
    const pMap = buckets(shown, grain);
    const cMap = buckets(compareRows, grain);
    let points = aligned(pFrom, pTo, cmp, pMap, cMap, grain);
    if (points.length > 366) {
      notes.push(`${num.format(points.length - 366)} points les plus anciens masqués.`);
      points = points.slice(-366);
    }
    let forme = s.forme;
    if (forme === "secteurs" && (cmp || points.some((p) => (montant && bucketSum(p.cur, montant) < 0) || (marge && bucketSum(p.cur, marge) < 0)))) {
      forme = "barres";
      notes.push("Comparaison ou valeurs négatives : barres plutôt que secteurs.");
    }
    const labels = points.map((p) => dateLabel(p.key, grain));
    const amountSpec = (col) => {
      if (!col || !points.length) return null;
      const series = [{ nom: "Période", unite: col.unite || "", valeurs: points.map((p) => bucketSum(p.cur, col)) }];
      if (cmp) series.push({ nom: cmp.label, unite: col.unite || "", valeurs: points.map((p) => bucketSum(p.prev, col)) });
      return { forme, etiquettes: labels, series, unite: col.unite || "", titre: col.libelle };
    };
    const charts = h("div", { class: "dsp-charts" });
    if (montant) charts.append(chartCard(`${montant.libelle} par ${grainWord}`, amountSpec(montant)));
    if (marge) charts.append(chartCard(`${marge.libelle} par ${grainWord}`, amountSpec(marge)));
    let rateCard = null;
    if (montant && marge && points.length) {
      const series = [{ nom: "Période", unite: "%", valeurs: points.map((p) => rateOf(bucketSum(p.cur, montant), bucketSum(p.cur, marge))) }];
      if (cmp) series.push({ nom: cmp.label, unite: "%", valeurs: points.map((p) => rateOf(bucketSum(p.prev, montant), bucketSum(p.prev, marge))) });
      const any = series.some((ser) => ser.valeurs.some((v) => v != null));
      rateCard = chartCard(`Taux de marge par ${grainWord}`, any ? { forme: "courbe", etiquettes: labels, series, unite: "%", titre: "Taux de marge" } : null);
    }

    const headers = ["Période"];
    if (montant) headers.push(montant.libelle, ...(cmp ? [`${montant.libelle} ${cmp.label}`, "Écart"] : []));
    if (marge) headers.push(marge.libelle, ...(cmp ? [`${marge.libelle} ${cmp.label}`, "Écart"] : []));
    if (montant && marge) headers.push("Taux %", ...(cmp ? [`Taux ${cmp.label}`, "Écart pts"] : []));
    const body = points.map((p) => {
      const a = bucketSum(p.cur, montant), g = bucketSum(p.cur, marge);
      const ap = cmp ? bucketSum(p.prev, montant) : null, gp = cmp ? bucketSum(p.prev, marge) : null;
      const cells = [dateLabel(p.key, grain)];
      if (montant) cells.push(a, ...(cmp ? [ap, varCell(a, ap)] : []));
      if (marge) cells.push(g, ...(cmp ? [gp, varCell(g, gp)] : []));
      if (montant && marge) cells.push(rateOf(a, g) ?? "—", ...(cmp ? [rateOf(ap, gp) ?? "—", varCell(rateOf(a, g), rateOf(ap, gp))] : []));
      return { cells };
    });
    if (points.length) {
      const cells = ["Total"];
      if (montant) cells.push(ca, ...(cmp ? [caP, varCell(ca, caP)] : []));
      if (marge) cells.push(mg, ...(cmp ? [mgP, varCell(mg, mgP)] : []));
      if (montant && marge) cells.push(rate ?? "—", ...(cmp ? [rateP ?? "—", varCell(rate, rateP)] : []));
      body.push({ cells });
    }
    const comp = h("section", { class: "dsp-panel" }, h("h3", { class: "dsp-card-t" }, `Comparaison par ${grainWord}`),
      body.length ? boardTable(headers, body, { pinLast: true, caption: `${num.format(points.length)} ${grainWord}${points.length > 1 ? "s" : ""}` }) : h("div", { class: "empty-row" }, "Aucune date exploitable."));
    stage.replaceChildren(...[charts, rateCard, h("div", { class: "dsp-boards" }, comp, ranking(shown)), lineDetail(shown)].filter(Boolean));
  }

  function paint() {
    paintChrome();
    const notes = [];
    if (b.tronque) notes.push(`Jeu limité aux ${num.format(b.lignes.length)} premières lignes.`);
    const base = b.lignes.filter((r) => passes(r, commercial ? dateCol.id : null));
    if (commercial) paintCommercial(base, notes);
    else paintGeneric(base, notes);
    note.textContent = notes.join(" ");
    note.hidden = !notes.length;
  }

  if (primaryRange) topBar.append(primaryRange);
  topBar.append(presetBar, compareSel);
  const filterCard = h("section", { class: "dsp-panel dsp-filters" }, topBar, filters,
    h("div", { class: "dsp-dash-bar" }, groupSel, periodSel, formSel, measures, reset));
  wrap.append(filterCard, kpis, note, stage);
  paint();
  return wrap;
}

// ---------------------------------------------------------------- table
const isNum = (v) => typeof v === "number";

function table(cols, rows, cut) {
  let sort = { col: -1, dir: 1 };
  const tbody = h("tbody");
  const heads = cols.map((c, j) => h("th", { scope: "col", class: rows.length && rows.every((r) => r[j] === "" || isNum(r[j])) ? "num" : "",
    on: { click: () => { sort = { col: j, dir: sort.col === j ? -sort.dir : 1 }; fill(); } } }, c || " ", h("span", { class: "dsp-sort" })));
  const fill = () => {
    const list = [...rows];
    if (sort.col >= 0) {
      const j = sort.col;
      list.sort((a, b) => {
        const x = a[j], y = b[j];
        if (isNum(x) && isNum(y)) return (x - y) * sort.dir;
        return String(x).localeCompare(String(y), "fr", { numeric: true, sensitivity: "base" }) * sort.dir;
      });
    }
    heads.forEach((th, j) => { th.dataset.sort = sort.col === j ? (sort.dir > 0 ? "asc" : "desc") : ""; });
    tbody.replaceChildren(...list.map((r) => h("tr", {}, ...r.map((c) => h("td", { class: isNum(c) ? "num" : "" }, isNum(c) ? num.format(c) : c)))));
  };
  fill();
  const csv = () => [cols, ...rows].map((r) => r.map((c) => {
    const s = isNum(c) ? String(c).replace(".", ",") : String(c);
    return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(";")).join("\n");
  return h("div", { class: "dsp-table" },
    h("div", { class: "dsp-tools" }, h("span", { class: "muted" }, `${rows.length.toLocaleString("fr-FR")} ligne${rows.length > 1 ? "s" : ""}${cut ? " (tronqué)" : ""}`),
      h("button", { type: "button", class: "btn small ghost", on: { click: () => copyText(csv()) } }, icon("copy"), "Copier (CSV)")),
    h("div", { class: "dsp-scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ...heads)), tbody)));
}

// ---------------------------------------------------------------- charts (SVG drawn here, sized to their box)
function el(tag, attrs = {}, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  n.append(...kids.filter((k) => k != null));
  return n;
}

function niceTicks(lo, hi, count = 5) {
  if (lo === hi) { hi = lo === 0 ? 1 : lo + Math.abs(lo) * 0.5; lo = Math.min(0, lo); }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  const step = (r >= 7.5 ? 10 : r >= 3.5 ? 5 : r >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}

/** A chart's drawing area: it follows its width from the window it is in. In the desktop app ("Intégré au
 * bureau") a window is a document of its own while this page stays hidden, and an observer made here would
 * never be told: the observer comes from the element's window, made again when the element changes window. */
class Plot extends HTMLElement {
  connectedCallback() {
    this.ro?.disconnect();
    this.ro = new this.ownerDocument.defaultView.ResizeObserver(() => this.draw?.());
    this.ro.observe(this);
  }
  disconnectedCallback() { this.ro?.disconnect(); this.ro = null; }
}
if (!customElements.get("jarvis-plot")) customElements.define("jarvis-plot", Plot);

function chart(b) {
  const wrap = h("div", { class: "dsp-chart" });
  const plot = h("jarvis-plot", { class: "dsp-plot" });
  const tip = h("div", { class: "dsp-tip", role: "status" });
  const legend = b.forme === "secteurs"
    ? h("ul", { class: "dsp-legend pie" }, ...pieRows(b).map((r) => h("li", { class: `s${r.k + 1}` }, h("i"), h("span", {}, r.label), h("b", {}, fmt(r.v, b.unite)), h("small", {}, `${num.format(Math.round(r.pct * 10) / 10)} %`))))
    : b.series.length > 1 ? h("ul", { class: "dsp-legend" }, ...b.series.map((s, k) => h("li", { class: `s${k + 1}` }, h("i", { class: b.forme === "courbe" ? "ln" : "" }), s.nom || `Série ${k + 1}`))) : null;
  let asTable = false;
  const toggle = h("button", { type: "button", class: "btn small ghost dsp-toggle", on: { click: () => {
    asTable = !asTable;
    toggle.textContent = asTable ? "Graphique" : "Tableau";
    plot.hidden = asTable; if (legend) legend.hidden = asTable;
    data.hidden = !asTable;
  } } }, "Tableau");
  const data = table(["", ...b.series.map((s, k) => s.nom || `Série ${k + 1}`)], b.etiquettes.map((l, i) => [l, ...b.series.map((s) => s.valeurs[i] ?? "")]));
  data.hidden = true;
  plot.append(tip);
  let lastW = 0;
  plot.draw = () => {
    const w = Math.round(plot.clientWidth);
    if (!w || w === lastW) return;
    lastW = w;
    plot.querySelector("svg")?.remove();
    const g = b.forme === "secteurs" ? drawPie(b, w, tip) : drawXY(b, w, tip);
    plot.prepend(g);
  };
  wrap.append(h("div", { class: "dsp-ctools" }, b.unite ? h("span", { class: "muted" }, `En ${b.unite}`) : h("span"), toggle), plot, legend, data);
  return wrap;
}

function showTip(tip, plot, x, y, title, rows) {
  tip.replaceChildren(h("div", { class: "dsp-tip-h" }, title),
    ...rows.map((r) => h("div", { class: `dsp-tip-r s${r.k + 1}` }, h("i"), h("span", {}, r.name), h("b", {}, r.value))));
  tip.classList.add("on");
  const pw = plot.clientWidth;
  const tw = tip.offsetWidth;
  tip.style.left = `${Math.max(4, Math.min(pw - tw - 4, x + 14 + tw > pw ? x - tw - 14 : x + 14))}px`;
  tip.style.top = `${Math.max(4, y - 10)}px`;
}
const hideTip = (tip) => tip.classList.remove("on");

function drawXY(b, W, tip) {
  const H = 240, n = b.etiquettes.length;
  const all = b.series.flatMap((s) => s.valeurs).filter((v) => v != null);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (b.forme === "barres" || lo >= 0 && lo < hi * 0.35) lo = Math.min(0, lo);
  if (b.forme === "barres") hi = Math.max(0, hi);
  const ticks = niceTicks(lo, hi);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const labelW = Math.max(...ticks.map((t) => compact.format(t).length)) * 7 + 10;
  const L = labelW, R = 8, T = 10, B = 26;
  const pw = W - L - R, ph = H - T - B;
  const Y = (v) => T + ph - ((v - y0) / (y1 - y0)) * ph;
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": b.titre || "Graphique" });
  const grid = el("g", { class: "grid" });
  for (const t of ticks) {
    grid.append(el("line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), class: t === 0 ? "zero" : "" }),
      el("text", { x: L - 8, y: Y(t) + 4, "text-anchor": "end" }, compact.format(t)));
  }
  svg.append(grid);
  const band = pw / n;
  const X = (i) => L + band * i + band / 2;
  const every = Math.ceil(n / Math.max(1, Math.floor(pw / 64)));
  const xl = el("g", { class: "xl" });
  b.etiquettes.forEach((lab, i) => {
    if (i % every) return;
    const s = lab.length > 14 ? `${lab.slice(0, 13)}…` : lab;
    xl.append(el("text", { x: X(i), y: H - 8, "text-anchor": "middle" }, s));
  });
  svg.append(xl);
  const hover = el("rect", { class: "hoverband", x: 0, y: T, width: 0, height: ph });
  const cross = el("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: T + ph });
  const marks = el("g");
  const plot = () => tip.parentElement;
  const rowsAt = (i) => b.series.map((s, k) => ({ k, name: s.nom || `Série ${k + 1}`, value: fmt(s.valeurs[i], b.unite) }));
  const pickAt = (i) => pickable(`point « ${b.etiquettes[i]} »`, `${b.titre ? `Graphique « ${b.titre} »` : "Graphique"}, ${b.etiquettes[i]} :`,
    ...rowsAt(i).map((r) => `${r.name} = ${r.value}`));

  if (b.forme === "barres") {
    svg.append(hover);
    const s = b.series.length;
    const bw = Math.max(2, Math.min(24, (band * 0.72 - (s - 1) * 2) / s));
    const groupW = s * bw + (s - 1) * 2;
    const base = Y(Math.max(y0, Math.min(0, y1)));
    b.series.forEach((ser, k) => ser.valeurs.forEach((v, i) => {
      if (v == null) return;
      const x = X(i) - groupW / 2 + k * (bw + 2);
      const y = Y(v);
      const top = Math.min(y, base), hgt = Math.abs(base - y);
      const r = Math.min(4, bw / 2, hgt);
      // rounded on the data end only, square on the baseline
      const d = v >= 0
        ? `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${base}Z`
        : `M${x},${base}V${base + hgt - r}Q${x},${base + hgt} ${x + r},${base + hgt}H${x + bw - r}Q${x + bw},${base + hgt} ${x + bw},${base + hgt - r}V${base}Z`;
      marks.append(el("path", { d, class: `bar s${k + 1}` }));
    }));
    svg.append(marks);
    const hits = el("g");
    b.etiquettes.forEach((lab, i) => {
      const r = el("rect", { x: L + band * i, y: T, width: band, height: ph, class: "hit", ...pickAt(i) });
      r.addEventListener("mouseenter", () => {
        hover.setAttribute("x", L + band * i + band * 0.08); hover.setAttribute("width", band * 0.84); hover.classList.add("on");
      });
      r.addEventListener("mousemove", (ev) => {
        const p = plot().getBoundingClientRect();
        showTip(tip, plot(), ev.clientX - p.left, ev.clientY - p.top, lab, rowsAt(i));
      });
      r.addEventListener("mouseleave", () => { hover.classList.remove("on"); hideTip(tip); });
      hits.append(r);
    });
    svg.append(hits);
    return svg;
  }

  // line: 2px, gaps where a value is missing, markers when the points are few
  b.series.forEach((ser, k) => {
    let d = "", pen = false;
    ser.valeurs.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true;
    });
    marks.append(el("path", { d, class: `ln s${k + 1}` }));
    if (n <= 24) ser.valeurs.forEach((v, i) => { if (v != null) marks.append(el("circle", { cx: X(i), cy: Y(v), r: 4, class: `pt s${k + 1}` })); });
  });
  svg.append(cross, marks);
  const focus = el("g");
  svg.append(focus);
  const area = el("rect", { x: L, y: T, width: pw, height: ph, class: "hit" });
  area.addEventListener("mousemove", (ev) => {
    const p = plot().getBoundingClientRect();
    const sx = (ev.clientX - svg.getBoundingClientRect().left) * (W / svg.getBoundingClientRect().width);
    const i = Math.max(0, Math.min(n - 1, Math.floor((sx - L) / band)));
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.classList.add("on");
    focus.replaceChildren(...b.series.flatMap((s, k) => (s.valeurs[i] == null ? [] : [el("circle", { cx: X(i), cy: Y(s.valeurs[i]), r: 5, class: `pt on s${k + 1}` })])));
    for (const [k, v] of Object.entries(pickAt(i))) area.setAttribute(k, v); // what a right click points at
    showTip(tip, plot(), ev.clientX - p.left, ev.clientY - p.top, b.etiquettes[i], rowsAt(i));
  });
  area.addEventListener("mouseleave", () => { cross.classList.remove("on"); focus.replaceChildren(); hideTip(tip); });
  svg.append(area);
  return svg;
}

function pieRows(b) {
  const vals = b.series[0].valeurs;
  const total = vals.reduce((a, v) => a + (v || 0), 0) || 1;
  return b.etiquettes.map((label, k) => ({ k, label, v: vals[k] || 0, pct: ((vals[k] || 0) / total) * 100 }));
}

function drawPie(b, W, tip) {
  const H = 220, rows = pieRows(b);
  const total = rows.reduce((a, r) => a + r.v, 0);
  const R = Math.min(H / 2 - 6, 100), r0 = R * 0.62, cx = W / 2, cy = H / 2;
  if (!total) {
    const svg0 = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": b.titre || "Graphique" });
    svg0.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "pie-sub" }, "0"));
    return svg0;
  }
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": b.titre || "Graphique" });
  let a = -Math.PI / 2;
  const pt = (rad, ang) => `${(cx + rad * Math.cos(ang)).toFixed(2)},${(cy + rad * Math.sin(ang)).toFixed(2)}`;
  for (const row of rows) {
    const sweep = (row.v / total) * Math.PI * 2;
    const a2 = a + sweep;
    const big = sweep > Math.PI ? 1 : 0;
    const d = sweep >= Math.PI * 2 - 1e-6
      ? `M${pt(R, a)}A${R},${R} 0 1 1 ${pt(R, a + Math.PI)}A${R},${R} 0 1 1 ${pt(R, a)}M${pt(r0, a)}A${r0},${r0} 0 1 0 ${pt(r0, a + Math.PI)}A${r0},${r0} 0 1 0 ${pt(r0, a)}Z`
      : `M${pt(R, a)}A${R},${R} 0 ${big} 1 ${pt(R, a2)}L${pt(r0, a2)}A${r0},${r0} 0 ${big} 0 ${pt(r0, a)}Z`;
    const p = el("path", { d, class: `arc s${row.k + 1}`, "fill-rule": "evenodd",
      ...pickable(`part « ${row.label} »`, `${b.titre ? `Graphique « ${b.titre} »` : "Graphique"}, ${row.label} : ${fmt(row.v, b.unite)} (${num.format(Math.round(row.pct * 10) / 10)} % du total)`) });
    p.addEventListener("mousemove", (ev) => {
      const box = tip.parentElement.getBoundingClientRect();
      showTip(tip, tip.parentElement, ev.clientX - box.left, ev.clientY - box.top, row.label,
        [{ k: row.k, name: `${num.format(Math.round(row.pct * 10) / 10)} %`, value: fmt(row.v, b.unite) }]);
    });
    p.addEventListener("mouseleave", () => hideTip(tip));
    svg.append(p);
    a = a2;
  }
  svg.append(el("text", { x: cx, y: cy - 2, "text-anchor": "middle", class: "pie-total" }, compact.format(total)),
    el("text", { x: cx, y: cy + 16, "text-anchor": "middle", class: "pie-sub" }, b.unite ? `total (${b.unite})` : "total"));
  return svg;
}

// ---------------------------------------------------------------- window and modal
const wins = new Map(); // entry id -> window id
let seq = 0;

/** A display in a window of the desktop (one per display: opening it again brings it forward). */
export function openDisplayWindow(taskId, key, color) {
  const e = entry(taskId, key);
  if (!e) return null;
  const id0 = `${taskId}|${key}`;
  const shown = wins.get(id0);
  if (shown && wm.has(shown)) { if (wm.isMinimized(shown)) wm.restore(shown); else wm.focus(shown); return shown; }
  const id = `dsp-${++seq}`;
  wins.set(id0, id);
  const view = renderDisplay(taskId, key, { mode: "fenetre", color });
  const close = () => { wm.unregister(id); wins.delete(id0); unmount(view); };
  const act = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  const title = h("span", { class: "win-title" }, e.doc.titre);
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "pv-ic", svg: "sparkle" }),
    h("div", { class: "vh" }, title, h("small", {}, "Affichage de Claude")),
    h("div", { class: "pv-actions" }, desktopPin(e)),
    h("div", { class: "win-actions" },
      pinButton(id),
      act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer", close)));
  const win = paint(h("section", { class: "win pv-win dsp-win", role: "dialog", "aria-label": e.doc.titre },
    head, h("div", { class: "pv-body dsp-wbody" }, view)), color);
  // the title follows updates
  e.mounts.add({ root: win, update: () => { title.textContent = e.doc.titre; } });
  const wide = e.doc.blocs.some((b) => b.type === "tableau_de_bord");
  wm.register(id, win, { handle: head, ephemeral: true, size: wide ? { w: 1180, h: 880 } : { w: 780, h: 680 }, fresh: true,
    meta: { title: e.doc.titre, subtitle: "Affichage de Claude", color: colorOf(color), icon: "sparkle", onClose: close,
      regard: { type: "affichage", task: taskId, key } } });
  return id;
}

let modal = null; // {id0, close}

/** A display in front of everything (Échap or a click outside closes it; it can become a window). */
export function openDisplayModal(taskId, key, color) {
  const e = entry(taskId, key);
  if (!e) return;
  if (wm.isNative()) {
    // windows on the desktop (app): "in front" is a window kept above the others
    const id = openDisplayWindow(taskId, key, color);
    if (id && !wm.isPinned(id)) wm.togglePin(id);
    return;
  }
  const id0 = `${taskId}|${key}`;
  if (modal?.id0 === id0) return;
  modal?.close();
  const view = renderDisplay(taskId, key, { mode: "modale", color });
  const title = h("h3", {}, e.doc.titre);
  const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(); } };
  const close = () => { overlay.remove(); unmount(view); document.removeEventListener("keydown", onKey, true); if (modal?.id0 === id0) modal = null; };
  const act = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  const box = paint(h("div", { class: "dialog dsp-modal", role: "dialog", "aria-modal": "true", "aria-label": e.doc.titre },
    h("div", { class: "dsp-mhead" }, icon("sparkle"), title,
      joinButton({ type: "affichage", task: taskId, key }),
      // pinned: a window that stays in front without blocking the rest of the console
      act("pin", "Épingler au premier plan (dans une fenêtre)", () => {
        close();
        const id = openDisplayWindow(taskId, key, color);
        if (id && !wm.isPinned(id)) wm.togglePin(id);
      }),
      act("max", "Garder dans une fenêtre", () => { close(); openDisplayWindow(taskId, key, color); }),
      act("close", "Fermer (Échap)", close)),
    h("div", { class: "dsp-mbody" }, view)), color);
  const overlay = h("div", { class: "overlay dsp-overlay", on: { mousedown: (ev) => { if (ev.target === overlay) close(); } } }, box);
  e.mounts.add({ root: overlay, update: () => { title.textContent = e.doc.titre; } });
  document.addEventListener("keydown", onKey, true);
  document.getElementById("modal-root").append(overlay);
  modal = { id0, close };
  box.querySelector(".icon-btn")?.focus();
}

export const displayTitle = (taskId, key) => entry(taskId, key)?.doc.titre || "Affichage";
export const hasDisplay = (taskId, key) => !!entry(taskId, key);
export const isWindowOpen = (taskId, key) => { const id = wins.get(`${taskId}|${key}`); return !!id && wm.has(id); };
