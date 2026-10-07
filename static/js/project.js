// Project = a working folder of an account, like a claude.ai Project: its instructions
// (CLAUDE.md of the folder and of the account), what Claude remembers there, its files and
// its discussions. The panel follows the folder chosen in the request bar.
import { actionStamp, renderActions } from "./actions.js";
import { api } from "./api.js";
import { fmtSize } from "./attach.js";
import { pickFolder } from "./folderpicker.js";
import { mdElement } from "./md.js";
import { allNotes, renderProjectNotes } from "./notes.js";
import { officeChanged, renderOffice } from "./office.js";
import { odooChanged, renderOdoo } from "./odoo.js";
import { projectTint } from "./tint.js";
import { $, STATUS, confirmDialog, dialog, fmtDate, h, paint, toast } from "./util.js";
import { openPreview } from "./viewer.js";
import * as wm from "./wm.js";

const TABS = [["instructions", "Consignes"], ["memory", "Mémoire"], ["files", "Fichiers"], ["tasks", "Discussions"], ["suivi", "Suivi"], ["actions", "Actions"], ["notes", "Notes"], ["rules", "Règles"]];
const TEMPLATE = "# Contexte\n\nÀ quoi sert ce dossier, pour qui, avec quels outils.\n\n# Règles\n\n- \n\n# Fichiers importants\n\n- \n";
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const join = (a, b) => (a ? `${a}/${b}` : b);

let ctx = null, ws = null, tab = "instructions", sub = "", note = null, loading = false;
let epoch = 0, paintGen = 0, wsParts = null, fileSig = null, filesGen = 0, sessionSig = null;
let liveTimer = 0, liveRunning = false, liveAgain = false;
const el = () => $("#project");

export function toggleProject(context) {
  ctx = context;
  const d = el();
  if (!d.hidden) { d.hidden = true; return; }
  ctx.closeDrawers?.("project");
  d.hidden = false;
  load();
}

/** Open the panel on a project (named in the bar, or opened by Claude with the tool projet), on a tab.
 * folder: shown without changing the request bar's folder; null: the bar's folder. */
export function showProject(context, { folder = null, tab: on = "tasks" } = {}) {
  ctx = context;
  const d = el();
  if (d.hidden) { ctx.closeDrawers?.("project"); d.hidden = false; }
  tab = on;
  note = null;
  if (folder && ws && fkey(ws.folder) === fkey(folder)) { render(); projectDiskChanged(); return; }
  load(folder, false);
}

/** The request bar changed account or folder: follow it while open. */
export function projectFollow() { if (ctx && !el().hidden) load(); }

/** Projects or routines changed (an action added or validated, a routine accepted): the Actions tab follows,
 * and the Suivi tab (an Odoo project linked from a discussion). */
export function projectActionsChanged() {
  if (!ctx || !ws || el().hidden) return;
  if (tab === "actions" || tab === "suivi") render();
  else syncBadges();
}
/** A discussion was archived or brought back: the Discussions tab follows. */
export function projectTasksChanged() { if (ctx && ws && !el().hidden && tab === "tasks") render(); }

/** The console read again what the Suivi tab shows (Odoo tasks, or Office 365). */
export function projectSuiviChanged({ folder } = {}) {
  if (!ctx || !ws) return;
  const shown = !el().hidden && tab === "suivi" && fkey(folder) === fkey(ws.folder);
  odooChanged(folder, shown, render);
  officeChanged(folder, shown, render);
}

async function load(folder = null, follow = true) {
  const mine = ++epoch;
  fileSig = null;
  sessionSig = null;
  loading = true;
  render();
  const f = folder ?? ctx.workdir();
  // a project is read with its own account: its memory lives in that account's configuration
  const pid = (f && ctx.project(f)?.profile) || ctx.currentProfile();
  const q = new URLSearchParams({ profile: pid });
  if (f) q.set("folder", f);
  try {
    const fresh = await api(`/api/workspace?${q}`);
    if (mine !== epoch) return;
    ws = fresh;
    wsParts = partSigs(ws);
    if (folder !== null && follow) ctx.setWorkdir(ws.folder, ws.folder === ws.folders[0]);
  } catch (e) {
    if (mine !== epoch) return;
    toast(e.message, "err");
    ws = null;
    wsParts = null;
  }
  if (mine !== epoch) return;
  sub = "";
  note = null;
  loading = false;
  render();
}

const scope = () => ({ profile: ws.profile, folder: ws.folder });
/** The colors of the panel's dialogs: the account's, mixed with the project's in a project. */
const tint = () => (ws ? projectTint(ws.folder, ws.profile) : null);
const fkey = (f) => String(f || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
const notesHere = (folder) => allNotes().filter((n) => fkey(n.folder) === fkey(folder)).length;

// ------------------------------------------------------------ live refresh
// The panel used to read the folder once, when it opened. A file Claude writes, a memory note, a rule
// or a command showed up only after a restart. While the panel is open it now re-reads, quietly:
// right after a tool runs, every few seconds, and when its window comes back to the front. A text
// the user is editing is left alone.

const docOf = (d) => `${d?.exists ? 1 : 0}:${d?.text || ""}`;
function partSigs(w) {
  if (!w) return null;
  return {
    memory: (w.memory?.files || []).map((f) => `${f.name}\t${f.size}\t${f.mtime}`).join("\n"),
    rules: (w.rules || []).map((r) => `${r.pattern}\t${r.created}`).join("\n"),
    instructions: [docOf(w.instructions?.folder), docOf(w.instructions?.profile)].join("\n--\n"),
    jarvis: w.jarvis_instructions || "",
  };
}

function panelOpen() {
  const d = el();
  return !!(ctx && ws && d && !d.hidden && d.ownerDocument.visibilityState !== "hidden");
}

/** A tool just ran, or the panel's window came forward: look at the folder again, soon. */
export function projectDiskChanged() {
  if (!ctx || el()?.hidden) return;
  clearTimeout(liveTimer);
  liveTimer = setTimeout(liveRefresh, 300);
}

setInterval(() => { if (panelOpen()) liveRefresh(); }, 3000);
wm.onDocument((doc) => doc.addEventListener("visibilitychange", () => {
  if (doc.visibilityState === "visible" && el()?.ownerDocument === doc) projectDiskChanged();
}));

function editing() {
  const d = el();
  if (!d) return false;
  return [...d.querySelectorAll("textarea.pj-text")].some((area) => {
    const btn = (area.closest("section") || area.parentElement)?.querySelector("button.primary");
    return btn && !btn.disabled;
  });
}

function tabCount(id) {
  const proj = ctx.project(ws.folder);
  const toReview = (proj?.actions || []).filter((a) => a.status !== "ok").length;
  if (id === "memory") return ws.memory.files.length ? { n: ws.memory.files.length } : null;
  if (id === "rules") return ws.rules?.length ? { n: ws.rules.length } : null;
  if (id === "actions") return toReview ? { n: toReview, warn: true, title: "Actions à valider" } : null;
  if (id === "notes") { const n = notesHere(ws.folder); return n ? { n } : null; }
  if (id === "suivi") return proj?.odoo?.length ? { n: proj.odoo.length } : null;
  return null;
}

function countNode(c) {
  return h("span", { class: c.warn ? "cnt warn" : "cnt", ...(c.title ? { title: c.title } : {}) }, String(c.n));
}

function syncBadges() {
  const root = el()?.querySelector(".pj-tabs");
  if (!root || !ws) return;
  for (const [id] of TABS) {
    const btn = root.querySelector(`button[data-tab="${id}"]`);
    if (!btn) continue;
    const c = tabCount(id);
    const span = btn.querySelector(".cnt");
    if (!c) { span?.remove(); continue; }
    if (!span) btn.append(countNode(c));
    else {
      span.textContent = String(c.n);
      span.className = c.warn ? "cnt warn" : "cnt";
      if (c.title) span.title = c.title; else span.removeAttribute("title");
    }
  }
}

function redraw() {
  const top = el().querySelector(".pj-body")?.scrollTop || 0;
  render();
  const body = el().querySelector(".pj-body");
  if (body) body.scrollTop = top;
}

async function liveRefresh() {
  if (!panelOpen() || loading || editing()) return;
  if (liveRunning) { liveAgain = true; return; }
  liveRunning = true;
  const mine = epoch;
  try {
    await refreshWorkspace(mine);
    if (mine !== epoch || !panelOpen() || editing()) return;
    if (tab === "files") await refreshFileList(mine);
    else if (tab === "tasks") await refreshSessions(mine);
    else if (tab === "actions") await refreshActions(mine);
    if (mine === epoch && panelOpen()) syncBadges();
  } catch { /* the next pass tries again; a manual refresh still reports the error */ }
  finally {
    liveRunning = false;
    if (liveAgain) { liveAgain = false; if (mine === epoch) liveRefresh(); }
  }
}

async function refreshWorkspace(mine) {
  const fresh = await api(`/api/workspace?${new URLSearchParams({ profile: ws.profile, folder: ws.folder })}`);
  if (mine !== epoch || !panelOpen() || editing()) return;
  const next = partSigs(fresh);
  const prev = wsParts;
  if (!prev || !next || (prev.memory === next.memory && prev.rules === next.rules
    && prev.instructions === next.instructions && prev.jarvis === next.jarvis)) return;
  const old = ws;
  ws = fresh;
  wsParts = next;
  if (editing()) { ws = old; wsParts = prev; return; }
  if (tab === "memory" && prev.memory !== next.memory) {
    if (note) await followOpenNote(old, fresh, mine);
    else redraw();
  } else if (tab === "rules" && prev.rules !== next.rules) redraw();
  else if (tab === "instructions" && (prev.instructions !== next.instructions || prev.jarvis !== next.jarvis)
    && !instructionsOnScreen(fresh, prev.jarvis === next.jarvis)) redraw();
}

/** The consignes on screen already are the ones just read (the user saved them): don't rebuild the editors. */
function instructionsOnScreen(fresh, jarvisSame) {
  if (!jarvisSame) return false;
  const areas = [...el().querySelectorAll(".pj-body textarea.pj-text")];
  return areas.length >= 2 && areas[0].value === (fresh.instructions?.folder?.text || "")
    && areas[1].value === (fresh.instructions?.profile?.text || "");
}

async function followOpenNote(old, fresh, mine) {
  const was = (old.memory?.files || []).find((f) => f.name === note?.name);
  const now = (fresh.memory?.files || []).find((f) => f.name === note?.name);
  if (!now) {
    if (mine !== epoch || editing()) return;
    note = null;
    redraw();
    return;
  }
  if (was && was.mtime === now.mtime && was.size === now.size) return;
  const n = await api(`/api/workspace/memory?${new URLSearchParams({ ...scope(), name: now.name })}`);
  if (mine !== epoch || tab !== "memory" || !note || note.name !== now.name || editing()) return;
  if (n.text === note.text) return;
  note.text = n.text;
  const area = el().querySelector("textarea.pj-text");
  if (area) area.value = n.text;
}

function fileSignature(res) {
  return `${res.sub}\n${res.truncated ? 1 : 0}\n${res.entries.map((e) => `${e.dir ? "d" : "f"}\t${e.name}\t${e.size}\t${e.mtime}`).join("\n")}`;
}

function fileRows(res) {
  const rows = res.entries.map((e) => {
    const rel = join(res.sub, e.name);
    const act = (label, title, fn) => h("button", { type: "button", class: "btn small ghost", title,
      on: { click: (ev) => { ev.stopPropagation(); fn(); } } }, label);
    return h("div", { class: `hrow file${e.dir ? " dir" : ""}`, style: { "--pc": e.dir ? "var(--warn)" : "var(--border-3)" },
      title: e.dir ? "Ouvrir le dossier" : "Aperçu",
      on: { click: () => { if (e.dir) { sub = rel; render(); } else openPreview({ ...scope(), path: rel }); } } },
      h("span", { class: "i", svg: e.dir ? "folder" : "file" }),
      h("div", { class: "hm" }, h("div", { class: "ht" }, e.name), h("div", { class: "hs" }, [e.dir ? "dossier" : fmtSize(e.size), fmtDate(e.mtime)].join(" · "))),
      e.dir ? null : act("Citer", "Ajouter son chemin à ta demande", () => ctx.mention(nativePath(rel))),
      act("Dossier", "Afficher dans l'explorateur", () => reveal(rel, true)));
  });
  return [...(rows.length ? rows : [h("div", { class: "empty-row" }, "Dossier vide.")]),
    ...(res.truncated ? [h("div", { class: "muted pad" }, "1 000 premiers éléments affichés.")] : [])];
}

async function refreshFileList(mine) {
  if (fileSig === null || tab !== "files") return;
  const gen = filesGen;
  const res = await api(`/api/workspace/files?${new URLSearchParams({ ...scope(), sub })}`);
  if (mine !== epoch || gen !== filesGen || tab !== "files" || !panelOpen()) return;
  const sig = fileSignature(res);
  if (sig === fileSig) return;
  const list = el().querySelector(".pj-files");
  if (!list) return;
  fileSig = sig;
  list.replaceChildren(...fileRows(res));
}

function externalSessions(sessions) {
  const known = new Set(ctx.tasks().map((t) => t.session_id));
  return (sessions || []).filter((r) => sameFolder(r.cwd, ws.folder) && !known.has(r.id));
}
function sessionSignature(rows) {
  return [...rows].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map((r) => [r.id, r.updated, r.title, r.prompts, r.origin].join("\t")).join("\n");
}

async function refreshSessions(mine) {
  if (sessionSig === null || tab !== "tasks") return;
  const seen = paintGen;
  const { sessions } = await api(`/api/sessions?profile=${encodeURIComponent(ws.profile)}`);
  if (mine !== epoch || paintGen !== seen || tab !== "tasks" || sessionSig === null || !panelOpen()) return;
  const sig = sessionSignature(externalSessions(sessions));
  if (sig === sessionSig) return;
  redraw();
}

async function refreshActions(mine) {
  const before = el().querySelector(".pj-body")?.dataset.live;
  if (!before || tab !== "actions") return;
  const seen = paintGen;
  const proj = ctx.project(ws.folder);
  if (!proj) {
    if (before !== "noproj" && mine === epoch && paintGen === seen && tab === "actions") redraw();
    return;
  }
  const res = await api(`/api/projects/actions?${new URLSearchParams({ folder: proj.folder, profile: ctx.currentProfile?.() || "" })}`);
  if (mine !== epoch || paintGen !== seen || tab !== "actions") return;
  const acc = ctx.profiles().find((p) => p.id === (proj.profile || ctx.currentProfile?.()));
  let account;
  if (acc?.account_actions) {
    try { account = (await api(`/api/accounts/${acc.id}/actions`)).actions || []; }
    catch { account = null; }
  }
  if (mine !== epoch || paintGen !== seen || tab !== "actions") return;
  if (el().querySelector(".pj-body")?.dataset.live === actionStamp(res.actions, res.routines, account)) return;
  redraw();
}

function renderNotes(body) {
  if (!ctx.project(ws.folder)) {
    body.append(h("p", { class: "pj-lead pad" }, "Les notes appartiennent à un projet (ou sont générales, depuis le bouton Notes). ",
      "Donne d'abord un nom à ce dossier pour y prendre des notes."),
    h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary",
      on: { click: async () => { if (await ctx.editProject(ws.folder, ws.profile)) render(); } } }, "En faire un projet")));
    return;
  }
  renderProjectNotes(body, ws.folder, () => { if (tab === "notes") render(); });
}

/** Any folder of the disk becomes a project: it joins the list of the request bar too. */
async function browse() {
  const path = await pickFolder({ title: "Dossier du projet", start: ws?.folder || "", recent: ctx.recentFolders(ws?.profile) });
  if (!path) { render(); return; }
  await load(path);
  if (ws && ws.folder) ctx.remember(ws.folder);
}

/** Take the folder out of the console's lists. The disk is not touched; choosing the folder again brings it back. */
async function forgetFolder(proj) {
  const what = proj ? `Le projet « ${proj.name} » reste dans Projets (Réglages → Retirer le projet pour l'enlever aussi). ` : "";
  if (!(await confirmDialog("Retirer ce dossier de la liste ?",
    `${ws.folder} n'apparaîtra plus dans les listes de dossiers de ce compte. ${what}`
    + "Le dossier et ses fichiers ne changent pas ; le choisir à nouveau le remet dans la liste.", "Retirer de la liste"))) return;
  ctx.forgetFolder(ws.folder, ws.profile);
  await load(ws.folders[0]);
  toast("Dossier retiré de la liste (rien n'a changé sur le disque).", "ok");
}

/** The project points at another folder of the disk: its settings, notes, rules, routines and follow-up go with it. */
async function moveProject(proj) {
  const from = ws.folder;
  const parent = from.replace(/[\\/]+$/, "").replace(/[\\/][^\\/]*$/, "");
  const target = await pickFolder({ title: `Nouveau dossier du projet « ${proj.name} »`, start: parent || from, recent: ctx.recentFolders(ws.profile) });
  if (!target || fkey(target) === fkey(from)) { render(); return; }
  if (!(await confirmDialog("Changer le dossier du projet ?",
    `« ${proj.name} » pointera vers ${target}. Ses réglages, notes, règles, routines, actions validées, le suivi Odoo / Office `
    + "et la mémoire de Claude le suivent. Rien n'est déplacé ni supprimé sur le disque : déplace toi-même les fichiers si besoin. "
    + "Les discussions passées restent dans l'ancien dossier (menu d'une discussion → Déplacer pour en reprendre une).", "Changer de dossier"))) return;
  try {
    const saved = await api("/api/projects/move", { method: "POST", body: { folder: from, target } });
    ctx.projectMoved(ws.profile, from, saved.folder);
    await load(saved.folder);
    toast(`Projet « ${proj.name} » rattaché à ${saved.folder}.`, "ok");
  } catch (e) { toast(e.message, "err"); }
}

function render() {
  paintGen++;
  const d = el();
  if (d.hidden) return;
  const proj = ws ? ctx.project(ws.folder) : null;
  paint(d, proj ? tint() : null); // a project's panel: the colors of the account and of the project
  const head = h("div", { class: "drawer-head" }, h("h2", {}, proj ? h("span", { class: "pj-name", style: { "--pc": proj.color } }, proj.name) : "Projet"),
    ws ? h("span", { class: "badge", style: { "--pc": ctx.profiles().find((p) => p.id === ws.profile)?.color || "var(--accent)" } }, ws.profile_name) : null,
    ws ? h("button", { type: "button", class: "btn small", title: proj ? "Nom, couleur, compte, autorisations et modèle par défaut" : "Donner un nom et des réglages par défaut à ce dossier",
      on: { click: async () => { if (await ctx.editProject(ws.folder, ws.profile)) render(); } } }, proj ? "Réglages" : "En faire un projet") : null,
    h("button", { type: "button", class: "icon-btn", title: "Ouvrir le dossier dans l'explorateur", svg: "folder", disabled: !ws,
      on: { click: () => reveal("") } }),
    h("button", { type: "button", class: "icon-btn", title: "Actualiser", svg: "retry", on: { click: () => load(ws?.folder ?? null) } }),
    h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } }));
  if (!ws) { d.replaceChildren(head, h("div", { class: "empty-row" }, loading ? "Chargement…" : "Projet indisponible.")); return; }
  const home = ws.folders[0];
  const hidden = ctx.hiddenFolders?.(ws.profile) || new Set();
  const folders = [...new Set([...ws.folders, ...ctx.workdirOptions(), ws.folder])]
    .filter((f) => f === home || f === ws.folder || !hidden.has(fkey(f)));
  const sel = h("select", { class: "pj-folder", title: ws.folder }, ...folders.map((f, i) =>
    h("option", { value: f, title: f }, i === 0 ? `${baseName(f)} (dossier du compte)` : baseName(f))),
    h("option", { value: "__other__" }, "Autre dossier…"));
  sel.value = ws.folder;
  sel.addEventListener("change", () => (sel.value === "__other__" ? browse() : load(sel.value)));
  const tabs = h("div", { class: "pj-tabs", role: "tablist" }, ...TABS.map(([id, label]) => {
    const c = tabCount(id);
    return h("button", {
      type: "button", role: "tab", class: tab === id ? "on" : "", "aria-selected": String(tab === id), "data-tab": id,
      on: { click: () => { tab = id; note = null; render(); projectDiskChanged(); } } }, label, c ? countNode(c) : null);
  }));
  const body = h("div", { class: "pj-body" });
  const pick = h("button", { type: "button", class: "btn small", title: "Choisir un autre dossier sur le disque", on: { click: browse } }, "Parcourir…");
  const forget = fkey(ws.folder) === fkey(home) ? null : h("button", { type: "button", class: "btn small",
    title: "Retirer ce dossier des listes de la console (le dossier et ses fichiers ne changent pas)", on: { click: () => forgetFolder(proj) } }, "Retirer de la liste");
  const move = proj ? h("button", { type: "button", class: "btn small",
    title: "Faire pointer ce projet vers un autre dossier du disque (rien n'est déplacé sur le disque)", on: { click: () => moveProject(proj) } }, "Changer de dossier…") : null;
  d.replaceChildren(head, h("div", { class: "pj-where" }, h("div", { class: "pj-pick" }, sel, pick),
    h("small", { title: ws.folder }, ws.folder), forget || move ? h("div", { class: "pj-pick" }, move, forget) : null), tabs, body);
  ({ instructions: renderInstructions, memory: renderMemory, files: renderFiles, tasks: renderTasks, rules: renderRules,
    actions: (b) => renderActions(b, ctx, ws, () => { if (tab === "actions") render(); }), notes: renderNotes,
    suivi: renderSuivi })[tab](body);
}

/** Odoo and Office 365 of this project: tasks, drafts, follow-ups. */
function renderSuivi(body) {
  const proj = ctx.project(ws.folder);
  const again = () => { if (tab === "suivi") render(); };
  if (!proj) {
    body.append(h("p", { class: "pj-lead pad" }, "Le suivi d'un projet réunit ses tâches Odoo et, dans Office 365, "
      + "ce qu'il y a à faire, les brouillons et les relances. Donne d'abord un nom à ce dossier."),
      h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary",
        on: { click: async () => { if (await ctx.editProject(ws.folder, ws.profile)) render(); } } }, "En faire un projet")));
    return;
  }
  renderOdoo(body, ws.folder, { isProject: true, tint: tint(), rerender: again, makeProject: async () => {} });
  renderOffice(body, ws.folder, { rerender: again });
}

// ------------------------------------------------------------ instructions
function editor(title, lead, doc, save, extra = null) {
  const area = h("textarea", { class: "pj-text", spellcheck: "true", rows: "12", placeholder: "Aucune consigne pour l'instant." });
  area.value = doc.text;
  const btn = h("button", { type: "button", class: "btn small primary", disabled: true }, "Enregistrer");
  const state = h("span", { class: "muted" }, doc.exists ? "" : "fichier pas encore créé");
  area.addEventListener("input", () => { btn.disabled = area.value === doc.text; state.textContent = btn.disabled ? "" : "modifié"; });
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { await save(area.value); doc.text = area.value; doc.exists = true; state.textContent = "enregistré"; toast("Consignes enregistrées.", "ok"); }
    catch (e) { btn.disabled = false; toast(e.message, "err"); }
  });
  const tpl = !doc.text ? h("button", { type: "button", class: "btn small ghost", on: { click: () => {
    area.value = TEMPLATE; area.dispatchEvent(new Event("input")); area.focus(); } } }, "Partir d'un modèle") : null;
  return h("section", { class: "pj-card" }, h("h3", {}, title), h("p", { class: "pj-lead" }, lead),
    h("div", { class: "pj-path", title: doc.path }, doc.path), area, h("div", { class: "row" }, btn, tpl, state, extra));
}

function renderInstructions(body) {
  const prof = ws.instructions.profile, fold = ws.instructions.folder;
  body.append(
    editor("Consignes de ce dossier", "Lues au début de chaque discussion dans ce dossier : console, Claude Desktop (onglet Code) et CLI.",
      fold, (text) => api("/api/workspace/instructions", { method: "PUT", body: { ...scope(), scope: "folder", text } })),
    editor(`Consignes du compte ${ws.profile_name}`, "Pour tous les dossiers de ce compte, y compris l'onglet Code de Claude Desktop.",
      prof, (text) => api("/api/workspace/instructions", { method: "PUT", body: { ...scope(), scope: "profile", text } })),
    h("section", { class: "pj-card" }, h("h3", {}, "Consignes de la console pour ce profil"),
      h("p", { class: "pj-lead" }, "Ajoutées par Jarvis seulement (pas par Claude Desktop), avec les consignes de sécurité."),
      ws.jarvis_instructions?.trim() ? h("div", { class: "pj-md" }, mdElement(ws.jarvis_instructions)) : h("p", { class: "muted" }, "Aucune."),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn small", on: { click: () => ctx.openConfig("profiles") } }, "Modifier dans la configuration"))));
}

// ------------------------------------------------------------ memory
async function renderMemory(body) {
  body.append(h("p", { class: "pj-lead pad" }, "Ce que Claude Code a retenu d'une session à l'autre dans ce dossier (mémoire automatique). ",
    "Tu peux corriger ou supprimer une note ; pour une règle à suivre à coup sûr, écris-la plutôt dans les Consignes."));
  if (note) {
    const area = h("textarea", { class: "pj-text tall", spellcheck: "true" });
    area.value = note.text;
    const save = h("button", { type: "button", class: "btn small primary", disabled: true, on: { click: async () => {
      try { await api("/api/workspace/memory", { method: "PUT", body: { ...scope(), name: note.name, text: area.value } }); note.text = area.value; save.disabled = true; toast("Note enregistrée.", "ok"); }
      catch (e) { toast(e.message, "err"); } } } }, "Enregistrer");
    area.addEventListener("input", () => { save.disabled = area.value === note.text; });
    const del = h("button", { type: "button", class: "btn small danger", on: { click: async () => {
      if (!(await confirmDialog("Supprimer cette note ?", `${note.name} sera effacée de la mémoire de Claude pour ce dossier.`, "Supprimer", "danger", tint()))) return;
      try { await api(`/api/workspace/memory?${new URLSearchParams({ ...scope(), name: note.name })}`, { method: "DELETE" }); note = null; load(ws.folder); }
      catch (e) { toast(e.message, "err"); } } } }, "Supprimer");
    body.append(h("section", { class: "pj-card" }, h("div", { class: "row" },
      h("button", { type: "button", class: "btn small ghost", on: { click: () => { note = null; render(); } } }, "← Notes"), h("b", {}, note.name)),
      area, h("div", { class: "row" }, save, del)));
    return;
  }
  const files = ws.memory.files;
  if (!files.length) {
    body.append(h("div", { class: "empty-row" }, `Claude n'a encore rien mémorisé pour ce dossier avec le compte ${ws.profile_name}. `,
      "Il n'écrit ici que ce qui lui semble utile pour les prochaines sessions ; demande-lui « retiens que… » pour qu'il le fasse."));
    return;
  }
  body.append(h("div", { class: "drawer-list flat" }, ...files.map((f) => h("div", { class: "hrow", style: { "--pc": "var(--violet)" },
    on: { click: async () => {
      try { note = await api(`/api/workspace/memory?${new URLSearchParams({ ...scope(), name: f.name })}`); render(); }
      catch (e) { toast(e.message, "err"); } } } },
    h("div", { class: "hm" }, h("div", { class: "ht" }, f.name === "MEMORY.md" ? "MEMORY.md — index" : f.name),
      h("div", { class: "hs" }, [fmtSize(f.size), fmtDate(f.mtime)].join(" · ")))))));
}

// ------------------------------------------------------------ files
function nativePath(rel) {
  const sep = ws.folder.includes("\\") ? "\\" : "/";
  return `${ws.folder}${sep}${rel.split("/").join(sep)}`;
}

function reveal(path, show = false) {
  api("/api/workspace/file/open", { method: "POST", body: { ...scope(), path, reveal: show } }).catch((e) => toast(e.message, "err"));
}

async function renderFiles(body) {
  const gen = ++filesGen;
  fileSig = null;
  const list = h("div", { class: "drawer-list flat pj-files" }, h("div", { class: "empty-row" }, "Lecture du dossier…"));
  const crumbs = h("div", { class: "pj-crumbs" });
  body.append(crumbs, list);
  const parts = sub ? sub.split("/") : [];
  crumbs.append(h("button", { type: "button", class: "lnk", on: { click: () => { sub = ""; render(); } } }, baseName(ws.folder)),
    ...parts.flatMap((p, i) => [h("span", { class: "muted" }, "/"),
      h("button", { type: "button", class: "lnk", on: { click: () => { sub = parts.slice(0, i + 1).join("/"); render(); } } }, p)]));
  let res;
  try { res = await api(`/api/workspace/files?${new URLSearchParams({ ...scope(), sub })}`); }
  catch (e) { if (gen === filesGen && list.isConnected) list.replaceChildren(h("div", { class: "line err" }, e.message)); return; }
  if (gen !== filesGen || !list.isConnected) return;
  fileSig = fileSignature(res);
  list.replaceChildren(...fileRows(res));
}

// ------------------------------------------------------------ discussions
const ORIGIN = { desktop: "Claude Desktop", cli: "CLI", console: "Console" };

/** Any session of the account (console, Claude Desktop, CLI) moves into this project and continues here. */
const sameFolder = (a, b) => String(a || "").replace(/[\\/]+$/, "").toLowerCase() === String(b || "").replace(/[\\/]+$/, "").toLowerCase();

/** Any session of the account (console, Claude Desktop, CLI) moves into this project: the same session. */
async function bringSession() {
  const folder = ws.folder, pid = ws.profile;
  let rows = [], chosen = null;
  const search = h("input", { type: "text", placeholder: "Rechercher une session…" });
  const list = h("div", { class: "ctx-list" }, h("div", { class: "muted" }, "Lecture des sessions…"));
  const msg = h("textarea", { rows: "2", placeholder: "Facultatif : un message pour la reprendre tout de suite" });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = rows.filter((r) => !sameFolder(r.cwd, folder) && (!q || `${r.title} ${r.first_prompt} ${r.cwd}`.toLowerCase().includes(q))).slice(0, 200);
    list.replaceChildren(...(shown.length ? shown.map((r) => {
      const radio = h("input", { type: "radio", name: "bring" });
      radio.checked = chosen?.id === r.id;
      radio.addEventListener("change", () => { chosen = r; });
      return h("label", { class: "ctx-row" }, radio, h("span", { class: "ctx-t" }, h("b", {}, r.title),
        h("small", {}, [ORIGIN[r.origin] || r.origin, baseName(r.cwd) || "dossier inconnu", fmtDate(r.updated)].filter(Boolean).join(" · "))));
    }) : [h("div", { class: "muted" }, rows.length ? "Aucune session ne correspond." : "Aucune autre session pour ce compte.")]));
  };
  search.addEventListener("input", draw);
  api(`/api/sessions?profile=${encodeURIComponent(pid)}`).then((r) => { rows = r.sessions || []; draw(); })
    .catch((e) => list.replaceChildren(h("div", { class: "line err" }, e.message)));
  const ok = await dialog({
    title: `Déplacer une session dans ${baseName(folder)}`,
    body: h("div", { class: "ctx-pick" }, h("p", { class: "muted ctx-note" },
      "La session elle-même passe dans ce projet (même session, pas de copie) ; Claude Desktop suit. Ferme-la d'abord dans Claude Desktop si elle y est ouverte."),
    search, list, msg),
    buttons: [{ label: "Annuler", value: false }, { label: "Déplacer ici", value: true, cls: "primary" }],
    onOpen: (box) => { box.classList.add("ctx-dialog"); search.focus(); }, tint: tint(),
  });
  if (!ok) return;
  if (!chosen) { toast("Choisis la session à déplacer.", "warn"); return; }
  try {
    const r = await api(`/api/sessions/${pid}/${chosen.id}/move`, { method: "POST", body: { workdir: folder } });
    ctx.remember(r.folder, pid);
    toast(r.desktop ? "Session déplacée (Claude Desktop mis à jour)." : "Session déplacée.", "ok");
  } catch (e) { toast(e.message, "err"); return; }
  if (msg.value.trim()) await ctx.launch(`/api/sessions/${pid}/${chosen.id}/resume`, { prompt: msg.value.trim(), fork: false });
  render();
}

async function resumeHere(r) {
  const text = await dialog({ title: `Reprendre « ${r.title} »`, body: "La même session continue dans la console.",
    input: { placeholder: "Ton message pour la continuer" }, tint: tint(),
    buttons: [{ label: "Annuler", value: null }, { label: "Reprendre", value: true, cls: "primary" }] });
  if (text && text.trim()) ctx.launch(`/api/sessions/${r.profile}/${r.id}/resume`, { prompt: text.trim(), fork: false });
}

let showArchived = false;

function renderTasks(body) {
  const all = ctx.tasks().filter((t) => t.profile === ws.profile && sameFolder(t.workdir, ws.folder) && t.origin !== "reglage" && !t.ephemeral)
    .sort((a, b) => (b.created || 0) - (a.created || 0));
  const archived = all.filter((t) => t.archived);
  const mine = showArchived ? archived : all.filter((t) => !t.archived);
  const idle = (t) => !["queued", "running", "awaiting"].includes(t.status);
  body.append(h("p", { class: "pj-lead pad" }, "Discussions de ce dossier : celles de la console, puis celles de Claude Desktop et de la CLI. ",
    "« Contexte » joint une discussion à ta prochaine demande ; « Copie » repart de tout son contexte dans une nouvelle fenêtre."),
  h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary", on: { click: bringSession } },
    "Déplacer une session ici…")));
  const btn = (label, title, fn, disabled = false) => h("button", { type: "button", class: "btn small ghost", title, disabled,
    on: { click: (e) => { e.stopPropagation(); fn(); } } }, label);
  const others = h("div", { class: "drawer-list flat" });
  const done = mine.filter((t) => !t.archived && idle(t));
  const bar = archived.length || done.length ? h("div", { class: "row pad" },
    archived.length || showArchived ? h("button", { type: "button", class: `btn small${showArchived ? " primary" : ""}`,
      title: "Les discussions archivées restent ici, dans l'historique et dans Ctrl+K",
      on: { click: () => { showArchived = !showArchived; render(); } } },
    showArchived ? "Retour aux discussions" : `Archivées (${archived.length})`) : null,
    !showArchived && done.length > 1 ? btn("Archiver les terminées", "Archiver toutes les discussions terminées de ce dossier",
      () => ctx.archive(done.map((t) => t.id), true)) : null) : null;
  body.append(...[bar, h("div", { class: "drawer-list flat" }, ...(mine.length ? mine.map((t) => h("div", { class: "hrow", style: { "--pc": t.color },
    on: { click: () => ctx.openTask(t.id) } },
    h("div", { class: "hm" }, h("div", { class: "ht" }, t.title), h("div", { class: "hs" }, [STATUS[t.status], fmtDate(t.created), t.preset_name,
      t.archived ? `archivée ${fmtDate(t.archived)}` : ""].filter(Boolean).join(" · "))),
    btn("Contexte", "Joindre cette discussion à ta prochaine demande", () => ctx.addContext(t), !t.resumable),
    btn("Copie", "Nouvelle discussion à partir de celle-ci", () => ctx.fork(t), !t.resumable),
    t.archived ? btn("Désarchiver", "Remettre cette discussion dans les listes courantes", () => ctx.archive([t.id], false))
      : btn("Archiver", idle(t) ? "Ranger cette discussion : elle reste dans « Archivées » et dans Ctrl+K" : "Une discussion en cours ne s'archive pas",
        () => ctx.archive([t.id], true), !idle(t))))
    : [h("div", { class: "empty-row" }, showArchived ? "Aucune discussion archivée dans ce dossier." : "Aucune discussion de la console dans ce dossier.")])),
  showArchived ? null : others].filter(Boolean),
  h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small", on: { click: () => ctx.sessions() } },
    "Toutes les sessions du compte…")));
  // sessions of this folder not driven by the console (Claude Desktop, CLI)
  sessionSig = null;
  const ticket = epoch;
  api(`/api/sessions?profile=${encodeURIComponent(ws.profile)}`).then(({ sessions }) => {
    if (ticket !== epoch || !others.isConnected) return;
    const rows = externalSessions(sessions);
    sessionSig = sessionSignature(rows);
    others.replaceChildren(...rows.map((r) => h("div", { class: "hrow", style: { "--pc": "var(--violet)" } },
      h("div", { class: "hm" }, h("div", { class: "ht" }, r.title), h("div", { class: "hs" }, [ORIGIN[r.origin] || r.origin, fmtDate(r.updated),
        `${r.prompts} demande${r.prompts > 1 ? "s" : ""}`].join(" · "))),
      btn("Contexte", "Joindre cette session à ta prochaine demande", () => ctx.addContext({ profile: r.profile, session_id: r.id, title: r.title, session_started: true })),
      btn("Reprendre", "Continuer cette session dans la console", () => resumeHere(r)))));
  }).catch(() => {});
}

// ------------------------------------------------------------ remembered rules
function renderRules(body) {
  body.append(h("p", { class: "pj-lead pad" }, "Actions autorisées sans validation pour les discussions de ce dossier, mémorisées avec « Toujours pour ce projet ». ",
    "Les chemins protégés et les refus permanents s'appliquent toujours."));
  const rules = ws.rules || [];
  if (!rules.length) {
    body.append(h("div", { class: "empty-row" }, "Aucune règle pour ce projet. Elles se créent depuis une demande de validation."));
    return;
  }
  body.append(h("div", { class: "drawer-list flat" }, ...rules.map((r) => h("div", { class: "hrow file", style: { "--pc": "var(--ok)" } },
    h("span", { class: "i", svg: "shield" }),
    h("div", { class: "hm" }, h("div", { class: "ht mono" }, r.pattern), h("div", { class: "hs" }, r.created ? `mémorisée ${fmtDate(r.created)}` : "")),
    h("button", { type: "button", class: "btn small ghost", title: "Ne plus autoriser sans validation", on: { click: async () => {
      if (!(await confirmDialog("Retirer cette règle ?", `${r.pattern} demandera de nouveau une validation dans ce projet.`, "Retirer", "danger", tint()))) return;
      try { await api(`/api/workspace/rules?${new URLSearchParams({ ...scope(), pattern: r.pattern })}`, { method: "DELETE" }); load(ws.folder); }
      catch (e) { toast(e.message, "err"); }
    } } }, "Retirer")))));
}
