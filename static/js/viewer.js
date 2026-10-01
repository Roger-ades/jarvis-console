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

/** Where a file comes from: a task (its folders, what it cited) or a project folder. frame: the
 * address of an HTML file on the preview origin (another origin than the console's, see content.py). */
function source(opts) {
  if (opts.folder) {
    const q = `profile=${encodeURIComponent(opts.profile || "")}&folder=${encodeURIComponent(opts.folder)}`;
    return { get: (path) => `/api/workspace/file?${q}&path=${encodeURIComponent(path)}`, open: "/api/workspace/file/open",
      frame: (path, remote) => `/api/workspace/frame?${q}&path=${encodeURIComponent(path)}&remote=${remote}`,
      extra: { profile: opts.profile, folder: opts.folder } };
  }
  return { get: (path) => `/api/tasks/${opts.taskId}/file?path=${encodeURIComponent(path)}`, open: `/api/tasks/${opts.taskId}/file/open`,
    frame: (path, remote) => `/api/tasks/${opts.taskId}/frame?path=${encodeURIComponent(path)}&remote=${remote}`, extra: {} };
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
const live = new Map();    // window id -> check(): reloads a file preview when the file changed
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

// Échap closes the preview window that has the focus (when no dialog is open)
document.addEventListener("keydown", (e) => {
  const id = wm.focused();
  if (e.key === "Escape" && id && closers.has(id) && !document.querySelector(".overlay")) { e.preventDefault(); closers.get(id)(); }
});

/** opts: {taskId, path} or {profile, folder, path} for a file, {url, kind:"web"|"image"} for the web,
 * {taskId, result: {id, contient, title, kind}} for a tool's result shown by Claude; color: accent. */
export async function openPreview(opts) {
  if (opts.result) return openResult(opts);
  const key = opts.url ? `url:${opts.url}` : `${opts.folder ? `dir:${opts.folder}` : `task:${opts.taskId}`}:${opts.path}`;
  const shown = open.get(key);
  if (shown && wm.has(shown)) { if (wm.isMinimized(shown)) wm.restore(shown); else wm.focus(shown); live.get(shown)?.(); return; }
  const id = `pv-${++seq}`;
  open.set(key, id);
  const urls = [];
  const kind = opts.url ? (opts.kind === "image" ? "image" : "web") : kindOf(opts.path);
  const body = h("div", { class: "pv-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const actions = h("div", { class: "pv-actions" });
  const title = opts.path ? baseName(opts.path) : opts.url;
  const close = () => { wm.unregister(id); open.delete(key); closers.delete(id); live.delete(id); urls.forEach((u) => URL.revokeObjectURL(u)); };
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
  const el = h("section", { class: `win pv-win k-${kind}`, role: "dialog", "aria-label": `Aperçu ${title}`,
    style: { "--pc": opts.color || "var(--accent)" } }, head, body);
  wm.register(id, el, { handle: head, ephemeral: true, size: SIZES[kind] || SIZES.other, fresh: true,
    meta: { title, subtitle: opts.path || opts.url, color: opts.color, icon: kind === "image" ? "image" : kind === "web" ? "globe" : "file", onClose: close } });
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
  let blob = null, stamp = "", where = path, remote = false, first = true, busy = false;
  actions.append(act("retry", "Recharger", () => check(true)), act("download", "Télécharger", () => blob && downloadBlob(blob, baseName(path))));
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
      node = h("iframe", { class: "pv-frame", src: url, title: baseName(path) });
    } else if (kind === "html") {
      // local HTML, with its styles and the images next to it, from the preview origin; never its scripts
      node = htmlFrame((r) => { remote ||= r; return api(src.frame(path, r)); }, baseName(path), remote);
    } else if (kind === "text") {
      const text = (await b.text()).slice(0, 2_000_000);
      if (/\.md$/i.test(path)) node = h("div", { class: "pv-text" }, mdElement(text));
      else if (/\.(csv|tsv)$/i.test(path)) node = h("div", { class: "pv-text" }, csvTable(text));
      else if (/\.json$/i.test(path)) {
        let pretty = text;
        try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep raw */ }
        node = h("pre", { class: "pv-pre" }, pretty);
      } else node = h("pre", { class: "pv-pre" }, text);
    } else {
      node = h("div", { class: "pv-other" },
        h("p", {}, "Pas d'aperçu intégré pour ce type de fichier."),
        h("p", { class: "muted" }, `${(b.size / 1024).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} Ko · ${b.type || "type inconnu"}`),
        btn("Ouvrir avec l'application", () => openWith(false), "primary"));
    }
    body.replaceChildren(node);
    body.scrollTop = top; body.scrollLeft = left;
    old.forEach((u) => URL.revokeObjectURL(u));
  };
  const load = async () => {
    const r = await api(src.get(path), { raw: true });
    where = decodeURIComponent(r.headers.get("X-File-Path") || "") || where;
    const b = await r.blob();
    const changed = !first;
    stamp = r.headers.get("X-File-Stamp") || "";
    blob = b;
    gone.remove();
    await draw(b);
    first = false;
    const at = changed ? `Mis à jour à ${new Date().toLocaleTimeString("fr-FR")} · ` : "";
    subtitle.textContent = at + where;
    subtitle.title = where;
    subtitle.classList.toggle("fresh", changed);
  };
  // a date asked every few seconds; the file itself only when it changed (or on the reload button)
  const check = async (force = false) => {
    if (busy || !wm.has(id)) return;
    busy = true;
    try {
      const s = force ? null : await api(`${src.get(path)}&stat=1`);
      if (force || s.stamp !== stamp || gone.isConnected) await load();
    } catch (e) {
      if (first) body.replaceChildren(h("div", { class: "line err pv-err" }, e.message));
      else if (e.status === 404 || e.status === 403) {
        gone.textContent = `${e.message} Le fichier a peut-être été supprimé ou déplacé : l'aperçu montre sa dernière version.`;
        if (!gone.isConnected) body.before(gone);
      }
      // console unreachable for a moment: the next check will tell
    } finally { busy = false; }
  };
  live.set(id, check);
  await check(true);
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
  const el = h("section", { class: `win pv-win k-${kind === "mail" || kind === "html" ? "html" : "text"}`, role: "dialog",
    "aria-label": `Aperçu ${result.title || "résultat"}`, style: { "--pc": opts.color || "var(--accent)" } }, head, body);
  wm.register(id, el, { handle: head, ephemeral: true, size: kind === "mail" ? { w: 860, h: 820 } : SIZES[kind] || SIZES.text, fresh: true,
    meta: { title: result.title || "Résultat", subtitle: subtitle.textContent, color: opts.color, icon, onClose: close } });

  const q = (remote) => `/api/tasks/${taskId}/result?id=${encodeURIComponent(result.id)}&contient=${encodeURIComponent(result.contient || "")}&remote=${remote}`;
  let v;
  try { v = await api(q(false)); } catch (e) { body.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
  title.textContent = v.title || "Résultat";
  subtitle.textContent = `Résultat de ${shortTool(v.tool)}`;
  if (v.weblink) {
    actions.append(act("external", "Ouvrir dans Outlook (navigateur)", () => window.open(v.weblink, "_blank", "noopener,noreferrer")),
      act("copy", "Copier le lien", () => copyText(v.weblink)));
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
