// "Ce que je regarde": what the user is looking at when writing, sent with the message (console/regard.py).
// It is the preview or display last brought forward, and the text last selected in the console (a
// preview, a display, an answer of Claude). A chip in the bar being used says what will go, its cross
// removes it. Sent once: it comes back when the user selects something else or goes back to a preview.
// Text selected inside a frame (a PDF, a mail, a web page) cannot be read by the console: only the
// file or the page is named then.
import { h } from "./util.js";
import * as wm from "./wm.js";

const MAX = 4000;
let current = null; // {type, path?, url?, task?, key?, call?, tool?, contient?, title?, selection?, element?, wid?}
const listeners = new Set();
let settings = { enabled: () => true, displayTitle: () => "", taskTitle: () => "" };

export function configure(s) { settings = { ...settings, ...s }; changed(); }
export function onChange(fn) { listeners.add(fn); }
let picked = null; // the element pointed at, outlined while it would go
function changed() {
  const el = current?.pickEl || null;
  if (el !== picked) { picked?.classList.remove("regard-pick"); picked = el; picked?.classList.add("regard-pick"); }
  listeners.forEach((fn) => fn());
}

/** What would go with the next message, or null. */
export function get() { return settings.enabled() ? current : null; }
export function clear() {
  clearTimeout(pending);
  mute = Date.now() + 500; // the click keeps the selection: do not put the chip back from it
  if (current) { current = null; changed(); }
}
/** After a message went with r: it is not sent again. */
export function sent(r) { if (r && current === r) clear(); }

/** What the server receives (console/regard.py: clean). */
export function payload(r) {
  if (!r) return null;
  const out = {};
  for (const k of ["type", "path", "url", "task", "key", "call", "tool", "contient", "selection", "element"]) if (r[k]) out[k] = r[k];
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
  if (r.element) return r.type === "texte" ? snippet(r.element.label, 60) : `${snippet(r.element.label, 44)} · ${what}`;
  return r.selection && r.type !== "discussion" && r.type !== "texte" ? `${what} · « ${snippet(r.selection, 36)} »` : what;
}

/** A window brought forward: what it shows becomes what the user looks at (its selection kept). */
function look(desc, wid = null) {
  if (!desc || Date.now() < mute) return;
  const same = current && current.wid === wid;
  const keep = same ? Object.fromEntries(["selection", "element", "pickEl"].filter((k) => current[k]).map((k) => [k, current[k]])) : {};
  current = { ...desc, ...keep, wid };
  changed();
}
export { look };

let front = null; // the window already in front: a click inside it must not rebuild the chip under the pointer
wm.onFocus((id) => {
  const moved = front !== id;
  front = id;
  if (!moved) return;
  const meta = wm.meta(id);
  if (meta?.regard) look(meta.regard, id);
  else changed(); // a bar shows the chip only while its window has the focus
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
let mute = 0; // a click on the cross leaves the selection in place: ignore it coming back for a moment
// every document: the page's, and each native window's in the desktop app
wm.onDocument((doc) => doc.addEventListener("selectionchange", () => {
  clearTimeout(pending);
  pending = setTimeout(() => {
    if (Date.now() < mute) return;
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

// ---------------------------------------------------------------- an element pointed at (right click)
// A display marks what can be pointed at with data-pick (its description for Claude) and data-pick-label;
// any table row with a header row describes itself (a display's table, an Excel or CSV preview, a table
// in an answer of Claude).
const cellText = (c) => c.textContent.replace(/\s+/g, " ").trim();

function pickOf(el) {
  const marked = el.closest("[data-pick]");
  if (marked) return { el: marked, label: marked.dataset.pickLabel || "élément", detail: marked.dataset.pick };
  const tr = el.closest("tr");
  const table = tr?.closest("table");
  if (!tr || !table || tr.closest("thead")) return null;
  const head = [...(table.querySelector("thead tr")?.children || [])].map(cellText);
  const cells = [...tr.children].map(cellText);
  if (!cells.some(Boolean)) return null;
  const where = tr.closest(".dsp-block, .pv-sheet")?.querySelector("h4")?.textContent.trim();
  const name = cells.find(Boolean);
  const detail = [where ? `Tableau « ${where} », une ligne :` : "Une ligne d'un tableau :",
    ...cells.map((c, i) => `${head[i] || `colonne ${i + 1}`} : ${c}`)].join("\n");
  return { el: tr, label: `ligne « ${name.length > 40 ? `${name.slice(0, 39)}…` : name} »`, detail };
}

/** The input the next message is typed in: the window's follow-up, or the command bar. */
function inputFor(el) {
  const win = el.closest(".win[data-id]");
  return win?.querySelector(".win-foot textarea") || el.ownerDocument.querySelector("#cmd-input")
    || document.querySelector("#cmd-input");
}

function pick(el, p, src) {
  mute = Date.now() + 500;
  el.ownerDocument.getSelection()?.removeAllRanges();
  current = { ...src.desc, element: { label: p.label, detail: p.detail.slice(0, 2000) }, pickEl: p.el, wid: src.wid };
  changed();
  inputFor(el)?.focus();
}

function menu(ev, items) {
  const doc = ev.target.ownerDocument;
  doc.querySelectorAll(".menu.popup.regard-menu").forEach((m) => m.remove());
  const box = h("div", { class: "menu popup regard-menu", role: "menu" });
  Object.assign(box.style, { position: "fixed", zIndex: "9500", right: "auto", bottom: "auto", left: `${ev.clientX}px`, top: `${ev.clientY}px` });
  const close = () => { box.remove(); doc.removeEventListener("pointerdown", off, true); doc.removeEventListener("keydown", esc, true); };
  const off = (e) => { if (!box.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  for (const it of items) {
    box.append(h("button", { type: "button", role: "menuitem", on: { click: () => { close(); it.run(); } } }, it.label));
  }
  doc.body.append(box);
  const view = doc.defaultView;
  box.style.left = `${Math.min(ev.clientX, view.innerWidth - box.offsetWidth - 8)}px`;
  box.style.top = `${Math.min(ev.clientY, view.innerHeight - box.offsetHeight - 8)}px`;
  doc.addEventListener("pointerdown", off, true);
  doc.addEventListener("keydown", esc, true);
}

wm.onDocument((doc) => doc.addEventListener("contextmenu", (ev) => {
  if (!settings.enabled() || ev.shiftKey) return; // Maj + clic droit: the browser's own menu
  const el = ev.target.nodeType === 1 ? ev.target : ev.target.parentElement;
  if (!el || el.closest("input, textarea, select, [contenteditable], a[href]:not([href='#'])")) return;
  const p = pickOf(el);
  const src = p && (sourceOf(el) || { desc: { type: "texte" }, wid: null });
  if (!p) return;
  ev.preventDefault();
  menu(ev, [
    { label: "Demander à Claude à propos de ceci", run: () => pick(el, p, src) },
    { label: "Copier", run: () => navigator.clipboard?.writeText(p.detail) },
  ]);
}));

/** A chip that follows what would be sent. visible(): whether this bar shows it now; here: the
 * discussion of this bar ("" for the command bar). Returns {render, dispose}. */
export function chip(list, { visible = () => true, here = "" } = {}) {
  // A click in the window focuses it first (capture on .win) and used to rebuild this chip
  // before the cross could run. The list itself stays, so the cross is caught here.
  list.addEventListener("pointerdown", (e) => {
    if (!e.target.closest?.(".att-x")) return;
    e.preventDefault();
    e.stopPropagation();
    clear();
    list.ownerDocument.getSelection()?.removeAllRanges();
  }, true);
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
      r.element ? `élément désigné :\n${r.element.detail}` : "",
      "La croix le retire de ce message.",
    ].filter(Boolean).join("\n");
    list.append(h("div", { class: "att ok regard", title: tip },
      h("span", { class: "att-ic", svg: "eye" }), h("span", { class: "att-name" }, h("b", {}, "Regard : "), what),
      h("button", { type: "button", class: "att-x", title: "Ne pas joindre", "aria-label": "Ne pas joindre ce que je regarde", svg: "x",
        on: {
          pointerdown: (e) => {
            e.preventDefault();
            e.stopPropagation();
            const doc = e.currentTarget.ownerDocument;
            clear();
            doc.getSelection()?.removeAllRanges();
          },
          click: (e) => { e.preventDefault(); e.stopPropagation(); },
        } })));
  };
  onChange(render);
  render();
  return { render, dispose: () => listeners.delete(render) };
}
