// The page Compétences: the skills of an account or of a project, the sheet of one, and a discussion with
// Claude to write and improve them. Claude only proposes (tool retenir): nothing is written without a click on
// the differences. The discussion is a normal one (Discussions), shown inside the page (TaskWindow.embed).
// The list is a tree of groups ("Métier/Incendie"), kept by JARVIS: only how it is shown, the skills stay flat.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { projects } from "./projects.js";
import { slashDescription } from "./slash-fr.js";
import { $, fmtDate, h, iconBtn, store, toast } from "./util.js";

const el = () => $("#skills");
const TARGET_KEY = "jarvis.skills.target", TALKS_KEY = "jarvis.skills.talks", CLOSED_KEY = "jarvis.skills.closed";
const DRAG = "text/x-jarvis-skill", MODEL_KEY = "jarvis.skills.model";
const S = {
  ctx: null, target: null, list: [], groups: [], editing: null, loading: false, error: "", q: "", sort: store.get("jarvis.skills.sort", "nom"),
  selected: null, file: "SKILL.md", fileData: null, fileError: "", embedded: null, fresh: false,
};

/** Open the page, on a project (folder) or an account (profile) when given, on one of its skills. */
export function openCompetences(ctx, { folder = "", profile = "", skill = "" } = {}) {
  S.ctx = ctx;
  if (folder) setTarget({ kind: "projet", id: folder });
  else if (profile) setTarget({ kind: "compte", id: profile });
  if (skill && skill !== S.selected) { release(); S.selected = skill; S.file = "SKILL.md"; S.fileData = null; }
  toggleCompetences(ctx, true);
}

export function toggleCompetences(ctx, open) {
  const d = el();
  if (!d) return;
  S.ctx = ctx;
  const show = open === true || (open !== false && d.hidden);
  if (!show) { close(); return; }
  ctx.closeDrawers?.("skills");
  d.hidden = false;
  if (!S.target || !validTarget(S.target)) S.target = validTarget(store.get(TARGET_KEY)) ? store.get(TARGET_KEY) : defaultTarget();
  render();
  load();
}

/** A skill was written (tool retenir, after the click): the list and the sheet are read again. */
export function skillsChanged({ competence } = {}) {
  const d = el();
  if (!d || d.hidden) return;
  load(competence);
}

function close() {
  const d = el();
  if (d) d.hidden = true;
  release();
}

// ------------------------------------------------------------ target: an account or a project
const profiles = () => S.ctx?.profiles?.() || [];
const targetKey = (t = S.target) => `${t?.kind}:${t?.id}`;
function validTarget(t) {
  if (!t?.id) return false;
  return t.kind === "projet" ? projects().some((p) => p.folder === t.id) : profiles().some((p) => p.id === t.id);
}
function defaultTarget() { return { kind: "compte", id: S.ctx?.currentProfile?.() || profiles()[0]?.id || "" }; }
function setTarget(t) {
  if (targetKey(t) === targetKey()) return;
  release();
  S.target = t;
  S.selected = null;
  S.list = [];
  S.groups = [];
  S.editing = null;
  S.fileData = null;
  store.set(TARGET_KEY, t);
}
function targetName(t = S.target) {
  if (t?.kind === "projet") return `du projet « ${projects().find((p) => p.folder === t.id)?.name || t.id} »`;
  return `du compte ${profiles().find((p) => p.id === t?.id)?.name || ""}`.trim();
}
const targetParams = (extra = {}) => new URLSearchParams(S.target.kind === "projet"
  ? { folder: S.target.id, ...extra } : { profile: S.target.id, ...extra });
const targetProfile = () => (S.target?.kind === "projet" ? projects().find((p) => p.folder === S.target.id)?.profile : S.target?.id) || "";
function targetColor() {
  const pid = targetProfile();
  return (S.target?.kind === "projet" && projects().find((p) => p.folder === S.target.id)?.color)
    || profiles().find((p) => p.id === pid)?.color || "var(--accent)";
}

// ------------------------------------------------------------ data
async function load(written = "") {
  const key = targetKey(), before = new Set(S.list.map((s) => s.name));
  S.loading = true;
  renderList();
  try {
    const r = await api(`/api/skills?${targetParams()}`);
    if (key !== targetKey()) return;
    S.list = (S.target.kind === "projet" ? r.projet : r.compte) || [];
    S.groups = r.groupes?.[S.target.kind] || [];
    S.error = "";
  } catch (e) { S.error = e.message; S.list = []; }
  finally { S.loading = false; }
  // a skill just created from the discussion "Nouvelle compétence": the page follows it, with its discussion
  if (written && !S.selected && !before.has(written) && S.list.some((s) => s.name === written)) {
    const talks = store.get(TALKS_KEY, {}) || {};
    const id = talks[`${key}|`];
    if (id) { talks[`${key}|${written}`] = id; delete talks[`${key}|`]; store.set(TALKS_KEY, talks); }
    S.selected = written;
    S.file = "SKILL.md";
  }
  if (S.selected && !S.list.some((s) => s.name === S.selected)) S.selected = null;
  renderList();
  renderSheet();
  if (S.selected && (!written || written === S.selected)) readFile();
  renderTalk();
}

async function readFile() {
  const name = S.selected, file = S.file, key = targetKey();
  S.fileData = null;
  S.fileError = "";
  renderFile();
  try {
    const r = await api(`/api/skills/file?${targetParams({ name, path: file })}`);
    if (name !== S.selected || file !== S.file || key !== targetKey()) return;
    S.fileData = r;
  } catch (e) { S.fileError = e.message; }
  renderFile();
}

// ------------------------------------------------------------ the page
function render() {
  const d = el();
  if (!d || d.hidden) return;
  const head = h("div", { class: "drawer-head" },
    h("h2", {}, "Compétences"),
    h("span", { class: "muted" }, "Ce que Claude sait faire, et une discussion pour l'écrire"),
    iconBtn("close", "Fermer", () => close()));
  S.listCol = h("section", { class: "sk-col sk-list", "aria-label": "Liste des compétences" });
  S.sheetCol = h("section", { class: "sk-col sk-sheet", "aria-label": "Fiche de la compétence" });
  S.talkCol = h("section", { class: "sk-col sk-talk", "aria-label": "Discussion avec Claude" });
  d.replaceChildren(head, h("div", { class: "sk-layout", style: { "--pc": targetColor() } }, S.listCol, S.sheetCol, S.talkCol));
  renderList(true);
  renderSheet();
  renderTalk();
}

function renderList(full = false) {
  const col = S.listCol;
  if (!col?.isConnected) return;
  if (full || !col.firstChild) {
    const sel = h("select", { class: "sk-target", "aria-label": "Compétences de", on: { change: (e) => {
      const [kind, ...rest] = e.target.value.split(":");
      setTarget({ kind, id: rest.join(":") });
      render();
      load();
    } } },
    h("optgroup", { label: "Comptes : pour tous leurs projets" },
      ...profiles().map((p) => h("option", { value: `compte:${p.id}` }, p.name))),
    projects().length ? h("optgroup", { label: "Projets : dans leur dossier seulement" },
      ...projects().map((p) => h("option", { value: `projet:${p.folder}` }, p.name))) : null);
    sel.value = targetKey();
    const search = h("input", { type: "search", class: "sk-search", placeholder: "Filtrer…", value: S.q, "aria-label": "Filtrer les compétences",
      on: { input: (e) => { S.q = e.target.value; fillRows(); } } });
    const sort = h("select", { "aria-label": "Trier", on: { change: (e) => { S.sort = e.target.value; store.set("jarvis.skills.sort", S.sort); fillRows(); } } },
      h("option", { value: "nom" }, "Par nom"), h("option", { value: "date" }, "Par modification"));
    sort.value = S.sort;
    S.rows = h("div", { class: "drawer-list flat sk-rows" });
    S.listNote = h("p", { class: "sk-note" });
    col.replaceChildren(sel, S.listNote, h("div", { class: "sk-tools" }, search, sort),
      h("div", { class: "sk-new" },
        h("button", { type: "button", class: "btn small", on: { click: () => select(null) } }, "Nouvelle compétence"),
        h("button", { type: "button", class: "btn small ghost", title: "Un groupe pour ranger la liste : glisse ensuite les compétences dessus",
          on: { click: () => { S.editing = { path: "", parent: "" }; fillRows(); } } }, "Nouveau groupe")),
      S.rows);
  }
  S.listNote.textContent = S.target?.kind === "projet"
    ? "Chargées seulement quand Claude travaille dans ce dossier (.claude/skills)."
    : "Chargées dans tous les projets de ce compte (skills/ du dossier de configuration).";
  fillRows();
}

function fillRows() {
  if (!S.rows) return;
  if (S.error) { S.rows.replaceChildren(h("div", { class: "line err" }, S.error)); return; }
  if (S.loading && !S.list.length) { S.rows.replaceChildren(h("div", { class: "empty-row" }, "Lecture des compétences…")); return; }
  const q = S.q.trim().toLowerCase();
  const list = S.list.filter((s) => !q || `${s.name} ${s.description} ${s.groupe || ""}`.toLowerCase().includes(q))
    .sort(S.sort === "date" ? (a, b) => (b.modified || 0) - (a.modified || 0) : (a, b) => a.name.localeCompare(b.name));
  if (!list.length && (q || !S.groups.length) && !S.editing) {
    S.rows.replaceChildren(h("div", { class: "empty-row" }, S.list.length ? "Aucune compétence ne correspond."
      : `Aucune compétence ${targetName()}. « Nouvelle compétence » pour en écrire une avec Claude.`));
    return;
  }
  // the tree: every group (the empty ones too, but not while filtering), its sub-groups, then its skills
  const node = (path) => ({ path, name: path.split("/").pop(), groups: new Map(), skills: [], count: 0 });
  const root = node("");
  const at = (path) => path.split("/").reduce((n, part, i, all) => {
    if (!n.groups.has(part)) n.groups.set(part, node(all.slice(0, i + 1).join("/")));
    return n.groups.get(part);
  }, root);
  if (!q) S.groups.forEach(at);
  for (const s of list) (s.groupe ? at(s.groupe) : root).skills.push(s);
  const count = (n) => (n.count = n.skills.length + [...n.groups.values()].reduce((a, c) => a + count(c), 0));
  count(root);
  // dropped on the list itself (not on a group): out of any group
  S.rows.ondragover = (e) => { if (dragged(e)) { e.preventDefault(); S.rows.classList.add("drop"); } };
  S.rows.ondragleave = (e) => { if (!S.rows.contains(e.relatedTarget)) S.rows.classList.remove("drop"); };
  S.rows.ondrop = (e) => { S.rows.classList.remove("drop"); drop(e, ""); };
  S.rows.replaceChildren(...[
    S.editing && !S.editing.path && !S.editing.parent ? groupInput("", "") : null,
    ...branch(root, q),
  ].filter(Boolean));
}

const closed = () => new Set(store.get(CLOSED_KEY, []) || []);
function toggleGroup(path, open) {
  const set = closed(), k = `${targetKey()}|${path}`;
  if (open ?? set.has(k)) set.delete(k); else set.add(k);
  store.set(CLOSED_KEY, [...set]);
  fillRows();
}

function branch(n, q) {
  const shut = closed();
  const out = [];
  for (const g of [...n.groups.values()].sort((a, b) => a.name.localeCompare(b.name, "fr", { sensitivity: "base" }))) {
    const open = !!q || !shut.has(`${targetKey()}|${g.path}`);
    out.push(S.editing?.path === g.path ? groupInput(n.path, g.path) : groupRow(g, open));
    const inner = open ? branch(g, q) : [];
    if (S.editing && !S.editing.path && S.editing.parent === g.path) inner.unshift(groupInput(g.path, ""));
    if (inner.length) out.push(h("div", { class: "sk-children", role: "group" }, ...inner));
  }
  out.push(...n.skills.map(skillRow));
  return out;
}

function groupRow(g, open) {
  const row = h("div", { class: `sk-group${open ? " open" : ""}`, tabindex: "0", role: "button", draggable: "true",
    "aria-expanded": String(open), title: g.path.split("/").join(" › "),
    on: {
      click: () => toggleGroup(g.path),
      keydown: (e) => {
        if (e.target !== row) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleGroup(g.path); }
        if (e.key === "F2") { e.preventDefault(); edit(g.path); }
      },
      dragstart: (e) => { e.dataTransfer.setData(DRAG, JSON.stringify({ group: g.path })); e.dataTransfer.effectAllowed = "move"; },
      dragover: (e) => { if (dragged(e)) { e.preventDefault(); e.stopPropagation(); row.classList.add("drop"); } },
      dragleave: () => row.classList.remove("drop"),
      drop: (e) => { e.stopPropagation(); row.classList.remove("drop"); drop(e, g.path); },
    } },
  h("span", { class: "sk-chev", svg: "chev" }),
  h("span", { class: "i", svg: "folder" }),
  h("span", { class: "sk-gname" }, g.name),
  h("span", { class: "sk-count" }, String(g.count)),
  h("span", { class: "sk-gtools" },
    g.path.split("/").length < 4 ? iconBtn("folder", "Nouveau sous-groupe", (e) => {
      e.stopPropagation();
      S.editing = { path: "", parent: g.path };
      toggleGroup(g.path, true);
    }) : null,
    iconBtn("edit", "Renommer (F2)", (e) => { e.stopPropagation(); edit(g.path); }),
    iconBtn("x", "Défaire le groupe : son contenu remonte d'un niveau", (e) => { e.stopPropagation(); groupAction("dissoudre", g.path); })));
  return row;
}

function skillRow(s) {
  const n = hints(s).length;
  return h("div", { class: `hrow file${s.name === S.selected ? " on" : ""}`, tabindex: "0", role: "button", draggable: "true",
    "aria-current": s.name === S.selected ? "true" : null,
    on: { click: () => select(s.name), keydown: (e) => { if (e.key === "Enter") select(s.name); },
      dragstart: (e) => { e.dataTransfer.setData(DRAG, JSON.stringify({ skill: s.name })); e.dataTransfer.effectAllowed = "move"; } } },
  h("span", { class: "i", svg: "sparkle" }),
  h("div", { class: "hm" }, h("div", { class: "ht" }, s.name),
    h("div", { class: "hs" }, slashDescription(s.name, s.description) || "Sans description")),
  h("span", { class: "sk-meta" }, n ? h("span", { class: "sk-dot", title: `${n} point${n > 1 ? "s" : ""} à revoir` }) : null,
    s.modified ? fmtDate(s.modified) : ""));
}

/** The name of a new group (under `parent`) or of the group `path` renamed, typed in the list. */
function groupInput(parent, path) {
  const was = path ? path.split("/").pop() : "";
  const input = h("input", { type: "text", class: "sk-ginput", maxlength: "40", value: was, "aria-label": "Nom du groupe",
    placeholder: parent ? `Dans ${parent.split("/").join(" › ")}` : "Nom du groupe" });
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const name = input.value.replace(/[/\\›>]/g, " ").trim();
    S.editing = null;
    if (!save || !name || name === was) { fillRows(); return; }
    const to = [parent, name].filter(Boolean).join("/");
    if (path) groupAction("renommer", path, to); else groupAction("creer", to);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  setTimeout(() => input.focus(), 0);
  return h("div", { class: "sk-group editing" }, h("span", { class: "i", svg: "folder" }), input);
}

function edit(path) { S.editing = { path }; fillRows(); }

const dragged = (e) => [...(e.dataTransfer?.types || [])].includes(DRAG);
function drop(e, path) {
  let data;
  try { data = JSON.parse(e.dataTransfer.getData(DRAG) || "{}"); } catch { return; }
  e.preventDefault();
  if (data.skill) {
    if ((S.list.find((s) => s.name === data.skill)?.groupe || "") !== path) classify(data.skill, path);
  } else if (data.group) {
    const to = [path, data.group.split("/").pop()].filter(Boolean).join("/");
    if (to !== data.group && path !== data.group && !path.startsWith(`${data.group}/`)) groupAction("renommer", data.group, to);
  }
}

const targetBody = () => (S.target.kind === "projet" ? { folder: S.target.id } : { profile: S.target.id });

function applyGroups(r) {
  S.groups = r.groupes || [];
  for (const s of S.list) s.groupe = r.classement?.[s.name] || "";
  fillRows();
  renderSheet();
}

async function classify(name, group) {
  try {
    applyGroups(await api("/api/skills/classement", { method: "POST", body: { competence: name, groupe: group, ...targetBody() } }));
  } catch (e) { toast(e.message, "err"); }
}

async function groupAction(action, group, nouveau = "") {
  const key = targetKey(), shut = closed();
  try {
    const r = await api("/api/skills/groupes", { method: "POST", body: { action, groupe: group, nouveau, ...targetBody() } });
    if (action === "renommer" && shut.delete(`${key}|${group}`)) { shut.add(`${key}|${nouveau}`); store.set(CLOSED_KEY, [...shut]); }
    if (key === targetKey()) applyGroups(r);
  } catch (e) { toast(e.message, "err"); fillRows(); }
}

function select(name) {
  if (name === S.selected && name !== null) return;
  release();
  S.selected = name;
  S.file = "SKILL.md";
  S.fileData = null;
  fillRows();
  renderSheet();
  if (name) readFile();
  renderTalk();
}

// ------------------------------------------------------------ the sheet
const current = () => S.list.find((s) => s.name === S.selected);

/** What makes a skill load at the wrong time, or cost for nothing: from its SKILL.md. */
function hints(s) {
  const out = [];
  const text = String(s.content || "");
  const fm = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const desc = (fm?.[1].match(/^description:\s*(.*(?:\r?\n[ \t]+.*)*)/m)?.[1] || "").replace(/\s+/g, " ").replace(/^["'>|-]\s*|["']$/g, "").trim();
  if (!fm || !desc) out.push(["warn", "Pas de description dans l'en-tête : Claude ne saura pas quand la charger."]);
  else if (desc.length < 60) out.push(["warn", "Description courte : dis quand la charger (sujets, mots que tu emploies, types de documents)."]);
  else if (desc.length > 700) out.push(["info", "Description longue : elle est lue à chaque demande ; garde ce qui décide du chargement."]);
  const body = fm ? text.slice(fm[0].length) : text;
  const lines = body.split(/\r?\n/).length;
  if (lines > 500 || text.length >= 20000) out.push(["warn", `SKILL.md long (${lines} lignes) : mets les référentiels et les exemples dans des fichiers annexes.`]);
  const unused = (s.files || []).filter((f) => /\.md$/i.test(f) && !body.includes(f.split("/").pop()));
  if (unused.length) out.push(["info", `${unused.join(", ")} : jamais cité dans SKILL.md, Claude ne saura pas qu'il existe.`]);
  return out;
}

function renderSheet() {
  const col = S.sheetCol;
  if (!col?.isConnected) return;
  const s = current();
  if (!s) {
    col.replaceChildren(h("div", { class: "sk-empty" },
      h("span", { class: "i", svg: "sparkle" }),
      h("h3", {}, S.selected === null && S.list.length ? "Choisis une compétence, ou crées-en une" : "Nouvelle compétence"),
      h("p", {}, "Une compétence n'est pas un bouton : Claude la charge de lui-même quand la demande s'y prête, d'après sa description. ",
        "Décris à droite ce qu'elle doit savoir faire ; Claude pose ses questions puis propose les fichiers, que tu valides d'un clic.")));
    return;
  }
  const hs = hints(s);
  const tabs = ["SKILL.md", ...(s.files || [])];
  S.fileBox = h("div", { class: "sk-file" });
  col.replaceChildren(...[
    h("header", { class: "sk-head" },
      h("h3", {}, s.name),
      h("span", { class: "muted" }, s.modified ? `Modifiée ${fmtDate(s.modified)}` : ""),
      iconBtn("copy", "Copier le chemin du dossier", () => navigator.clipboard?.writeText(s.path.replace(/[\\/]SKILL\.md$/i, ""))
        .then(() => toast("Chemin copié.", "ok")).catch(() => {}))),
    h("p", { class: "sk-desc" }, slashDescription(s.name, s.description) || "Sans description."),
    groupSelect(s),
    hs.length ? h("ul", { class: "sk-hints" }, ...hs.map(([k, t]) => h("li", { class: k }, t))) : null,
    h("nav", { class: "sk-tabs", role: "tablist" }, ...tabs.map((f) => h("button", {
      type: "button", role: "tab", class: f === S.file ? "on" : "", "aria-selected": String(f === S.file), title: f,
      on: { click: () => { if (f !== S.file) { S.file = f; renderSheet(); readFile(); } } },
    }, f))),
    S.fileBox].filter(Boolean));
  renderFile();
}

function groupSelect(s) {
  const sel = h("select", { "aria-label": "Groupe", on: { change: (e) => classify(s.name, e.target.value) } },
    h("option", { value: "" }, "Hors groupe"),
    ...S.groups.map((g) => h("option", { value: g }, `${"\u00a0\u00a0\u00a0".repeat(g.split("/").length - 1)}${g.split("/").pop()}`)));
  sel.value = s.groupe || "";
  return h("label", { class: "sk-group-pick", title: "Seulement pour ranger la liste : Claude Code ne voit pas les groupes" },
    h("span", { class: "muted" }, "Groupe"), sel);
}

function renderFile() {
  const box = S.fileBox;
  if (!box?.isConnected) return;
  const r = S.fileData;
  if (S.fileError) { box.replaceChildren(h("div", { class: "line err" }, S.fileError)); return; }
  if (!r) { box.replaceChildren(h("div", { class: "empty-row" }, "Lecture…")); return; }
  if (r.binary) { box.replaceChildren(h("div", { class: "empty-row" }, `Fichier binaire (${Math.ceil(r.size / 1024)} Ko) : non affiché.`)); return; }
  let text = r.text || "";
  const parts = [];
  if (/\.md$/i.test(r.path)) {
    const fm = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
    if (fm) { parts.push(h("pre", { class: "sk-front", title: "En-tête : ce que Claude lit toujours" }, fm[1])); text = text.slice(fm[0].length); }
    parts.push(mdElement(text, "md sk-md"));
  } else parts.push(h("pre", { class: "sk-code" }, text));
  if (r.truncated) parts.push(h("p", { class: "muted" }, "Fichier tronqué à l'affichage."));
  box.replaceChildren(...parts);
}

// ------------------------------------------------------------ the discussion with Claude
const talkKey = () => `${targetKey()}|${S.selected || ""}`;
function talkId() { return (store.get(TALKS_KEY, {}) || {})[talkKey()] || ""; }
function rememberTalk(id) {
  const talks = store.get(TALKS_KEY, {}) || {};
  if (id) talks[talkKey()] = id; else delete talks[talkKey()];
  store.set(TALKS_KEY, talks);
}

function release() {
  if (S.embedded) S.ctx?.releaseTask?.(S.embedded);
  S.embedded = null;
}

function renderTalk() {
  const col = S.talkCol;
  if (!col?.isConnected) return;
  const id = talkId();
  const t = id && S.ctx?.tasks?.().find((x) => x.id === id && !x.archived);
  const head = h("header", { class: "sk-talk-head" },
    h("h3", {}, "Avec Claude"),
    h("span", { class: "muted" }, S.selected ? `sur « ${S.selected} »` : "nouvelle compétence"),
    t ? h("button", { type: "button", class: "btn small ghost", title: "Laisser cette discussion (elle reste dans Discussions) et en commencer une autre",
      on: { click: () => { release(); rememberTalk(""); renderTalk(); } } }, "Nouvelle discussion") : null);
  if (t) {
    const box = h("div", { class: "sk-win" });
    col.replaceChildren(head, box);
    if (S.embedded && S.embedded !== t.id) release();
    if (S.ctx.embedTask(t.id, box)) { S.embedded = t.id; return; }
  }
  if (id && !t) rememberTalk("");
  release();
  col.replaceChildren(head, startForm());
}

function startForm() {
  const name = S.selected;
  const area = h("textarea", { rows: "5", lang: "fr", spellcheck: "true",
    placeholder: name
      ? `Ce qui ne va pas avec « ${name} », ce qu'elle doit savoir en plus, un exemple de travail raté…`
      : "Quand doit-elle servir ? Ce qu'elle doit savoir faire, les règles, un exemple de travail réussi…" });
  const go = h("button", { type: "button", class: "btn primary" }, name ? "Améliorer avec Claude" : "Écrire avec Claude");
  // the model of the discussion, remembered per account; never Haiku by itself (console/engine.py skills_workshop)
  const pid = targetProfile(), chosen = store.get(MODEL_KEY, {}) || {};
  const model = h("select", { "aria-label": "Modèle", title: "Le modèle de cette discussion",
    on: { change: (e) => store.set(MODEL_KEY, { ...(store.get(MODEL_KEY, {}) || {}), [pid]: e.target.value }) } },
  ...(S.ctx.modelsOf?.(pid) || [["", "défaut du compte"]]).map(([v, l]) => h("option", { value: v }, l)));
  model.value = chosen[pid] || "";
  if (model.value !== (chosen[pid] || "")) model.value = "";
  const start = async () => {
    const text = area.value.trim() || (name ? `Relis la compétence « ${name} » et dis-moi ce que tu améliorerais.`
      : "Aide-moi à créer une nouvelle compétence.");
    go.disabled = true;
    try {
      const body = { prompt: text, competence: name || "", model: model.value, ...targetBody() };
      const t = await api("/api/skills/atelier", { method: "POST", body });
      rememberTalk(t.id);
      S.ctx.onTask?.(t);
      renderTalk();
    } catch (e) { toast(e.message, "err"); go.disabled = false; }
  };
  go.addEventListener("click", start);
  area.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); start(); } });
  return h("div", { class: "sk-start" },
    h("p", {}, name
      ? "Claude relit la compétence, te pose les questions qui manquent, puis propose ses modifications. "
      : "Claude t'aide à définir la compétence : quand elle sert, ce qu'elle contient, puis il propose les fichiers. ",
    "Rien n'est écrit sans ton clic sur les différences."),
    area,
    h("div", { class: "sk-start-foot" }, model, h("span", { class: "muted" }, "Ctrl+Entrée"), go));
}
