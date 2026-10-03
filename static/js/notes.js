// Notes: general ones (the color of their account) or of a project (the account's color mixed with the
// project's), each with an optional reminder. A reminder due pops up at the top right (above modals too), in the note's
// colors, until it is marked seen or postponed; every open page follows (the server publishes "notes").
import { api } from "./api.js";
import { projectFor, projects } from "./projects.js";
import { tintOf } from "./tint.js";
import { $, confirmDialog, dialog, fmtDate, h, paint, toast } from "./util.js";

let ctx = null, notes = [], filter = "all", search = "";
const shown = new Map(); // note id -> its reminder popup
const el = () => $("#notes");
const pad = (n) => String(n).padStart(2, "0");
const keyOf = (f) => String(f || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();

/** ctx: profiles(), currentProfile(), currentFolder(), closeDrawers(except), openProject(folder),
    alert({title, body, tag, onClick}) (sound and system notification). */
export async function initNotes(context) {
  ctx = context;
  try { notes = (await api("/api/notes")).notes || []; } catch { notes = []; }
  badge();
  checkReminders();
  setInterval(checkReminders, 15_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkReminders(); });
}

/** The server changed a note (here or in another page). */
export function notesChanged({ note, deleted } = {}) {
  if (deleted) notes = notes.filter((n) => n.id !== deleted);
  if (note) notes = [note, ...notes.filter((n) => n.id !== note.id)];
  for (const [id, pop] of shown) {
    const n = notes.find((x) => x.id === id);
    if (!n || n.reminded || !due(n)) { pop.remove(); shown.delete(id); }
  }
  checkReminders();
  rerender();
}

/** Accounts or projects changed (names, colors): the notes shown follow. */
export function notesRecolor() {
  for (const [id, pop] of shown) { const n = notes.find((x) => x.id === id); if (n) paint(pop, tintFor(n)); }
  rerender();
}

const listeners = new Set();
function rerender() {
  badge();
  if (!el().hidden) render();
  for (const fn of listeners) fn();
}

export const allNotes = () => notes;

/** The top bar button counts the reminders to come. */
function badge() {
  const b = document.querySelector("#btn-notes .nt-count");
  if (!b) return;
  const n = notes.filter((x) => x.remind_at && !x.reminded).length;
  b.hidden = !n;
  b.textContent = String(n);
  b.closest("button").title = `Notes générales et de projet, avec rappels${n ? ` (${n} rappel${n > 1 ? "s" : ""} en attente)` : ""}`;
}

// ------------------------------------------------------------ colors & labels
const account = (pid) => ctx.profiles().find((p) => p.id === pid) || ctx.profiles()[0];
/** A general note: its account's color. A project note: its account's color mixed with the project's. */
export function tintFor(n) { return tintOf(account(n.profile)?.color, projectFor(n.folder)?.color); }
const titleOf = (n) => (n.text || "").split("\n")[0].trim() || "Note";
const restOf = (n) => (n.text || "").split("\n").slice(1).join("\n").trim();
function scopeLabel(n) {
  if (!n.folder) return "Note générale";
  return projectFor(n.folder)?.name || `${n.folder.replace(/[\\/]+$/, "").split(/[\\/]/).pop()} (projet retiré)`;
}
const due = (n) => n.remind_at && !n.reminded && n.remind_at * 1000 <= Date.now();
function whenLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((new Date(d.toDateString()) - new Date(now.toDateString())) / 86_400_000);
  if (days === 0) return `aujourd'hui ${time}`;
  if (days === 1) return `demain ${time}`;
  if (days === -1) return `hier ${time}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}
const toLocalInput = (ts) => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** The quick reminder choices: in an hour, this evening, tomorrow morning, Monday morning. */
function presets() {
  const now = new Date(), at = (d, hh, mm = 0) => { const x = new Date(d); x.setHours(hh, mm, 0, 0); return x; };
  const out = [["Dans 1 h", new Date(Date.now() + 3_600_000)]];
  if (now.getHours() < 17) out.push(["Ce soir 18:00", at(now, 18)]);
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  out.push(["Demain 9:00", at(tomorrow, 9)]);
  const monday = new Date(now); monday.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7));
  out.push(["Lundi 9:00", at(monday, 9)]);
  return out.map(([label, d]) => [label, Math.floor(d.getTime() / 60_000) * 60]);
}

// ------------------------------------------------------------ editing
/** Create (opts.folder: "" general, a folder: that project) or edit a note. Resolves with the saved note. */
export async function editNote(n = null, opts = {}) {
  const cur = n || { text: "", folder: opts.folder ?? "", profile: "", remind_at: null };
  const text = h("textarea", { class: "nt-text", rows: "7", spellcheck: "true", placeholder: "La première ligne sert de titre." });
  text.value = cur.text || opts.text || "";
  const projs = projects();
  const scope = h("select", {}, h("option", { value: "" }, "Note générale"),
    ...projs.map((p) => h("option", { value: p.folder }, `Projet ${p.name}`)));
  if (cur.folder && !projs.some((p) => keyOf(p.folder) === keyOf(cur.folder))) scope.append(h("option", { value: cur.folder }, scopeLabel(cur)));
  scope.value = projs.find((p) => keyOf(p.folder) === keyOf(cur.folder))?.folder ?? cur.folder ?? "";
  const defaultAccount = (folder) => projectFor(folder)?.profile || ctx.currentProfile();
  const acc = h("select", {}, ...ctx.profiles().map((p) => h("option", { value: p.id }, p.name)));
  acc.value = cur.profile || defaultAccount(scope.value);
  let accTouched = !!cur.profile;
  const when = h("input", { type: "datetime-local", value: cur.remind_at ? toLocalInput(cur.remind_at) : "" });
  const chips = h("div", { class: "nt-chips" },
    ...presets().map(([label, ts]) => h("button", { type: "button", class: "nt-chip", on: { click: () => { when.value = toLocalInput(ts); } } }, label)),
    h("button", { type: "button", class: "nt-chip", on: { click: () => { when.value = ""; } } }, "Aucun rappel"));
  const row = (label, control) => h("label", { class: "pf-row" }, h("span", {}, label), control);
  const body = h("div", { class: "pf nt-form" }, text, row("Où", scope), row("Compte", acc),
    row("Rappel", when), chips,
    h("p", { class: "muted" }, "Le rappel s'affiche dans la console à l'heure dite (et en notification si la page est en arrière-plan)."));
  let box = null;
  const repaint = () => box && paint(box, tintOf(account(acc.value)?.color, projectFor(scope.value)?.color));
  scope.addEventListener("change", () => { if (!accTouched) acc.value = defaultAccount(scope.value); repaint(); });
  acc.addEventListener("change", () => { accTouched = true; repaint(); });
  const buttons = [{ label: "Annuler", value: null }];
  if (n) buttons.push({ label: "Supprimer", value: "delete", cls: "danger" });
  buttons.push({ label: n ? "Enregistrer" : "Ajouter la note", value: "save", cls: "primary" });
  const v = await dialog({ title: n ? "Note" : opts.folder ? `Nouvelle note · ${scopeLabel({ folder: opts.folder })}` : "Nouvelle note",
    body, buttons, tint: tintOf(account(acc.value)?.color, projectFor(scope.value)?.color),
    onOpen: (b) => {
      box = b;
      b.classList.add("nt-dialog");
      text.focus();
      // Ctrl+Entrée enregistre
      text.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); b.querySelector(".dialog-actions .primary")?.click(); } });
    } });
  if (v === "delete") { await removeNote(n); return null; }
  if (v !== "save") return null;
  if (!text.value.trim()) { toast("La note est vide : rien d'enregistré.", "warn"); return null; }
  const remind = when.value ? Math.floor(new Date(when.value).getTime() / 1000) : null;
  if (remind && remind * 1000 < Date.now() - 60_000 && remind !== cur.remind_at) toast("Ce rappel est déjà passé : il s'affiche tout de suite.", "warn");
  const payload = { text: text.value, folder: scope.value, profile: acc.value };
  if (!n || remind !== (cur.remind_at || null)) payload.remind_at = remind;
  try {
    const saved = await api(n ? `/api/notes/${n.id}` : "/api/notes", { method: n ? "PATCH" : "POST", body: payload });
    notesChanged({ note: saved });
    toast(saved.remind_at ? `Note enregistrée · rappel ${whenLabel(saved.remind_at)}` : "Note enregistrée.", "ok");
    return saved;
  } catch (e) { toast(e.message, "err"); return null; }
}

async function removeNote(n) {
  if (!(await confirmDialog("Supprimer cette note ?", `« ${titleOf(n)} » sera effacée, avec son rappel.`, "Supprimer", "danger", tintFor(n)))) return false;
  try { await api(`/api/notes/${n.id}`, { method: "DELETE" }); notesChanged({ deleted: n.id }); return true; }
  catch (e) { toast(e.message, "err"); return false; }
}

async function patch(n, body) {
  try { notesChanged({ note: await api(`/api/notes/${n.id}`, { method: "PATCH", body }) }); }
  catch (e) { toast(e.message, "err"); }
}

// ------------------------------------------------------------ list rows (drawer and project tab)
function noteRow(n, { showScope = true } = {}) {
  const rest = restOf(n);
  const pending = n.remind_at && !n.reminded;
  const bell = n.remind_at ? h("span", { class: `nt-bell${pending ? (due(n) ? " due" : " on") : ""}`, svg: "bell",
    title: pending ? `Rappel ${whenLabel(n.remind_at)}` : `Rappel passé (${whenLabel(n.remind_at)})` }) : null;
  const meta = [showScope ? scopeLabel(n) : "", account(n.profile)?.name || "",
    n.remind_at ? (pending ? `rappel ${whenLabel(n.remind_at)}` : "rappel vu") : "", `modifiée ${fmtDate(n.updated)}`].filter(Boolean);
  const del = h("button", { type: "button", class: "icon-btn nt-del", title: "Supprimer", "aria-label": "Supprimer la note", svg: "close",
    on: { click: (e) => { e.stopPropagation(); removeNote(n); } } });
  return paint(h("div", { class: "hrow nt-row", tabindex: "0", on: {
    click: () => editNote(n), keydown: (e) => { if (e.key === "Enter") editNote(n); } } },
  h("div", { class: "hm" }, h("div", { class: "ht" }, bell, titleOf(n)),
    rest ? h("div", { class: "nt-ex" }, rest) : null,
    h("div", { class: "hs" }, meta.join(" · "))),
  del), tintFor(n));
}

/** Pending reminders first (soonest first), then the others, last changed first. */
function sorted(list) {
  const p = (n) => (n.remind_at && !n.reminded ? 0 : 1);
  return [...list].sort((a, b) => p(a) - p(b) || (p(a) === 0 ? a.remind_at - b.remind_at : b.updated - a.updated));
}

// ------------------------------------------------------------ the Notes drawer
export function toggleNotes(context = ctx) {
  ctx = context || ctx;
  const d = el();
  if (!d.hidden) { d.hidden = true; return; }
  ctx.closeDrawers?.("notes");
  d.hidden = false;
  render();
}

function render() {
  const d = el();
  if (d.hidden) return;
  const here = projectFor(ctx.currentFolder());
  if (filter === "project" && !here) filter = "all";
  const list = sorted(notes.filter((n) => (filter === "general" ? !n.folder : filter === "project" ? keyOf(n.folder) === keyOf(here.folder) : true)));
  const chip = (id, label) => h("button", { type: "button", class: `nt-chip${filter === id ? " on" : ""}`, "aria-pressed": String(filter === id),
    on: { click: () => { filter = id; render(); } } }, label);
  const input = h("input", { type: "text", placeholder: "Chercher dans les notes", value: search });
  input.addEventListener("input", () => { search = input.value; renderList(); });
  const body = h("div", { class: "drawer-list" });
  const renderList = () => {
    const qq = search.trim().toLowerCase();
    const rows = qq ? list.filter((n) => n.text.toLowerCase().includes(qq) || scopeLabel(n).toLowerCase().includes(qq)) : list;
    body.replaceChildren(...(rows.length ? rows.map((n) => noteRow(n))
      : [h("div", { class: "empty-row" }, notes.length ? "Aucune note ne correspond." : "Aucune note. Une note générale prend la couleur de son compte ; une note de projet mêle celle du compte et celle du projet.")]));
  };
  const pending = notes.filter((n) => n.remind_at && !n.reminded).length;
  d.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Notes"),
      pending ? h("span", { class: "muted" }, `${pending} rappel${pending > 1 ? "s" : ""} en attente`) : null,
      h("button", { type: "button", class: "btn small primary", title: "Nouvelle note (générale, ou du projet choisi)",
        on: { click: () => editNote(null, { folder: filter === "project" && here ? here.folder : "" }) } }, "Nouvelle note"),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } })),
    h("div", { class: "drawer-filters" }, chip("all", "Toutes"), chip("general", "Générales"),
      here ? chip("project", `Projet ${here.name}`) : null, input),
    body);
  renderList();
}

/** The "Notes" tab of the project panel. */
export function renderProjectNotes(body, folder, refresh) {
  const proj = projectFor(folder);
  const draw = () => {
    if (!body.isConnected) { listeners.delete(draw); return; }
    const mine = sorted(notes.filter((n) => keyOf(n.folder) === keyOf(proj.folder)));
    list.replaceChildren(...(mine.length ? mine.map((n) => noteRow(n, { showScope: false }))
      : [h("div", { class: "empty-row" }, "Aucune note pour ce projet.")]));
  };
  body.append(h("p", { class: "pj-lead pad" }, "Les notes de ce projet, avec leurs rappels. Elles ne sont pas envoyées à Claude ; ",
    "leur couleur mêle celle du compte et celle du projet."),
  h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary",
    on: { click: () => editNote(null, { folder: proj.folder }).then(() => refresh?.()) } }, "Nouvelle note")));
  const list = h("div", { class: "drawer-list flat" });
  body.append(list);
  listeners.add(draw);
  draw();
}

// ------------------------------------------------------------ reminders
function checkReminders() {
  if (!ctx) return;
  for (const n of notes) if (due(n) && !shown.has(n.id)) popup(n);
}

function popup(n) {
  const root = $("#reminders");
  const rest = restOf(n);
  const later = (secs) => patch(n, { remind_at: Math.floor(Date.now() / 1000) + secs });
  const tomorrow9 = () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return Math.floor(d.getTime() / 1000); };
  const menu = h("div", { class: "rm-later", hidden: true },
    ...[["10 min", 600], ["1 h", 3600], ["3 h", 10_800]].map(([label, s]) => h("button", { type: "button", on: { click: () => later(s) } }, label)),
    h("button", { type: "button", on: { click: () => patch(n, { remind_at: tomorrow9() }) } }, "Demain 9:00"));
  const pop = paint(h("div", { class: "reminder", role: "alertdialog", "aria-label": `Rappel : ${titleOf(n)}` },
    h("div", { class: "rm-head" }, h("span", { class: "rm-ic", svg: "bell" }),
      h("div", { class: "rm-where" }, h("b", {}, scopeLabel(n)), h("small", {}, `${account(n.profile)?.name || ""} · ${whenLabel(n.remind_at)}`)),
      h("button", { type: "button", class: "icon-btn", title: "Vu", "aria-label": "Marquer comme vu", svg: "close", on: { click: () => patch(n, { reminded: true }) } })),
    h("div", { class: "rm-body" }, h("div", { class: "rm-title" }, titleOf(n)), rest ? h("div", { class: "rm-text" }, rest) : null),
    h("div", { class: "rm-actions" },
      h("button", { type: "button", class: "btn small", on: { click: () => { patch(n, { reminded: true }); if (n.folder && projectFor(n.folder)) ctx.openProject(n.folder); editNote(notes.find((x) => x.id === n.id) || n); } } }, "Ouvrir"),
      h("div", { class: "rm-wrap" }, h("button", { type: "button", class: "btn small", "aria-haspopup": "true",
        on: { click: () => { menu.hidden = !menu.hidden; } } }, "Plus tard…"), menu),
      h("button", { type: "button", class: "btn small primary", on: { click: () => patch(n, { reminded: true }) } }, "Vu"))), tintFor(n));
  shown.set(n.id, pop);
  root.append(pop);
  ctx.alert?.({ title: `Rappel · ${scopeLabel(n)}`, body: titleOf(n), tag: `note-${n.id}`, onClick: () => pop.querySelector(".rm-actions .primary")?.focus() });
}
