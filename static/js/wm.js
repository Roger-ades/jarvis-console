// Window manager: drag, resize, focus, minimise, pin, maximise, arrange.
// Positions and sizes persist on the server (ui-state) so they survive restarts.
// Two engines: windows drawn on the console's desktop (the page), or, in the desktop app with
// "Intégré au bureau" (shell/main.js, docs/electron.md), native windows of the OS. A native window is
// a child of this page (window.open) into which the window's element moves: one JavaScript context,
// one live stream; the app does the moving, sizing and stacking.
import { api } from "./api.js";
import { addLookupDocument, debounce, h, removeLookupDocument, setHostDocument, setRevealDocument, store } from "./util.js";

const wins = new Map(); // id -> { el, st, onFocus }
const listeners = new Set();
const focusListeners = new Set();
let ui = { windows: {}, prefs: {} };
let settings = { default_width: 640, default_height: 480 };
let topZ = 10;
let focusedId = null;

const desktop = () => document.getElementById("desktop");
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const notify = () => listeners.forEach((fn) => fn());

const bridge = window.jarvis || null;          // the desktop app's preload (shell/preload.js)
const NATIVE = bridge?.mode === "integre";
const docInits = new Set();
if (NATIVE) document.documentElement.classList.add("bureau-integre");
/** True when windows are native windows of the OS (desktop app, "Intégré au bureau"). */
export function isNative() { return NATIVE; }
/** fn(doc) for this page's document and for each native window's: listeners that a module puts on
 * `document` (click delegation, shortcuts, selection) must be on every document. */
export function onDocument(fn) {
  docInits.add(fn);
  fn(document);
  for (const w of wins.values()) if (w.doc) fn(w.doc);
  for (const f of frames.values()) fn(f.doc);
  if (barDoc) fn(barDoc);
}
/** The document a window lives in (a native window's, or this page's). */
export function docOf(id) { return wins.get(id)?.doc || document; }
/** Where dialogs and toasts go (kind: toast | dialog | config | palette | setup). In the browser, this
 * page. In the desktop app ("Intégré au bureau"), where the user's last click or key was: a dialog asked
 * in a discussion's window (or in a dialog's) shows in it; the configuration has a window of its own; a
 * dialog asked from the bar, a panel or nowhere gets a window of its own. A toast stays in the window of
 * the last action, else goes to the bar. */
let lastDoc = document;
let barDoc = null;
const frames = new Map();   // framed windows: id -> {id, win, doc, dialog, remember, onClose, onGone}
export function activeDocument(kind = "dialog") {
  if (!NATIVE) return document;
  const w = [...wins.values()].find((x) => x.doc === lastDoc && !x.win.closed && !x.st.min);
  const f = [...frames.values()].find((x) => x.doc === lastDoc);
  if (kind === "toast") return w || f || lastDoc === barDoc ? lastDoc : (barDoc || document);
  if (kind === "config") return dialogFrame("config");
  if (w || f?.dialog) return lastDoc;
  return dialogFrame(kind);
}
setHostDocument(activeDocument);
setRevealDocument((doc) => {
  if (!NATIVE) return;
  const w = [...wins.entries()].find(([, x]) => x.doc === doc);
  const f = [...frames.values()].find((x) => x.doc === doc);
  if (w) bridge.win("focus", w[0]);
  else if (f) bridge.win("focus", FRAME + f.id);
});
if (NATIVE) {
  onDocument((doc) => {
    const used = () => { lastDoc = doc; };
    doc.addEventListener("pointerdown", used, true);
    doc.addEventListener("keydown", used, true);
  });
}

const persist = debounce(() => {
  store.set("jarvis.ui", ui);
  api("/api/ui-state", { method: "PUT", body: ui }).catch(() => {});
}, 700);

export function configure(s) { settings = { ...settings, ...s }; }
export function onChange(fn) { listeners.add(fn); }
/** fn(id) each time a window is brought forward or clicked. */
export function onFocus(fn) { focusListeners.add(fn); }
export function prefs() { return ui.prefs || {}; }
export function savePrefs(p) { ui.prefs = { ...(ui.prefs || {}), ...p }; persist(); }
export function has(id) { return wins.has(id); }
/** What a window shows when minimized, for windows that are not tasks (previews): {title, color, icon, onClose};
    regard: what the window shows, for "Ce que je regarde" (regard.js). */
export function meta(id) { return wins.get(id)?.meta || null; }
/** Height the floating command bar keeps at the bottom of the desktop. */
export function reservedHeight() { return reservedH; }
/** Per-window persisted option (e.g. inspector panel open). */
export function flag(id, key, value) {
  const st = wins.get(id)?.st || ui.windows[id];
  if (!st) return undefined;
  if (value === undefined) return st[key];
  st[key] = value;
  persist();
  return value;
}
export function width(id) { const st = wins.get(id)?.st; return (NATIVE ? st?.native?.w || settings.default_width : st?.w) || 0; }
export function isMinimized(id) { return !!wins.get(id)?.st.min; }
export function isPinned(id) { return !!wins.get(id)?.st.pinned; }
export function focused() { return focusedId; }
export function ids() { return [...wins.keys()]; }
export function minimizedIds() { return [...wins.entries()].filter(([, w]) => w.st.min).map(([id]) => id); }
export function visibleCount() { return [...wins.values()].filter((w) => !w.st.min).length; }

export async function loadState() {
  try { ui = (await api("/api/ui-state")) || {}; } catch { ui = store.get("jarvis.ui", {}) || {}; }
  ui.windows = ui.windows || {};
  ui.prefs = ui.prefs || {};
  topZ = Math.max(10, ...Object.values(ui.windows).map((w) => w.z || 0));
}

function bounds() {
  const r = desktop().getBoundingClientRect();
  return { w: Math.max(320, r.width), h: Math.max(200, r.height) };
}

/** Height the floating command bar takes at the bottom, measured with its text field at rest so a
    long prompt or a list of attachments overlays the windows instead of moving them. */
let reservedH = 0;
function measureDock() {
  const dock = document.getElementById("dock");
  if (!dock) return 0;
  const bar = dock.querySelector(".composer-bar");
  const tb = dock.querySelector(".taskbar");
  const pad = parseFloat(getComputedStyle(dock).paddingBottom) || 0;
  return Math.round(pad + 50 + (bar?.offsetHeight || 0) + (tb && !tb.hidden ? tb.offsetHeight : 0) + 10);
}
/** Where windows open, maximize and tile: above the command bar (they can still be dragged under it). */
function usable() {
  const b = bounds();
  return { w: b.w, h: Math.max(200, b.h - reservedH) };
}
function watchDock() {
  const dock = document.getElementById("dock");
  if (!dock || typeof ResizeObserver === "undefined") return;
  new ResizeObserver(() => {
    const hh = measureDock();
    if (hh === reservedH) return;
    reservedH = hh;
    document.documentElement.style.setProperty("--dock-h", `${hh}px`);
    for (const w of wins.values()) if (!w.win) { fit(w.st); apply(w); }
  }).observe(dock);
}
watchDock();

function placeNew(size = null) {
  const b = usable();
  const w = Math.min(size?.w || settings.default_width, b.w - 24), hh = Math.min(size?.h || settings.default_height, b.h - 24);
  const n = visibleCount();
  const spanX = Math.max(1, b.w - w - 40), spanY = Math.max(1, b.h - hh - 30);
  return { x: 20 + ((n * 36) % spanX), y: 14 + ((n * 30) % spanY), w, h: hh, z: ++topZ, min: false, pinned: false };
}

function apply(w) {
  const { el, st } = w;
  if (st.max) {
    const b = usable();
    Object.assign(el.style, { left: "0px", top: "0px", width: `${b.w}px`, height: `${b.h}px` });
  } else {
    Object.assign(el.style, { left: `${st.x}px`, top: `${st.y}px`, width: `${st.w}px`, height: `${st.h}px` });
  }
  el.style.zIndex = String(st.pinned ? 5000 + st.z : st.z);
  el.classList.toggle("pinned", !!st.pinned);
  el.hidden = !!st.min;
}

function fit(st) {
  const b = bounds(), u = usable();
  st.w = clamp(st.w || settings.default_width, 340, b.w);
  st.h = clamp(st.h || settings.default_height, 180, b.h);
  st.x = clamp(st.x ?? 20, -st.w + 120, b.w - 120);
  st.y = clamp(st.y ?? 14, 0, u.h - 36);
}

/** ephemeral: a preview window, not remembered across reloads nor minimized with the others.
    size: {w, h} for a new window. onClose: what closing it by its own button does (native windows;
    else meta.onClose, else it just goes). title: the native window's title. */
export function register(id, el, { handle, onFocus, onClose = null, fresh = false, ephemeral = false, size = null, meta = null, title = "" } = {}) {
  if (NATIVE && registerNative(id, el, { onFocus, onClose, fresh, ephemeral, size, meta, title })) return;
  let st = ephemeral ? null : ui.windows[id];
  const isNew = !st;
  if (!st) { st = placeNew(size); if (!ephemeral) ui.windows[id] = st; }
  if (fresh) st.min = false;
  fit(st);
  const w = { el, st, onFocus, ephemeral, meta };
  wins.set(id, w);
  el.dataset.wid = id;
  for (const dir of ["n", "s", "e", "w", "ne", "nw", "se", "sw"]) {
    const g = h("div", { class: `rz ${dir}` });
    g.addEventListener("pointerdown", (e) => startResize(e, id, dir));
    el.append(g);
  }
  el.addEventListener("pointerdown", () => focus(id), true);
  if (handle) makeDraggable(id, handle);
  desktop().append(el);
  apply(w);
  if (isNew || fresh) focus(id);
  persist();
  notify();
}

export function unregister(id) {
  const w = wins.get(id);
  if (!w) return;
  wins.delete(id);
  if (w.win) bridge.win("close", id);
  w.el.remove();
  delete ui.windows[id];
  if (focusedId === id) focusedId = null;
  persist();
  notify();
}

export function focus(id) {
  const w = wins.get(id);
  if (!w) return;
  if (w.win) { bridge.win("focus", id); markFocus(id); return; }
  if (focusedId !== id || w.st.z < topZ) { w.st.z = ++topZ; apply(w); persist(); }
  markFocus(id);
}

function markFocus(id) {
  const w = wins.get(id);
  if (!w) return;
  focusedId = id;
  for (const [oid, o] of wins) o.el.classList.toggle("focused", oid === id);
  w.onFocus?.();
  focusListeners.forEach((fn) => fn(id));
}

export function minimize(id) {
  const w = wins.get(id);
  if (!w) return;
  w.st.min = true;
  if (w.win) bridge.win("minimize", id);
  else apply(w);
  if (focusedId === id) focusedId = null;
  persist();
  notify();
}

export function restore(id) {
  const w = wins.get(id);
  if (!w) return;
  w.st.min = false;
  if (w.win) { bridge.win("restore", id); markFocus(id); persist(); notify(); return; }
  fit(w.st);
  apply(w);
  focus(id);
  persist();
  notify();
}

export function togglePin(id) {
  const w = wins.get(id);
  if (!w) return false;
  w.st.pinned = !w.st.pinned;
  if (w.win) { bridge.win("pin", id, w.st.pinned); w.el.classList.toggle("pinned", w.st.pinned); } else apply(w);
  persist();
  return w.st.pinned;
}

/** New size for a window (e.g. a preview fitted to its image), kept inside the desktop. */
export function setSize(id, width, height) {
  const w = wins.get(id);
  if (!w) return;
  if (w.win) { bridge.win("size", id, { width, height }); return; }
  const b = usable();
  w.st.w = Math.min(width, b.w - 24);
  w.st.h = Math.min(height, b.h - 24);
  w.st.x = Math.min(w.st.x, Math.max(0, b.w - w.st.w - 12));
  w.st.y = Math.min(w.st.y, Math.max(0, b.h - w.st.h - 12));
  fit(w.st);
  apply(w);
  persist();
}

export function toggleMax(id) {
  const w = wins.get(id);
  if (!w) return;
  if (w.win) { bridge.win("max", id); return; }
  w.st.max = !w.st.max;
  apply(w);
  persist();
}

/** The JARVIS logo: minimize every window (previews and pinned ones too); once they all are, bring them back. */
export function toggleDesktop() {
  arrange(visibleCount() ? "minimize" : "restore");
}

function makeDraggable(id, handle) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button, input, select, textarea, a")) return;
    const w = wins.get(id);
    if (!w || w.st.max) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, ox = w.st.x, oy = w.st.y;
    const b = bounds(), u = usable();
    document.body.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    const move = (ev) => {
      w.st.x = clamp(ox + ev.clientX - sx, -w.st.w + 120, b.w - 120);
      w.st.y = clamp(oy + ev.clientY - sy, 0, u.h - 36);
      apply(w);
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.body.classList.remove("dragging");
      persist();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("dblclick", (e) => { if (!e.target.closest("button")) toggleMax(id); });
}

function startResize(e, id, dir) {
  const w = wins.get(id);
  if (!w || e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  if (w.st.max) { w.st.max = false; apply(w); }
  const sx = e.clientX, sy = e.clientY, o = { ...w.st };
  const b = bounds();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  document.body.classList.add("dragging");
  const move = (ev) => {
    const dx = ev.clientX - sx, dy = ev.clientY - sy;
    if (dir.includes("e")) w.st.w = clamp(o.w + dx, 340, b.w - o.x);
    if (dir.includes("s")) w.st.h = clamp(o.h + dy, 180, b.h - o.y);
    if (dir.includes("w")) { const nw = clamp(o.w - dx, 340, o.x + o.w); w.st.x = o.x + o.w - nw; w.st.w = nw; }
    if (dir.includes("n")) { const nh = clamp(o.h - dy, 180, o.y + o.h); w.st.y = o.y + o.h - nh; w.st.h = nh; }
    apply(w);
  };
  const up = () => {
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", up);
    grip.removeEventListener("pointercancel", up);
    document.body.classList.remove("dragging");
    persist();
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up);
  grip.addEventListener("pointercancel", up);
}

export function arrange(mode) {
  const b = usable();
  if (mode === "minimize") { for (const [id, w] of wins) if (!w.st.min) minimize(id); return; }
  if (mode === "restore") { for (const id of minimizedIds()) restore(id); return; }
  if (NATIVE) {
    bridge.win("arrange", null, { mode, ids: [...wins.entries()].filter(([, w]) => w.win && !w.st.min && !w.st.pinned).map(([id]) => id) });
    return;
  }
  const list = [...wins.values()].filter((w) => !w.st.min && !w.st.pinned).sort((a, c) => a.st.z - c.st.z);
  if (!list.length) return;
  if (mode === "mosaique") {
    const n = list.length, cols = Math.ceil(Math.sqrt(n * (b.w / b.h) / 1.4)) || 1;
    const c = Math.min(cols, n), rows = Math.ceil(n / c), gap = 8;
    const cw = (b.w - gap * (c + 1)) / c, ch = (b.h - gap * (rows + 1)) / rows;
    list.forEach((w, i) => {
      Object.assign(w.st, { max: false, x: gap + (i % c) * (cw + gap), y: gap + Math.floor(i / c) * (ch + gap),
        w: Math.max(340, cw), h: Math.max(180, ch) });
      apply(w);
    });
  } else {
    const ww = Math.min(settings.default_width, b.w - 60), hh = Math.min(settings.default_height, b.h - 60);
    list.forEach((w, i) => {
      Object.assign(w.st, { max: false, x: 20 + ((i * 34) % Math.max(1, b.w - ww - 40)),
        y: 14 + ((i * 30) % Math.max(1, b.h - hh - 30)), w: ww, h: hh, z: ++topZ });
      apply(w);
    });
  }
  persist();
}

window.addEventListener("resize", debounce(() => { for (const w of wins.values()) if (!w.win) { fit(w.st); apply(w); } }, 150));

// ------------------------------------------------------------ native windows (desktop app)
/** The window's element in a native window of its own; false when the app refused to open it. */
function registerNative(id, el, { onFocus, onClose, fresh, ephemeral, size, meta, title }) {
  let st = ephemeral ? null : ui.windows[id];
  if (!st) { st = { min: false, pinned: false }; if (!ephemeral) ui.windows[id] = st; }
  const nb = st.native || {};
  const width = Math.round(nb.w || size?.w || settings.default_width), height = Math.round(nb.h || size?.h || settings.default_height);
  const features = [`width=${width}`, `height=${height}`, ...(Number.isFinite(nb.x) ? [`left=${nb.x}`, `top=${nb.y}`] : [])].join(",");
  const win = window.open("about:blank", `jarvis-win:${id}`, features);
  if (!win) return false;
  const doc = nativeDocument(win, [], () => { const x = wins.get(id); if (x) { x.cssReady = true; colorize(id); } });
  el.classList.add("native");
  el.classList.toggle("pinned", !!st.pinned);
  doc.body.append(el, h("div", { id: "modal-root" }), h("div", { id: "toasts", class: "toasts", "aria-live": "polite" }));
  const w = { el, st, onFocus, onClose, ephemeral, meta, win, doc };
  wins.set(id, w);
  el.dataset.wid = id;
  win.addEventListener("focus", () => markFocus(id));
  el.addEventListener("pointerdown", () => markFocus(id), true);  // (each click: what the user looks at, regard.js)
  docInits.forEach((fn) => fn(doc));
  setTitle(id, title || meta?.title || el.getAttribute("aria-label") || "JARVIS");
  if (st.pinned) bridge.win("pin", id, true);
  if (st.min && !fresh) bridge.win("minimize", id);
  else { st.min = false; markFocus(id); }
  persist();
  notify();
  return true;
}

/** A native window's document: the console's stylesheet and theme. onCss: once the stylesheet is in. */
function nativeDocument(win, classes, onCss = null) {
  const doc = win.document;
  doc.open();
  doc.write('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>JARVIS</title></head><body></body></html>');
  doc.close();
  doc.documentElement.setAttribute("data-theme", document.documentElement.getAttribute("data-theme") || "sombre");
  doc.documentElement.classList.add("native-doc", `os-${bridge.platform}`, ...classes);
  const css = doc.createElement("link");
  css.rel = "stylesheet";
  css.href = new URL("/static/css/app.css", location.href).href;
  if (onCss) css.addEventListener("load", onCss);
  doc.head.append(css);
  return doc;
}

/** Desktop app, "Intégré au bureau": an element (the command bar) in the floating bar window, a window
 * of its own that is not one of the windows (no taskbar pill, not arranged). Returns its document. */
export function detachBar(el) {
  if (!NATIVE) return null;
  const win = window.open("about:blank", "jarvis-win:barre", "width=820,height=190");
  if (!win) return null;
  const doc = nativeDocument(win, ["bar-doc"]);
  doc.title = "JARVIS — nouvelle demande";
  doc.body.append(h("div", { id: "toasts", class: "toasts", "aria-live": "polite" }), el);
  barDoc = doc;
  addLookupDocument(doc);
  docInits.forEach((fn) => fn(doc));
  return doc;
}

// ------------------------------------------------------------ framed windows ("Intégré au bureau")
// Without the JARVIS window, what it held opens in windows of the OS of their own, with a title strip and
// the native buttons: its panels (Historique, Sessions, Routines, Projet, Notes: the drawer's element
// moves into the window) and its modals (configuration, Ctrl+K, the assistant, the questions asked from
// the bar or a panel: built straight into the window's #modal-root, see util.modalHost).
const FRAME = "cadre-";     // window.open name: jarvis-win:cadre-<id>
const FRAME_HEAD = 38;      // the title strip of a dialog's window (.frame-head)
const PANEL_HEAD = 48;      // a panel's own head is its title strip
let frameSeq = 0;

function openFrame(id, { width, height, place = null, hidden = false, classes = [], title = "JARVIS", head = FRAME_HEAD }) {
  const at = place && Number.isFinite(place.x) ? [`left=${place.x}`, `top=${place.y}`] : [];
  const features = [`width=${Math.round(width)}`, `height=${Math.round(height)}`, ...at, ...(hidden ? ["hidden=1"] : [])].join(",");
  const win = window.open("about:blank", `jarvis-win:${FRAME}${id}`, features);
  if (!win) return null;
  let ready;
  const f = { id, win, doc: null, head, ready: new Promise((r) => { ready = r; }), dialog: false, remember: "", onClose: null, onGone: null };
  f.doc = nativeDocument(win, ["frame-doc", ...classes], () => { frameColors(f); ready(); });
  f.doc.title = title;
  frames.set(id, f);
  addLookupDocument(f.doc);
  docInits.forEach((fn) => fn(f.doc));
  return f;
}

/** The native buttons take the colors of the window's title strip. */
function frameColors(f) {
  const cs = f.win.getComputedStyle(f.doc.body);
  bridge.win("overlay", FRAME + f.id, { color: hexColor(cs.backgroundColor), symbolColor: hexColor(cs.color), height: f.head });
}

function forgetFrame(f) {
  frames.delete(f.id);
  removeLookupDocument(f.doc);
  if (lastDoc === f.doc) lastDoc = barDoc || document;
}

function closeFrame(id) {
  const f = frames.get(id);
  if (!f) return;
  forgetFrame(f);
  bridge.win("close", FRAME + id);
}

function frameEvent(id, type, b) {
  const f = frames.get(id);
  if (!f) return;
  if (type === "focus") lastDoc = f.doc;
  else if (type === "bounds" && b && f.remember) savePrefs({ frames: { ...(prefs().frames || {}), [f.remember]: { x: b.x, y: b.y, w: b.width, h: b.height } } });
  else if (type === "close-request") { lastDoc = f.doc; (f.onClose || (() => closeFrame(id)))(); }
  else if (type === "closed") { forgetFrame(f); f.onGone?.(); }   // (gone without the page asking)
}

/** A window for a modal: the configuration (its own place, kept), or a dialog sized to what it shows. It
 * closes once its last modal is gone; its close button is Échap. Returns its document. */
function dialogFrame(kind) {
  const id = `dialogue-${++frameSeq}`;
  const big = kind === "config";
  const place = big ? prefs().frames?.config : null;
  const sw = window.screen.availWidth || 1280, sh = window.screen.availHeight || 800;
  const f = openFrame(id, big
    ? { width: place?.w || Math.min(1180, sw - 80), height: place?.h || Math.min(880, sh - 60), place, classes: ["dialog-doc", "fitted"], title: "Configuration — JARVIS" }
    : { width: Math.min(1200, sw - 60), height: Math.min(940, sh - 40), hidden: true, classes: ["dialog-doc"] });
  if (!f) return document;
  f.dialog = true;
  if (big) f.remember = "config";
  const title = h("span", { class: "frame-title" }, big ? "Configuration" : "JARVIS");
  f.doc.body.append(h("div", { class: "frame-head" }, h("span", { class: "frame-mark", "aria-hidden": "true" }), title),
    h("div", { id: "modal-root" }), h("div", { id: "toasts", class: "toasts", "aria-live": "polite" }));
  const root = f.doc.getElementById("modal-root");
  let shown = big;
  new MutationObserver(() => {
    if (!root.childElementCount) { closeFrame(id); return; }
    const box = root.firstElementChild.firstElementChild;
    const text = (box?.getAttribute("aria-label") || box?.querySelector("h2, h3")?.textContent || "JARVIS").trim();
    if (title.textContent !== text) { title.textContent = text; f.doc.title = `${text} — JARVIS`; bridge.win("title", FRAME + id, f.doc.title); }
    if (!shown) { shown = true; f.ready.then(() => setTimeout(() => fitFrame(f), 30)); }
  }).observe(root, { childList: true });
  // its close button: Échap to the modal on top (a question may come first), else it goes
  f.onClose = () => {
    const top = root.lastElementChild;
    f.doc.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    setTimeout(() => { if (top?.isConnected && root.lastElementChild === top) top.remove(); }, 80);
  };
  return f.doc;
}

/** The dialog's natural size (its scrolling parts unrolled), measured in the large hidden window; then the
 * window takes that size and shows, and the dialog fills it. */
function fitFrame(f) {
  if (!frames.has(f.id)) return;
  const box = f.doc.querySelector("#modal-root > .overlay > *");
  if (!box) return;
  let extra = 0;
  const counted = [];
  for (const x of box.querySelectorAll("*")) {
    if (counted.some((c) => c.contains(x))) continue;
    if (x.scrollHeight > x.clientHeight + 1 && /auto|scroll/.test(f.win.getComputedStyle(x).overflowY)) {
      counted.push(x);
      extra += x.scrollHeight - x.clientHeight;
    }
  }
  const r = box.getBoundingClientRect();
  f.doc.documentElement.classList.add("fitted");
  bridge.win("frame-show", FRAME + f.id, { width: Math.ceil(r.width), height: Math.ceil(r.height + extra) + FRAME_HEAD });
}

/** The panels (drawers) of the JARVIS window: each one in a window of its own while it is shown. */
export function nativePanels(els) {
  if (!NATIVE) return;
  for (const el of els) {
    const id = `panneau-${el.id}`;
    new MutationObserver(() => {
      const f = frames.get(id);
      if (!el.hidden && !f) openPanel(el, id);
      else if (el.hidden && f) { document.body.append(el); closeFrame(id); }
    }).observe(el, { attributes: true, attributeFilter: ["hidden"] });
  }
}

function openPanel(el, id) {
  const place = prefs().frames?.[id];
  const f = openFrame(id, { width: place?.w || (el.classList.contains("wide") ? 640 : 500), height: place?.h || 800, place,
    classes: ["panel-doc"], title: `${el.getAttribute("aria-label") || "JARVIS"} — JARVIS`, head: PANEL_HEAD });
  if (!f) return;
  f.remember = id;
  f.doc.body.append(el, h("div", { id: "modal-root" }), h("div", { id: "toasts", class: "toasts", "aria-live": "polite" }));
  f.onClose = () => { el.hidden = true; };
  f.onGone = () => { document.body.append(el); el.hidden = true; };
}

/** A panel already in its window comes forward (rather than closing, as its button does in the page). */
export function focusPanel(el) {
  if (!NATIVE || !frames.has(`panneau-${el.id}`)) return false;
  bridge.win("focus", `${FRAME}panneau-${el.id}`);
  return true;
}

/** Tout fermer: the panels and the dialogs' windows too. */
export function closeFrames() {
  for (const f of [...frames.values()]) (f.onClose || (() => closeFrame(f.id)))();
}

/** A modal is open (in this page, or in a window of its own). */
export function modalOpen() {
  return !!document.querySelector("#modal-root .overlay") || [...frames.values()].some((f) => f.dialog);
}

/** The native window's title (taskbar, Alt+Tab). */
export function setTitle(id, text) {
  const w = wins.get(id);
  if (!w?.win || w.doc.title === (text || "JARVIS")) return;
  w.doc.title = text || "JARVIS";
  bridge.win("title", id, w.doc.title);
}

const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
function hexColor(css) {
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = "#000";
  probe.fillStyle = css;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

/** The native buttons (minimize, maximize, close) take the colors of the window's header. */
export function colorize(id) {
  const w = wins.get(id);
  const head = w?.win && w.cssReady && w.el.querySelector(".win-head");
  if (!head) return;
  const cs = w.win.getComputedStyle(head);
  const colors = { color: hexColor(cs.backgroundColor), symbolColor: hexColor(cs.color) };
  const key = `${colors.color}${colors.symbolColor}`;
  if (w.colors === key) return;   // (called on each update of a task)
  w.colors = key;
  bridge.win("overlay", id, colors);
  if (!colorize.done) { colorize.done = true; bridge.win("overlay", null, colors); } // (the next windows open with them)
}

/** The theme changed: native windows follow. */
export function retheme() {
  colorize.done = false;
  const theme = document.documentElement.getAttribute("data-theme") || "sombre";
  barDoc?.documentElement.setAttribute("data-theme", theme);
  for (const f of frames.values()) { f.doc.documentElement.setAttribute("data-theme", theme); setTimeout(() => frameColors(f), 50); }
  if (NATIVE) bridge.win("theme", null, theme);
  for (const [id, w] of wins) {
    if (!w.win) continue;
    w.colors = "";
    w.doc.documentElement.setAttribute("data-theme", document.documentElement.getAttribute("data-theme") || "sombre");
    colorize(id);
  }
}

if (NATIVE) {
  bridge.onWin(({ id, type, bounds: b }) => {
    if (String(id).startsWith(FRAME)) { frameEvent(String(id).slice(FRAME.length), type, b); return; }
    const w = wins.get(id);
    if (!w) return;
    if (type === "focus") markFocus(id);
    else if (type === "minimize") { w.st.min = true; if (focusedId === id) focusedId = null; persist(); notify(); }
    else if (type === "restore") { w.st.min = false; persist(); notify(); }
    else if (type === "bounds" && b) { w.st.native = { x: b.x, y: b.y, w: b.width, h: b.height }; if (!w.ephemeral) persist(); }
    else if (type === "close-request") {
      lastDoc = w.doc;   // (a question before closing shows in this window)
      (w.onClose || w.meta?.onClose || (() => unregister(id)))();
    }
    else if (type === "closed") {
      // gone without the page asking (the app closed it): its owner forgets it
      wins.delete(id);
      if (focusedId === id) focusedId = null;
      (w.onClose || w.meta?.onClose)?.();
      notify();
    }
  });
}
