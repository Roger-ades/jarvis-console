// Small DOM helpers. Untrusted text only ever goes through textContent or esc().

const lookups = [];
/** Another document whose elements `$` also finds (the windows of the desktop app: the floating bar, a
 * panel moved into a window of its own). */
export function addLookupDocument(doc) { if (!lookups.includes(doc)) lookups.push(doc); }
export function removeLookupDocument(doc) { const i = lookups.indexOf(doc); if (i >= 0) lookups.splice(i, 1); }
export const $ = (sel, root) => (root ? root.querySelector(sel)
  : document.querySelector(sel) || lookups.reduce((found, d) => found || d.querySelector(sel), null));
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** h("div", {class: "x", on: {click}}, "text", child) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k === "svg") el.innerHTML = ICONS[v] || "";
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const S = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const ICONS = {
  stop: S('<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>'),
  more: S('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
  pin: S('<path d="M9 4h6l-1 6 4 3v2H6v-2l4-3z"/><path d="M12 15v6"/>'),
  min: S('<path d="M6 16h12"/>'),
  close: S('<path d="M7 7l10 10M17 7L7 17"/>'),
  send: S('<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>'),
  copy: S('<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  retry: S('<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4v4h4"/>'),
  panel: S('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/>'),
  chev: S('<path d="M9 6l6 6-6 6"/>'),
  file: S('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
  edit: S('<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'),
  terminal: S('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9l3 3-3 3"/><path d="M13 15h4"/>'),
  search: S('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>'),
  folder: S('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  globe: S('<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18"/>'),
  bot: S('<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4"/><circle cx="12" cy="3.5" r="1"/><path d="M9 13v1.5M15 13v1.5"/><path d="M2 13v3M22 13v3"/>'),
  list: S('<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>'),
  plug: S('<path d="M9 2v5M15 2v5"/><path d="M6 7h12v4a6 6 0 0 1-12 0z"/><path d="M12 17v5"/>'),
  sparkle: S('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'),
  brain: S('<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>'),
  check: S('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  x: S('<path d="M7 7l10 10M17 7L7 17"/>'),
  shield: S('<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5"/>'),
  shieldq: S('<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M10 9.5a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2v.5"/><path d="M12 15.5h.01"/>'),
  help: S('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.4v.3"/><path d="M12 17h.01"/>'),
  info: S('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>'),
  alert: S('<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><path d="M12 17h.01"/>'),
  clock: S('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  user: S('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
  model: S('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>'),
  gauge: S('<path d="M4 18h3v-4H4zM10.5 18h3V10h-3zM17 18h3V6h-3z"/>'),
  dot: S('<circle cx="12" cy="12" r="3"/>'),
  link: S('<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/>'),
  branch: S('<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10"/><path d="M18 10c0 4-6 3-11.3 7.4"/>'),
  book: S('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M8 7h8M8 11h6"/>'),
  clip: S('<path d="M20.5 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9"/>'),
  eye: S('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'),
  regard: S('<path d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"/><circle cx="12" cy="12" r="2.2"/>'),
  diff: S('<rect x="4" y="2.5" width="16" height="19" rx="2.5"/><path d="M12 6v6M9 9h6"/><path d="M9 16.5h6"/>'),
  undo: S('<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'),
  mail: S('<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.8 7 12 13l8.2-6"/>'),
  download: S('<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>'),
  flame: S('<path d="M12 3c.6 3.2 4.5 5.3 4.5 10a4.5 4.5 0 0 1-9 0c0-2.3 1.2-3.8 2.3-4.8.2 1.7 1 2.8 2.2 3.3-.4-3 .1-5.6 0-8.5z"/>'),
  max: S('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
  image: S('<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-8 8"/>'),
  bolt: S('<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>'),
  app: S('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M6.5 6.5h.01M9 6.5h.01"/>'),
  external: S('<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>'),
  note: S('<path d="M5 4h14v11l-5 5H5z"/><path d="M14 20v-5h5"/><path d="M8.5 8.5h7M8.5 12h4.5"/>'),
  puzzle: S('<path d="M10 4.5a2 2 0 0 1 4 0V6h3a1 1 0 0 1 1 1v3h-1.5a2 2 0 0 0 0 4H18v3a1 1 0 0 1-1 1h-3v-1.5a2 2 0 0 0-4 0V18H7a1 1 0 0 1-1-1v-3h1.5a2 2 0 0 0 0-4H6V7a1 1 0 0 1 1-1h3z"/>'),
  bell: S('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>'),
};

/** The console's own tool: Claude opens files in preview windows. */
export const SHOW_TOOL = "mcp__jarvis__afficher";
export const RESULT_TOOL = "mcp__jarvis__afficher_resultat";
export const PRESENT_TOOL = "mcp__jarvis__presenter";
export const PROPOSE_TOOL = "mcp__jarvis__proposer";

/** Icon of a tool call, by family. */
export function toolIcon(name = "") {
  if (name === SHOW_TOOL || name === RESULT_TOOL) return "eye";
  if (name === PRESENT_TOOL) return "sparkle";
  if (name === PROPOSE_TOOL) return "bolt";
  if (name.startsWith("mcp__")) return "plug";
  return ({
    Read: "file", NotebookRead: "file", Write: "edit", Edit: "edit", MultiEdit: "edit", NotebookEdit: "edit",
    Bash: "terminal", PowerShell: "terminal", BashOutput: "terminal", KillShell: "terminal",
    Glob: "folder", LS: "folder", Grep: "search", WebFetch: "globe", WebSearch: "globe",
    web_search: "globe", web_fetch: "globe", Task: "bot", Agent: "bot", TodoWrite: "list",
    Skill: "sparkle", AskUserQuestion: "help", ExitPlanMode: "list", ToolSearch: "search",
  })[name] || "dot";
}

export function fmtTokens(n) {
  if (!n) return "0";
  if (n < 1000) return String(n);
  if (n < 1e6) return `${(n / 1000).toLocaleString("fr-FR", { maximumFractionDigits: n < 1e4 ? 1 : 0 })} k`;
  return `${(n / 1e6).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} M`;
}

export function icon(name) {
  const s = document.createElement("span");
  s.className = "ic";
  s.innerHTML = ICONS[name] || "";
  return s.firstChild;
}

export function iconBtn(name, title, onClick, cls = "") {
  return h("button", { type: "button", class: `icon-btn ${cls}`, title, "aria-label": title, svg: name, on: { click: onClick } });
}

export function fmtDuration(ms) {
  if (!ms || ms < 0) return "0 s";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return `${m} min ${String(r).padStart(2, "0")}`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

export function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  return `${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} ${time}`;
}

export function fmtCost(usd) {
  if (!usd) return "";
  return `${Number(usd).toLocaleString("fr-FR", { maximumFractionDigits: usd < 1 ? 3 : 2 })} $`;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copié dans le presse-papiers.", "ok"); }
  catch { toast("Copie impossible dans ce navigateur.", "err"); }
}

// Where dialogs and toasts go: this page, or the native window in use (wm.js, desktop app).
// kind: toast | dialog | config | palette | setup
let hostDocument = (_kind) => document;
export function setHostDocument(fn) { hostDocument = fn; }

// Brings forward the window a document belongs to (desktop app; nothing in the browser).
let revealDocument = (_doc) => {};
export function setRevealDocument(fn) { revealDocument = fn; }
export function reveal(doc) { revealDocument(doc); }

/** Where a modal goes (a dialog, the configuration, Ctrl+K…): {root: its #modal-root, doc: the document
 * whose keys it listens to}. */
export function modalHost(kind = "dialog") {
  const root = hostDocument(kind).getElementById("modal-root") || document.getElementById("modal-root");
  return { root, doc: root.ownerDocument };
}

// Where a notice of the console goes (an update to install): the top of this page, or the desktop app's bar.
let noticeHost = () => document.body;
export function setNoticeHost(fn) { noticeHost = fn; }
export function noticeRoot() { return noticeHost(); }

/** The launcher: in the desktop app, its own "JARVIS" shortcuts (with its taskbar identity); else the
 * console's (it opens what Configuration → Général → Ouverture says). Returns the shortcuts' paths. */
export async function createLauncher(api) {
  if (window.jarvis?.createLauncher && window.jarvis.platform === "win32") {
    const r = await window.jarvis.createLauncher();
    if (r?.error) throw new Error(r.error);
    return r.paths || [];
  }
  return (await api("/api/system/launcher", { method: "POST" })).paths;
}

export function toast(message, kind = "") {
  const box = hostDocument("toast").getElementById("toasts") || $("#toasts");
  const el = h("div", { class: `toast ${kind}`, role: "status" }, message);
  box.append(el);
  setTimeout(() => el.remove(), kind === "err" ? 7000 : 3500);
}

export const store = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
  del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

/** Modal dialog. Resolves with the clicked button's value (or null). */
/** Paints an element with a plain color, or a tint {account, project, color} (see tint.js): --pc is the
    color (the mix in a project), --pc-a and --pc-p the two colors for the gradients of .tinted. */
export function paint(el, c) {
  if (!el) return el;
  const t = typeof c === "string" || !c ? { color: c, project: "" } : c;
  el.style.setProperty("--pc", t.color || "var(--accent)");
  if (t.project) {
    el.style.setProperty("--pc-a", t.account);
    el.style.setProperty("--pc-p", t.project);
    el.classList.add("tinted");
  } else {
    el.style.removeProperty("--pc-a");
    el.style.removeProperty("--pc-p");
    el.classList.remove("tinted");
  }
  return el;
}

/** tint: the colors of the account (and of the project) the dialog is about. */
export function dialog({ title, body, buttons = [{ label: "OK", value: true, cls: "primary" }], input = null, onOpen = null, tint = null }) {
  return new Promise((resolve) => {
    const { root } = modalHost("dialog");
    const field = input ? h("input", { type: input.type || "text", value: input.value || "", placeholder: input.placeholder || "" }) : null;
    const done = (v) => { overlay.remove(); root.ownerDocument.removeEventListener("keydown", onKey, true); resolve(v); };
    const actions = buttons.map((b) => h("button", { type: "button", class: `btn ${b.cls || ""}`, on: { click: () => done(field && b.value === true ? field.value : b.value) } }, b.label));
    const content = typeof body === "string" ? h("p", {}, body) : body;
    const box = h("div", { class: "dialog", role: "dialog", "aria-modal": "true" },
      h("h3", {}, title), h("div", { class: "dialog-body" }, content, field), h("div", { class: "dialog-actions" }, actions));
    if (tint) paint(box, tint);
    const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) done(null); } } }, box);
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); done(null); }
      if (e.key === "Enter" && field && root.ownerDocument.activeElement === field) { e.preventDefault(); done(field.value); }
    }
    root.ownerDocument.addEventListener("keydown", onKey, true);
    root.append(overlay);
    (field || actions[actions.length - 1])?.focus();
    onOpen?.(box);
  });
}

export const confirmDialog = (title, message, label = "Confirmer", cls = "primary", tint = null) =>
  dialog({ title, body: message, buttons: [{ label: "Annuler", value: false }, { label, value: true, cls }], tint });

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export const TOOL_LABELS = {
  Read: "Lecture", Write: "Écriture", Edit: "Modification", MultiEdit: "Modifications", NotebookEdit: "Notebook",
  Bash: "Commande", PowerShell: "PowerShell", Glob: "Fichiers", Grep: "Recherche", LS: "Dossier",
  WebFetch: "Page web", WebSearch: "Recherche web", Task: "Sous-agent", Agent: "Sous-agent", TodoWrite: "Plan",
  Skill: "Skill", AskUserQuestion: "Question", ExitPlanMode: "Plan", ToolSearch: "Outils",
  web_search: "Recherche web", web_fetch: "Page web",
};

export function toolLabel(name) {
  if (!name) return "Outil";
  if (name === SHOW_TOOL || name === RESULT_TOOL || name === PRESENT_TOOL) return "Affichage";
  if (name === PROPOSE_TOOL) return "Proposition pour le projet";
  if (name.startsWith("mcp__")) {
    const [server, ...rest] = name.slice(5).split("__");
    return `${server.replace(/^claude_ai_/, "")} · ${rest.join("__")}`;
  }
  return TOOL_LABELS[name] || name;
}

/** "Opus 5.5" for claude-opus-5-5, "Haiku 4.5" for claude-haiku-4-5-20251001. */
export function modelName(id = "") {
  const m = id.match(/(opus|sonnet|haiku|fable)-(\d+)-(\d+)/i);
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1).toLowerCase()} ${m[2]}.${m[3]}` : id || "modèle";
}

export const STATUS = {
  queued: "En file", running: "En cours", awaiting: "À valider", done: "Terminée",
  error: "En erreur", cancelled: "Annulée", interrupted: "Interrompue",
};
export const ACTIVE = new Set(["queued", "running", "awaiting"]);

/** Claude's plan (TodoWrite): steps done, total, and the step under way (or the next one). */
export function planProgress(t) {
  const todos = t.todos || [];
  const done = todos.filter((x) => x.status === "completed").length;
  const cur = todos.find((x) => x.status === "in_progress") || todos.find((x) => x.status !== "completed");
  return { todos, done, total: todos.length, current: cur ? (cur.status === "in_progress" && cur.active) || cur.content : "" };
}

/** Status label, "Programmée · 15:01" for a task waiting for its start time, "En cours · 3/7" with a plan. */
export function statusLabel(t) {
  if (t.status === "queued" && t.not_before && t.not_before * 1000 > Date.now()) {
    const d = new Date(t.not_before * 1000);
    const same = d.toDateString() === new Date().toDateString();
    return `Programmée · ${same ? "" : `${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} `}${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
  }
  const p = planProgress(t);
  const label = STATUS[t.status] || t.status;
  return (t.status === "running" || t.status === "awaiting") && p.total ? `${label} · ${p.done}/${p.total}` : label;
}
