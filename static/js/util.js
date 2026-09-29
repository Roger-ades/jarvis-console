// Small DOM helpers. Untrusted text only ever goes through textContent or esc().

export const $ = (sel, root = document) => root.querySelector(sel);
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
  eye: S('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'),
};

/** Icon of a tool call, by family. */
export function toolIcon(name = "") {
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

export function toast(message, kind = "") {
  const box = $("#toasts");
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
export function dialog({ title, body, buttons = [{ label: "OK", value: true, cls: "primary" }], input = null, onOpen = null }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const field = input ? h("input", { type: input.type || "text", value: input.value || "", placeholder: input.placeholder || "" }) : null;
    const done = (v) => { overlay.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const actions = buttons.map((b) => h("button", { type: "button", class: `btn ${b.cls || ""}`, on: { click: () => done(field && b.value === true ? field.value : b.value) } }, b.label));
    const content = typeof body === "string" ? h("p", {}, body) : body;
    const box = h("div", { class: "dialog", role: "dialog", "aria-modal": "true" },
      h("h3", {}, title), h("div", { class: "dialog-body" }, content, field), h("div", { class: "dialog-actions" }, actions));
    const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) done(null); } } }, box);
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); done(null); }
      if (e.key === "Enter" && field && document.activeElement === field) { e.preventDefault(); done(field.value); }
    }
    document.addEventListener("keydown", onKey, true);
    root.append(overlay);
    (field || actions[actions.length - 1])?.focus();
    onOpen?.(box);
  });
}

export const confirmDialog = (title, message, label = "Confirmer", cls = "primary") =>
  dialog({ title, body: message, buttons: [{ label: "Annuler", value: false }, { label, value: true, cls }] });

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
  if (name.startsWith("mcp__")) {
    const [server, ...rest] = name.slice(5).split("__");
    return `${server.replace(/^claude_ai_/, "")} · ${rest.join("__")}`;
  }
  return TOOL_LABELS[name] || name;
}

export const STATUS = {
  queued: "En file", running: "En cours", awaiting: "À valider", done: "Terminée",
  error: "En erreur", cancelled: "Annulée", interrupted: "Interrompue",
};
export const ACTIVE = new Set(["queued", "running", "awaiting"]);
