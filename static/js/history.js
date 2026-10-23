// Discussions drawer: JARVIS tasks, including closed windows, and (filter) Claude Desktop / CLI sessions.
// Archived discussions are behind the « Archivées » filter; several can be archived (or brought back) at once.
import { download } from "./api.js";
import { mountSessions } from "./library.js";
import { $, STATUS, fmtCost, fmtDate, h, statusLabel } from "./util.js";

let ctx = null, q = "", profile = "", status = "", shelf = "", source = "jarvis", scope = "";
const picked = new Set();
const ACTIVE = new Set(["queued", "running", "awaiting"]);
const norm = (p) => String(p || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();

/** Open the discussions drawer. src "jarvis" lists JARVIS tasks; "hors" mounts the Claude Desktop / CLI list.
 *  The same source clicked again closes the drawer. */
export function openDiscussions(context, src = "jarvis") {
  ctx = context;
  const el = $("#history");
  if (!el.hidden && source === src) { el.hidden = true; return; }
  source = src;
  ctx.closeDrawers?.("history");
  el.hidden = false;
  picked.clear();
  renderHistory();
  if (src === "jarvis") el.querySelector("input")?.focus();
}

export function toggleHistory(context) { openDiscussions(context, "jarvis"); }

export function renderHistory() {
  const el = $("#history");
  if (!ctx || el.hidden) return;
  if (source === "hors" && el.querySelector(".sess-host")) return;
  const src = h("select", { title: "Où se sont tenues les discussions" },
    h("option", { value: "jarvis" }, "Dans JARVIS"), h("option", { value: "hors" }, "Hors JARVIS"));
  src.value = source;
  src.addEventListener("change", () => { source = src.value; picked.clear(); renderHistory(); });
  const where = h("select", { title: "Limiter aux discussions du dossier choisi dans la barre" },
    h("option", { value: "" }, "Tous les dossiers"), h("option", { value: "folder" }, "Ce dossier"));
  where.value = scope;
  where.hidden = source !== "jarvis";
  where.addEventListener("change", () => { scope = where.value; picked.clear(); renderList(); });
  const head = h("div", { class: "drawer-head" }, h("h2", {}, "Discussions"),
    source === "jarvis" ? h("button", { type: "button", class: "btn small", on: { click: () => download("/api/history/export", "historique.json") } }, "Exporter") : null,
    h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { el.hidden = true; } } }));
  if (source === "hors") {
    const host = h("div", { class: "sess-host" });
    el.replaceChildren(head, h("div", { class: "drawer-filters" }, src), host);
    mountSessions(ctx, host);
    return;
  }
  const search = h("input", { type: "text", placeholder: "Rechercher dans les demandes…", value: q });
  search.addEventListener("input", () => { q = search.value; renderList(); });
  const prof = h("select", {}, h("option", { value: "" }, "Tous les profils"), ...ctx.profiles().map((p) => h("option", { value: p.id }, p.name)));
  prof.value = profile;
  prof.addEventListener("change", () => { profile = prof.value; renderList(); });
  const st = h("select", {}, h("option", { value: "" }, "Tous les statuts"), ...Object.entries(STATUS).map(([k, v]) => h("option", { value: k }, v)));
  st.value = status;
  st.addEventListener("change", () => { status = st.value; renderList(); });
  const sh = h("select", { title: "Les discussions archivées quittent les listes courantes ; Ctrl+K les trouve toujours" },
    h("option", { value: "" }, "Courantes"), h("option", { value: "archived" }, "Archivées"), h("option", { value: "all" }, "Toutes"));
  sh.value = shelf;
  sh.addEventListener("change", () => { shelf = sh.value; picked.clear(); renderList(); });
  const list = h("div", { class: "drawer-list" });
  const bulk = h("div", { class: "drawer-bulk" });
  function renderBulk(rows) {
    const sel = rows.filter((t) => picked.has(t.id));
    if (!sel.length) { bulk.hidden = true; return; }
    bulk.hidden = false;
    const toArchive = sel.filter((t) => !t.archived && !ACTIVE.has(t.status)), toRestore = sel.filter((t) => t.archived);
    const act = async (xs, on) => { await ctx.archive(xs.map((t) => t.id), on); picked.clear(); renderList(); };
    bulk.replaceChildren(...[h("span", {}, `${sel.length} sélectionnée${sel.length > 1 ? "s" : ""}`), h("span", { class: "grow" }),
      toArchive.length ? h("button", { type: "button", class: "btn small", on: { click: () => act(toArchive, true) } }, `Archiver (${toArchive.length})`) : null,
      toRestore.length ? h("button", { type: "button", class: "btn small", on: { click: () => act(toRestore, false) } }, `Désarchiver (${toRestore.length})`) : null,
      h("button", { type: "button", class: "btn small ghost", on: { click: () => { picked.clear(); renderList(); } } }, "Annuler")].filter(Boolean));
  }
  function renderList() {
    const needle = q.trim().toLowerCase();
    const here = norm((ctx.folder || ctx.workdir)?.() || "");
    const rows = ctx.tasks().filter((t) => (!profile || t.profile === profile) && (!status || t.status === status)
      && (shelf === "all" || (shelf === "archived" ? t.archived : !t.archived))
      && t.origin !== "reglage" && !t.ephemeral
      && (scope !== "folder" || !here || norm(t.workdir) === here)
      && (!needle || `${t.title} ${t.prompt}`.toLowerCase().includes(needle)))
      .sort((a, b) => (b.created || 0) - (a.created || 0));
    for (const id of [...picked]) if (!rows.some((t) => t.id === id)) picked.delete(id);
    list.replaceChildren(...(rows.length ? rows.map((t) => {
      const box = h("input", { type: "checkbox", title: "Sélectionner (archiver plusieurs discussions à la fois)",
        on: { click: (e) => e.stopPropagation(), change: (e) => { if (e.target.checked) picked.add(t.id); else picked.delete(t.id); renderBulk(rows); } } });
      box.checked = picked.has(t.id);
      return h("div", { class: `hrow${t.archived ? " archived" : ""}`, style: { "--pc": t.color }, tabindex: "0",
        on: { click: () => ctx.openTask(t.id), keydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) ctx.openTask(t.id); } } },
        box,
        h("div", { class: "hm" }, h("div", { class: "ht", title: t.prompt }, t.title),
          h("div", { class: "hs" }, [t.profile_name, t.preset_name, fmtDate(t.created), fmtCost(t.cost_usd),
            t.archived ? `archivée ${fmtDate(t.archived)}` : ""].filter(Boolean).join(" · "))),
        h("span", { class: `chip s-${t.status}` }, statusLabel(t)));
    }) : [h("div", { class: "hrow" }, h("span", { class: "muted" }, shelf === "archived" ? "Aucune discussion archivée." : "Aucune discussion."))]));
    renderBulk(rows);
  }
  renderList();
  el.replaceChildren(head, h("div", { class: "drawer-filters" }, src, where, search, prof, st, sh), bulk, list);
}
