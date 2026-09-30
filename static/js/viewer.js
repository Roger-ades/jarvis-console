// Preview of files and links: images, PDF, local HTML (no scripts), text/CSV/JSON,
// and web pages in a sandboxed frame. Local files come from the task's folders
// through the console (token header), as blob URLs.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { copyText, downloadBlob, h, toast } from "./util.js";
import * as wm from "./wm.js";

const IMG = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
const TEXT = /\.(txt|md|csv|tsv|json|log|xml)$/i;
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

export function kindOf(name) {
  if (IMG.test(name)) return "image";
  if (/\.pdf$/i.test(name)) return "pdf";
  if (/\.html?$/i.test(name)) return "html";
  if (TEXT.test(name)) return "text";
  return "other";
}

/** Where a file comes from: a task (its folders, what it cited) or a project folder. */
function source(opts) {
  if (opts.folder) {
    const q = `profile=${encodeURIComponent(opts.profile || "")}&folder=${encodeURIComponent(opts.folder)}`;
    return { get: (path) => `/api/workspace/file?${q}&path=${encodeURIComponent(path)}`, open: "/api/workspace/file/open",
      extra: { profile: opts.profile, folder: opts.folder } };
  }
  return { get: (path) => `/api/tasks/${opts.taskId}/file?path=${encodeURIComponent(path)}`, open: `/api/tasks/${opts.taskId}/file/open`, extra: {} };
}

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

function csvTable(text) {
  const sep = (text.split("\n")[0].match(/;/g) || []).length > (text.split("\n")[0].match(/,/g) || []).length ? ";" : text.includes("\t") ? "\t" : ",";
  const rows = text.split(/\r?\n/).filter(Boolean).slice(0, 501).map((l) => l.split(sep));
  const table = h("table", { class: "csv" }, h("thead", {}, h("tr", {}, ...(rows[0] || []).map((c) => h("th", {}, c)))),
    h("tbody", {}, ...rows.slice(1).map((r) => h("tr", {}, ...r.map((c) => h("td", {}, c))))));
  return h("div", { class: "md" }, table, rows.length > 500 ? h("p", { class: "muted" }, "500 premières lignes.") : null);
}

// Previews open as windows of the desktop (move, resize, enlarge, several side by side).
const SIZES = { image: { w: 720, h: 560 }, pdf: { w: 780, h: 900 }, html: { w: 980, h: 760 }, web: { w: 1040, h: 780 },
  text: { w: 760, h: 640 }, other: { w: 480, h: 300 } };
const open = new Map();   // what is shown -> window id
const closers = new Map(); // window id -> close()
let seq = 0;

// Échap closes the preview window that has the focus (when no dialog is open)
document.addEventListener("keydown", (e) => {
  const id = wm.focused();
  if (e.key === "Escape" && id && closers.has(id) && !document.querySelector(".overlay")) { e.preventDefault(); closers.get(id)(); }
});

/** opts: {taskId, path} or {profile, folder, path} for a file, or {url, kind:"web"|"image"} for the web; color: accent. */
export async function openPreview(opts) {
  const key = opts.url ? `url:${opts.url}` : `${opts.folder ? `dir:${opts.folder}` : `task:${opts.taskId}`}:${opts.path}`;
  const shown = open.get(key);
  if (shown && wm.has(shown)) { wm.focus(shown); return; }
  const id = `pv-${++seq}`;
  open.set(key, id);
  const urls = [];
  const kind = opts.url ? (opts.kind === "image" ? "image" : "web") : kindOf(opts.path);
  const body = h("div", { class: "pv-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const actions = h("div", { class: "pv-actions" });
  const title = opts.path ? baseName(opts.path) : opts.url;
  const close = () => { wm.unregister(id); open.delete(key); closers.delete(id); urls.forEach((u) => URL.revokeObjectURL(u)); };
  closers.set(id, close);
  const btn = (label, fn, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, on: { click: fn } }, label);
  const act = (icon, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: icon, on: { click: fn } });
  const subtitle = h("small", { title: opts.path || opts.url }, opts.path || opts.url);
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "pv-ic", svg: kind === "image" ? "image" : kind === "web" ? "globe" : "file" }),
    h("div", { class: "vh" }, h("span", { class: "win-title" }, title), subtitle),
    actions,
    h("div", { class: "win-actions" },
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer (Échap)", close)));
  const el = h("section", { class: `win pv-win k-${kind}`, role: "dialog", "aria-label": `Aperçu ${title}`,
    style: { "--pc": opts.color || "var(--accent)" } }, head, body);
  wm.register(id, el, { handle: head, ephemeral: true, size: SIZES[kind] || SIZES.other, fresh: true });
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
  actions.append(act("external", "Ouvrir avec l'application", () => openWith(false)), act("folder", "Afficher dans le dossier", () => openWith(true)));
  let blob;
  try {
    const r = await api(src.get(path), { raw: true });
    const where = decodeURIComponent(r.headers.get("X-File-Path") || "");
    if (where) { subtitle.textContent = where; subtitle.title = where; }
    blob = await r.blob();
  } catch (e) { body.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
  actions.append(act("download", "Télécharger", () => downloadBlob(blob, baseName(path))));
  const url = URL.createObjectURL(blob);
  urls.push(url);
  if (kind === "image") {
    const img = h("img", { class: "pv-img", src: url, alt: baseName(path), title: "Cliquer : taille réelle / ajustée" });
    img.addEventListener("click", () => img.classList.toggle("full"));
    fitImage(img);
    body.replaceChildren(img);
  } else if (kind === "pdf") {
    body.replaceChildren(h("iframe", { class: "pv-frame", src: url, title: baseName(path) }));
  } else if (kind === "html") {
    // local HTML: shown without scripts (the blob shares the console's origin)
    body.replaceChildren(h("div", { class: "pv-note" }, "Page affichée sans ses scripts, par sécurité."),
      h("iframe", { class: "pv-frame white", src: url, sandbox: "" }));
  } else if (kind === "text") {
    const text = (await blob.text()).slice(0, 2_000_000);
    if (/\.md$/i.test(path)) body.replaceChildren(h("div", { class: "pv-text" }, mdElement(text)));
    else if (/\.(csv|tsv)$/i.test(path)) body.replaceChildren(h("div", { class: "pv-text" }, csvTable(text)));
    else if (/\.json$/i.test(path)) {
      let pretty = text;
      try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep raw */ }
      body.replaceChildren(h("pre", { class: "pv-pre" }, pretty));
    } else body.replaceChildren(h("pre", { class: "pv-pre" }, text));
  } else {
    body.replaceChildren(h("div", { class: "pv-other" },
      h("p", {}, "Pas d'aperçu intégré pour ce type de fichier."),
      h("p", { class: "muted" }, `${(blob.size / 1024).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} Ko · ${blob.type || "type inconnu"}`),
      btn("Ouvrir avec l'application", () => openWith(false), "primary")));
  }
}
