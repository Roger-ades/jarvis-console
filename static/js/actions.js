// Project actions: the commands (.claude/commands/*.md) and skills (.claude/skills/*/SKILL.md) of a
// project folder, shown as buttons. Each one is read and validated by the user first, pinned by its
// fingerprint: once changed (by Claude or anyone), it asks again. A launch is a normal discussion of
// the project ("/nom arguments") with the project's account and preset, and the model and effort
// chosen for the action (else the project's). Its launches are listed under it.
import { api, ApiError } from "./api.js";
import { accountActionRows } from "./accountactions.js";
import { STATUS, dialog, fmtDate, h, modelName, toast } from "./util.js";
import { openRoutineEditor, setRoutineEnabled } from "./routines.js";
import { projectTint } from "./tint.js";

const STATE = { ok: "validée", nouvelle: "à valider", modifiee: "modifiée depuis sa validation" };

/** What the project panel compares to know the Actions tab changed. account: the list, null if that
 * fetch failed, omitted when the account has no actions of its own. */
export function actionStamp(actions, routines, account) {
  const one = (a) => [a.name, a.hash, a.status, a.model || "", a.effort || "",
    (a.runs || []).map((r) => `${r.id}:${r.status}:${r.created}`).join(",")].join("\t");
  const rout = (r) => [r.id, r.name, r.enabled ? 1 : 0, r.next_run || "", r.schedule_label || ""].join("\t");
  const tail = account === undefined ? "" : account === null ? "\u0000" : account.map(one).join("\n");
  return [(actions || []).map(one).join("\n"), (routines || []).map(rout).join("\n"), tail].join("\f");
}
const KIND = { commande: "Commande", skill: "Skill" };
const EFFORT = { "": "Par défaut", low: "Faible", medium: "Moyen", high: "Élevé", xhigh: "Très élevé", max: "Max" };
const ORIGIN = { action: "bouton", routine: "routine" };
const opened = new Set(); // actions whose details are shown, kept across refreshes

async function fullAction(folder, name) {
  const { actions } = await api(`/api/projects/actions?${new URLSearchParams({ folder })}`);
  return actions.find((x) => x.name === name) || null;
}

/** "Opus 5.5 · défaut du compte Perso": the model a launch uses when the action has none of its own. */
function inheritedLabel(inh) {
  if (!inh) return "celui du projet";
  const name = inh.model === "default" ? (inh.resolved ? modelName(inh.resolved) : "modèle par défaut de Claude Code") : modelName(inh.resolved || inh.model);
  const from = inh.source === "projet" ? "réglé sur le projet" : inh.source === "autorisations" ? "imposé par les autorisations"
    : inh.model === "default" ? `défaut de Claude Code sur le compte ${inh.profile_name}` : `défaut du compte ${inh.profile_name}`;
  return `${name} · ${from}`;
}

/** The model of the action's launches, in a few words. */
function modelLabel(ctx, a) {
  if (!a.model) return inheritedLabel(a.inherited);
  const opt = ctx.models?.().find(([v]) => v === a.model);
  return `${opt ? opt[1] : modelName(a.model)} · choisi pour l'action`;
}

/** What the action makes Claude do, read before it is validated. Resolves true when validated. */
export function reviewAction(a, { launching = false, readOnly = false, tint = null, why: note = null } = {}) {
  const why = readOnly ? "Action validée : ce que Claude suit quand tu la lances. Une modification du fichier demandera une nouvelle validation."
    : note && a.status !== "modifiee" ? note
    : a.status === "modifiee"
    ? "Son contenu a changé depuis ta dernière validation (par Claude ou par quelqu'un d'autre) : relis-le."
    : "Claude suivra ces consignes avec les autorisations du projet. Toute modification future demandera une nouvelle validation.";
  return dialog({
    title: readOnly ? `Action /${a.name}` : `${a.status === "modifiee" ? "Revalider" : "Valider"} l'action /${a.name}`,
    body: h("div", { class: "act-review" },
      h("p", { class: "prop-note" }, why),
      h("dl", { class: "prop-fields" },
        h("div", { class: "prop-field" }, h("dt", {}, "Bouton"), h("dd", {}, a.label)),
        h("div", { class: "prop-field" }, h("dt", {}, KIND[a.kind] || a.kind), h("dd", { class: "mono" }, `/${a.name}`)),
        a.hint ? h("div", { class: "prop-field" }, h("dt", {}, "À saisir"), h("dd", {}, a.hint)) : null,
        h("div", { class: "prop-field" }, h("dt", {}, "Fichier"), h("dd", { class: "mono", title: a.path }, a.path)),
        a.files?.length ? h("div", { class: "prop-field" }, h("dt", {}, "Avec"), h("dd", { class: "mono" }, a.files.join("\n"))) : null),
      a.description ? h("p", {}, a.description) : null,
      h("pre", { class: "prop-code" }, a.content || "")),
    buttons: readOnly ? [{ label: "Fermer", value: false, cls: "primary" }]
      : [{ label: "Annuler", value: false }, { label: launching ? "Valider et lancer" : "Valider", value: true, cls: "primary" }],
    onOpen: (box) => box.classList.add("act-dialog"), tint,
  });
}

/** Launch an action of the project `folder`: its parameters asked if it takes some, its validation if needed. */
export async function runAction(ctx, folder, a, { args = null } = {}) {
  let text = args;
  const tint = projectTint(folder, ctx.currentProfile?.());
  if (text === null && a.hint) {
    text = await dialog({ title: a.label, body: a.description || `/${a.name}`, input: { placeholder: a.hint }, tint,
      buttons: [{ label: "Annuler", value: null }, { label: "Lancer", value: true, cls: "primary" }] });
    if (text === null || text === undefined) return null;
  }
  const body = { folder, name: a.name, arguments: (text || "").trim(), profile: ctx.currentProfile?.() };
  if (a.status && a.status !== "ok") {
    let full;
    try { full = await fullAction(folder, a.name); } catch (e) { toast(e.message, "err"); return null; }
    if (!full) { toast(`Action introuvable : /${a.name}`, "err"); return null; }
    if (full.status !== "ok") {
      if (!(await reviewAction(full, { launching: true, tint }))) return null;
      body.approve = full.hash;
    }
  }
  try {
    const t = await api("/api/projects/actions/run", { method: "POST", body });
    ctx.onTask?.(t);
    return t;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.data?.need_approval && e.data.action) {
      // changed between the list and the click: show the new content
      if (!(await reviewAction(e.data.action, { launching: true, tint }))) return null;
      return ctx.launch("/api/projects/actions/run", { ...body, approve: e.data.action.hash });
    }
    if (e instanceof ApiError && e.status === 409 && e.data?.need_confirm) return ctx.launch("/api/projects/actions/run", body);
    toast(e.message, "err");
    return null;
  }
}

/** The "Actions" tab of the project panel: its buttons and its routines. */
export async function renderActions(body, ctx, ws, refresh) {
  const proj = ctx.project(ws.folder);
  if (!proj) {
    body.dataset.live = "noproj";
    body.append(h("p", { class: "pj-lead pad" }, "Les actions et les routines appartiennent à un projet. ",
      "Donne d'abord un nom à ce dossier : ses commandes (.claude/commands) et ses skills (.claude/skills) deviendront des boutons."),
    h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary",
      on: { click: async () => { if (await ctx.editProject(ws.folder, ws.profile)) refresh(); } } }, "En faire un projet")));
    return;
  }
  body.append(h("p", { class: "pj-lead pad" }, "Les commandes et les skills du dossier, en boutons. Claude peut en proposer pendant une discussion : ",
    "rien n'est ajouté ni lancé sans ton accord, et une action modifiée doit être relue avant de resservir."));
  const list = h("div", { class: "drawer-list flat" }, h("div", { class: "empty-row" }, "Lecture des actions…"));
  const routines = h("div", { class: "drawer-list flat" });
  body.append(list, h("div", { class: "sec-title pad" }, "Routines du projet"), routines,
    h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small", on: { click: () => newRoutine(ctx, proj, ws, refresh) } },
      "Nouvelle routine")));
  let res;
  try { res = await api(`/api/projects/actions?${new URLSearchParams({ folder: proj.folder, profile: ctx.currentProfile?.() || "" })}`); }
  catch (e) {
    if (list.isConnected) list.replaceChildren(h("div", { class: "line err" }, e.message));
    if (body.isConnected) body.dataset.live = "error";
    return;
  }
  if (!list.isConnected) return;
  list.replaceChildren(...(res.actions.length ? res.actions.flatMap((a) => actionRow(ctx, proj, a, refresh))
    : [h("div", { class: "empty-row" }, "Aucune action. Demande à Claude d'en proposer une, ou ajoute un fichier dans .claude/commands du dossier.")]));
  routines.replaceChildren(...(res.routines.length ? res.routines.map((r) => routineRow(ctx, proj, r, refresh))
    : [h("div", { class: "empty-row" }, "Aucune routine pour ce projet.")]));
  // the actions of the account, launched in this project's folder (once the user turned them on)
  const acc = ctx.profiles().find((p) => p.id === (proj.profile || ctx.currentProfile?.()));
  let account;
  if (acc?.account_actions) {
    const own = h("div", { class: "drawer-list flat" });
    list.after(h("div", { class: "sec-title pad" }, `Du compte ${acc.name}`), own);
    try {
      account = (await api(`/api/accounts/${acc.id}/actions`)).actions || [];
      if (own.isConnected) own.replaceChildren(...accountActionRows({ ...ctx, workdir: () => proj.folder }, acc, account, refresh));
    } catch (e) {
      account = null;
      if (own.isConnected) own.replaceChildren(h("div", { class: "line err" }, e.message));
    }
  }
  if (body.isConnected) body.dataset.live = actionStamp(res.actions, res.routines, account);
}

function actionRow(ctx, proj, a, refresh) {
  const btn = (label, title, fn, cls = "ghost") => h("button", { type: "button", class: `btn small ${cls}`, title,
    on: { click: (e) => { e.stopPropagation(); fn(); } } }, label);
  const validate = async () => {
    if (!(await reviewAction(a, { tint: projectTint(proj.folder) }))) return;
    try {
      await api("/api/projects/actions/approve", { method: "POST", body: { folder: proj.folder, name: a.name, hash: a.hash } });
      toast(`Action /${a.name} validée.`, "ok");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) toast(e.message, "warn"); else toast(e.message, "err");
    }
    refresh();
  };
  const key = `${proj.folder}|${a.name}`;
  const runs = a.runs || [];
  const last = runs[0];
  const details = h("div", { class: "act-details", hidden: !opened.has(key) });
  const toggle = () => {
    details.hidden = !details.hidden;
    if (details.hidden) opened.delete(key); else opened.add(key);
    row.setAttribute("aria-expanded", String(!details.hidden));
    if (!details.hidden && !details.childElementCount) fillDetails(details, ctx, proj, a, refresh);
  };
  const row = h("div", { class: `hrow file act-row s-${a.status}`, tabindex: "0", "aria-expanded": String(opened.has(key)),
    title: "Modèle, effort et exécutions de l'action", style: { "--pc": a.status === "ok" ? "var(--accent)" : "var(--warn)" },
    on: { click: toggle, keydown: (e) => { if (e.key === "Enter" && e.target === row) toggle(); } } },
    h("span", { class: "i", svg: "bolt" }),
    h("div", { class: "hm" }, h("div", { class: "ht" }, a.label, a.status !== "ok" ? h("span", { class: "act-st" }, STATE[a.status]) : null),
      h("div", { class: "hs" }, [`/${a.name}${a.hint ? ` ${a.hint}` : ""}`, a.description].filter(Boolean).join(" · ")),
      h("div", { class: "hs act-meta" }, [modelLabel(ctx, a), a.effort ? `effort ${EFFORT[a.effort].toLowerCase()}` : "",
        runs.length ? `${runs.length} exécution${runs.length > 1 ? "s" : ""}` : "jamais lancée",
        last ? `dernière : ${STATUS[last.status] || last.status} le ${fmtDate(last.created)}` : ""].filter(Boolean).join(" · "))),
    a.status !== "ok" ? btn("Relire", "Voir ce que fait l'action et la valider", validate) : btn("Voir", "Voir ce que fait l'action", () => reviewAction(a, { readOnly: true, tint: projectTint(proj.folder) })),
    btn("Lancer", a.hint ? `Lancer (à saisir : ${a.hint})` : "Lancer une discussion avec cette action", () => runAction(ctx, proj.folder, a).then(refresh), "primary"));
  if (opened.has(key)) fillDetails(details, ctx, proj, a, refresh);
  return [row, details];
}

const runModel = (r) => r.model_resolved ? modelName(r.model_resolved) : r.model && r.model !== "default" ? modelName(r.model) : "";

/** Under an action: the model and effort of its launches, and the discussions it launched. */
function fillDetails(box, ctx, proj, a, refresh) {
  const sel = (value, options) => {
    const s = h("select", {}, ...options.map(([v, l]) => h("option", { value: v }, l)));
    s.value = options.some(([v]) => v === value) ? value : "";
    return s;
  };
  const inh = a.inherited || {};
  const models = (ctx.models?.() || []).filter(([v]) => v && v !== "default");
  if (a.model && !models.some(([v]) => v === a.model)) models.push([a.model, a.model]);
  const model = sel(a.model, [["", `Celui du projet : ${inheritedLabel(inh)}`], ...models]);
  const effort = sel(a.effort, [["", `Celui du projet : ${EFFORT[inh.effort || ""]}${inh.effort ? ` (${inh.effort_source === "projet" ? "réglé sur le projet" : `compte ${inh.profile_name}`})` : ""}`],
    ...Object.entries(EFFORT).filter(([v]) => v)]);
  const save = async () => {
    model.disabled = effort.disabled = true;
    try {
      await api("/api/projects/actions/settings", { method: "POST", body: { folder: proj.folder, name: a.name, model: model.value, effort: effort.value } });
      toast(`Réglages de /${a.name} enregistrés : ils servent dès le prochain lancement.`, "ok");
    } catch (e) { toast(e.message, "err"); }
    refresh();
  };
  model.addEventListener("change", save);
  effort.addEventListener("change", save);
  const runs = a.runs || [];
  box.replaceChildren(
    h("div", { class: "act-settings" },
      h("label", { class: "field" }, h("span", {}, "Modèle"), model),
      h("label", { class: "field" }, h("span", {}, "Effort"), effort)),
    h("div", { class: "act-runs-title" }, "Exécutions"),
    runs.length ? h("div", { class: "act-runs" }, ...runs.map((r) => h("button", { type: "button", class: "act-run", title: "Ouvrir la fenêtre de cette exécution",
      on: { click: () => ctx.openTask(r.id) } },
      h("span", { class: `run-st s-${r.status}` }, STATUS[r.status] || r.status),
      h("span", { class: "muted" }, fmtDate(r.created)),
      h("span", { class: "act-run-t" }, r.prompt && r.prompt !== `/${a.name}` ? r.prompt : r.title),
      h("span", { class: "muted" }, [runModel(r), ORIGIN[r.origin] || (r.origin ? "" : "saisie")].filter(Boolean).join(" · ")))))
      : h("div", { class: "empty-row" }, "Aucune exécution pour l'instant : chaque lancement (bouton, routine, Ctrl+K ou /" + a.name + " tapé) s'ajoute ici."));
}

function routineRow(ctx, proj, r, refresh) {
  const toggle = h("input", { type: "checkbox", title: r.enabled ? "Désactiver" : "Activer" });
  toggle.checked = r.enabled;
  toggle.addEventListener("click", (e) => e.stopPropagation());
  toggle.addEventListener("change", async () => {
    toggle.disabled = true;
    try { await setRoutineEnabled(r, toggle.checked); } catch (e) { toast(e.message, "err"); }
    refresh();
  });
  const last = r.runs?.[0];
  return h("div", { class: "hrow file", tabindex: "0", style: { "--pc": proj.color || "var(--accent)" },
    on: { click: () => openRoutineEditor(ctx, r, {}, refresh) } },
    h("span", { class: "i", svg: "clock" }),
    h("div", { class: "hm" }, h("div", { class: "ht" }, r.name),
      h("div", { class: "hs" }, [r.schedule_label, r.enabled && r.next_run ? `prochaine : ${fmtDate(r.next_run)}` : (r.enabled ? "" : "désactivée"),
        last ? `dernière : ${STATUS[last.status] || last.status}` : ""].filter(Boolean).join(" · "))),
    toggle);
}

function newRoutine(ctx, proj, ws, refresh) {
  openRoutineEditor(ctx, null, { workdir: proj.folder, profile: proj.profile || ws.profile, preset: proj.preset || "", model: proj.model || "" }, refresh);
}
