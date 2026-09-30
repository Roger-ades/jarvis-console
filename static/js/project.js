// Project = a working folder of an account, like a claude.ai Project: its instructions
// (CLAUDE.md of the folder and of the account), what Claude remembers there, its files and
// its discussions. The panel follows the folder chosen in the request bar.
import { api } from "./api.js";
import { fmtSize } from "./attach.js";
import { pickFolder } from "./folderpicker.js";
import { mdElement } from "./md.js";
import { STATUS, confirmDialog, dialog, fmtDate, h, toast } from "./util.js";
import { openPreview } from "./viewer.js";

const TABS = [["instructions", "Consignes"], ["memory", "Mémoire"], ["files", "Fichiers"], ["tasks", "Discussions"], ["rules", "Règles"]];
const TEMPLATE = "# Contexte\n\nÀ quoi sert ce dossier, pour qui, avec quels outils.\n\n# Règles\n\n- \n\n# Fichiers importants\n\n- \n";
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const join = (a, b) => (a ? `${a}/${b}` : b);

let ctx = null, ws = null, tab = "instructions", sub = "", note = null, loading = false;
const el = () => document.getElementById("project");

export function toggleProject(context) {
  ctx = context;
  const d = el();
  if (!d.hidden) { d.hidden = true; return; }
  ctx.closeDrawers?.("project");
  d.hidden = false;
  load();
}

/** The request bar changed account or folder: follow it while open. */
export function projectFollow() { if (ctx && !el().hidden) load(); }

async function load(folder = null) {
  loading = true;
  render();
  const pid = ctx.currentProfile();
  const q = new URLSearchParams({ profile: pid });
  const f = folder ?? ctx.workdir();
  if (f) q.set("folder", f);
  try {
    ws = await api(`/api/workspace?${q}`);
    if (folder !== null) ctx.setWorkdir(ws.folder, ws.folder === ws.folders[0]);
  } catch (e) { toast(e.message, "err"); ws = null; }
  sub = "";
  note = null;
  loading = false;
  render();
}

const scope = () => ({ profile: ws.profile, folder: ws.folder });

/** Any folder of the disk becomes a project: it joins the list of the request bar too. */
async function browse() {
  const path = await pickFolder({ title: "Dossier du projet", start: ws?.folder || "", recent: ctx.recentFolders(ws?.profile) });
  if (!path) { render(); return; }
  await load(path);
  if (ws && ws.folder) ctx.remember(ws.folder);
}

function render() {
  const d = el();
  if (d.hidden) return;
  const proj = ws ? ctx.project(ws.folder) : null;
  const head = h("div", { class: "drawer-head" }, h("h2", {}, proj ? h("span", { class: "pj-name", style: { "--pc": proj.color } }, proj.name) : "Projet"),
    ws ? h("span", { class: "badge", style: { "--pc": ctx.profiles().find((p) => p.id === ws.profile)?.color || "var(--accent)" } }, ws.profile_name) : null,
    ws ? h("button", { type: "button", class: "btn small", title: proj ? "Nom, couleur, compte, autorisations et modèle par défaut" : "Donner un nom et des réglages par défaut à ce dossier",
      on: { click: async () => { if (await ctx.editProject(ws.folder, ws.profile)) render(); } } }, proj ? "Réglages" : "En faire un projet") : null,
    h("button", { type: "button", class: "icon-btn", title: "Ouvrir le dossier dans l'explorateur", svg: "folder", disabled: !ws,
      on: { click: () => reveal("") } }),
    h("button", { type: "button", class: "icon-btn", title: "Actualiser", svg: "retry", on: { click: () => load(ws?.folder ?? null) } }),
    h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } }));
  if (!ws) { d.replaceChildren(head, h("div", { class: "empty-row" }, loading ? "Chargement…" : "Projet indisponible.")); return; }
  const folders = [...new Set([...ws.folders, ...ctx.workdirOptions(), ws.folder])];
  const sel = h("select", { class: "pj-folder", title: ws.folder }, ...folders.map((f, i) =>
    h("option", { value: f, title: f }, i === 0 ? `${baseName(f)} (dossier du compte)` : baseName(f))),
    h("option", { value: "__other__" }, "Autre dossier…"));
  sel.value = ws.folder;
  sel.addEventListener("change", () => (sel.value === "__other__" ? browse() : load(sel.value)));
  const tabs = h("div", { class: "pj-tabs", role: "tablist" }, ...TABS.map(([id, label]) => h("button", {
    type: "button", role: "tab", class: tab === id ? "on" : "", "aria-selected": String(tab === id),
    on: { click: () => { tab = id; note = null; render(); } } }, label, id === "memory" && ws.memory.files.length ? h("span", { class: "cnt" }, String(ws.memory.files.length))
      : id === "rules" && ws.rules?.length ? h("span", { class: "cnt" }, String(ws.rules.length)) : null)));
  const body = h("div", { class: "pj-body" });
  const pick = h("button", { type: "button", class: "btn small", title: "Choisir un autre dossier sur le disque", on: { click: browse } }, "Parcourir…");
  d.replaceChildren(head, h("div", { class: "pj-where" }, h("div", { class: "pj-pick" }, sel, pick), h("small", { title: ws.folder }, ws.folder)), tabs, body);
  ({ instructions: renderInstructions, memory: renderMemory, files: renderFiles, tasks: renderTasks, rules: renderRules })[tab](body);
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
      if (!(await confirmDialog("Supprimer cette note ?", `${note.name} sera effacée de la mémoire de Claude pour ce dossier.`, "Supprimer", "danger"))) return;
      try { await api(`/api/workspace/memory?${new URLSearchParams({ ...scope(), name: note.name })}`, { method: "DELETE" }); note = null; load(ws.folder); }
      catch (e) { toast(e.message, "err"); } } } }, "Supprimer");
    body.append(h("section", { class: "pj-card" }, h("div", { class: "row" },
      h("button", { type: "button", class: "btn small ghost", on: { click: () => { note = null; render(); } } }, "← Notes"), h("b", {}, note.name)),
      area, h("div", { class: "row" }, save, del)));
    return;
  }
  const files = ws.memory.files;
  if (!files.length) { body.append(h("div", { class: "empty-row" }, "Claude n'a encore rien mémorisé pour ce dossier.")); return; }
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
  const list = h("div", { class: "drawer-list flat" }, h("div", { class: "empty-row" }, "Lecture du dossier…"));
  const crumbs = h("div", { class: "pj-crumbs" });
  body.append(crumbs, list);
  const parts = sub ? sub.split("/") : [];
  crumbs.append(h("button", { type: "button", class: "lnk", on: { click: () => { sub = ""; render(); } } }, baseName(ws.folder)),
    ...parts.flatMap((p, i) => [h("span", { class: "muted" }, "/"),
      h("button", { type: "button", class: "lnk", on: { click: () => { sub = parts.slice(0, i + 1).join("/"); render(); } } }, p)]));
  let res;
  try { res = await api(`/api/workspace/files?${new URLSearchParams({ ...scope(), sub })}`); }
  catch (e) { list.replaceChildren(h("div", { class: "line err" }, e.message)); return; }
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
  list.replaceChildren(...(rows.length ? rows : [h("div", { class: "empty-row" }, "Dossier vide.")]),
    ...(res.truncated ? [h("div", { class: "muted pad" }, "1 000 premiers éléments affichés.")] : []));
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
    onOpen: (box) => { box.classList.add("ctx-dialog"); search.focus(); },
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
    input: { placeholder: "Ton message pour la continuer" },
    buttons: [{ label: "Annuler", value: null }, { label: "Reprendre", value: true, cls: "primary" }] });
  if (text && text.trim()) ctx.launch(`/api/sessions/${r.profile}/${r.id}/resume`, { prompt: text.trim(), fork: false });
}

function renderTasks(body) {
  const mine = ctx.tasks().filter((t) => t.profile === ws.profile && sameFolder(t.workdir, ws.folder))
    .sort((a, b) => (b.created || 0) - (a.created || 0));
  body.append(h("p", { class: "pj-lead pad" }, "Discussions de ce dossier : celles de la console, puis celles de Claude Desktop et de la CLI. ",
    "« Contexte » joint une discussion à ta prochaine demande ; « Copie » repart de tout son contexte dans une nouvelle fenêtre."),
  h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary", on: { click: bringSession } },
    "Déplacer une session ici…")));
  const btn = (label, title, fn, disabled = false) => h("button", { type: "button", class: "btn small ghost", title, disabled,
    on: { click: (e) => { e.stopPropagation(); fn(); } } }, label);
  const others = h("div", { class: "drawer-list flat" });
  body.append(h("div", { class: "drawer-list flat" }, ...(mine.length ? mine.map((t) => h("div", { class: "hrow", style: { "--pc": t.color },
    on: { click: () => ctx.openTask(t.id) } },
    h("div", { class: "hm" }, h("div", { class: "ht" }, t.title), h("div", { class: "hs" }, [STATUS[t.status], fmtDate(t.created), t.preset_name].filter(Boolean).join(" · "))),
    btn("Contexte", "Joindre cette discussion à ta prochaine demande", () => ctx.addContext(t), !t.resumable),
    btn("Copie", "Nouvelle discussion à partir de celle-ci", () => ctx.fork(t), !t.resumable)))
    : [h("div", { class: "empty-row" }, "Aucune discussion de la console dans ce dossier.")])),
  others,
  h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small", on: { click: () => ctx.sessions() } },
    "Toutes les sessions du compte…")));
  // sessions of this folder not driven by the console (Claude Desktop, CLI)
  const known = new Set(ctx.tasks().map((t) => t.session_id));
  api(`/api/sessions?profile=${encodeURIComponent(ws.profile)}`).then(({ sessions }) => {
    const rows = (sessions || []).filter((r) => sameFolder(r.cwd, ws.folder) && !known.has(r.id));
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
    "Les chemins protégés, les refus permanents et les contraintes Odoo s'appliquent toujours."));
  const rules = ws.rules || [];
  if (!rules.length) {
    body.append(h("div", { class: "empty-row" }, "Aucune règle pour ce projet. Elles se créent depuis une demande de validation."));
    return;
  }
  body.append(h("div", { class: "drawer-list flat" }, ...rules.map((r) => h("div", { class: "hrow file", style: { "--pc": "var(--ok)" } },
    h("span", { class: "i", svg: "shield" }),
    h("div", { class: "hm" }, h("div", { class: "ht mono" }, r.pattern), h("div", { class: "hs" }, r.created ? `mémorisée ${fmtDate(r.created)}` : "")),
    h("button", { type: "button", class: "btn small ghost", title: "Ne plus autoriser sans validation", on: { click: async () => {
      if (!(await confirmDialog("Retirer cette règle ?", `${r.pattern} demandera de nouveau une validation dans ce projet.`, "Retirer", "danger"))) return;
      try { await api(`/api/workspace/rules?${new URLSearchParams({ ...scope(), pattern: r.pattern })}`, { method: "DELETE" }); load(ws.folder); }
      catch (e) { toast(e.message, "err"); }
    } } }, "Retirer")))));
}
