// History drawer: every task, including closed windows; click to reopen. Archived discussions are behind
// the « Archivées » filter; several can be archived (or brought back) at once.
import { download } from "./api.js";
import { $, STATUS, fmtCost, fmtDate, h, statusLabel } from "./util.js";

let ctx = null, q = "", profile = "", status = "", shelf = "";
const picked = new Set();
const ACTIVE = new Set(["queued", "running", "awaiting"]);

export function toggleHistory(context) {
  ctx = context;
  const el = $("#history");
  if (!el.hidden) { el.hidden = true; return; }
  el.hidden = false;
  picked.clear();
  renderHistory();
  el.querySelector("input")?.focus();
}

export function renderHistory() {
  const el = $("#history");
  if (!ctx || el.hidden) return;
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
    const rows = ctx.tasks().filter((t) => (!profile || t.profile === profile) && (!status || t.status === status)
      && (shelf === "all" || (shelf === "archived" ? t.archived : !t.archived))
      && t.origin !== "reglage" && !t.ephemeral
      && (!needle || `${t.title} ${t.prompt}`.toLowerCase().includes(needle)));
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
    }) : [h("div", { class: "hrow" }, h("span", { class: "muted" }, shelf === "archived" ? "Aucune discussion archivée." : "Aucune tâche."))]));
    renderBulk(rows);
  }
  renderList();
  el.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Historique"),
      h("button", { type: "button", class: "btn small", on: { click: () => download("/api/history/export", "historique.json") } }, "Exporter"),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { el.hidden = true; } } })),
    h("div", { class: "drawer-filters" }, search, prof, st, sh), bulk, list);
}
