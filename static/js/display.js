// Displays composed by Claude (tool mcp__jarvis__presenter): typed blocks checked by the server, drawn
// here always the same way. A display lives in the conversation, in a window or in a modal; every place
// that shows it is redrawn when Claude updates it (same id) or when the user answers a choice.
// Web images outside the approved domains are only loaded after a click (an address written by a model
// could carry data out); local files come through the console's own API, like previews.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { confirmDialog, copyText, h, toast } from "./util.js";
import { openPreview, fileBlob, pinButton, thumbnail } from "./viewer.js";
import { look } from "./regard.js";
import { colorOf, paint } from "./tint.js";
import * as wm from "./wm.js";

const SVGNS = "http://www.w3.org/2000/svg";
const entries = new Map(); // `${taskId}|${key}` -> {taskId, key, doc, rev, answers: Map, mounts: Set, …}
let settings = { autoImages: () => false, appAction: null };
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

function content(e, m) {
  const { doc } = e;
  const out = [];
  if (m.mode === "conversation") {
    out.push(h("div", { class: "dsp-head" }, icon("sparkle"), h("span", { class: "dsp-title" }, doc.titre),
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

function chart(b) {
  const wrap = h("div", { class: "dsp-chart" });
  const plot = h("div", { class: "dsp-plot" });
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
  const draw = () => {
    const w = Math.round(plot.clientWidth);
    if (!w || w === lastW) return;
    lastW = w;
    plot.querySelector("svg")?.remove();
    const g = b.forme === "secteurs" ? drawPie(b, w, tip) : drawXY(b, w, tip);
    plot.prepend(g);
  };
  new ResizeObserver(draw).observe(plot);
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
    h("div", { class: "pv-actions" }),
    h("div", { class: "win-actions" },
      pinButton(id),
      act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer", close)));
  const win = paint(h("section", { class: "win pv-win dsp-win", role: "dialog", "aria-label": e.doc.titre },
    head, h("div", { class: "pv-body dsp-wbody" }, view)), color);
  // the title follows updates
  e.mounts.add({ root: win, update: () => { title.textContent = e.doc.titre; } });
  wm.register(id, win, { handle: head, ephemeral: true, size: { w: 780, h: 680 }, fresh: true,
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
  look({ type: "affichage", task: taskId, key }); // in front of everything: what the user looks at
  box.querySelector(".icon-btn")?.focus();
}

export const displayTitle = (taskId, key) => entry(taskId, key)?.doc.titre || "Affichage";
export const hasDisplay = (taskId, key) => !!entry(taskId, key);
export const isWindowOpen = (taskId, key) => { const id = wins.get(`${taskId}|${key}`); return !!id && wm.has(id); };
