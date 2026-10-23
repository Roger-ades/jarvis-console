// Office 365 in the project's Suivi tab: mail drafts and follow-ups (console/office_link.py).
// To Do is not read: the connector does not provide it. A draft is never sent.
import { api } from "./api.js";
import { dialog, fmtDate, h, toast } from "./util.js";
import { openPreview } from "./viewer.js";

const shown = new Map();

export function renderOffice(body, folder, { rerender }) {
  const box = h("div", { class: "office-block" }, h("div", { class: "empty-row" }, "Lecture…"));
  body.append(box);
  const draw = (st) => {
    shown.set(folder, st);
    box.replaceChildren(view(st, folder, rerender));
  };
  const cached = shown.get(folder);
  if (cached) draw(cached);
  api(`/api/projects/office?${new URLSearchParams({ folder })}`).then((st) => {
    draw(st);
    if (!st.running && (!st.updated || st.stale) && !st.error) refresh(folder).then(() => { if (box.isConnected) draw(shown.get(folder)); });
  }).catch((e) => box.replaceChildren(h("div", { class: "line err" }, e.message)));
}

async function refresh(folder) {
  try { shown.set(folder, await api("/api/projects/office/refresh", { method: "POST", body: { folder } })); }
  catch (e) { toast(e.message, "err"); }
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return fmtDate(ts);
}

function view(st, folder, rerender) {
  const n = (st.drafts?.length || 0) + (st.followups?.length || 0);
  const state = st.running ? "Lecture d'Office 365 en cours…" : st.updated ? `À jour ${ago(st.updated)}` : "";
  const head = h("div", { class: "row odoo-bar" },
    h("b", {}, n ? `${n} élément${n > 1 ? "s" : ""}` : "Office 365"),
    h("span", { class: "muted" }, state),
    h("span", { class: "grow" }),
    h("button", { type: "button", class: "btn small", disabled: !!st.running,
      title: "Relire les brouillons et les relances (lecture seule, rien n'est envoyé)",
      on: { click: async () => { await refresh(folder); rerender(); } } }, "Actualiser"));
  const lead = h("p", { class: "pj-lead" }, "Les mails de ce projet laissés en brouillon, et les relances. "
    + "La lecture passe par Outlook. Microsoft To Do n'est pas lu : le connecteur ne le fournit pas. Un brouillon n'est jamais envoyé.");
  const err = st.error ? h("div", { class: "line err" }, st.error) : null;
  const groups = [
    group("Brouillons", st.drafts, (t) => [t.to, t.date ? fr(t.date) : ""].filter(Boolean).join(" · "), "mail"),
    group("Relances", st.followups, (t) => [t.who, t.since ? `depuis le ${fr(t.since)}` : ""].filter(Boolean).join(" · "), "alert"),
  ].filter(Boolean);
  const empty = !st.running && st.updated && !st.error && !groups.length
    ? h("div", { class: "empty-row" }, "Aucun brouillon ni relance pour ce projet.") : null;
  const wait = st.running && !groups.length ? h("div", { class: "empty-row" }, "Lecture d'Office 365 en cours…") : null;
  return h("section", { class: "pj-card" }, h("h3", {}, "Office 365"), lead, head, err, ...groups, empty, wait);
}

function fr(day) { return day.split("-").reverse().join("/"); }

function group(title, rows, sub, icon, onClose = null) {
  if (!rows?.length) return null;
  const today = new Date().toISOString().slice(0, 10);
  return h("div", { class: "office-group" }, h("div", { class: "sec-title" }, `${title} · ${rows.length}`),
    h("div", { class: "drawer-list flat odoo-tree" }, ...rows.map((t) => row(t, sub(t), icon, today, onClose))));
}

function row(t, sub, icon, today, onClose = null) {
  const late = t.deadline && t.deadline < today;
  const kind = icon === "mail" ? "brouillon" : icon === "alert" ? "relance" : "tâche";
  const detail = [`${kind} Office 365 « ${t.title} »`, sub].filter(Boolean).join("\n");
  return h("div", {
    class: `hrow odoo-task${late ? " late" : ""}`,
    style: { "--pc": late || t.priority ? "var(--warn)" : "var(--accent)" },
    title: t.url ? "Ouvrir · clic droit : demander à Claude" : "Clic droit : demander à Claude",
    "data-pick": detail, "data-pick-label": `${kind} « ${t.title} »`,
    on: { click: () => { if (t.url) openPreview({ url: t.url, kind: "web" }); } } },
    h("span", { class: "i", svg: t.priority ? "alert" : icon }),
    h("div", { class: "hm" }, h("div", { class: "ht" }, t.title), sub ? h("div", { class: "hs" }, sub) : null),
    onClose ? h("button", { type: "button", class: "btn small ghost", title: "Retirer de cette liste, sans envoyer de mail",
      on: { click: (e) => { e.stopPropagation(); onClose(t); } } }, "Clôturer") : null);
}

/** The console read Office 365 again (event office_suivi): the tab follows. */
export function officeChanged(folder, isShown, rerender) {
  shown.delete(folder);
  if (isShown) rerender();
}

// The account's own feed, outside a project. To Do is not on the connector: local tasks stay in the
// console, and closing one only hides it here. Mail is never sent. The list still comes from one short
// read-only discussion.
let accountOpen = null;

export function officeAccountChanged(profile) {
  if (accountOpen?.profile === profile && accountOpen.box.isConnected) accountOpen.draw();
}

export async function openAccount(profile) {
  const box = h("div", { class: "office-block" }, h("div", { class: "empty-row" }, "Lecture…"));
  const draw = async () => {
    let st;
    try { st = await api(`/api/office/account?${new URLSearchParams({ profile })}`); }
    catch (e) { box.replaceChildren(h("div", { class: "line err" }, e.message)); return; }
    box.replaceChildren(accountView(st, profile, draw));
  };
  accountOpen = { profile, box, draw };
  draw();
  await dialog({
    title: "Suivi du compte",
    body: box,
    buttons: [{ label: "Fermer", value: true, cls: "primary" }],
  });
  if (accountOpen?.box === box) accountOpen = null;
}

function accountView(st, profile, draw) {
  const n = (st.drafts?.length || 0) + (st.followups?.length || 0) + (st.local?.length || 0);
  const state = st.running ? "Lecture d'Outlook en cours…" : st.updated ? `À jour ${ago(st.updated)}` : "";
  const head = h("div", { class: "row odoo-bar" },
    h("b", {}, st.name || "Compte"),
    h("span", { class: "muted" }, state),
    h("span", { class: "grow" }),
    h("button", { type: "button", class: "btn small", disabled: !!st.running,
      title: "Relire les brouillons et les relances de toute la boîte (lecture seule, rien n'est envoyé)",
      on: { click: async () => {
        try { await api("/api/office/account/refresh", { method: "POST", body: { profile } }); }
        catch (e) { toast(e.message, "err"); }
        draw();
      } } }, "Actualiser"),
    h("button", { type: "button", class: "btn small", title: "Une tâche gardée par la console, pas dans Microsoft To Do",
      on: { click: () => newLocal(profile, draw) } }, "Nouvelle tâche"));
  const lead = h("p", { class: "pj-lead" }, "Brouillons et relances de toute la boîte, lus par une courte discussion en lecture seule. "
    + "Microsoft To Do n'est pas disponible : une tâche créée ici reste dans la console. Clôturer retire l'élément de cette liste, sans envoyer de mail.");
  const err = st.error ? h("div", { class: "line err" }, st.error) : null;
  const groups = [
    group("Brouillons", st.drafts, (t) => [t.to, t.date ? fr(t.date) : ""].filter(Boolean).join(" · "), "mail", (t) => closeLocal(profile, t, draw)),
    group("Relances", st.followups, (t) => [t.who, t.since ? `depuis le ${fr(t.since)}` : ""].filter(Boolean).join(" · "), "alert", (t) => closeLocal(profile, t, draw)),
    group("Tâches de la console", st.local, () => "pas dans To Do", "list", (t) => closeLocal(profile, t, draw)),
  ].filter(Boolean);
  const empty = !st.running && !st.error && !groups.length
    ? h("div", { class: "empty-row" }, st.updated ? "Rien à suivre sur ce compte." : "Pas encore lu.") : null;
  return h("div", {}, lead, head, err, ...groups, empty);
}

async function closeLocal(profile, t, draw) {
  try {
    await api("/api/office/account/local", { method: "POST", body: { profile, id: t.id, done: true } });
    draw();
  } catch (e) { toast(e.message, "err"); }
}

async function newLocal(profile, draw) {
  const title = await dialog({
    title: "Tâche du compte",
    body: "Gardée par la console. Elle n'est pas créée dans Microsoft To Do, et aucun mail n'est envoyé.",
    input: { placeholder: "Titre" },
    buttons: [{ label: "Annuler", value: false }, { label: "Créer", value: true, cls: "primary" }],
  });
  if (!title) return;
  const label = String(title).trim();
  if (!label) return;
  try {
    await api("/api/office/account/local", { method: "POST", body: { profile, title: label } });
    draw();
  } catch (e) { toast(e.message, "err"); }
}
