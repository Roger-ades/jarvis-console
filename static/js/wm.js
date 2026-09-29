// Window manager: drag, resize, focus, minimise, pin, maximise, arrange.
// Positions and sizes persist on the server (ui-state) so they survive restarts.
import { api } from "./api.js";
import { debounce, h, store } from "./util.js";

const wins = new Map(); // id -> { el, st, onFocus }
const listeners = new Set();
let ui = { windows: {}, prefs: {} };
let settings = { default_width: 640, default_height: 480 };
let topZ = 10;
let focusedId = null;

const desktop = () => document.getElementById("desktop");
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const notify = () => listeners.forEach((fn) => fn());

const persist = debounce(() => {
  store.set("jarvis.ui", ui);
  api("/api/ui-state", { method: "PUT", body: ui }).catch(() => {});
}, 700);

export function configure(s) { settings = { ...settings, ...s }; }
export function onChange(fn) { listeners.add(fn); }
export function prefs() { return ui.prefs || {}; }
export function savePrefs(p) { ui.prefs = { ...(ui.prefs || {}), ...p }; persist(); }
export function has(id) { return wins.has(id); }
/** Per-window persisted option (e.g. inspector panel open). */
export function flag(id, key, value) {
  const st = wins.get(id)?.st || ui.windows[id];
  if (!st) return undefined;
  if (value === undefined) return st[key];
  st[key] = value;
  persist();
  return value;
}
export function width(id) { return wins.get(id)?.st.w || 0; }
export function isMinimized(id) { return !!wins.get(id)?.st.min; }
export function isPinned(id) { return !!wins.get(id)?.st.pinned; }
export function focused() { return focusedId; }
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

function placeNew() {
  const b = bounds();
  const w = Math.min(settings.default_width, b.w - 24), hh = Math.min(settings.default_height, b.h - 24);
  const n = visibleCount();
  const spanX = Math.max(1, b.w - w - 40), spanY = Math.max(1, b.h - hh - 30);
  return { x: 20 + ((n * 36) % spanX), y: 14 + ((n * 30) % spanY), w, h: hh, z: ++topZ, min: false, pinned: false };
}

function apply(w) {
  const { el, st } = w;
  if (st.max) {
    const b = bounds();
    Object.assign(el.style, { left: "0px", top: "0px", width: `${b.w}px`, height: `${b.h}px` });
  } else {
    Object.assign(el.style, { left: `${st.x}px`, top: `${st.y}px`, width: `${st.w}px`, height: `${st.h}px` });
  }
  el.style.zIndex = String(st.pinned ? 5000 + st.z : st.z);
  el.classList.toggle("pinned", !!st.pinned);
  el.hidden = !!st.min;
}

function fit(st) {
  const b = bounds();
  st.w = clamp(st.w || settings.default_width, 340, b.w);
  st.h = clamp(st.h || settings.default_height, 180, b.h);
  st.x = clamp(st.x ?? 20, -st.w + 120, b.w - 120);
  st.y = clamp(st.y ?? 14, 0, b.h - 36);
}

export function register(id, el, { handle, onFocus, fresh = false } = {}) {
  let st = ui.windows[id];
  const isNew = !st;
  if (!st) st = ui.windows[id] = placeNew();
  if (fresh) st.min = false;
  fit(st);
  const w = { el, st, onFocus };
  wins.set(id, w);
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
  w.el.remove();
  wins.delete(id);
  delete ui.windows[id];
  if (focusedId === id) focusedId = null;
  persist();
  notify();
}

export function focus(id) {
  const w = wins.get(id);
  if (!w) return;
  if (focusedId !== id || w.st.z < topZ) { w.st.z = ++topZ; apply(w); persist(); }
  focusedId = id;
  for (const [oid, o] of wins) o.el.classList.toggle("focused", oid === id);
  w.onFocus?.();
}

export function minimize(id) {
  const w = wins.get(id);
  if (!w) return;
  w.st.min = true;
  apply(w);
  if (focusedId === id) focusedId = null;
  persist();
  notify();
}

export function restore(id) {
  const w = wins.get(id);
  if (!w) return;
  w.st.min = false;
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
  apply(w);
  persist();
  return w.st.pinned;
}

export function toggleMax(id) {
  const w = wins.get(id);
  if (!w) return;
  w.st.max = !w.st.max;
  apply(w);
  persist();
}

function makeDraggable(id, handle) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button, input, select, textarea, a")) return;
    const w = wins.get(id);
    if (!w || w.st.max) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, ox = w.st.x, oy = w.st.y;
    const b = bounds();
    document.body.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    const move = (ev) => {
      w.st.x = clamp(ox + ev.clientX - sx, -w.st.w + 120, b.w - 120);
      w.st.y = clamp(oy + ev.clientY - sy, 0, b.h - 36);
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
  const b = bounds();
  if (mode === "minimize") { for (const id of wins.keys()) if (!isPinned(id)) minimize(id); return; }
  if (mode === "restore") { for (const id of minimizedIds()) restore(id); return; }
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

window.addEventListener("resize", debounce(() => { for (const w of wins.values()) { fit(w.st); apply(w); } }, 150));
