// The account's actions (docs/boite-de-reception.md): the commands and skills of an account's configuration
// folder (~/.claude-work/commands, …/skills), as buttons once the user turned them on for that account
// (Configuration → Profils). Same rules as a project's actions (actions.js): read and validated first,
// pinned by fingerprint, asked again once changed. A launch is a normal discussion of the account, in the
// folder chosen in the bar (a project brings its preset, model and effort).
import { api, ApiError } from "./api.js";
import { reviewAction } from "./actions.js";
import { tintOf } from "./tint.js";
import { dialog, h, toast } from "./util.js";

const lists = new Map();      // profile id -> its actions (without their content)
const listeners = new Set();

export const accountActions = (pid) => lists.get(pid) || [];
export function onAccountActions(fn) { listeners.add(fn); }

/** The actions of every account that has them on (at start, after a change of the configuration or of an action). */
export async function loadAccountActions(profiles) {
  await Promise.all(profiles.map(async (p) => {
    if (!p.account_actions) { lists.delete(p.id); return; }
    try { lists.set(p.id, (await api(`/api/accounts/${p.id}/actions`)).actions || []); } catch { /* keep the last list */ }
  }));
  for (const fn of listeners) fn();
}

const WHY = "Une action du compte vaut pour tous ses dossiers : Claude suivra ces consignes avec les autorisations du dossier où tu la lances. "
  + "Toute modification future du fichier demandera une nouvelle validation.";

async function fullAction(pid, name) {
  const { actions } = await api(`/api/accounts/${pid}/actions?content=1`);
  return actions.find((x) => x.name === name) || null;
}

/** Read an action of the account (and validate it if it is not yet). Resolves true when validated now. */
export async function reviewAccountAction(profile, a) {
  const full = a.content ? a : await fullAction(profile.id, a.name).catch(() => null);
  if (!full) { toast(`Action introuvable : /${a.name}`, "err"); return false; }
  const tint = tintOf(profile.color);
  if (full.status === "ok") { await reviewAction(full, { readOnly: true, tint }); return false; }
  if (!(await reviewAction(full, { tint, why: WHY }))) return false;
  try {
    await api(`/api/accounts/${profile.id}/actions/approve`, { method: "POST", body: { name: full.name, hash: full.hash } });
    toast(`Action /${full.name} validée pour le compte ${profile.name}.`, "ok");
    return true;
  } catch (e) { toast(e.message, e instanceof ApiError && e.status === 409 ? "warn" : "err"); return false; }
}

/** Launch an action of the account `profile` in `workdir`: its parameters asked if it takes some, its
 * validation if needed. ctx: onTask(t), launch(path, body) (the confirmations of a launch). */
export async function runAccountAction(ctx, profile, a, { workdir = "", args = null } = {}) {
  const tint = tintOf(profile.color);
  let text = args;
  if (text === null && a.hint) {
    text = await dialog({ title: a.label, body: a.description || `/${a.name}`, input: { placeholder: a.hint }, tint,
      buttons: [{ label: "Annuler", value: null }, { label: "Lancer", value: true, cls: "primary" }] });
    if (text === null || text === undefined) return null;
  }
  const path = `/api/accounts/${profile.id}/actions/run`;
  const body = { name: a.name, arguments: (text || "").trim(), workdir };
  const review = async (full) => (await reviewAction(full, { launching: true, tint, why: WHY }) ? full.hash : null);
  if (a.status && a.status !== "ok") {
    const full = await fullAction(profile.id, a.name).catch(() => null);
    if (!full) { toast(`Action introuvable : /${a.name}`, "err"); return null; }
    if (full.status !== "ok") {
      body.approve = await review(full);
      if (!body.approve) return null;
    }
  }
  try {
    const t = await api(path, { method: "POST", body });
    ctx.onTask?.(t);
    return t;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.data?.need_approval && e.data.action) {
      const approve = await review(e.data.action);   // changed between the list and the click
      return approve ? ctx.launch(path, { ...body, approve }) : null;
    }
    if (e instanceof ApiError && e.status === 409 && e.data?.need_confirm) return ctx.launch(path, body);
    toast(e.message, "err");
    return null;
  }
}

const STATE = { ok: "validée", nouvelle: "à valider", modifiee: "modifiée depuis sa validation" };

/** The list of an account's actions (Configuration → Profils): read, validate, in the menu, launch. */
export function accountActionRows(ctx, profile, actions, refresh) {
  if (!actions.length) {
    return [h("div", { class: "empty-row" }, "Aucune commande ni skill dans le dossier de configuration du compte (commands/, skills/).")];
  }
  return actions.map((a) => {
    const menu = h("input", { type: "checkbox", title: "Un bouton dans le menu JARVIS et sur l'accueil" });
    menu.checked = !!a.menu;
    menu.addEventListener("change", async () => {
      try {
        await api(`/api/accounts/${profile.id}/actions/settings`, { method: "POST", body: { name: a.name, model: a.model, effort: a.effort, menu: menu.checked } });
      } catch (e) { toast(e.message, "err"); menu.checked = !menu.checked; }
      refresh();
    });
    const btn = (label, fn, cls = "ghost") => h("button", { type: "button", class: `btn small ${cls}`, on: { click: fn } }, label);
    return h("div", { class: `hrow file act-row s-${a.status}`, style: { "--pc": a.status === "ok" ? profile.color : "var(--warn)" } },
      h("span", { class: "i", svg: "bolt" }),
      h("div", { class: "hm" }, h("div", { class: "ht" }, a.label, a.status !== "ok" ? h("span", { class: "act-st" }, STATE[a.status]) : null),
        h("div", { class: "hs" }, [`/${a.name}${a.hint ? ` ${a.hint}` : ""}`, a.description].filter(Boolean).join(" · "))),
      h("label", { class: "check act-menu" }, menu, "Dans le menu"),
      btn(a.status === "ok" ? "Voir" : "Relire", async () => { if (await reviewAccountAction(profile, a)) refresh(); }),
      btn("Lancer", () => runAccountAction(ctx, profile, a, { workdir: ctx.workdir?.() || "" }), "primary"));
  });
}
