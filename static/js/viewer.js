// Preview of files and links: images, PDF, local HTML (no scripts), text/CSV/JSON,
// and web pages in a sandboxed frame. Local files come from the task's folders
// through the console (token header), as blob URLs.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { copyText, downloadBlob, h, toast } from "./util.js";

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

export async function fileBlob(taskId, path) {
  const r = await api(`/api/tasks/${taskId}/file?path=${encodeURIComponent(path)}`, { raw: true });
  return r.blob();
}

/** Inline thumbnail for a local image reference (safe: served by the console itself). */
export async function thumbnail(el, taskId) {
  if (!taskId || el.dataset.thumb) return;
  el.dataset.thumb = "1";
  try {
    const url = URL.createObjectURL(await fileBlob(taskId, el.dataset.path));
    const img = h("img", { class: "thumb", src: url, alt: baseName(el.dataset.path), title: "Cliquer pour agrandir" });
    img.addEventListener("click", () => openPreview({ taskId, path: el.dataset.path }));
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

/** opts: {taskId, path} for a file, or {url, kind:"web"|"image"} for the web. */
export async function openPreview(opts) {
  const urls = [];
  const body = h("div", { class: "pv-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const actions = h("div", { class: "pv-actions" });
  const title = opts.path ? baseName(opts.path) : opts.url;
  const close = () => { overlay.remove(); urls.forEach((u) => URL.revokeObjectURL(u)); document.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const btn = (label, fn, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, on: { click: fn } }, label);
  const box = h("div", { class: "dialog pv", role: "dialog", "aria-modal": "true", "aria-label": `Aperçu ${title}` },
    h("div", { class: "pv-head" }, h("div", { class: "vh" }, h("h3", {}, title), h("small", {}, opts.path || opts.url)), actions,
      h("button", { type: "button", class: "icon-btn", title: "Fermer (Échap)", svg: "close", on: { click: close } })),
    body);
  const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) close(); } } }, box);
  document.getElementById("modal-root").append(overlay);
  document.addEventListener("keydown", onKey, true);

  if (opts.url) {
    const open = () => window.open(opts.url, "_blank", "noopener,noreferrer");
    actions.append(btn("Ouvrir dans le navigateur", open, "primary"), btn("Copier le lien", () => copyText(opts.url)));
    if (opts.kind === "image") {
      body.replaceChildren(h("img", { class: "pv-img", src: opts.url, referrerpolicy: "no-referrer", alt: "" }));
    } else {
      body.replaceChildren(
        h("div", { class: "pv-note" }, "Aperçu isolé du site. S'il reste blanc, le site refuse l'affichage intégré : ouvre-le dans le navigateur."),
        h("iframe", { class: "pv-frame", src: opts.url, referrerpolicy: "no-referrer",
          sandbox: "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox" }));
    }
    return;
  }

  const { taskId, path } = opts;
  body.dataset.task = taskId; // links to other files inside a previewed document
  const openWith = async (reveal) => {
    try { await api(`/api/tasks/${taskId}/file/open`, { method: "POST", body: { path, reveal } }); }
    catch (e) { toast(e.message, "err"); }
  };
  actions.append(btn("Ouvrir avec l'application", () => openWith(false), "primary"), btn("Afficher dans le dossier", () => openWith(true)));
  let blob;
  try { blob = await fileBlob(taskId, path); }
  catch (e) { body.replaceChildren(h("div", { class: "line err" }, e.message)); return; }
  actions.append(btn("Télécharger", () => downloadBlob(blob, baseName(path))));
  const url = URL.createObjectURL(blob);
  urls.push(url);
  const kind = kindOf(path);
  if (kind === "image") {
    const img = h("img", { class: "pv-img", src: url, alt: baseName(path), title: "Cliquer : taille réelle / ajustée" });
    img.addEventListener("click", () => img.classList.toggle("full"));
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
