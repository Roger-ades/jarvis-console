// The inbox (docs/boite-de-reception.md): what waits for the user, whatever the discussion, window,
// account or project. The server computes it (console/inbox.py) and publishes it ("inbox"); this page
// shows it, marks what the user read and decides like a window: an approval of a tool call is approved or
// refused here (Détail shows the call in full), what needs reading in full (a question, a plan, a
// proposal) opens its window. Claude never sees the inbox.
import { api } from "./api.js";
import { hasDisplay, renderDisplay, setAnswer, setDoc } from "./display.js";
import { accountState } from "./limits.js";
import { projectFor } from "./projects.js";
import { tintOf } from "./tint.js";
import { inputView } from "./taskwin.js";
import { $, fmtDate, h, noticeRoot, paint, toast, toolIcon, toolLabel } from "./util.js";

let ctx = null;
let data = { entries: [], counts: { todo: 0, read: 0, reminder: 0 } };
const filt = { profile: "", folder: "" };
const detail = new Set();        // approvals whose call is shown in full
const refusing = new Map();      // approvals whose "Refuser" asks for a message -> what is typed
const unfolded = new Set();      // the routines at the top shown in full
let focusRefusal = null;         // the message field to focus once (not at every redraw)
const busy = new Set();          // entries waiting for the server
const listeners = new Set();     // the JARVIS menu's summary, the top bar
const el = () => $("#inbox");
const APPROVALS = new Set(["hook", "permission", "question", "plan", "proposal"]);
const keyOf = (f) => String(f || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
const time = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

/** ctx: profiles(), openTask(id), retry(id), openRoutines(), editNote(note), notes(), routines(),
 * closeDrawers(except), openDisplay(taskId, key, color), reveal() (a notice shown: the desktop app's bar). */
export async function initInbox(context) {
  ctx = context;
  await refresh();
}

export async function refresh() {
  try { inboxChanged(await api("/api/inbox")); } catch { /* the stream brings it */ }
}

/** The server published the inbox (or a page asked for it). */
export function inboxChanged(d) {
  if (d?.entries) data = d;
  for (const id of [...busy]) if (!data.entries.some((e) => e.id === id)) busy.delete(id);
  rerender();
}

export const inboxCounts = () => data.counts || { todo: 0, read: 0, reminder: 0 };
export function onInbox(fn) { listeners.add(fn); }
export function rerender() {
  badge();
  if (el() && !el().hidden) render();
  for (const fn of listeners) fn();
}

/** The discussions whose end the user has not looked at (a window's pill shows it). */
const ENDED = new Set(["done", "error", "interrupted"]);
export function isUnread(taskId) {
  return data.entries.some((e) => (ENDED.has(e.kind) && e.task_id === taskId) || (e.kind === "routine" && e.task_ids?.includes(taskId)));
}

/** The user looked at these discussions: read. Only the unread ones are sent, unless `force` (a turn that
 * just ended under the user's eyes: the inbox may not have it yet). */
export function markTasksRead(ids, force = false) {
  const mine = ids.filter((id) => id && (force || isUnread(id)));
  if (!mine.length) return;
  data = { ...data, entries: data.entries.filter((e) => !(e.task_id && mine.includes(e.task_id) && ENDED.has(e.kind))) };
  api("/api/inbox/read", { method: "POST", body: { tasks: mine } }).catch(() => {});
  rerender();
}

// ------------------------------------------------------------ the top bar button
function badge() {
  const b = $("#btn-inbox .ib-count");   // (in the desktop app, the button is in the bar's menu)
  if (!b) return;
  const c = inboxCounts(), n = (c.todo || 0) + (c.read || 0) + (c.reminder || 0);
  b.hidden = !n;
  b.textContent = String(n);
  b.classList.toggle("urgent", !!(c.todo || c.reminder));
  b.closest("button").title = `Boîte de réception${n ? ` : ${summary(c)}` : " : rien n'attend"}`;
}

/** "2 à faire · 3 à lire · 1 rappel" */
export function summary(c = inboxCounts()) {
  return [c.todo ? `${c.todo} à faire` : "", c.read ? `${c.read} à lire` : "",
    c.reminder ? `${c.reminder} rappel${c.reminder > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ") || "rien n'attend";
}

// ------------------------------------------------------------ the summary (JARVIS menu, home screen)
/** The most urgent entries and "Tout voir": at the top of the JARVIS menu (desktop app) or of the home
 * screen (browser). A click opens the inbox on that entry. */
export function renderPeek() {
  const box = $("#inbox-peek");
  if (!box || !ctx) return;
  const all = (data.entries || []).filter((e) => e.section !== "head");
  box.hidden = !all.length;
  if (!all.length) { box.replaceChildren(); return; }
  const top = all.slice(0, 3).map((e) => paint(h("button", { type: "button", class: `ib-peek-row k-${e.kind}`,
    on: { click: () => openOn(e.id) } },
  h("span", { class: "ib-ic", svg: ICON[e.kind] || (APPROVALS.has(e.kind) ? "shieldq" : "dot") }),
  h("span", { class: "t" }, e.kind === "routine" ? e.title : `${LABELS[e.kind] || ""} · ${e.title}`),
  h("small", {}, peekNote(e))), tintFor(e)));
  box.replaceChildren(
    h("div", { class: "ib-peek-head" }, h("h3", {}, "Boîte de réception"), h("span", { class: "muted" }, summary()),
      h("button", { type: "button", class: "btn small", on: { click: () => openOn(null) } }, `Tout voir (${all.length})`)),
    ...top);
}

function peekNote(e) {
  if (e.kind === "hook" || e.kind === "permission") return `${toolLabel(e.tool)} · expire à ${time(e.expires)}`;
  if (e.kind === "routine" && e.count > 1) return `${e.count} résultats · ${fmtDate(e.ts)}`;
  return [e.summary || e.excerpt || "", fmtDate(e.ts)].filter(Boolean).join(" · ").slice(0, 140);
}

function openOn(id) {
  toggleInbox(ctx, true);
  if (id) setTimeout(() => el().querySelector(`.ib-row[data-id="${CSS.escape(id)}"]`)?.focus(), 60);
}
onInbox(() => renderPeek());

// ------------------------------------------------------------ back from an absence
let awaySince = 0, awayBar = null;

/** The session locked or asleep (desktop app), or the page hidden a long time (browser). */
export function away(ts = Date.now() / 1000) { if (!awaySince) awaySince = ts; }

/** Back: what arrived meanwhile, in one line above the bar (or at the top of the page), once, without sound. */
export async function back() {
  const since = awaySince;
  awaySince = 0;
  if (!since || !ctx) return;
  await refresh();
  const news = (data.entries || []).filter((e) => e.section !== "head" && (e.ts || 0) >= since);
  const n = (kinds) => news.filter((e) => kinds.includes(e.kind)).length;
  const say = (k, one, many = one) => (k ? `${k} ${k > 1 ? many : one}` : "");
  const parts = [say(n(["hook", "permission", "question", "plan", "proposal"]), "à valider"),
    say(n(["expired"]), "validation expirée", "validations expirées"), say(n(["error", "interrupted"]), "erreur", "erreurs"),
    say(n(["choice"]), "choix en attente"), say(n(["done", "routine", "run"]), "résultat", "résultats"),
    say(n(["reminder"]), "rappel", "rappels")].filter(Boolean);
  if (!parts.length) return;
  const close = () => { awayBar?.remove(); awayBar = null; };
  close();
  awayBar = h("div", { class: "update-bar away-bar", role: "status" }, h("span", { class: "i", svg: "bell" }),
    h("span", {}, `Pendant ton absence : ${parts.join(", ")}.`),
    h("button", { type: "button", class: "btn small primary", on: { click: () => { close(); toggleInbox(ctx, true); } } }, "Ouvrir la boîte"),
    h("button", { type: "button", class: "icon-btn", title: "Masquer", "aria-label": "Masquer", svg: "close", on: { click: close } }));
  noticeRoot().append(awayBar);
  ctx.reveal?.();
}

// ------------------------------------------------------------ the panel
export function toggleInbox(context = ctx, force = null) {
  ctx = context || ctx;
  const d = el();
  const show = force === null ? d.hidden : force;
  if (!show) { d.hidden = true; return; }
  ctx.closeDrawers?.("inbox");
  d.hidden = false;
  render();
  refresh();
}

const tintFor = (e) => tintOf(e.color, projectFor(e.folder)?.color);
const account = (pid) => ctx.profiles().find((p) => p.id === pid);
function where(e) {
  const proj = projectFor(e.folder);
  return [e.profile_name || account(e.profile)?.name || "", proj?.name || ""].filter(Boolean).join(" · ");
}

function render() {
  const d = el();
  const all = data.entries || [];
  const shown = all.filter((e) => (!filt.profile || e.profile === filt.profile) && (!filt.folder || keyOf(e.folder) === filt.folder));
  const folders = [...new Map(all.filter((e) => e.folder && projectFor(e.folder)).map((e) => [keyOf(e.folder), projectFor(e.folder)])).entries()];
  const profSel = h("select", { "aria-label": "Compte" }, h("option", { value: "" }, "Tous les comptes"),
    ...ctx.profiles().map((p) => h("option", { value: p.id }, p.name)));
  profSel.value = filt.profile;
  profSel.addEventListener("change", () => { filt.profile = profSel.value; render(); });
  const projSel = h("select", { "aria-label": "Projet" }, h("option", { value: "" }, "Tous les projets"),
    ...folders.map(([k, p]) => h("option", { value: k }, p.name)));
  projSel.value = folders.some(([k]) => k === filt.folder) ? filt.folder : "";
  projSel.addEventListener("change", () => { filt.folder = projSel.value; render(); });
  const body = h("div", { class: "drawer-list ib-list", on: { keydown: onKeys } });
  for (const e of shown.filter((x) => x.section === "head")) body.append(headline(e));
  const today = todayStrip();
  if (today) body.append(today);
  const sec = (title, rows, tools = null) => (rows.length ? body.append(h("div", { class: "ib-sec" },
    h("div", { class: "sec-title ib-sec-title" }, h("span", {}, `${title} · ${rows.length}`), tools), ...rows)) : null);
  const todo = shown.filter((e) => e.section === "todo"), toRead = shown.filter((e) => e.section === "read");
  const reminders = shown.filter((e) => e.section === "reminder");
  sec("À faire", todoRows(todo));
  sec("À lire", toRead.map(row), toRead.length ? h("button", { type: "button", class: "btn small ghost",
    on: { click: () => mark({ section: "read" }) } }, "Tout marquer comme lu") : null);
  sec("Rappels", reminders.map(row));
  if (!shown.some((e) => e.section !== "head")) {
    body.append(h("div", { class: "empty-row" }, all.length ? "Rien pour ce filtre."
      : "Rien n'attend. Les validations, les réponses arrivées pendant que tu regardais ailleurs, les résultats des routines et les rappels arrivent ici."));
  }
  const focusedId = d.querySelector(".ib-row:focus")?.dataset.id;
  d.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Boîte de réception"),
      h("span", { class: "muted" }, summary()),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", "aria-label": "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } })),
    h("div", { class: "drawer-filters" }, profSel, folders.length ? projSel : null),
    body);
  if (focusedId) d.querySelector(`.ib-row[data-id="${CSS.escape(focusedId)}"]`)?.focus();
}

// ------------------------------------------------------------ at the top: the routines' latest result, today
const loaded = new Set();   // `${task}:${display ts}` whose events were read (the displays of the top)

/** A routine shown at the top (a morning brief): its latest display, folded, or its answer. */
function headline(e) {
  const tint = tintFor(e);
  const view = h("div", { class: "ib-une-body" });
  const d = e.display;
  if (d && hasDisplay(e.task_id, d.key)) view.append(renderDisplay(e.task_id, d.key, { mode: "conversation", color: tint }));
  else if (d) {
    view.append(h("div", { class: "muted" }, "Chargement de l'affichage…"));
    loadDisplays(e.task_id, d.ts);
  } else view.append(h("div", { class: "ib-excerpt" }, e.excerpt || "Pas de réponse."));
  const card = paint(h("div", { class: `ib-une${unfolded.has(e.id) ? " open" : ""}`, "data-id": e.id },
    h("div", { class: "ib-une-head" }, h("span", { class: "ib-ic", svg: e.brief ? "sparkle" : "clock" }),
      h("b", {}, e.title), h("small", {}, [e.profile_name, fmtDate(e.ts)].filter(Boolean).join(" · ")),
      h("button", { type: "button", class: "btn small ghost", title: "Voir tout", on: { click: () => {
        if (!unfolded.delete(e.id)) unfolded.add(e.id);
        card.classList.toggle("open", unfolded.has(e.id));
      } } }, "Déplier"),
      h("button", { type: "button", class: "btn small", on: { click: () => {
        if (d) ctx.openDisplay(e.task_id, d.key, tint); else ctx.openTask(e.task_id);
        if (e.unread) mark({ ids: [e.id] });
      } } }, "Ouvrir"),
      h("button", { type: "button", class: "icon-btn", title: "Masquer jusqu'à la prochaine exécution", "aria-label": "Masquer", svg: "close",
        on: { click: () => dismiss(e) } })),
    view), tint);
  return card;
}

async function loadDisplays(taskId, ts) {
  const k = `${taskId}:${ts}`;
  if (loaded.has(k)) return;
  loaded.add(k);
  try {
    const { events } = await api(`/api/tasks/${taskId}/events`);
    for (const ev of events) {
      if (ev.kind === "display") setDoc(taskId, ev.data);
      else if (ev.kind === "display_answer") setAnswer(taskId, ev.data);
    }
  } catch { loaded.delete(k); }
  rerender();
}

/** What comes today, without Claude: reminders, routines, and an account close to its limits. */
function todayStrip() {
  const end = new Date(); end.setHours(23, 59, 59, 999);
  const now = Date.now() / 1000, until = end.getTime() / 1000;
  const items = [];
  for (const n of ctx.notes?.() || []) {
    if (n.remind_at && !n.reminded && n.remind_at > now && n.remind_at <= until) {
      items.push([n.remind_at, "bell", `Rappel · ${(n.text || "").split("\n")[0].trim() || "Note"}`]);
    }
  }
  for (const r of ctx.routines?.() || []) {
    if (r.enabled && r.next_run && r.next_run > now && r.next_run <= until) items.push([r.next_run, "clock", `Routine · ${r.name}`]);
  }
  items.sort((a, b) => a[0] - b[0]);
  const lines = items.slice(0, 6).map(([ts, ic, text]) => h("div", { class: "ib-today-row" }, h("span", { class: "i", svg: ic }),
    h("b", {}, time(ts)), h("span", {}, text)));
  for (const p of ctx.profiles()) {
    const st = accountState(p.id);
    if (!st.known) continue;
    const hot = [st.session >= 0.7 ? `session ${Math.round(st.session * 100)} %` : "", st.week >= 0.7 ? `semaine ${Math.round(st.week * 100)} %` : ""].filter(Boolean);
    if (st.blocked || hot.length) {
      lines.push(paint(h("div", { class: "ib-today-row acc" }, h("span", { class: "i", svg: "gauge" }), h("b", {}, p.name),
        h("span", {}, st.blocked ? `limite atteinte${st.resetText ? ` · ${st.resetText}` : ""}` : hot.join(" · "))), p.color));
    }
  }
  if (!lines.length) return null;
  return h("div", { class: "ib-sec ib-today" }, h("div", { class: "sec-title" }, "Aujourd'hui"), ...lines);
}

/** The approvals of a discussion together, under its title; then the rest, as the server sorted it. */
function todoRows(todo) {
  const out = [], groups = new Map();
  for (const e of todo) {
    if (!APPROVALS.has(e.kind)) { out.push(row(e)); continue; }
    let g = groups.get(e.task_id);
    if (!g) {
      g = paint(h("div", { class: "ib-group" }, h("div", { class: "ib-ghead" },
        h("span", { class: "ib-gt" }, e.title || "Discussion"), h("small", {}, where(e)),
        h("button", { type: "button", class: "btn small ghost", on: { click: () => ctx.openTask(e.task_id) } }, "Ouvrir"))), tintFor(e));
      groups.set(e.task_id, g);
      out.push(g);
    }
    g.append(row(e, true));
  }
  return out;
}

const LABELS = {
  hook: "Validation", permission: "Validation", question: "Question de Claude", plan: "Plan à approuver",
  proposal: "Proposition de Claude", expired: "Validation expirée", choice: "Claude attend ton choix",
  error: "En erreur", interrupted: "Interrompue", done: "Terminée", routine: "Routine", run: "Routine",
  reminder: "Rappel",
};
const ICON = { question: "help", plan: "list", proposal: "bolt", expired: "clock", choice: "sparkle", error: "error",
  interrupted: "alert", done: "check", routine: "clock", run: "clock", reminder: "bell" };

function row(e, inGroup = false) {
  const actions = h("div", { class: "ib-actions" });
  const btn = (label, fn, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, disabled: busy.has(e.id),
    on: { click: (ev) => { ev.stopPropagation(); fn(); } } }, label);
  const lines = [];
  let label = LABELS[e.kind] || e.kind, main = () => {};
  const meta = [inGroup ? "" : where(e), fmtDate(e.ts)];
  switch (e.kind) {
    case "hook": case "permission": {
      label = `${LABELS[e.kind]} · ${toolLabel(e.tool)}`;
      if (e.target) lines.push(h("div", { class: "appr-target" }, e.target));
      if (e.reason) lines.push(h("div", { class: "appr-reason" }, e.reason));
      const left = (e.expires || 0) - Date.now() / 1000;
      meta.push(h("span", { class: left < 300 ? "ib-soon" : "" }, `expire à ${time(e.expires)}`));
      main = () => ctx.openTask(e.task_id);
      if (refusing.has(e.id)) {
        const msg = h("input", { type: "text", placeholder: "Message pour Claude (facultatif)", value: refusing.get(e.id),
          on: { input: () => refusing.set(e.id, msg.value),
            keydown: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); decide(e, "deny", msg.value); } if (ev.key === "Escape") { ev.stopPropagation(); refusing.delete(e.id); render(); } } } });
        actions.append(msg, btn("Annuler", () => { refusing.delete(e.id); render(); }, "ghost"),
          btn("Refuser", () => decide(e, "deny", msg.value), "danger"));
        if (focusRefusal === e.id) { focusRefusal = null; setTimeout(() => msg.focus(), 0); }
      } else {
        actions.append(...[btn("Refuser", () => { refusing.set(e.id, ""); focusRefusal = e.id; render(); }, "danger"),
          btn("Approuver", () => decide(e, "allow"), "ok"),
          btn(detail.has(e.id) ? "Masquer le détail" : "Détail", () => { if (!detail.delete(e.id)) detail.add(e.id); render(); }, "ghost"),
          inGroup ? null : btn("Ouvrir", main, "ghost")].filter(Boolean));   // (in a group: its head opens the discussion)
      }
      if (detail.has(e.id)) lines.push(h("div", { class: "ib-detail" }, inputView(e.tool, e.input)));
      break;
    }
    case "question": case "plan": case "proposal":
      lines.push(h("div", { class: "ib-text" }, e.summary));
      main = () => ctx.openTask(e.task_id);
      actions.append(btn(e.kind === "question" ? "Répondre" : "Lire et décider", main, "primary"));
      break;
    case "expired":
      lines.push(h("div", { class: "ib-text" }, e.summary),
        h("div", { class: "appr-reason" }, `Refusée à ${time(e.ts)} faute de réponse dans le délai de validation.`));
      main = () => ctx.openTask(e.task_id);
      actions.append(e.actions[0] === "resume"
        ? btn("Reprendre", () => resume(e), "primary") : btn("Relancer", () => { ctx.retry(e.task_id); dismiss(e); }, "primary"),
      btn("Ouvrir", main, "ghost"), btn("Ignorer", () => dismiss(e), "ghost"));
      break;
    case "choice":
      lines.push(h("div", { class: "ib-text" }, `« ${e.summary} »`));
      main = () => ctx.openDisplay(e.task_id, e.key, tintFor(e));
      actions.append(btn("Ouvrir l'affichage", main, "primary"), btn("Ignorer", () => dismiss(e), "ghost"));
      break;
    case "error": case "interrupted":
      if (e.summary) lines.push(h("div", { class: "ib-text" }, e.summary));
      main = () => { ctx.openTask(e.task_id); mark({ ids: [e.id] }); };
      actions.append(btn("Relancer", () => { ctx.retry(e.task_id); mark({ ids: [e.id] }); }, "primary"),
        btn("Ouvrir", main, "ghost"), btn("Ignorer", () => mark({ ids: [e.id] }), "ghost"));
      break;
    case "done": case "routine":
      if (e.kind === "routine") label = e.count > 1 ? `Routine · ${e.count} résultats` : "Résultat de routine";
      if (e.excerpt) lines.push(h("div", { class: "ib-excerpt" }, e.excerpt));
      main = () => { ctx.openTask(e.task_id); mark({ ids: [e.id] }); };
      actions.append(btn("Ouvrir", main, "primary"), btn("Lu", () => mark({ ids: [e.id] }), "ghost"));
      break;
    case "run":
      label = `Routine ${e.status}`;
      if (e.summary) lines.push(h("div", { class: "ib-text" }, e.summary));
      main = () => { ctx.openRoutines(); mark({ ids: [e.id] }); };
      actions.append(btn("Routines", main, "ghost"),
        btn("Lancer maintenant", () => runRoutine(e), "primary"), btn("Lu", () => mark({ ids: [e.id] }), "ghost"));
      break;
    case "reminder": {
      const note = () => ctx.notes().find((n) => n.id === e.note_id);
      main = () => { const n = note(); if (n) ctx.editNote(n); };
      const later = h("select", { class: "ib-later", "aria-label": "Plus tard", on: { click: (ev) => ev.stopPropagation(),
        change: () => { snooze(e, later.value); } } },
      h("option", { value: "" }, "Plus tard…"), h("option", { value: "3600" }, "Dans 1 h"),
      h("option", { value: "10800" }, "Dans 3 h"), h("option", { value: "demain" }, "Demain 9:00"));
      actions.append(btn("Ouvrir", main, "ghost"), later, btn("Vu", () => dismiss(e), "primary"));
      break;
    }
    default:
      main = () => e.task_id && ctx.openTask(e.task_id);
  }
  const r = h("div", { class: `ib-row k-${e.kind}${inGroup ? " in-group" : ""}`, tabindex: "0", "data-id": e.id,
    on: { click: (ev) => { if (!ev.target.closest("button, input, select, details, pre")) main(); },
      keydown: (ev) => { if (ev.key === "Enter" && ev.target === r) { ev.preventDefault(); main(); } } } },
  h("div", { class: "ib-head" }, h("span", { class: "ib-ic", svg: e.kind === "hook" || e.kind === "permission" ? null : ICON[e.kind] || "dot" },
    e.kind === "hook" || e.kind === "permission" ? h("span", { svg: toolIcon(e.tool) }) : null),
  h("b", {}, label), inGroup ? null : h("span", { class: "ib-title" }, e.title)),
  ...lines,
  h("div", { class: "ib-meta" }, ...meta.filter(Boolean).flatMap((m, i) => (i ? [" · ", m] : [m]))),
  actions);
  return inGroup ? r : paint(r, tintFor(e));
}

function onKeys(ev) {
  if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
  const rows = [...el().querySelectorAll(".ib-row")];
  const i = rows.indexOf(ev.target.closest(".ib-row"));
  const next = rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + (ev.key === "ArrowDown" ? 1 : -1)))];
  if (next) { ev.preventDefault(); next.focus(); }
}

// ------------------------------------------------------------ what a click does
async function call(e, fn) {
  busy.add(e.id);
  render();
  try {
    await fn();
    setTimeout(refresh, 1500);   // (the stream brings the new inbox; this, if it was down)
  } catch (err) { toast(err.message, "err"); busy.delete(e.id); render(); }
}

function decide(e, decision, message = "") {
  const aid = e.id.split(":").pop();
  refusing.delete(e.id);
  return call(e, async () => {
    await api(`/api/tasks/${e.task_id}/approvals/${aid}`, { method: "POST", body: { decision, message, via: "boite" } });
    toast(decision === "allow" ? "Approuvé." : "Refusé.", decision === "allow" ? "ok" : "");
  });
}

function resume(e) {
  return call(e, async () => {
    await api(`/api/tasks/${e.task_id}/resume-expired`, { method: "POST", body: { aid: e.id.split(":").pop() } });
    toast("Demande renvoyée à la discussion : la validation va revenir.", "ok");
    ctx.openTask(e.task_id);
  });
}

function runRoutine(e) {
  return call(e, async () => {
    const t = await api(`/api/routines/${e.routine.id}/run`, { method: "POST" });
    await api("/api/inbox/read", { method: "POST", body: { ids: [e.id] } });
    toast(`« ${e.title} » lancée.`, "ok");
    if (t?.id) ctx.openTask(t.id);
  });
}

function snooze(e, v) {
  if (!v) return;
  const d = new Date();
  const at = v === "demain" ? (d.setDate(d.getDate() + 1), d.setHours(9, 0, 0, 0), Math.floor(d.getTime() / 1000))
    : Math.floor(Date.now() / 1000) + Number(v);
  return call(e, () => api(`/api/notes/${e.note_id}`, { method: "PATCH", body: { remind_at: at } }));
}

function mark(body) { return call({ id: body.ids?.[0] || `section:${body.section}` }, () => api("/api/inbox/read", { method: "POST", body })); }
function dismiss(e) { return call(e, () => api("/api/inbox/dismiss", { method: "POST", body: { ids: [e.id] } })); }
