// Ctrl+K: one place to find anything (actions, projects, discussions, Claude Code sessions)
// and to act on it, with the keyboard.
import { api } from "./api.js";
import { fmtDate, h, modalHost, reveal, statusLabel } from "./util.js";
import { openPreview } from "./viewer.js";

let open = null;
const ORIGIN = { desktop: "Claude Desktop", cli: "CLI", console: "Console" };
const KIND = { pdf: "PDF", word: "Word", excel: "Excel", powerpoint: "PowerPoint", mail: "Mail", page: "Page HTML", texte: "Texte" };
const parentName = (p) => baseName(String(p || "").replace(/[\\/][^\\/]*$/, ""));

/** A passage of a document, the words found between \x02 and \x03 (by the server): text nodes only. */
function serverMarked(text) {
  return String(text || "").split(/(\x02[^\x03]*\x03)/).map((s) => (s.startsWith("\x02") ? h("mark", {}, s.slice(1, -1)) : s));
}
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** A text with the searched words highlighted (text nodes only: no markup injection). */
function marked(text, q) {
  const t = String(text || "");
  const i = q ? fold(t).indexOf(fold(q)) : -1;
  if (i < 0) return t;
  return [t.slice(0, i), h("mark", {}, t.slice(i, i + q.length)), t.slice(i + q.length)];
}

/** ctx: actions(), projects(), tasks(), openTask(id), useProject(p), openSession(pid, sid), projectName(folder). */
export function openPalette(ctx) {
  if (open) { reveal(open.input.ownerDocument); open.input.focus(); return; }
  const input = h("input", { type: "text", class: "pal-input", placeholder: "Rechercher une discussion, un document, un projet ou une action…",
    spellcheck: "false", "aria-label": "Rechercher" });
  const list = h("div", { class: "pal-list", role: "listbox" });
  const box = h("div", { class: "pal", role: "dialog", "aria-modal": "true", "aria-label": "Recherche" },
    h("div", { class: "pal-top" }, h("span", { class: "i", svg: "search" }), input, h("kbd", {}, "Échap")), list,
    h("div", { class: "pal-foot" }, h("span", {}, h("kbd", {}, "↑"), h("kbd", {}, "↓"), " choisir"), h("span", {}, h("kbd", {}, "Entrée"), " ouvrir"),
      h("span", { class: "grow" }), h("span", { class: "muted" }, "Recherche aussi dans le contenu des discussions")));
  const overlay = h("div", { class: "overlay pal-overlay", on: { mousedown: (e) => { if (e.target === overlay) close(); } } }, box);
  const { root, doc } = modalHost("palette");
  let items = [], index = 0, server = { tasks: [], sessions: [], documents: [] }, timer = null, seq = 0, loading = false;

  function close() {
    overlay.remove();
    doc.removeEventListener("keydown", onKey, true);
    open = null;
  }

  function build() {
    const q = input.value.trim();
    const f = fold(q);
    const has = (...xs) => !f || xs.some((x) => fold(x).includes(f));
    const groups = [];
    const acts = ctx.actions().filter((a) => has(a.label, a.keywords || ""));
    const projs = ctx.projects().filter((p) => has(p.name, p.folder));
    const localTasks = ctx.tasks().filter((t) => has(t.title, t.prompt)).sort((a, b) => (b.created || 0) - (a.created || 0));
    const taskIds = new Set(localTasks.map((t) => t.id));
    const tasks = [...localTasks.slice(0, q ? 8 : 5).map((t) => ({ t, snippet: "" })),
      ...server.tasks.filter((t) => !taskIds.has(t.id)).map((t) => ({ t, snippet: t.snippet }))];
    if (projs.length) groups.push(["Projets", projs.slice(0, 6).map((p) => ({
      icon: "book", color: p.color, title: p.name, sub: baseName(p.folder), run: () => ctx.useProject(p) }))]);
    if (tasks.length) groups.push(["Discussions", tasks.slice(0, 12).map(({ t, snippet }) => ({
      icon: "list", color: t.color, title: t.title, snippet,
      sub: [t.profile_name, ctx.projectName(t.workdir) || baseName(t.workdir), statusLabel(t), fmtDate(t.created)].filter(Boolean).join(" · "),
      run: () => ctx.openTask(t.id) }))]);
    if (server.sessions.length) groups.push(["Sessions Claude Code", server.sessions.slice(0, 10).map((s) => ({
      icon: "retry", color: s.color, title: s.title, snippet: s.snippet,
      sub: [s.profile_name, ORIGIN[s.origin] || s.origin, baseName(s.cwd), fmtDate(s.updated)].filter(Boolean).join(" · "),
      run: () => ctx.openSession(s.profile, s.id) }))]);
    if (server.documents?.length) groups.push(["Documents", server.documents.slice(0, 10).map((d) => ({
      icon: "file", title: baseName(d.path), snippet: d.snippet, serverMarks: true,
      sub: [KIND[d.kind] || d.kind, d.title && d.title !== baseName(d.path).replace(/\.[^.]+$/, "") ? d.title : "", parentName(d.path), fmtDate(d.mtime)].filter(Boolean).join(" · "),
      run: () => openPreview({ doc: true, path: d.path, focus: { text: d.snippet, words: (d.snippet.match(/\x02[^\x03]*\x03/g) || []).map((w) => w.slice(1, -1)), page: d.page || 0 } }) }))]);
    if (acts.length) groups.push(["Actions", acts.slice(0, q ? 8 : 10).map((a) => ({ icon: a.icon || "sparkle", title: a.label, sub: a.hint || "", run: a.run }))]);
    items = groups.flatMap(([, xs]) => xs);
    index = Math.min(index, Math.max(0, items.length - 1));
    let k = 0;
    list.replaceChildren(...groups.map(([name, xs]) => h("div", { class: "pal-group" }, h("div", { class: "pal-gh" }, name),
      ...xs.map((it) => {
        const i = k++;
        const el = h("button", { type: "button", role: "option", class: `pal-item${i === index ? " on" : ""}`, "aria-selected": String(i === index),
          style: it.color ? { "--pc": it.color } : undefined, on: { click: () => { close(); it.run(); }, mousemove: () => { if (index !== i) { index = i; highlight(); } } } },
          h("span", { class: "i", svg: it.icon }),
          h("span", { class: "pal-t" }, h("b", {}, marked(it.title, q)),
            it.snippet ? h("span", { class: "pal-snip" }, it.serverMarks ? serverMarked(it.snippet) : marked(it.snippet, q)) : null,
            it.sub ? h("small", {}, it.sub) : null));
        it.el = el;
        return el;
      }))),
    ...(items.length ? [] : [h("div", { class: "pal-empty" }, loading ? "Recherche…" : q ? "Rien trouvé." : "")]),
    ...(loading && items.length ? [h("div", { class: "pal-empty small" }, "Recherche dans le contenu…")] : []));
  }

  function highlight() {
    items.forEach((it, i) => { it.el?.classList.toggle("on", i === index); it.el?.setAttribute("aria-selected", String(i === index)); });
    items[index]?.el?.scrollIntoView({ block: "nearest" });
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); index = Math.min(items.length - 1, index + 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); index = Math.max(0, index - 1); highlight(); }
    else if (e.key === "Enter" && items[index]) { e.preventDefault(); const it = items[index]; close(); it.run(); }
  }

  input.addEventListener("input", () => {
    index = 0;
    server = { tasks: [], sessions: [], documents: [] };
    const q = input.value.trim();
    clearTimeout(timer);
    loading = q.length >= 2;
    build();
    if (!loading) return;
    const my = ++seq;
    timer = setTimeout(async () => {
      try { const r = await api(`/api/search?q=${encodeURIComponent(q)}`); if (my === seq) server = r; }
      catch { /* local results stay */ }
      if (my === seq) { loading = false; build(); }
    }, 250);
  });
  doc.addEventListener("keydown", onKey, true);
  root.append(overlay);
  open = { input, close };
  build();
  input.focus();
}
