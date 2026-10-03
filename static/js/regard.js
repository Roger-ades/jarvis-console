// "Ce que je regarde": what the user is looking at when writing, sent with the message (console/regard.py).
// It is the preview or display last brought forward, and the text last selected in the console (a
// preview, a display, an answer of Claude). A chip in the bar being used says what will go, its cross
// removes it. Sent once: it comes back when the user selects something else or goes back to a preview.
// Text selected inside a frame (a PDF, a mail, a web page) cannot be read by the console: only the
// file or the page is named then.
import { h } from "./util.js";
import * as wm from "./wm.js";

const MAX = 4000;
let current = null; // {type, path?, url?, task?, key?, call?, tool?, contient?, title?, selection?, wid?}
const listeners = new Set();
let settings = { enabled: () => true, displayTitle: () => "", taskTitle: () => "" };

export function configure(s) { settings = { ...settings, ...s }; changed(); }
export function onChange(fn) { listeners.add(fn); }
function changed() { listeners.forEach((fn) => fn()); }

/** What would go with the next message, or null. */
export function get() { return settings.enabled() ? current : null; }
export function clear() { if (current) { current = null; changed(); } }
/** After a message went with r: it is not sent again. */
export function sent(r) { if (r && current === r) clear(); }

/** What the server receives (console/regard.py: clean). */
export function payload(r) {
  if (!r) return null;
  const out = {};
  for (const k of ["type", "path", "url", "task", "key", "call", "tool", "contient", "selection"]) if (r[k]) out[k] = r[k];
  const title = titleOf(r);
  if (title) out.title = title;
  return out;
}

function titleOf(r) {
  if (r.type === "affichage") return settings.displayTitle(r.task, r.key) || r.title || "";
  if (r.type === "discussion") return settings.taskTitle(r.task) || r.title || "";
  return r.title || "";
}

const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const snippet = (s, n = 48) => { const t = s.replace(/\s+/g, " ").trim(); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };

/** A few words for the chip. here: the discussion whose bar shows it ("" for the command bar). */
export function label(r, here = "") {
  let what;
  if (r.type === "fichier") what = baseName(r.path);
  else if (r.type === "page") what = String(r.url || "").replace(/^https?:\/\//, "");
  else if (r.type === "affichage") what = `affichage « ${titleOf(r) || "sans titre"} »`;
  else if (r.type === "resultat") what = `${r.kind === "mail" ? "mail" : "résultat"} « ${titleOf(r) || "outil"} »`;
  else if (r.type === "discussion") what = r.task && r.task === here ? "passage de cette discussion" : `passage de « ${titleOf(r) || "discussion"} »`;
  else what = "texte sélectionné";
  return r.selection && r.type !== "discussion" && r.type !== "texte" ? `${what} · « ${snippet(r.selection, 36)} »` : what;
}

/** A window brought forward: what it shows becomes what the user looks at (its selection kept). */
function look(desc, wid = null) {
  if (!desc) return;
  const keep = current && current.wid === wid && current.selection ? { selection: current.selection } : {};
  current = { ...desc, ...keep, wid };
  changed();
}
export { look };

wm.onFocus((id) => {
  const meta = wm.meta(id);
  if (meta?.regard) look(meta.regard, id);
  else changed(); // (a bar shows the chip only while its window has the focus)
});
// the window it came from is closed: the user no longer looks at it
wm.onChange(() => { if (current?.wid && !wm.has(current.wid)) clear(); });

/** The source of a selection: a display (in the conversation, a window or the modal), a preview, or a
 * discussion's own text. */
function sourceOf(el) {
  const dsp = el.closest(".dsp[data-task][data-key]");
  const win = el.closest(".win[data-wid]");
  const wid = win?.dataset.wid || null;
  if (dsp) return { desc: { type: "affichage", task: dsp.dataset.task, key: dsp.dataset.key }, wid };
  if (!win) return el.closest(".dsp-modal") ? { desc: { type: "texte" }, wid: null } : null;
  const meta = wm.meta(wid);
  if (meta?.regard) return { desc: meta.regard, wid };
  if (win.dataset.id && el.closest(".win-body, .win-approvals")) return { desc: { type: "discussion", task: win.dataset.id }, wid };
  return null;
}

let pending = 0;
// every document: the page's, and each native window's in the desktop app
wm.onDocument((doc) => doc.addEventListener("selectionchange", () => {
  clearTimeout(pending);
  pending = setTimeout(() => {
    const sel = doc.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const node = sel.anchorNode;
    const el = node?.nodeType === 1 ? node : node?.parentElement;
    if (!el || el.closest("input, textarea, select, [contenteditable], .att-list, .msg-regard")) return;
    const text = sel.toString().trim();
    if (!text) return;
    const src = sourceOf(el);
    if (!src) return;
    current = { ...src.desc, selection: text.length > MAX ? `${text.slice(0, MAX)} […]` : text, wid: src.wid };
    changed();
  }, 180);
}));

/** A chip that follows what would be sent. visible(): whether this bar shows it now; here: the
 * discussion of this bar ("" for the command bar). Returns {render, dispose}. */
export function chip(list, { visible = () => true, here = "" } = {}) {
  const render = () => {
    const r = get();
    list.replaceChildren();
    list.hidden = !r || !visible();
    if (list.hidden) return;
    const what = label(r, here);
    const tip = [
      "Part avec ton message, pour que Claude sache de quoi tu parles :",
      r.path ? `fichier : ${r.path}` : r.url ? `page : ${r.url}` : `${label({ ...r, selection: "" }, here)}`,
      r.selection ? `texte sélectionné (${r.selection.length.toLocaleString("fr-FR")} caractères)` : "",
      "La croix le retire de ce message.",
    ].filter(Boolean).join("\n");
    list.append(h("div", { class: "att ok regard", title: tip },
      h("span", { class: "att-ic", svg: "eye" }), h("span", { class: "att-name" }, h("b", {}, "Regard : "), what),
      h("button", { type: "button", class: "att-x", title: "Ne pas joindre", "aria-label": "Ne pas joindre ce que je regarde", svg: "x",
        on: { click: (e) => { e.preventDefault(); clear(); } } })));
  };
  onChange(render);
  render();
  return { render, dispose: () => listeners.delete(render) };
}
