// History drawer: every task, including closed windows; click to reopen.
import { download } from "./api.js";
import { STATUS, fmtCost, fmtDate, h, statusLabel } from "./util.js";

let ctx = null, q = "", profile = "", status = "";

export function toggleHistory(context) {
  ctx = context;
  const el = document.getElementById("history");
  if (!el.hidden) { el.hidden = true; return; }
  el.hidden = false;
  renderHistory();
  el.querySelector("input")?.focus();
}

export function renderHistory() {
  const el = document.getElementById("history");
  if (!ctx || el.hidden) return;
  const search = h("input", { type: "text", placeholder: "Rechercher dans les demandes…", value: q });
  search.addEventListener("input", () => { q = search.value; renderList(); });
  const prof = h("select", {}, h("option", { value: "" }, "Tous les profils"), ...ctx.profiles().map((p) => h("option", { value: p.id }, p.name)));
  prof.value = profile;
  prof.addEventListener("change", () => { profile = prof.value; renderList(); });
  const st = h("select", {}, h("option", { value: "" }, "Tous les statuts"), ...Object.entries(STATUS).map(([k, v]) => h("option", { value: k }, v)));
  st.value = status;
  st.addEventListener("change", () => { status = st.value; renderList(); });
  const list = h("div", { class: "drawer-list" });
  function renderList() {
    const needle = q.trim().toLowerCase();
    const rows = ctx.tasks().filter((t) => (!profile || t.profile === profile) && (!status || t.status === status)
      && (!needle || `${t.title} ${t.prompt}`.toLowerCase().includes(needle)));
    list.replaceChildren(...(rows.length ? rows.map((t) => h("div", { class: "hrow", style: { "--pc": t.color }, tabindex: "0",
      on: { click: () => ctx.openTask(t.id), keydown: (e) => { if (e.key === "Enter") ctx.openTask(t.id); } } },
      h("div", { class: "hm" }, h("div", { class: "ht", title: t.prompt }, t.title),
        h("div", { class: "hs" }, [t.profile_name, t.preset_name, fmtDate(t.created), fmtCost(t.cost_usd)].filter(Boolean).join(" · "))),
      h("span", { class: `chip s-${t.status}` }, statusLabel(t))))
      : [h("div", { class: "hrow" }, h("span", { class: "muted" }, "Aucune tâche."))]));
  }
  renderList();
  el.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Historique"),
      h("button", { type: "button", class: "btn small", on: { click: () => download("/api/history/export", "historique.json") } }, "Exporter"),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { el.hidden = true; } } })),
    h("div", { class: "drawer-filters" }, search, prof, st), list);
}
