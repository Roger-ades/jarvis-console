// Routines: tasks the console launches on a schedule.
import { api } from "./api.js";
import { $, STATUS, confirmDialog, fmtDate, h, modalHost, toast } from "./util.js";

let ctx = null, data = { routines: [], startup: false };
const cloud = {}; // profile id -> { loading, error, res }
const el = () => $("#routines");
const DAYS = ["L", "M", "M", "J", "V", "S", "D"];
const DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const RUN_LABEL = { ...STATUS, "lancée": "lancée", "non lancée": "non lancée", "manquée": "manquée" };

export function toggleRoutines(context) {
  ctx = context;
  const d = el();
  if (!d.hidden) { d.hidden = true; return; }
  ctx.closeDrawers?.("routines");
  d.hidden = false;
  load();
}

export function routinesChanged(payload) {
  if (payload?.routines) data.routines = payload.routines;
  if (!el().hidden) render();
}

async function load() {
  try { data = await api("/api/routines"); } catch (e) { toast(e.message, "err"); }
  render();
  for (const p of ctx.profiles()) if (!cloud[p.id]) loadCloud(p.id, false);
}

async function loadCloud(pid, refresh) {
  cloud[pid] = { ...(cloud[pid] || {}), loading: true, error: "" };
  render();
  try { cloud[pid].res = await api(`/api/cloud-routines?profile=${encodeURIComponent(pid)}&refresh=${refresh ? 1 : 0}`); }
  catch (e) { cloud[pid].error = e.message; }
  cloud[pid].loading = false;
  render();
}

function localTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : fmtDate(d.getTime() / 1000);
}

function cloudSection() {
  const blocks = ctx.profiles().map((p) => {
    const st = cloud[p.id] || {};
    const rows = st.res?.routines || [];
    const head = h("div", { class: "cloud-head", style: { "--pc": p.color } },
      h("span", { class: "sw" }), h("b", {}, p.name),
      h("span", { class: "muted" }, st.loading ? "lecture via Claude Code…" : st.res ? `${rows.length} routine(s) · lues ${fmtDate(st.res.fetched)}` : ""),
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn small ghost", disabled: st.loading, title: "Relire la liste sur claude.ai (quelques secondes)",
        on: { click: () => loadCloud(p.id, true) } }, st.loading ? "…" : "Actualiser"),
      h("button", { type: "button", class: "btn small ghost", title: "Créer une routine claude.ai avec la skill /schedule",
        on: { click: () => ctx.compose(p.id, "/schedule ") } }, "Nouvelle"));
    const list = rows.map((r) => {
      const toggle = h("input", { type: "checkbox", title: r.enabled ? "Mettre en pause" : "Réactiver" });
      toggle.checked = r.enabled;
      toggle.addEventListener("click", (e) => e.stopPropagation());
      toggle.addEventListener("change", async () => {
        toggle.disabled = true;
        try { await api(`/api/cloud-routines/${p.id}/${r.id}/toggle`, { method: "POST", body: { enabled: toggle.checked } }); await loadCloud(p.id, false); }
        catch (e) { toast(e.message, "err"); toggle.checked = !toggle.checked; }
        toggle.disabled = false;
      });
      return h("div", { class: "hrow", style: { "--pc": p.color }, tabindex: "0", on: { click: () => cloudDetail(p, r) } },
        h("div", { class: "hm" }, h("div", { class: "ht" }, r.name),
          h("div", { class: "hs" }, [r.schedule_label, r.enabled && r.next_run_at ? `prochaine : ${localTime(r.next_run_at)}` : (r.enabled ? "" : "en pause"),
            r.device ? `liée à ${r.device}` : "", r.kind === "cowork_task" ? "Cowork" : ""].filter(Boolean).join(" · "))),
        h("button", { type: "button", class: "btn small", title: "Lancer maintenant sur claude.ai", on: { click: async (e) => {
          e.stopPropagation();
          try { await api(`/api/cloud-routines/${p.id}/${r.id}/run`, { method: "POST" }); toast(`« ${r.name} » lancée sur claude.ai.`, "ok"); }
          catch (err) { toast(err.message, "err"); }
        } } }, "Lancer"),
        toggle);
    });
    return h("div", { class: "cloud-block" }, head,
      st.error ? h("div", { class: "line err" }, st.error) : null,
      ...list,
      !st.loading && st.res && !rows.length ? h("div", { class: "empty-row" }, "Aucune routine visible avec la connexion Claude Code de ce compte.") : null);
  });
  return h("div", { class: "section cloud" },
    h("div", { class: "sec-title" }, "Routines claude.ai"),
    h("div", { class: "drawer-note" }, "Exécutées dans le cloud par claude.ai. Les tâches planifiées créées depuis Cowork (Claude Desktop) ne sont renvoyées qu'à l'app desktop : gère-les dans Claude Desktop. Celles créées avec Claude Code (/schedule) apparaissent ici."),
    ...blocks);
}

async function cloudDetail(p, r) {
  const runs = h("div", {}, h("div", { class: "muted" }, "Lecture des exécutions…"));
  const box = h("div", { class: "dialog routine-ed", role: "dialog", "aria-modal": "true" },
    h("h3", {}, r.name),
    h("div", { class: "dialog-body" },
      h("dl", { class: "kvs" },
        h("dt", {}, "Compte"), h("dd", {}, p.name),
        h("dt", {}, "Planification"), h("dd", {}, `${r.schedule_label} (${r.cron} UTC)`),
        h("dt", {}, "Prochaine"), h("dd", {}, localTime(r.next_run_at) || "—"),
        h("dt", {}, "Dernière"), h("dd", {}, localTime(r.last_fired_at) || "—"),
        h("dt", {}, "Modèle"), h("dd", {}, r.model || "—"),
        h("dt", {}, "Autorisations"), h("dd", {}, r.permission_mode || "—"),
        h("dt", {}, "Dossiers"), h("dd", {}, (r.folders || []).join("\n") || "—"),
        r.device ? h("dt", {}, "Appareil") : null, r.device ? h("dd", {}, r.device) : null,
        r.suspension ? h("dt", {}, "Suspendue") : null, r.suspension ? h("dd", {}, r.suspension) : null),
      r.prompt ? h("details", {}, h("summary", { class: "muted" }, "Consigne"), h("pre", {}, r.prompt)) : null,
      h("div", { class: "section" }, h("h3", {}, "Exécutions récentes"), runs)),
    h("div", { class: "dialog-actions" },
      h("a", { class: "btn ghost", href: "https://claude.ai/code", target: "_blank", rel: "noopener noreferrer" }, "Ouvrir claude.ai"),
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn primary", on: { click: () => overlay.remove() } }, "Fermer")));
  const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) overlay.remove(); } } }, box);
  modalHost("dialog").root.append(overlay);
  try {
    const { runs: items } = await api(`/api/cloud-routines/${p.id}/${r.id}/runs`);
    runs.replaceChildren(items.length ? h("table", { class: "tbl" }, h("tbody", {}, ...items.map((x) => h("tr", {},
      h("td", { class: "narrow" }, localTime(x.started_at || x.created_at)),
      h("td", { class: "narrow" }, ({ succeeded: "réussie", failed: "échouée", running: "en cours" })[x.status] || x.status || ""),
      h("td", {}, x.title || x.id))))) : h("div", { class: "muted" }, "Aucune exécution."));
  } catch (e) { runs.replaceChildren(h("div", { class: "line err" }, e.message)); }
}

function render() {
  const d = el();
  if (d.hidden) return;
  const startup = h("input", { type: "checkbox" });
  startup.checked = !!data.startup;
  startup.addEventListener("change", async () => {
    try { data.startup = (await api("/api/system/startup", { method: "POST", body: { on: startup.checked } })).startup; toast(data.startup ? "La console démarrera à l'ouverture de session." : "Démarrage automatique retiré.", "ok"); }
    catch (e) { toast(e.message, "err"); startup.checked = !startup.checked; }
  });
  const list = h("div", { class: "drawer-list" }, cloudSection(),
    h("div", { class: "sec-title" }, "Routines de la console"),
    ...(data.routines.length ? data.routines.map(row)
      : [h("div", { class: "empty-row" }, "Aucune routine locale. Elle lance une demande sur ce PC à heure fixe, avec son compte et ses autorisations.")]));
  d.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Routines"),
      h("button", { type: "button", class: "btn small primary", on: { click: () => editor() } }, "Nouvelle routine"),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } })),
    h("div", { class: "drawer-note" },
      h("label", { class: "check" }, startup, "Lancer la console à l'ouverture de session (Windows ou macOS), sans fenêtre"),
      h("div", {}, "Les routines tournent tant que la console est lancée. Chaque exécution est une tâche normale : mêmes autorisations, mêmes validations, visible dans l'historique.")),
    list);
}

function row(r) {
  const prof = ctx.profiles().find((p) => p.id === r.profile);
  const last = r.runs?.[0];
  const toggle = h("input", { type: "checkbox", title: r.enabled ? "Désactiver" : "Activer" });
  toggle.checked = r.enabled;
  toggle.addEventListener("click", (e) => e.stopPropagation());
  toggle.addEventListener("change", () => save({ ...r, enabled: toggle.checked }));
  return h("div", { class: "hrow", style: { "--pc": prof?.color || "#72c9ff" }, tabindex: "0", on: { click: () => editor(r) } },
    h("div", { class: "hm" },
      h("div", { class: "ht" }, r.name),
      h("div", { class: "hs" }, [prof?.name, r.schedule_label, r.enabled && r.next_run ? `prochaine : ${fmtDate(r.next_run)}` : (r.enabled ? "" : "désactivée")].filter(Boolean).join(" · ")),
      last ? h("div", { class: "hs" }, `Dernière : ${fmtDate(last.ts)} · `, h("span", { class: `run-st s-${last.status}` }, RUN_LABEL[last.status] || last.status),
        last.error ? ` — ${last.error}` : "") : null),
    h("button", { type: "button", class: "btn small", title: "Lancer maintenant", on: { click: (e) => { e.stopPropagation(); runNow(r); } } }, "Lancer"),
    toggle);
}

async function runNow(r) {
  try {
    const t = await api(`/api/routines/${r.id}/run`, { method: "POST" });
    toast(`Routine « ${r.name} » lancée.`, "ok");
    if (t?.id) ctx.onTask?.(t);
  } catch (e) { toast(e.message, "err"); }
}

async function save(r) {
  try { await api("/api/routines", { method: "POST", body: r }); await load(); return true; }
  catch (e) { toast(e.message, "err"); return false; }
}

/** The routine editor, also opened from a project page (its folder already filled in); `after` runs once saved or deleted. */
export function openRoutineEditor(context, r = null, defaults = {}, after = null) {
  ctx = context;
  editor(r, defaults, after);
}

/** A routine switched on or off from elsewhere (a project page). */
export async function setRoutineEnabled(r, enabled) {
  await api("/api/routines", { method: "POST", body: { ...r, enabled } });
}

function editor(r = null, defaults = {}, after = null) {
  const done = after || load;
  const profiles = ctx.profiles();
  const presets = ctx.presets().filter((p) => p.enabled && !p.require_confirm);
  const cur = r || { name: "", prompt: "", profile: ctx.currentProfile(), preset: "", model: "", effort: "", workdir: "",
    schedule: { kind: "daily", time: "08:00", days: [0, 1, 2, 3, 4], every_min: 60, at: null }, enabled: true, open_window: true, catch_up: false, ...defaults };
  const sch = { kind: "daily", time: "08:00", days: [0, 1, 2, 3, 4], every_min: 60, at: null, ...cur.schedule };
  const field = (label, control, help) => h("label", { class: "field" }, h("span", {}, label), control, help ? h("small", {}, help) : null);
  const name = h("input", { type: "text", value: cur.name, placeholder: "Ex. Résumé des mails du matin" });
  const prompt = h("textarea", { rows: "4", class: "prose", placeholder: "La demande envoyée à Claude à chaque exécution" });
  prompt.value = cur.prompt;
  const prof = h("select", {}, ...profiles.map((p) => h("option", { value: p.id }, p.name)));
  prof.value = cur.profile || profiles[0]?.id;
  const pre = h("select", {}, ...presets.map((p) => h("option", { value: p.id }, p.name)));
  const syncPreset = () => { pre.value = cur.preset && presets.some((p) => p.id === cur.preset) ? cur.preset : (profiles.find((p) => p.id === prof.value)?.default_preset || presets[0]?.id); };
  syncPreset();
  prof.addEventListener("change", () => { cur.preset = ""; syncPreset(); });
  const model = h("select", {}, ...ctx.models().map(([v, l]) => h("option", { value: v }, l)));
  model.value = cur.model || "";
  const effort = h("select", {}, ...[["", "défaut du profil"], ["low", "faible"], ["medium", "moyen"], ["high", "élevé"], ["xhigh", "très élevé"], ["max", "max"]].map(([v, l]) => h("option", { value: v }, l)));
  effort.value = cur.effort || "";
  const workdir = h("input", { type: "text", value: cur.workdir, placeholder: "vide = dossier du profil" });
  const kind = h("select", {}, h("option", { value: "daily" }, "Chaque jour à heure fixe"), h("option", { value: "interval" }, "À intervalle régulier"), h("option", { value: "once" }, "Une seule fois"));
  kind.value = sch.kind;
  const time = h("input", { type: "time", value: sch.time });
  const days = new Set(sch.days);
  const dayBtns = h("div", { class: "days" }, ...DAYS.map((l, i) => {
    const b = h("button", { type: "button", class: `day${days.has(i) ? " on" : ""}`, title: DAY_NAMES[i] }, l);
    b.addEventListener("click", () => { days.has(i) ? days.delete(i) : days.add(i); b.classList.toggle("on", days.has(i)); });
    return b;
  }));
  const every = h("input", { type: "number", min: "5", value: String(sch.every_min % 60 === 0 && sch.every_min >= 60 ? sch.every_min / 60 : sch.every_min) });
  const unit = h("select", {}, h("option", { value: "60" }, "heures"), h("option", { value: "1" }, "minutes"));
  unit.value = sch.every_min % 60 === 0 && sch.every_min >= 60 ? "60" : "1";
  const at = h("input", { type: "datetime-local" });
  if (sch.at) { const dt = new Date(sch.at * 1000); at.value = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
  const daily = h("div", { class: "row" }, time, dayBtns);
  const interval = h("div", { class: "row" }, h("span", {}, "toutes les"), every, unit);
  const once = h("div", { class: "row" }, at);
  const showKind = () => { daily.hidden = kind.value !== "daily"; interval.hidden = kind.value !== "interval"; once.hidden = kind.value !== "once"; };
  kind.addEventListener("change", showKind);
  showKind();
  const cb = (label, value, help) => {
    const i = h("input", { type: "checkbox" });
    i.checked = value;
    return [i, h("div", { class: "field" }, h("label", { class: "check" }, i, h("span", {}, label)), help ? h("small", {}, help) : null)];
  };
  const [enabled, enabledEl] = cb("Routine active", cur.enabled);
  const [openWin, openWinEl] = cb("Ouvrir une fenêtre à chaque exécution", cur.open_window, "Sinon la tâche tourne en arrière-plan (historique, notifications).");
  const [catchUp, catchUpEl] = cb("Rattraper une exécution manquée", cur.catch_up, "Si la console était arrêtée à l'heure prévue, lancer au démarrage suivant.");
  const [teamCb, teamEl] = cb("Mode équipe", !!cur.team, "Le modèle choisi dirige et délègue à des sous-agents (Configuration → Modèles).");
  const err = h("div", { class: "line err", hidden: true });
  const { root, doc } = modalHost("dialog");
  const close = () => { overlay.remove(); doc.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape" && !doc.querySelector(".dialog:not(.routine-ed)")) close(); };
  const submit = async () => {
    const schedule = { kind: kind.value, time: time.value || "08:00", days: [...days].sort(), every_min: Math.round(Number(every.value || 60) * Number(unit.value)),
      at: at.value ? new Date(at.value).getTime() / 1000 : null };
    const body = { ...(r ? { id: r.id } : {}), name: name.value.trim(), prompt: prompt.value.trim(), profile: prof.value, preset: pre.value,
      model: model.value, effort: effort.value, workdir: workdir.value.trim(), schedule, enabled: enabled.checked,
      open_window: openWin.checked, catch_up: catchUp.checked, team: teamCb.checked };
    try {
      await api("/api/routines", { method: "POST", body });
      close();
      await done();
      toast("Routine enregistrée.", "ok");
    } catch (e) { err.hidden = false; err.textContent = e.message; }
  };
  const remove = async () => {
    if (!(await confirmDialog("Supprimer la routine ?", `« ${r.name} » ne sera plus lancée. Les tâches déjà exécutées restent dans l'historique.`, "Supprimer", "danger"))) return;
    try { await api(`/api/routines/${r.id}`, { method: "DELETE" }); close(); await done(); } catch (e) { toast(e.message, "err"); }
  };
  const box = h("div", { class: "dialog routine-ed", role: "dialog", "aria-modal": "true" },
    h("h3", {}, r ? "Modifier la routine" : "Nouvelle routine"),
    h("div", { class: "dialog-body" },
      h("div", { class: "grid" },
        field("Nom", name), field("Compte", prof),
        h("label", { class: "field wide" }, h("span", {}, "Demande"), prompt, h("small", {}, "Astuce : une skill fonctionne aussi, par exemple /deep-research …")),
        field("Autorisations", pre, "Les presets à confirmation (Complet) sont exclus."), field("Modèle", model),
        field("Effort", effort), field("Dossier de travail", workdir),
        h("label", { class: "field wide" }, h("span", {}, "Planification"), kind, daily, interval, once),
        enabledEl, openWinEl, catchUpEl, teamEl),
      err,
      r?.runs?.length ? h("div", { class: "section" }, h("h3", {}, "Dernières exécutions"),
        h("table", { class: "tbl" }, h("tbody", {}, ...r.runs.slice(0, 8).map((x) => h("tr", {},
          h("td", { class: "narrow" }, fmtDate(x.ts)), h("td", { class: "narrow" }, h("span", { class: `run-st s-${x.status}` }, RUN_LABEL[x.status] || x.status)),
          h("td", {}, x.manual ? "manuelle" : "planifiée"), h("td", { class: "muted" }, x.error || "")))))) : null),
    h("div", { class: "dialog-actions" },
      r ? h("button", { type: "button", class: "btn danger", on: { click: remove } }, "Supprimer") : null,
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "btn ghost", on: { click: close } }, "Annuler"),
      h("button", { type: "button", class: "btn primary", on: { click: submit } }, "Enregistrer")));
  const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) close(); } } }, box);
  root.append(overlay);
  doc.addEventListener("keydown", onKey, true);
  name.focus();
}
