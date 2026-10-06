// Preview of files and links: images, PDF, local HTML (no scripts), text/CSV/JSON,
// and web pages in a sandboxed frame. Text files (Markdown, CSV, JSON, …) can be
// edited in the same window and saved back. Local files come from the task's
// folders through the console (token header), as blob URLs.
import { api } from "./api.js";
import { highlight } from "./highlight.js";
import { mdElement } from "./md.js";
import { confirmDialog, copyText, dialog, downloadBlob, h, ICONS, toast } from "./util.js";
import { projectFor, projects as allProjects } from "./projects.js";
import { colorOf, paint } from "./tint.js";
import * as wm from "./wm.js";

const IMG = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
// Plain text the preview can edit. Keep in step with Engine.EDITABLE_EXT.
const TEXT = /\.(txt|text|md|markdown|csv|tsv|json|log|xml|yaml|yml|ini|toml|cfg|conf|rst)$/i;
const EDIT_MAX = 2_000_000;
const OFFICE = /\.(docx|docm|odt|xlsx|xlsm|ods|pptx|odp|eml)$/i;
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

export function kindOf(name) {
  if (IMG.test(name)) return "image";
  if (/\.pdf$/i.test(name)) return "pdf";
  if (/\.html?$/i.test(name)) return "html";
  if (TEXT.test(name)) return "text";
  if (OFFICE.test(name)) return "office";
  return "other";
}

/** Where a file comes from: a task (its folders, what it cited), a project folder, or the documents'
 * index (doc). frame: the address of an HTML file on the preview origin (another origin than the
 * console's, see content.py); text: the text of an Office document, read by the console. */
function source(opts) {
  const p = (path) => `path=${encodeURIComponent(path)}`;
  if (opts.doc) {
    return { get: (path) => `/api/documents/file?${p(path)}`, open: "/api/documents/file/open", save: "/api/documents/file",
      frame: (path, remote) => `/api/documents/frame?${p(path)}&remote=${remote}`, text: (path) => `/api/documents/text?${p(path)}`, extra: {} };
  }
  if (opts.folder) {
    const q = `profile=${encodeURIComponent(opts.profile || "")}&folder=${encodeURIComponent(opts.folder)}`;
    return { get: (path) => `/api/workspace/file?${q}&${p(path)}`, open: "/api/workspace/file/open", save: "/api/workspace/file",
      frame: (path, remote) => `/api/workspace/frame?${q}&${p(path)}&remote=${remote}`, text: (path) => `/api/workspace/text?${q}&${p(path)}`,
      extra: { profile: opts.profile, folder: opts.folder } };
  }
  return { get: (path) => `/api/tasks/${opts.taskId}/file?${p(path)}`, open: `/api/tasks/${opts.taskId}/file/open`,
    save: `/api/tasks/${opts.taskId}/file`,
    frame: (path, remote) => `/api/tasks/${opts.taskId}/frame?${p(path)}&remote=${remote}`, text: (path) => `/api/tasks/${opts.taskId}/text?${p(path)}`, extra: {} };
}

/** An Office document or a saved mail: its text, read by the console (the layout is not kept). */
async function officeText(src, path, openWith) {
  const r = await api(src.text(path));
  const note = h("div", { class: "pv-note" }, h("span", {}, r.note ? `Texte illisible : ${r.note}.` : "Aperçu du texte seul : la mise en forme n'est pas reproduite."),
    h("button", { type: "button", class: "btn small", on: { click: () => openWith(false) } }, "Ouvrir avec l'application"));
  if (!r.text) return h("div", {}, note);
  const tabular = r.kind === "excel" && /\t/.test(r.text);
  const body = tabular
    ? h("div", { class: "pv-text" }, ...r.text.split(/\n\n(?=Feuille « )/).map((sheet) => {
      const [head, ...rows] = sheet.split("\n");
      return h("div", { class: "pv-sheet" }, h("h4", {}, head), csvTable(rows.join("\n"), "\t"));
    }))
    : h("pre", { class: "pv-pre" }, r.text);
  return h("div", {}, note, body);
}

/** An HTML page (file, mail) from the preview origin: its styles kept, no script, nothing from the web
 * until the user asks (a mail's tracking pixel would tell the sender it was read). load(remote) → {url, remote}. */
function htmlFrame(load, title, remote0 = false) {
  const wrap = h("div", { class: "pv-html" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const show = async (remote) => {
    let f;
    try { f = await load(remote); } catch (e) { wrap.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
    const frame = h("iframe", { class: "pv-frame white", src: f.url, title: title || "Aperçu", referrerpolicy: "no-referrer",
      sandbox: "allow-popups allow-popups-to-escape-sandbox" });
    const bar = f.remote && !remote ? h("div", { class: "pv-note pv-remote" },
      h("span", { svg: "shield" }),
      h("span", {}, `${f.remote} élément${f.remote > 1 ? "s" : ""} du web bloqué${f.remote > 1 ? "s" : ""} (images, polices…) : le site qui les héberge saurait que tu as ouvert ce document.`),
      h("button", { type: "button", class: "btn small", on: { click: () => show(true) } }, "Afficher les images")) : null;
    wrap.replaceChildren(...[bar, frame].filter(Boolean));
  };
  show(remote0);
  return wrap;
}

const RESULT_ICON = { mail: "mail", html: "globe", json: "list", text: "file", image: "image" };
const shortTool = (name) => String(name || "").replace(/^mcp__/, "").replace(/__/g, " · ");
const when = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("fr-FR", { dateStyle: "full", timeStyle: "short" });
};
const kb = (n) => (n ? ` (${Math.max(1, Math.round(n / 1024)).toLocaleString("fr-FR")} Ko)` : "");

export async function fileBlob(taskId, path, opts = null) {
  const r = await api(source(opts || { taskId }).get(path), { raw: true });
  return r.blob();
}

/** Inline thumbnail for a local image reference (safe: served by the console itself). */
export async function thumbnail(el, taskId, color = "") {
  if (!taskId || el.dataset.thumb) return;
  el.dataset.thumb = "1";
  try {
    const url = URL.createObjectURL(await fileBlob(taskId, el.dataset.path));
    const img = h("img", { class: "thumb", src: url, alt: baseName(el.dataset.path), title: "Cliquer pour agrandir" });
    img.addEventListener("click", () => openPreview({ taskId, path: el.dataset.path, color }));
    el.after(img);
  } catch { /* outside the task folders or missing: the link still explains on click */ }
}

/** Web image placeholder → the image itself (loaded only now: the site sees the request). */
export function revealImage(btn) {
  const src = btn.dataset.src || "";
  if (!/^https:\/\//i.test(src)) { window.open(src, "_blank", "noopener,noreferrer"); return; }
  const img = h("img", { class: "thumb web", src, alt: btn.title || "", referrerpolicy: "no-referrer", title: "Cliquer pour agrandir" });
  img.addEventListener("click", () => openPreview({ url: src, kind: "image" }));
  img.addEventListener("error", () => img.replaceWith(h("span", { class: "muted" }, `Image introuvable : ${src}`)), { once: true });
  btn.replaceWith(img);
}

function csvTable(text, sep = "") {
  if (!sep) {
    const head = text.split("\n")[0] || "";
    const semis = (head.match(/;/g) || []).length;
    sep = semis > (head.match(/,/g) || []).length ? ";" : text.includes("\t") ? "\t" : ",";
  }
  const rows = text.split(/\r?\n/).filter(Boolean).slice(0, 501).map((l) => l.split(sep));
  const n = rows.reduce((m, r) => Math.max(m, r.length), 0);
  const cols = Array.from({ length: n }, () => h("col"));
  const cell = (tag, value) => h(tag, value ? { title: value } : {}, h("span", { class: "col-label" }, value));
  const ths = Array.from({ length: n }, (_, i) => {
    const th = cell("th", (rows[0] || [])[i] || "");
    th.append(h("button", { type: "button", class: "col-grip", "aria-label": "Redimensionner la colonne",
      title: "Redimensionner la colonne. Double-clic : ajuster au contenu." }));
    return th;
  });
  const table = h("table", { class: "csv" },
    h("colgroup", {}, ...cols),
    h("thead", {}, h("tr", {}, ...ths)),
    h("tbody", {}, ...rows.slice(1).map((r) => h("tr", {}, ...Array.from({ length: n }, (_, i) => cell("td", r[i] || ""))))));
  ths.forEach((th, i) => bindColResize(table, th, i));
  return h("div", { class: "md" }, table, rows.length > 500 ? h("p", { class: "muted" }, "500 premières lignes.") : null);
}

/** Drag the header edge to set a column width. Widths stay as they are until the first drag. */
function bindColResize(table, th, index) {
  const grip = th.querySelector(".col-grip");
  const freeze = () => {
    const cols = [...table.querySelectorAll("col")];
    [...table.tHead.rows[0].cells].forEach((cell, k) => {
      cols[k].style.width = `${Math.max(36, Math.round(cell.getBoundingClientRect().width))}px`;
    });
    table.style.tableLayout = "fixed";
    table.style.width = `${cols.reduce((s, c) => s + parseFloat(c.style.width), 0)}px`;
    return cols;
  };
  const fit = () => {
    const doc = table.ownerDocument;
    const sample = table.rows[0]?.cells[index];
    if (!sample) return 72;
    const probe = doc.createElement("span");
    const font = doc.defaultView.getComputedStyle(sample).font;
    probe.style.cssText = `position:absolute;visibility:hidden;white-space:nowrap;font:${font};padding:0 10px`;
    doc.body.append(probe);
    let max = 72;
    for (const row of table.rows) {
      probe.textContent = row.cells[index]?.textContent || "";
      max = Math.max(max, probe.offsetWidth + 18);
    }
    probe.remove();
    return Math.min(max, 720);
  };
  grip.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const cols = freeze();
    const startX = e.clientX, startW = parseFloat(cols[index].style.width);
    const doc = table.ownerDocument;
    doc.documentElement.classList.add("col-resizing");
    const move = (ev) => {
      cols[index].style.width = `${Math.max(36, Math.round(startW + ev.clientX - startX))}px`;
      table.style.width = `${cols.reduce((s, c) => s + parseFloat(c.style.width), 0)}px`;
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up);
      doc.documentElement.classList.remove("col-resizing");
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    grip.addEventListener("pointercancel", up);
    try { grip.setPointerCapture(e.pointerId); } catch { /* the move listeners still follow the pointer */ }
  });
  grip.addEventListener("dblclick", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const cols = freeze();
    cols[index].style.width = `${fit()}px`;
    table.style.width = `${cols.reduce((s, c) => s + parseFloat(c.style.width), 0)}px`;
  });
}

/** UTF-8 text small enough to edit and save whole. Null when the file is binary, too big, or not UTF-8. */
async function editableText(b) {
  if (!b || b.size > EDIT_MAX) return null;
  const bytes = new Uint8Array(await b.arrayBuffer());
  if (bytes.includes(0)) return null;
  let i = 0;
  if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) i = 3;
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(i)); }
  catch { return null; }
}

// Previews open as windows of the desktop (move, resize, enlarge, several side by side).
const SIZES = { image: { w: 720, h: 560 }, pdf: { w: 780, h: 900 }, html: { w: 980, h: 760 }, web: { w: 1040, h: 780 },
  text: { w: 760, h: 640 }, office: { w: 820, h: 760 }, other: { w: 480, h: 300 } };
const open = new Map();   // what is shown -> window id
const closers = new Map(); // window id -> close()
const live = new Map();    // window id -> check(): reloads a file preview when the file changed
const aimers = new Map();  // window id -> aim(focus): shows another place of the file already open
let seq = 0;

/** Keeps a window above the others (preview, display); a second click frees it. The pinned look comes
 * from the window's class (.win.pinned), set by wm. */
export function pinButton(id) {
  const label = "Épingler au premier plan (cliquer à nouveau pour libérer)";
  return h("button", { type: "button", class: "icon-btn pin-btn", title: label, "aria-label": label, svg: "pin",
    on: { click: () => wm.togglePin(id) } });
}

/** Open file previews follow their file: what Claude writes shows up without reopening it. Called after
 * each tool result, and every few seconds while the console is visible (only a date is asked for). */
export function refreshPreviews() {
  for (const [id, check] of live) if (wm.has(id) && !wm.isMinimized(id)) check();
}
setInterval(() => { if (document.visibilityState === "visible") refreshPreviews(); }, 3000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshPreviews(); });

// Échap closes the preview window that has the focus (when no dialog is open), in its own window too
wm.onDocument((doc) => doc.addEventListener("keydown", (e) => {
  const id = wm.focused();
  if (e.key === "Escape" && id && closers.has(id) && !doc.querySelector(".overlay")) { e.preventDefault(); closers.get(id)(); }
}));

/** opts: {taskId, path} or {profile, folder, path} for a file, {url, kind:"web"|"image"} for the web,
 * {taskId, result: {id, contient, title, kind}} for a tool's result shown by Claude; color: accent;
 * focus: {text, words, page}, the place of the file to show (a passage, words to mark, a page of a PDF). */
export async function openPreview(opts) {
  if (opts.result) return openResult(opts);
  // desktop app: a web page opens in a window of its own, signed in with the site's own session
  if (opts.url && opts.kind !== "image" && window.jarvis?.openSite) { window.jarvis.openSite(opts.url); return; }
  const key = opts.url ? `url:${opts.url}` : `${opts.doc ? "doc" : opts.folder ? `dir:${opts.folder}` : `task:${opts.taskId}`}:${opts.path}`;
  const shown = open.get(key);
  if (shown && wm.has(shown)) {
    if (wm.isMinimized(shown)) wm.restore(shown); else wm.focus(shown);
    live.get(shown)?.();
    if (opts.focus) aimers.get(shown)?.(opts.focus);
    return;
  }
  const id = `pv-${++seq}`;
  open.set(key, id);
  const urls = [];
  const kind = opts.url ? (opts.kind === "image" ? "image" : "web") : kindOf(opts.path);
  const body = h("div", { class: "pv-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const actions = h("div", { class: "pv-actions" });
  const title = opts.path ? baseName(opts.path) : opts.url;
  let dirty = false, closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    if (dirty) {
      const ok = await confirmDialog("Modifications non enregistrées", `Fermer ${title} sans enregistrer ?`, "Fermer sans enregistrer", "danger", opts.color);
      if (!ok) { closing = false; return; }
    }
    wm.unregister(id); open.delete(key); closers.delete(id); live.delete(id); aimers.delete(id); urls.forEach((u) => URL.revokeObjectURL(u));
  };
  closers.set(id, close);
  const btn = (label, fn, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, on: { click: fn } }, label);
  const act = (icon, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: icon, on: { click: fn } });
  const subtitle = h("small", { title: opts.path || opts.url }, opts.path || opts.url);
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "pv-ic", svg: kind === "image" ? "image" : kind === "web" ? "globe" : "file" }),
    h("div", { class: "vh" }, h("span", { class: "win-title" }, title), subtitle),
    actions,
    h("div", { class: "win-actions" },
      pinButton(id),
      act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer (Échap)", close)));
  const finder = h("div", { class: "pv-note pv-find", hidden: true });
  const el = paint(h("section", { class: `win pv-win k-${kind}`, role: "dialog", "aria-label": `Aperçu ${title}` }, head, finder, body), opts.color);
  // what "Ce que je regarde" sends when this window is in front (regard.js); the path is the real one once loaded
  const regard = opts.url ? { type: "page", url: opts.url } : { type: "fichier", path: opts.path, ...(opts.taskId ? { task: opts.taskId } : {}) };
  wm.register(id, el, { handle: head, ephemeral: true, size: SIZES[kind] || SIZES.other, fresh: true,
    meta: { title, subtitle: opts.path || opts.url, color: colorOf(opts.color), icon: kind === "image" ? "image" : kind === "web" ? "globe" : "file", onClose: close, regard } });
  const fitImage = (img) => img.addEventListener("load", () => {
    if (img.naturalWidth) wm.setSize(id, Math.max(360, img.naturalWidth + 34), Math.max(220, img.naturalHeight + 86));
  }, { once: true });

  if (opts.url) {
    const openTab = () => window.open(opts.url, "_blank", "noopener,noreferrer");
    actions.append(act("globe", "Ouvrir dans le navigateur", openTab), act("copy", "Copier le lien", () => copyText(opts.url)));
    if (opts.kind === "image") {
      const img = h("img", { class: "pv-img", src: opts.url, referrerpolicy: "no-referrer", alt: "" });
      fitImage(img);
      body.replaceChildren(img);
    } else {
      body.replaceChildren(
        h("div", { class: "pv-note" }, "Aperçu isolé du site. S'il reste blanc, le site refuse l'affichage intégré : ouvre-le dans le navigateur."),
        h("iframe", { class: "pv-frame", src: opts.url, referrerpolicy: "no-referrer",
          sandbox: "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox" }));
    }
    return;
  }

  const { taskId, path } = opts;
  const src = source(opts);
  if (taskId) body.dataset.task = taskId; // links to other files inside a previewed document
  const openWith = async (reveal) => {
    try { await api(src.open, { method: "POST", body: { ...src.extra, path, reveal } }); }
    catch (e) { toast(e.message, "err"); }
  };
  const setIcon = (button, name, label) => { button.innerHTML = ICONS[name] || ""; button.title = label; button.setAttribute("aria-label", label); };
  let editing = false, editor = null, baseline = "", saving = false, warned = "";
  const editBtn = act("edit", "Modifier ce fichier", () => (editing ? leaveEdit() : enterEdit()));
  const saveBtn = act("check", "Enregistrer (Ctrl+S)", () => saveEdit());
  editBtn.hidden = true;
  saveBtn.hidden = true;
  actions.append(editBtn, saveBtn, act("external", "Ouvrir avec l'application", () => openWith(false)), act("folder", "Afficher dans le dossier", () => openWith(true)));
  let blob = null, stamp = "", where = path, remote = false, first = true, busy = false, focus = opts.focus || null;
  el.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S") && editing) { e.preventDefault(); saveEdit(); }
  });
  const pdfUrl = (url) => url + (focus?.page ? `#page=${focus.page}` : "");
  // the place Claude (or a search) points at: the passage marked, and scrolled to when asked
  const point = (scroll) => {
    finder.hidden = true;
    const root = body.querySelector(".pv-pre, .pv-text");
    if (!focus || !root || !(focus.text || focus.words?.length)) return;
    const marks = highlight(root, focus);
    if (!marks.length) {
      if (scroll && focus.text) { finder.replaceChildren(h("span", {}, "Passage introuvable dans l'aperçu : le fichier a peut-être changé.")); finder.hidden = false; }
      return;
    }
    let i = 0;
    const count = h("span", {});
    const cur = (on) => marks[i].forEach((m) => m.classList.toggle("cur", on));
    const go = (k) => {
      cur(false);
      i = (k + marks.length) % marks.length;
      cur(true);
      marks[i][0].scrollIntoView({ block: "center" });
      count.textContent = marks.length > 1 ? `${i + 1} / ${marks.length}` : "";
    };
    cur(true);
    if (scroll) go(0);
    if (marks.length > 1) {
      count.textContent = `${i + 1} / ${marks.length}`;
      finder.replaceChildren(h("span", {}, "Mots surlignés"), count,
        btn("Précédent", () => go(i - 1)), btn("Suivant", () => go(i + 1)));
      finder.hidden = false;
    }
  };
  aimers.set(id, (f) => {
    focus = f;
    const frame = kind === "pdf" && body.querySelector("iframe");
    if (frame && urls[0]) frame.src = pdfUrl(urls[0]);
    else point(true);
  });
  actions.append(act("retry", "Recharger", () => check(true)), act("download", "Télécharger", () => {
    const file = editing && editor ? new Blob([editor.value], { type: "text/plain;charset=utf-8" }) : blob;
    if (file) downloadBlob(file, baseName(path));
  }));
  const gone = h("div", { class: "pv-note pv-gone" });
  const draw = async (b) => {
    const url = URL.createObjectURL(b);
    const old = urls.splice(0, urls.length, url);
    const top = body.scrollTop, left = body.scrollLeft;
    let node;
    if (kind === "image") {
      node = h("img", { class: "pv-img", src: url, alt: baseName(path), title: "Cliquer : taille réelle / ajustée" });
      if (body.querySelector(".pv-img.full")) node.classList.add("full");
      node.addEventListener("click", () => node.classList.toggle("full"));
      if (first) fitImage(node);
    } else if (kind === "pdf") {
      node = h("iframe", { class: "pv-frame", src: pdfUrl(url), title: baseName(path) });
    } else if (kind === "html") {
      // local HTML, with its styles and the images next to it, from the preview origin; never its scripts
      node = htmlFrame((r) => { remote ||= r; return api(src.frame(path, r)); }, baseName(path), remote);
    } else if (kind === "text") {
      const text = (await b.text()).slice(0, 2_000_000);
      if (/\.(md|markdown)$/i.test(path)) node = h("div", { class: "pv-text" }, mdElement(text));
      else if (/\.(csv|tsv)$/i.test(path)) node = h("div", { class: "pv-text" }, csvTable(text));
      else if (/\.json$/i.test(path)) {
        let pretty = text;
        try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep raw */ }
        node = h("pre", { class: "pv-pre" }, pretty);
      } else node = h("pre", { class: "pv-pre" }, text);
    } else if (kind === "office") {
      try { node = await officeText(src, path, openWith); }
      catch (e) { node = h("div", { class: "line err pv-err" }, e.message); }
    } else {
      node = h("div", { class: "pv-other" },
        h("p", {}, "Pas d'aperçu intégré pour ce type de fichier."),
        h("p", { class: "muted" }, `${(b.size / 1024).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} Ko · ${b.type || "type inconnu"}`),
        btn("Ouvrir avec l'application", () => openWith(false), "primary"));
    }
    body.replaceChildren(node);
    body.scrollTop = top; body.scrollLeft = left;
    point(first);
    old.forEach((u) => URL.revokeObjectURL(u));
  };
  const disk = h("div", { class: "pv-note pv-gone" });
  const showPath = (prefix = "") => { subtitle.textContent = prefix + where; subtitle.title = where; };
  const markDirty = (on) => {
    dirty = on;
    saveBtn.classList.toggle("on", on);
    saveBtn.disabled = !on;
    subtitle.classList.toggle("dirty", on);
    if (on) showPath("Modifié · ");
  };
  const showDisk = () => {
    if (disk.isConnected) return;
    disk.replaceChildren(h("span", {}, "Ce fichier a changé sur le disque."),
      btn("Recharger", () => { disk.remove(); markDirty(false); check(true); }),
      btn("Garder ma version", () => disk.remove()));
    body.before(disk);
  };
  const enterEdit = async () => {
    if (editing || !blob) return;
    const raw = await editableText(blob);
    if (raw === null) {
      toast(blob.size > EDIT_MAX ? "Ce fichier est trop volumineux pour être modifié ici (2 Mo au plus)."
        : "Ce fichier n'est pas du texte UTF-8 : ouvre-le avec l'application.", "err");
      return;
    }
    const csv = /\.(csv|tsv)$/i.test(path);
    editor = h("textarea", { class: `pv-edit${csv ? " csv" : ""}`, wrap: csv ? "off" : "soft",
      spellcheck: /\.(md|markdown|txt|text|rst)$/i.test(path) ? "true" : "false", "aria-label": `Modifier ${baseName(path)}` });
    editor.value = raw;
    baseline = editor.value;
    editor.addEventListener("input", () => markDirty(editor.value !== baseline));
    editor.addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        editor.setRangeText("\t", editor.selectionStart, editor.selectionEnd, "end");
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    finder.hidden = true;
    body.replaceChildren(editor);
    editing = true;
    el.classList.add("editing");
    setIcon(editBtn, "eye", "Aperçu");
    saveBtn.hidden = false;
    markDirty(false);
    editor.focus();
  };
  const leaveEdit = async () => {
    if (!editing) return;
    if (dirty) {
      const ok = await confirmDialog("Modifications non enregistrées", "Revenir à l'aperçu sans enregistrer ?", "Ne pas enregistrer", "danger", opts.color);
      if (!ok) return;
    }
    editing = false;
    editor = null;
    markDirty(false);
    showPath();
    subtitle.classList.remove("dirty");
    setIcon(editBtn, "edit", "Modifier ce fichier");
    saveBtn.hidden = true;
    el.classList.remove("editing");
    disk.remove();
    if (blob) await draw(blob);
  };
  const saveEdit = async () => {
    if (!editing || !editor || saving || !dirty) return;
    saving = true;
    saveBtn.disabled = true;
    let force = false;
    try {
      const text = editor.value;
      for (;;) {
        try {
          const r = await api(src.save, { method: "PUT", body: { ...src.extra, path, text, stamp, force } });
          stamp = r.stamp || stamp;
          blob = new Blob([text], { type: "text/plain;charset=utf-8" });
          baseline = editor.value;
          markDirty(false);
          showPath();
          disk.remove();
          warned = "";
          subtitle.classList.add("fresh");
          toast("Enregistré.", "ok");
          return;
        } catch (e) {
          if (e.status === 409 && !force) {
            const ok = await confirmDialog("Fichier modifié ailleurs", "Le fichier a changé sur le disque. Enregistrer remplace cette version par la tienne.", "Remplacer", "danger", opts.color);
            if (!ok) return;
            force = true;
            continue;
          }
          toast(e.message, "err");
          return;
        }
      }
    } finally { saving = false; saveBtn.disabled = !dirty; }
  };
  const adopt = async (b) => {
    if (!(editing && editor)) { await draw(b); return; }
    const raw = await editableText(b);
    if (raw === null) { editing = false; editor = null; setIcon(editBtn, "edit", "Modifier ce fichier"); saveBtn.hidden = true; el.classList.remove("editing"); await draw(b); return; }
    const norm = raw.replace(/\r\n/g, "\n");
    if (norm !== baseline) {
      const pos = editor.selectionStart;
      editor.value = raw;
      baseline = editor.value;
      const p = Math.min(pos, baseline.length);
      editor.setSelectionRange(p, p);
    }
  };
  const load = async () => {
    const r = await api(src.get(path), { raw: true });
    const resolved = decodeURIComponent(r.headers.get("X-File-Path") || "") || where;
    const b = await r.blob();
    const newStamp = r.headers.get("X-File-Stamp") || "";
    if (editing && dirty) {
      if (warned !== newStamp) { warned = newStamp; showDisk(); }
      return;
    }
    where = resolved;
    regard.path = where;
    const changed = !first;
    stamp = newStamp;
    blob = b;
    gone.remove();
    disk.remove();
    warned = "";
    editBtn.hidden = kind !== "text";
    await adopt(b);
    first = false;
    if (!dirty) {
      showPath(changed ? `Mis à jour à ${new Date().toLocaleTimeString("fr-FR")} · ` : "");
      subtitle.classList.toggle("fresh", changed);
      subtitle.classList.remove("dirty");
    }
  };
  // a date asked every few seconds; the file itself only when it changed (or on the reload button)
  const check = async (force = false) => {
    if (busy || saving || !wm.has(id)) return;
    if (force && dirty) {
      const ok = await confirmDialog("Recharger le fichier", "Les modifications non enregistrées seront perdues.", "Recharger", "danger", opts.color);
      if (!ok) return;
      markDirty(false);
    }
    busy = true;
    try {
      const s = force ? null : await api(`${src.get(path)}&stat=1`);
      if (!force && editing && dirty && s.stamp !== stamp) {
        if (warned !== s.stamp) { warned = s.stamp; showDisk(); }
        return;
      }
      if (force || s.stamp !== stamp || gone.isConnected) await load();
    } catch (e) {
      if (first) body.replaceChildren(h("div", { class: "line err pv-err" }, e.message));
      else if (e.status === 404 || e.status === 403) {
        gone.textContent = `${e.message} Le fichier a peut-être été supprimé ou déplacé : l'aperçu montre sa dernière version.`;
        if (!gone.isConnected) body.before(gone);
        if (!editing) editBtn.hidden = true;
      }
      // console unreachable for a moment: the next check will tell
    } finally { busy = false; }
  };
  live.set(id, check);
  await check(true);
}

/** The user asks to follow a mail's sender in a project's brief. The console reads the address from the header. */
async function followSender(from, projectFolder) {
  const list = allProjects();
  if (!list.length) { toast("Crée d'abord un projet : le suivi se range dans ses mails.", "err"); return; }
  const sel = h("select", {}, ...list.map((p) => h("option", { value: p.folder }, p.name)));
  const here = projectFor(projectFolder);
  if (here) sel.value = here.folder;
  const ok = await dialog({
    title: "Suivre cet interlocuteur",
    body: h("div", {},
      h("p", {}, "Son adresse sera ajoutée aux mails suivis du projet. C'est toi qui l'ajoutes : un mail ne s'inscrit pas tout seul."),
      h("p", {}, h("b", {}, from || "")),
      h("label", { class: "field" }, h("span", {}, "Projet"), sel)),
    buttons: [{ label: "Annuler", value: false }, { label: "Suivre", value: true, cls: "primary" }],
  });
  if (!ok) return;
  try {
    const r = await api("/api/projects/follow", { method: "POST", body: { folder: sel.value, sender: from } });
    toast(`${r.sender} sera suivi dans le projet « ${r.project} ».`, "ok");
  } catch (e) { toast(e.message, "err"); }
}

/** A tool's result that Claude shows as it is (afficher_resultat): a mail with its header, a page,
 * data or text. The console reads it from the task's tool calls: Claude never copies it. */
async function openResult(opts) {
  const { taskId, result } = opts;
  const key = `res:${taskId}:${result.id}:${result.contient || ""}`;
  const shown = open.get(key);
  if (shown && wm.has(shown)) { if (wm.isMinimized(shown)) wm.restore(shown); else wm.focus(shown); return; }
  const id = `pv-${++seq}`;
  open.set(key, id);
  const kind = result.kind || "text";
  const icon = RESULT_ICON[kind] || "file";
  const body = h("div", { class: "pv-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const actions = h("div", { class: "pv-actions" });
  const title = h("span", { class: "win-title" }, result.title || "Résultat");
  const subtitle = h("small", {}, result.tool ? `Résultat de ${shortTool(result.tool)}` : "Résultat d'un outil");
  const close = () => { wm.unregister(id); open.delete(key); closers.delete(id); };
  closers.set(id, close);
  const act = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "pv-ic", svg: icon }), h("div", { class: "vh" }, title, subtitle), actions,
    h("div", { class: "win-actions" },
      pinButton(id),
      act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer (Échap)", close)));
  const el = paint(h("section", { class: `win pv-win k-${kind === "mail" || kind === "html" ? "html" : "text"}`, role: "dialog",
    "aria-label": `Aperçu ${result.title || "résultat"}` }, head, body), opts.color);
  const regard = { type: "resultat", task: taskId, call: result.id, tool: result.tool || "", title: result.title || "",
    kind: result.kind || "", ...(result.contient ? { contient: result.contient } : {}) };
  wm.register(id, el, { handle: head, ephemeral: true, size: kind === "mail" ? { w: 860, h: 820 } : SIZES[kind] || SIZES.text, fresh: true,
    meta: { title: result.title || "Résultat", subtitle: subtitle.textContent, color: colorOf(opts.color), icon, onClose: close, regard } });

  const q = (remote) => `/api/tasks/${taskId}/result?id=${encodeURIComponent(result.id)}&contient=${encodeURIComponent(result.contient || "")}&remote=${remote}`;
  let v;
  try { v = await api(q(false)); } catch (e) { body.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
  title.textContent = v.title || "Résultat";
  subtitle.textContent = `Résultat de ${shortTool(v.tool)}`;
  Object.assign(regard, { title: v.title || regard.title, tool: v.tool || regard.tool, kind: v.kind || regard.kind });
  if (v.weblink) {
    actions.append(act("external", "Ouvrir dans Outlook (navigateur)", () => window.open(v.weblink, "_blank", "noopener,noreferrer")),
      act("copy", "Copier le lien", () => copyText(v.weblink)));
  }
  if (v.kind === "mail" && (v.meta || {}).from) {
    actions.append(h("button", { type: "button", class: "btn small", title: "Ajouter cet expéditeur aux mails suivis d'un projet",
      on: { click: () => followSender(v.meta.from, opts.projectFolder) } }, "Suivre"));
  }
  const parts = [];
  if (v.kind === "mail") {
    const m = v.meta || {};
    const row = (label, value) => (value ? h("div", { class: "pv-mrow" }, h("span", { class: "pv-mk" }, label), h("span", { class: "pv-mv" }, value)) : null);
    const files = (m.attachments || []).map((a) => `${a.name}${kb(a.size)}`).join(" · ");
    parts.push(h("div", { class: "pv-mailhead" },
      h("div", { class: "pv-msubject" }, v.title),
      row("De", m.from), row("À", m.to), row("Cc", m.cc), row("Date", m.date ? when(m.date) : ""),
      row("Pièces jointes", files),
      v.count > 1 ? h("div", { class: "pv-mnote" }, `1 des ${v.count} mails de ce résultat.`) : null,
      v.inline_images ? h("div", { class: "pv-mnote" }, `${v.inline_images} image${v.inline_images > 1 ? "s" : ""} jointe${v.inline_images > 1 ? "s" : ""} au corps du mail non affichée${v.inline_images > 1 ? "s" : ""} (ouvre-le dans Outlook pour ${v.inline_images > 1 ? "les" : "la"} voir).`) : null));
  }
  if (v.url) {
    let first = { url: v.url, remote: v.remote };
    parts.push(htmlFrame(async (remote) => {
      if (!remote && first) { const f = first; first = null; return f; }
      const r = await api(q(remote));
      return { url: r.url, remote: r.remote };
    }, v.title));
  } else if (v.kind === "image") {
    parts.push(h("img", { class: "pv-img", src: v.image, alt: v.title || "" }));
  } else if (v.text !== undefined) {
    actions.append(act("copy", "Copier le texte", () => copyText(v.text)));
    parts.push(h("pre", { class: "pv-pre" }, v.text));
  }
  body.replaceChildren(...parts);
}
