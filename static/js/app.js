// Main: command bar, task windows, live stream, shortcuts, notifications.
import { ApiError, api, bootstrapAuth, openStream } from "./api.js";
import { Attacher, hasFiles } from "./attach.js";
import { ContextPicker } from "./context.js";
import { pickFolder } from "./folderpicker.js";
import { baseName as pname, editProject, loadProjects, projectFor, projects, setProjects } from "./projects.js";
import { accountState, gauges, initLimits, limitsBlock, limitsTitle, openLimits, sessionUsed, updateLimits } from "./limits.js";
import { projectFollow, toggleProject } from "./project.js";
import { checkVersion, restartConsole, updateConsole } from "./system.js";
import { openPalette } from "./palette.js";
import { openSetup } from "./setup.js";
import { openConfig, updateProbe } from "./config.js";
import { renderHistory, toggleHistory } from "./history.js";
import { showSession, toggleSessions } from "./library.js";
import { routinesChanged, toggleRoutines } from "./routines.js";
import { IMG_EXT } from "./md.js";
import { TaskWindow, autoGrow } from "./taskwin.js";
import { $, STATUS, confirmDialog, copyText, dialog, fmtDate, h, statusLabel, store, toast } from "./util.js";
import { openPreview, revealImage } from "./viewer.js";
import * as wm from "./wm.js";

const S = {
  config: null, meta: null, state: {}, probes: {},
  tasks: new Map(), windows: new Map(), attention: new Map(),
  profile: null, lastRecall: -1,
};
const input = $("#cmd-input");
const suggest = $("#suggest");
const MAX_HISTORY = 100;

const profiles = () => S.config?.profiles || [];
const profile = (id = S.profile) => profiles().find((p) => p.id === id) || profiles()[0];
const theme = () => S.config?.general.theme || "sombre";

// ------------------------------------------------------------ boot
async function boot() {
  document.documentElement.setAttribute("data-theme", store.get("jarvis.theme", "sombre"));
  await bootstrapAuth();
  const [{ config, meta }, state, probes] = await Promise.all([api("/api/config"), api("/api/state"), api("/api/probes")]);
  applyConfig(config, meta);
  S.state = state;
  S.probes = probes.probes || {};
  await loadProjects();
  await wm.loadState();
  wm.configure({ default_width: config.ui.default_width, default_height: config.ui.default_height });
  wm.onChange(renderTaskbar);
  S.profile = wm.prefs().profile && config.profiles.some((p) => p.id === wm.prefs().profile) ? wm.prefs().profile : config.general.default_profile;
  renderProfiles();
  selectProfile(S.profile, false);
  setTeam(!!wm.prefs().team, false);
  const { tasks } = await api("/api/tasks");
  for (const t of tasks.reverse()) {
    S.tasks.set(t.id, t);
    if (!t.closed) openWindow(t, false);
  }
  renderState();
  initLimits(await api("/api/limits").catch(() => ({})), () => { renderPills(); renderHome(); }, onLimitAlert);
  renderHome();
  renderPills();
  setInterval(renderPills, 60_000); // a window past its reset time goes back to 0
  openStream({
    hello: resync, task: onTask, ev: onEvent, state: (st) => { S.state = { ...S.state, ...st }; renderState(); },
    probe: ({ profile: pid, probe }) => { S.probes[pid] = { ...(S.probes[pid] || {}), ...probe }; updateProbe(pid, probe); renderPills(); },
    config: reloadConfig, deleted: ({ id }) => { S.tasks.delete(id); closeWindow(id); renderHistory(); },
    reload: resync, routines: routinesChanged, limits: ({ profile: pid, limits: l }) => updateLimits(pid, l),
    projects: ({ projects: list }) => { setProjects(list); refreshProjectsUI(); },
  }, () => $("#banner").dataset.down === "1" && renderBanner(false), () => renderBanner(true));
  input.focus();
  checkVersion();
  setInterval(checkVersion, 60_000);
  if (S.config.general.setup_done === false) startSetup(); // fresh install: the first-run assistant
}

function startSetup() {
  openSetup({
    state: () => S.state, profiles: () => profiles().map((p) => ({ ...p, workdir: S.meta?.profiles?.[p.id]?.workdir || p.workdir })),
    probes: () => S.probes, newProject, recentFolders: (pid) => recentFolders(pid),
    onDone: () => toast("Bienvenue ! Ctrl+K pour tout retrouver.", "ok"),
  });
}
window.addEventListener("jarvis-check-version", () => checkVersion());

let firstHello = true;
async function resync() {
  if (firstHello) { firstHello = false; return; }
  try {
    const [{ tasks }, state] = await Promise.all([api("/api/tasks"), api("/api/state")]);
    S.state = state;
    const ids = new Set(tasks.map((t) => t.id));
    for (const id of [...S.tasks.keys()]) if (!ids.has(id)) { S.tasks.delete(id); closeWindow(id); }
    for (const t of tasks) onTask(t);
    for (const w of S.windows.values()) w.load();
    renderState();
  } catch { /* the stream will retry */ }
}

function applyConfig(config, meta) {
  S.config = config;
  S.meta = meta;
  document.documentElement.setAttribute("data-theme", config.general.theme);
  store.set("jarvis.theme", config.general.theme);
  wm.configure({ default_width: config.ui.default_width, default_height: config.ui.default_height });
}

async function reloadConfig() {
  try {
    const { config, meta } = await api("/api/config");
    applyConfig(config, meta);
    if (!profiles().some((p) => p.id === S.profile)) S.profile = config.general.default_profile;
    renderProfiles();
    selectProfile(S.profile, false);
    renderPills();
    recolorTasks();
  } catch { /* keep the old one */ }
}

/** An account's color or name changed: every discussion shown takes it at once. */
function recolorTasks() {
  for (const t of S.tasks.values()) {
    const p = profile(t.profile);
    if (!p || (t.color === p.color && t.profile_name === p.name)) continue;
    t.color = p.color;
    t.profile_name = p.name;
    S.windows.get(t.id)?.update(t);
  }
  renderTaskbar();
  renderHistory();
  renderHome();
}

// ------------------------------------------------------------ top bar & state
function renderState() {
  const st = S.state;
  const counter = (cls, n, label) => h("span", { class: `counter ${cls}${n ? " live" : ""}` },
    h("span", { class: "d" }), h("b", {}, String(n || 0)), label);
  $("#counters").replaceChildren(
    counter("run", st.running, "en cours"), counter("await", st.awaiting, "à valider"), counter("queue", st.queued, "en file"));
  const btn = $("#btn-emergency");
  btn.textContent = st.emergency_stop ? "Réactiver" : "Arrêt d'urgence";
  btn.className = `btn ${st.emergency_stop ? "ok" : "danger"}`;
  renderBanner();
  renderPills();
  document.title = `${st.awaiting ? `(${st.awaiting}) ` : ""}JARVIS · Console d'agents`;
  if (!st.cli?.path) renderBanner();
}

function renderBanner(down = null) {
  const el = $("#banner");
  if (down !== null) el.dataset.down = down ? "1" : "0";
  const parts = [];
  if (el.dataset.down === "1") parts.push(h("span", {}, h("b", {}, "Connexion au serveur perdue"), " — reconnexion automatique…"));
  if (S.state.emergency_stop) {
    parts.push(h("span", {}, h("b", {}, "Arrêt d'urgence actif"), " : toutes les tâches ont été stoppées et les nouvelles sont bloquées."),
      h("button", { type: "button", class: "btn small ok", on: { click: () => setEmergency(false) } }, "Réactiver la console"));
  } else if (S.state.cli && !S.state.cli.path) {
    parts.push(h("span", {}, h("b", {}, "Claude Code introuvable"), " : installe-le ou indique son chemin dans Configuration → Général."));
  }
  el.replaceChildren(...parts);
  el.hidden = !parts.length;
}

function renderPills() {
  $("#profile-pills").replaceChildren(...profiles().map((p) => {
    const pr = S.probes[p.id] || S.state.probes?.[p.id];
    const known = pr && (pr.checked || pr.logged_in !== undefined);
    const cls = !known ? "" : pr.logged_in ? "ok" : "ko";
    const label = !known ? "connexion non testée" : pr.logged_in ? "connecté" : "non connecté";
    const pill = h("button", { type: "button", class: "ppill", style: { "--pc": p.color },
      title: `${limitsTitle(p.id, p.name)} · ${label}`,
      on: { click: () => openLimits(pill, p, { onConfig: () => showConfig("profiles") }) } },
      h("span", { class: "sw" }), h("span", { class: "pn" }, p.name), gauges(p.id), h("span", { class: `st ${cls}` }));
    return pill;
  }));
  renderProfileBars();
}

/** A thin line under each account button of the request bar: its 5-hour session used. */
function renderProfileBars() {
  $$pbtn().forEach((b, i) => {
    const p = profiles()[i];
    if (!p) return;
    const used = sessionUsed(p.id);
    let bar = b.querySelector(".pbar");
    if (used === null) { bar?.remove(); return; }
    if (!bar) { bar = h("span", { class: "pbar" }, h("i")); b.append(bar); }
    bar.firstChild.style.width = `${Math.round(used * 100)}%`;
    bar.className = `pbar ${used >= 0.9 ? "hot" : used >= 0.7 ? "warm" : "ok"}`;
    b.title = `Profil ${p.name}${i < 9 ? ` (Alt+${i + 1})` : ""} · session ${Math.round(used * 100)} % utilisée`;
  });
}

async function setEmergency(on) {
  if (on && !(await confirmDialog("Arrêt d'urgence", "Toutes les tâches en cours sont stoppées et les nouvelles bloquées jusqu'à réactivation.", "Tout arrêter", "danger"))) return;
  try { S.state = { ...S.state, ...(await api("/api/emergency", { method: "POST", body: { on } })) }; renderState(); }
  catch (e) { toast(e.message, "err"); }
}

function showConfig(tabName = null) {
  openConfig({
    onSaved: (config, meta) => { applyConfig(config, meta); renderProfiles(); selectProfile(S.profile, false); renderPills(); recolorTasks(); },
    state: () => S.state, setEmergency, theme, openSetup: startSetup,
  }, tabName).catch((e) => toast(e.message, "err"));
}

// ------------------------------------------------------------ command bar
function renderProfiles() {
  $("#cmd-profiles").replaceChildren(...profiles().map((p, i) => h("button", {
    type: "button", class: "pbtn", role: "radio", "aria-checked": String(p.id === S.profile), style: { "--pc": p.color },
    title: `Profil ${p.name}${i < 9 ? ` (Alt+${i + 1})` : ""}`, on: { click: () => { selectProfile(p.id); input.focus(); } },
  }, h("span", { class: "sw" }), p.name)));
  renderProfileBars();
}

function option(value, label, title = "") { return h("option", { value, title: title || undefined }, label); }

/** A folder as a short label: its name, and its parent when that helps ("Visiotech — Fournisseurs"). */
function folderLabel(dir) {
  const parts = String(dir || "").replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  const name = parts.pop() || dir;
  const parent = parts.pop();
  return parent && !/^[A-Za-z]:$/.test(parent) ? `${name} — ${parent}` : name;
}
const folderOption = (dir) => option(dir, projectFor(dir)?.name || folderLabel(dir), dir);
const dirKey = (d) => String(d || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();

/** One chip sums up model, permissions, effort and team mode; the panel holds the controls. */
const EFFORT = { low: "effort faible", medium: "effort moyen", high: "effort élevé", xhigh: "effort très élevé", max: "effort max" };
function updateSummary() {
  const p = profile(S.profile);
  if (!p) return;
  const m = $("#opt-model").value || p.default_model || "default";
  const model = m === "default" ? "Modèle par défaut" : m.charAt(0).toUpperCase() + m.slice(1);
  const preset = $("#opt-preset").selectedOptions[0]?.textContent || "";
  const team = $("#opt-team").getAttribute("aria-pressed") === "true";
  const effort = EFFORT[$("#opt-effort").value];
  const text = [team ? "Équipe" : "", model, preset, effort].filter(Boolean).join(" · ");
  $("#opt-summary-text").textContent = text;
  $("#opt-summary").classList.toggle("team", team);
  $("#opt-summary").title = `Réglages de la demande : ${text}`;
  const wd = $("#opt-workdir");
  wd.parentElement.title = `Dossier de travail : ${wd.value && wd.value !== "__other__" ? wd.value : (S.meta?.profiles?.[p.id]?.workdir || p.workdir)}`;
}

function toggleOptions(open = $("#opt-panel").hidden) {
  $("#opt-panel").hidden = !open;
  $("#opt-summary").setAttribute("aria-expanded", String(open));
  if (open) $("#opt-model").focus();
}
$("#opt-summary").addEventListener("click", () => toggleOptions());
for (const id of ["#opt-model", "#opt-preset", "#opt-effort"]) $(id).addEventListener("change", updateSummary);
document.addEventListener("pointerdown", (e) => {
  if (!$("#opt-panel").hidden && !e.target.closest("#opt-panel, #opt-summary")) toggleOptions(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#opt-panel").hidden) { e.stopPropagation(); toggleOptions(false); $("#opt-summary").focus(); }
}, true);

function selectProfile(id, remember = true) {
  const p = profile(id);
  if (!p) return;
  S.profile = p.id;
  contextPicker.profileChanged();
  queueMicrotask(projectFollow);
  if (remember) wm.savePrefs({ profile: p.id });
  $$pbtn().forEach((b, i) => b.setAttribute("aria-checked", String(profiles()[i]?.id === p.id)));
  $("#commandbar").style.setProperty("--pc", p.color);
  input.style.setProperty("--pc", p.color);

  const models = new Map([["", `défaut du profil (${p.default_model})`]]);
  for (const m of S.meta?.base_models || []) if (m !== "default") models.set(m, m);
  for (const m of S.probes[p.id]?.models || []) if (m.value && m.value !== "default") models.set(m.value, m.label ? `${m.value} — ${m.label}` : m.value);
  $("#opt-model").replaceChildren(...[...models].map(([v, l]) => option(v, l)));

  const presets = S.config.presets.filter((x) => x.enabled);
  $("#opt-preset").replaceChildren(...presets.map((x) => option(x.id, x.name)));
  $("#opt-preset").value = presets.some((x) => x.id === p.default_preset) ? p.default_preset : presets[0]?.id || "";
  $("#opt-effort").value = "";

  const recent = (wm.prefs().workdirs || {})[p.id] || [];
  const wd = $("#opt-workdir");
  const home = S.meta?.profiles?.[p.id]?.workdir || p.workdir;
  // pinned projects of this account first, then the folders used lately
  const pinnedDirs = projects().filter((x) => x.pinned && (!x.profile || x.profile === p.id)).map((x) => x.folder);
  const dirs = [...new Map([...pinnedDirs, ...recent].map((d) => [dirKey(d), d])).values()].filter((d) => dirKey(d) !== dirKey(home));
  wd.replaceChildren(option("", `${projectFor(home)?.name || String(home).replace(/[\\/]+$/, "").split(/[\\/]/).pop()} (défaut)`, home),
    ...dirs.map(folderOption), option("__other__", "Autre dossier…"));
  wd.value = "";
  lastWorkdir = "";
  applyProject(projectFor(home));
  updateSummary();
}
const $$pbtn = () => Array.from(document.querySelectorAll("#cmd-profiles .pbtn"));

$("#opt-workdir").addEventListener("change", async (e) => {
  if (e.target.value !== "__other__") {
    lastWorkdir = e.target.value;
    const proj = projectFor(currentFolder());
    if (proj?.profile && proj.profile !== S.profile && profile(proj.profile)) { useProject(proj); return; }
    applyProject(proj);
    updateSummary();
    projectFollow();
    return;
  }
  const sel = $("#opt-workdir");
  const path = await pickFolder({ title: "Dossier de travail", start: lastWorkdir || S.meta?.profiles?.[S.profile]?.workdir || "",
    recent: recentFolders() });
  if (!path) { sel.value = lastWorkdir; return; }
  chooseWorkdir(path);
});
let lastWorkdir = "";

function recentFolders(pid = S.profile) {
  const p = profile(pid);
  const home = S.meta?.profiles?.[pid]?.workdir || p?.workdir || "";
  return [...new Set([home, ...((wm.prefs().workdirs || {})[pid] || [])].filter(Boolean))];
}

/** Move a console discussion into another project: a copy of its session continues there. */
async function moveTask(t) {
  const path = await pickFolder({ title: `Déplacer « ${t.title} » vers…`, start: t.workdir, recent: recentFolders(t.profile) });
  if (!path) return;
  if (!(await confirmDialog("Déplacer la discussion ?",
    `« ${t.title} » passe dans ${path} : c'est la même discussion, qui continue là (dans Claude Desktop aussi). `
    + "Si elle est ouverte dans Claude Desktop, ferme-la d'abord.", "Déplacer"))) return;
  try {
    const n = await api(`/api/tasks/${t.id}/move`, { method: "POST", body: { workdir: path } });
    onTask(n);
    rememberWorkdir(t.profile, n.workdir);
    projectFollow();
    toast("Discussion déplacée.", "ok");
  } catch (e) { toast(e.message, "err"); }
}

/** Move an existing session (console, Claude Desktop, CLI) into a project folder: the same session. */
async function moveSession(pid, sid, title, from) {
  const path = await pickFolder({ title: `Déplacer « ${title} » vers…`, start: from, recent: recentFolders(pid) });
  if (!path) return null;
  if (!(await confirmDialog("Déplacer la session ?",
    `« ${title} » passe dans ${path} : c'est la même session, qui continue là (dans Claude Desktop aussi). `
    + "Si elle est ouverte dans Claude Desktop, ferme-la d'abord.", "Déplacer"))) return null;
  try {
    const r = await api(`/api/sessions/${pid}/${sid}/move`, { method: "POST", body: { workdir: path } });
    rememberWorkdir(pid, r.folder);
    projectFollow();
    toast(r.desktop ? "Session déplacée (Claude Desktop mis à jour)." : "Session déplacée.", "ok");
    return r;
  } catch (e) { toast(e.message, "err"); return null; }
}

/** A folder picked by the user: selected in the bar and kept in its list (and in the Projet panel). */
function chooseWorkdir(path) {
  const sel = $("#opt-workdir");
  if (![...sel.options].some((o) => o.value === path)) sel.insertBefore(folderOption(path), sel.lastChild);
  sel.value = path;
  lastWorkdir = path;
  rememberWorkdir(S.profile, path);
  applyProject(projectFor(path));
  updateSummary();
  projectFollow();
}

function currentFolder() {
  const v = $("#opt-workdir").value;
  return v && v !== "__other__" ? v : (S.meta?.profiles?.[S.profile]?.workdir || profile(S.profile)?.workdir || "");
}

/** A project chosen: the bar takes its defaults (they stay changeable for one request). */
function applyProject(p) {
  if (p) {
    if (p.preset && [...$("#opt-preset").options].some((o) => o.value === p.preset)) $("#opt-preset").value = p.preset;
    if (p.model) {
      if (![...$("#opt-model").options].some((o) => o.value === p.model)) $("#opt-model").append(option(p.model, p.model));
      $("#opt-model").value = p.model;
    }
    $("#opt-effort").value = p.effort || "";
  }
  updateProjectButton();
}

function useProject(p) {
  if (p.profile && p.profile !== S.profile && profile(p.profile)) selectProfile(p.profile);
  chooseWorkdir(p.folder);
}

function updateProjectButton() {
  const p = projectFor(currentFolder());
  const b = $("#btn-project");
  b.querySelector(".lbl").textContent = p ? p.name : "Projet";
  b.classList.toggle("has-project", !!p);
  if (p) b.style.setProperty("--pc", p.color); else b.style.removeProperty("--pc");
  b.title = p ? `Projet ${p.name} : consignes, mémoire, fichiers, discussions et règles (${p.folder})`
    : "Projet : consignes, mémoire, fichiers et discussions du dossier de travail";
}

/** Projects changed (created, renamed, removed): names, home screen, top bar. */
function refreshProjectsUI() {
  for (const o of $("#opt-workdir").options) {
    if (!o.value || o.value === "__other__") continue;
    o.textContent = projectFor(o.value)?.name || folderLabel(o.value);
  }
  updateProjectButton();
  renderHome();
}

async function newProject() {
  const folder = await pickFolder({ title: "Dossier du nouveau projet", recent: recentFolders() });
  if (!folder) return;
  const saved = await editProject({ folder, profiles: profiles(), presets: S.config.presets, models: panelCtx.models(), current: { profile: S.profile } });
  if (saved && saved !== "deleted") { refreshProjectsUI(); useProject(saved); }
}

/** The home screen: pinned projects, discussions to pick up again, the accounts' limits. */
function renderHome() {
  const home = $("#home");
  if (!home) return;
  const pinned = projects().filter((p) => p.pinned);
  const recent = [...S.tasks.values()].sort((a, b) => (b.created || 0) - (a.created || 0)).slice(0, 6);
  const card = (p) => h("button", { type: "button", class: "home-card", style: { "--pc": p.color }, title: p.folder,
    on: { click: () => { useProject(p); input.focus(); } } },
    h("b", {}, p.name),
    h("small", {}, [pname(p.folder), p.discussions ? `${p.discussions} discussion${p.discussions > 1 ? "s" : ""}` : "aucune discussion",
      p.last ? fmtDate(p.last) : ""].filter(Boolean).join(" · ")),
    p.profile && profile(p.profile) ? h("span", { class: "home-acc", style: { "--ac": profile(p.profile).color } }, profile(p.profile).name) : null);
  home.replaceChildren(...[
    h("section", { class: "home-sec" }, h("h3", {}, "Projets"), h("div", { class: "home-cards" }, ...pinned.map(card),
      h("button", { type: "button", class: "home-card add", on: { click: newProject } }, h("b", {}, "+ Nouveau projet"),
        h("small", {}, "Un dossier, ses consignes et ses réglages")))),
    recent.length ? h("section", { class: "home-sec" }, h("h3", {}, "Reprendre"), h("div", { class: "home-list" }, ...recent.map((t) =>
      h("button", { type: "button", class: "home-row", style: { "--pc": t.color }, on: { click: () => openTask(t.id) } },
        h("span", { class: "t" }, t.title),
        h("small", {}, [projectFor(t.workdir)?.name || pname(t.workdir), statusLabel(t), fmtDate(t.created)].filter(Boolean).join(" · ")))))) : null,
    h("section", { class: "home-sec" }, h("h3", {}, "Limites des comptes"), h("div", { class: "home-lims" }, ...profiles().map((p) =>
      limitsBlock(p, (e) => openLimits(e.currentTarget, p, { onConfig: () => showConfig("profiles") }))))),
  ].filter(Boolean));
}

function rememberWorkdir(pid, dir) {
  if (!dir) return;
  const all = { ...(wm.prefs().workdirs || {}) };
  all[pid] = [dir, ...(all[pid] || []).filter((d) => d !== dir)].slice(0, 12);
  wm.savePrefs({ workdirs: all });
}

function history() { return store.get("jarvis.prompts", []); }

const attacher = new Attacher($("#cmd-files"));
const contextPicker = new ContextPicker($("#cmd-context"), () => S.profile);
$("#cmd-send").before(contextPicker.button("chip-toggle icon-only"), attacher.button("chip-toggle icon-only"));

/** From a window: this discussion becomes context of the next request. */
function addContext(t) {
  if (!t.session_started && !t.resumable) { toast("Cette discussion n'a pas encore démarré.", "err"); return; }
  if (S.profile !== t.profile) selectProfile(t.profile);
  contextPicker.add({ profile: t.profile, session: t.session_id, title: t.title });
  toast("Contexte ajouté à ta prochaine demande.", "ok");
  input.focus();
}

async function forkTask(t) {
  const text = await dialog({ title: "Nouvelle discussion à partir d'ici",
    body: "Une copie repart avec tout le contexte de cette discussion ; l'originale ne change pas. Que veux-tu demander ?",
    input: { placeholder: "Ta demande pour la nouvelle discussion" },
    buttons: [{ label: "Annuler", value: null }, { label: "Lancer", value: true, cls: "primary" }] });
  if (text && text.trim()) launch(`/api/tasks/${t.id}/fork`, { prompt: text.trim() });
}
attacher.bindDrop($("#commandbar"));
attacher.bindPaste(input);
// Files dropped anywhere else on the page join the next request (and never replace the app).
window.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  attacher.add([...e.dataTransfer.files]);
  input.focus();
});

async function submit(extra = {}) {
  const prompt = input.value.trim();
  const files = attacher.ids();
  const context = contextPicker.sources();
  if (!prompt && !files.length && !context.length && !attacher.busy()) return;
  if (!attacher.ready()) return;
  const wd = $("#opt-workdir").value;
  const body = {
    prompt, profile: S.profile, model: $("#opt-model").value || null, preset: $("#opt-preset").value || null,
    effort: $("#opt-effort").value || null, workdir: wd && wd !== "__other__" ? wd : null,
    team: $("#opt-team").getAttribute("aria-pressed") === "true", attachments: files, context, ...extra,
  };
  const checked = await limitGuard(body);
  if (!checked) return; // cancelled: the text stays in the bar
  Object.assign(body, checked);
  // Clear at once so the next request can be typed while this one is sent.
  input.value = "";
  autoGrow(input, 220);
  hideSuggest();
  S.lastRecall = -1;
  if (prompt) {
    const hist = history().filter((x) => x !== prompt);
    hist.unshift(prompt);
    store.set("jarvis.prompts", hist.slice(0, MAX_HISTORY));
  }
  const t = await launch("/api/tasks", body);
  if (t) { rememberWorkdir(body.profile, body.workdir); attacher.sent(files); contextPicker.clear(); }
  else if (!input.value.trim()) { input.value = prompt; autoGrow(input, 220); }
}

/** Before a launch on an account at (or near) its limit: another account, later, or anyway. */
async function limitGuard(body) {
  const st = accountState(body.profile);
  if (!st.known || (!st.blocked && !st.near)) return body;
  const p = profile(body.profile);
  const pc = (x) => `${Math.round((x ?? 0) * 100)} %`;
  const other = profiles().find((x) => {
    if (x.id === body.profile) return false;
    const o = accountState(x.id);
    return !o.blocked && (o.session ?? 0) < 0.8;
  });
  const msg = st.blocked
    ? `La limite du compte ${p.name} est atteinte ; elle se réinitialise ${st.resetText}.`
    : `Le compte ${p.name} est presque à sa limite : session ${pc(st.session)}, semaine ${pc(st.week)}.`;
  const buttons = [{ label: "Annuler", value: null }];
  if (st.blocked && st.resetAt) buttons.push({ label: "À la réinitialisation", value: "later", cls: other ? "" : "primary" });
  buttons.push({ label: "Lancer quand même", value: "anyway" });
  if (other) buttons.push({ label: `Lancer sur ${other.name}`, value: "other", cls: "primary" });
  const v = await dialog({ title: st.blocked ? "Limite atteinte" : "Limite presque atteinte",
    body: `${msg}${other ? ` Le compte ${other.name} a encore de la marge.` : ""}`, buttons });
  if (!v) return null;
  if (v === "other") { selectProfile(other.id); return { profile: other.id }; }
  if (v === "later") return { not_before: st.resetAt + 60 };
  return {};
}

function onLimitAlert(a) {
  const p = profile(a.pid);
  if (!p) return;
  const text = a.rejected ? `${p.name} : limite atteinte (${a.label}) ; réinitialisation ${a.when}.`
    : `${p.name} : ${a.label} à ${Math.round(a.used * 100)} % ; réinitialisation ${a.when}.`;
  toast(text, a.rejected || a.used >= 0.9 ? "err" : "warn");
  if (S.config?.general?.notifications && document.hidden && "Notification" in window && Notification.permission === "granted") {
    new Notification("Limite Claude", { body: text, tag: `limit-${a.pid}-${a.window}`, icon: "/static/img/favicon.svg" });
  }
}

/** POST a launch; handles the confirmation round-trip (HTTP 409 need_confirm). */
async function launch(path, body, onOk = () => {}) {
  try {
    const t = await api(path, { method: "POST", body });
    onOk();
    onTask(t, true);
    return t;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.data.need_confirm) {
      const ok = await confirmDialog("Confirmer le lancement", h("div", {},
        h("p", {}, e.message), h("p", {}, h("span", { class: "badge", style: { "--pc": e.data.color || "#40dcff" } }, e.data.profile || ""), " ", e.data.preset || "")), "Lancer");
      if (ok) return launch(path, { ...body, confirmed: true }, onOk);
    } else toast(e.message, "err");
  }
  return null;
}

$("#commandbar").addEventListener("submit", (e) => { e.preventDefault(); submit(); });

function setTeam(on, remember = true) {
  const b = $("#opt-team");
  b.setAttribute("aria-pressed", String(on));
  const t = S.config?.team;
  b.title = on && t
    ? `Mode équipe actif : le modèle choisi dirige ; éclaireur ${t.scout_model}, exécutant ${t.worker_model}, expert ${t.expert_model} selon le chef`
    : "Mode équipe : le modèle choisi dirige, des sous-agents moins chers (ou un expert) font le travail";
  if (remember) wm.savePrefs({ team: on });
  updateSummary();
}
$("#opt-team").addEventListener("click", () => setTeam($("#opt-team").getAttribute("aria-pressed") !== "true"));

input.addEventListener("input", () => { autoGrow(input, 220); updateSuggest(); });
input.addEventListener("keydown", (e) => {
  if (!suggest.hidden) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); moveSuggest(e.key === "ArrowDown" ? 1 : -1); return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { if (acceptSuggest()) { e.preventDefault(); return; } }
    if (e.key === "Escape") { e.preventDefault(); hideSuggest(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); return; }
  const atStart = input.selectionStart === 0 && input.selectionEnd === 0;
  if (e.key === "ArrowUp" && (atStart || !input.value.includes("\n"))) {
    const hist = history();
    if (!hist.length || S.lastRecall >= hist.length - 1) return;
    e.preventDefault();
    S.lastRecall += 1;
    input.value = hist[S.lastRecall];
    autoGrow(input, 220);
  } else if (e.key === "ArrowDown" && S.lastRecall >= 0 && !input.value.slice(input.selectionStart).includes("\n")) {
    e.preventDefault();
    S.lastRecall -= 1;
    input.value = S.lastRecall >= 0 ? history()[S.lastRecall] : "";
    autoGrow(input, 220);
  }
});

// ------------------------------------------------------------ suggestions (/skills, @profiles)
let sgItems = [], sgIndex = 0, sgMode = "";
function updateSuggest() {
  const v = input.value;
  const upto = v.slice(0, input.selectionStart);
  let m;
  if ((m = upto.match(/^\/([\w:.-]*)$/))) {
    const q = m[1].toLowerCase();
    const cmds = S.probes[S.profile]?.commands || [];
    sgItems = cmds.filter((c) => c.name && c.name.toLowerCase().includes(q)).sort((a, b) => a.name.toLowerCase().indexOf(q) - b.name.toLowerCase().indexOf(q)).slice(0, 40)
      .map((c) => ({ value: `/${c.name} `, label: `/${c.name}`, desc: c.description }));
    sgMode = cmds.length ? "Skills et commandes du profil" : "Lance une tâche ou teste la connexion pour charger les skills";
  } else if ((m = upto.match(/^@([\w-]*)$/))) {
    const q = m[1].toLowerCase();
    sgItems = profiles().filter((p) => p.id.startsWith(q) || p.name.toLowerCase().startsWith(q))
      .map((p) => ({ value: `@${p.id} `, label: `@${p.id}`, desc: p.name }));
    sgMode = "Profil";
  } else { hideSuggest(); return; }
  sgIndex = 0;
  if (!sgItems.length && !sgMode.startsWith("Lance")) { hideSuggest(); return; }
  renderSuggest();
}
function renderSuggest() {
  suggest.replaceChildren(h("div", { class: "sg-head" }, sgMode), ...sgItems.map((it, i) => h("div", {
    class: `sg${i === sgIndex ? " active" : ""}`, on: { mousedown: (e) => { e.preventDefault(); sgIndex = i; acceptSuggest(); } },
  }, h("b", {}, it.label), h("span", { title: it.desc || "" }, it.desc || ""))));
  suggest.hidden = false;
  suggest.querySelector(".sg.active")?.scrollIntoView({ block: "nearest" });
}
function moveSuggest(d) { if (!sgItems.length) return; sgIndex = (sgIndex + d + sgItems.length) % sgItems.length; renderSuggest(); }
function acceptSuggest() {
  const it = sgItems[sgIndex];
  if (!it) return false;
  input.value = it.value + input.value.slice(input.selectionStart);
  input.setSelectionRange(it.value.length, it.value.length);
  hideSuggest();
  input.focus();
  return true;
}
function hideSuggest() { suggest.hidden = true; sgItems = []; }
input.addEventListener("blur", () => setTimeout(hideSuggest, 150));

// ------------------------------------------------------------ windows
const winCtx = {
  profiles,
  retry: (id) => launch(`/api/tasks/${id}/retry`, {}),
  duplicate: (id, pid) => launch(`/api/tasks/${id}/duplicate`, { profile: pid }),
  onClosed: (id) => { const t = S.tasks.get(id); if (t) t.closed = true; closeWindow(id); renderHistory(); },
  onFocus: (id) => { if (S.attention.delete(id)) renderTaskbar(); },
  onAttention: (id, kind) => attention(id, kind),
  autoImages: () => !!S.config?.ui?.auto_images,
  addContext: (t) => addContext(t),
  fork: (t) => forkTask(t),
  move: (t) => moveTask(t),
  projectName: (folder) => projectFor(folder)?.name || "",
};

function openWindow(t, fresh) {
  let w = S.windows.get(t.id);
  if (!w) {
    w = new TaskWindow(t, winCtx);
    S.windows.set(t.id, w);
    w.mount(fresh);
    w.load();
  } else if (fresh) wm.restore(t.id);
  $("#empty-hint").hidden = S.windows.size > 0;
  return w;
}

function closeWindow(id) {
  const w = S.windows.get(id);
  if (w) { w.destroy(); S.windows.delete(id); }
  S.attention.delete(id);
  $("#empty-hint").hidden = S.windows.size > 0;
  renderTaskbar();
}

function openTask(id) {
  const t = S.tasks.get(id);
  if (!t) return;
  if (t.closed) { t.closed = false; api(`/api/tasks/${id}`, { method: "PATCH", body: { closed: false } }).catch(() => {}); }
  openWindow(t, true);
  wm.restore(id);
  document.getElementById("history").hidden = true;
}

function onTask(t, fresh = false) {
  const prev = S.tasks.get(t.id);
  S.tasks.set(t.id, t);
  if (!prev) queueMicrotask(renderHome);
  const w = S.windows.get(t.id);
  if (w) w.update(t);
  else if (t.status === "awaiting" && prev) openTask(t.id); // a validation always surfaces, even from a closed window
  else if (!t.closed && (fresh || !prev)) openWindow(t, fresh || !prev);
  renderTaskbar();
  renderHistory();
}

function onEvent(ev) {
  const w = S.windows.get(ev.task_id);
  if (w) w.addEvent(ev);
  if (ev.kind === "show") showFiles(ev);
}

/** Claude asked to show files (tool mcp__jarvis__afficher): one preview window each, even if the
    task's window is minimized or closed. Live events only, never when replaying a conversation. */
function showFiles(ev) {
  if (ev.ts && Date.now() / 1000 - ev.ts > 120) return;
  const color = S.tasks.get(ev.task_id)?.color;
  for (const path of ev.data?.files || []) openPreview({ taskId: ev.task_id, path, color });
  for (const url of ev.data?.urls || []) {
    let image = false;
    try { image = IMG_EXT.test(new URL(url).pathname); } catch { /* shown as a page */ }
    openPreview({ url, kind: image ? "image" : "web", color });
  }
}

function renderTaskbar() {
  const ids = wm.minimizedIds();
  const bar = $("#taskbar");
  bar.replaceChildren(...ids.map((id) => {
    const t = S.tasks.get(id);
    if (!t) return null;
    const n = S.attention.get(id) || 0;
    return h("button", { type: "button", class: "tpill", style: { "--pc": t.color }, title: `${t.profile_name} · ${STATUS[t.status]} · ${t.prompt}`,
      on: { click: () => wm.restore(id) } }, h("span", { class: "sw" }), h("span", { class: "tt" }, t.title),
      h("span", { class: `dot s-${t.status}` }),
      t.status === "awaiting" ? h("span", { class: "cnt" }, "!") : n ? h("span", { class: "cnt" }, String(n)) : null);
  }));
  bar.hidden = !ids.length;
  $("#empty-hint").hidden = S.windows.size > 0;
}

// ------------------------------------------------------------ notifications
let audioCtx = null;
function beep(freq) {
  try {
    audioCtx = audioCtx || new AudioContext();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = freq;
    g.gain.setValueAtTime(0.08, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
    o.connect(g).connect(audioCtx.destination);
    o.start();
    o.stop(audioCtx.currentTime + 0.36);
  } catch { /* no audio */ }
}

function attention(id, kind) {
  const t = S.tasks.get(id);
  if (!t) return;
  const away = document.hidden || wm.isMinimized(id) || wm.focused() !== id;
  if (!away) return;
  S.attention.set(id, (S.attention.get(id) || 0) + 1);
  renderTaskbar();
  const title = kind === "awaiting" ? `À valider · ${t.profile_name}` : kind === "error" ? `En erreur · ${t.profile_name}` : `Terminée · ${t.profile_name}`;
  if (S.config.ui.sounds) beep(kind === "awaiting" ? 880 : kind === "error" ? 220 : 560);
  if (S.config.general.notifications && document.hidden && "Notification" in window && Notification.permission === "granted") {
    const n = new Notification(title, { body: t.title, tag: `${id}-${kind}`, icon: "/static/img/favicon.svg" });
    n.onclick = () => { window.focus(); openTask(id); n.close(); };
  }
}

// ------------------------------------------------------------ global UI
const isLoopback = (host) => /^(localhost|127\.|\[::1\]|0\.0\.0\.0|.*\.localhost$)/i.test(host);

document.addEventListener("click", (e) => {
  const b = e.target.closest(".copy-code");
  if (b) { copyText(b.parentElement.querySelector("code")?.textContent || ""); return; }
  const ref = e.target.closest(".fileref");
  if (ref) {
    e.preventDefault();
    const taskId = ref.closest("[data-task]")?.dataset.task || ref.closest(".win")?.dataset.id;
    if (taskId) openPreview({ taskId, path: ref.dataset.path, color: S.tasks.get(taskId)?.color });
    else toast("Aperçu disponible depuis la fenêtre de la tâche.");
    return;
  }
  const img = e.target.closest(".ext-img");
  if (img) { revealImage(img); return; }
  // Web links open in the built-in preview; Ctrl/⌘/Maj-clic keeps the real browser.
  const a = e.target.closest(".md a[href]");
  if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
    let url;
    try { url = new URL(a.href); } catch { return; }
    if (url.protocol !== "https:" || isLoopback(url.hostname) || !S.config?.ui?.link_preview) return;
    e.preventDefault();
    openPreview({ url: url.href, kind: IMG_EXT.test(url.pathname) ? "image" : "web" });
  }
});

document.addEventListener("keydown", (e) => {
  if (e.altKey && /^[1-9]$/.test(e.key)) {
    const p = profiles()[Number(e.key) - 1];
    if (p) { e.preventDefault(); selectProfile(p.id); input.focus(); }
  } else if ((e.ctrlKey || e.metaKey) && e.key === ",") {
    e.preventDefault();
    showConfig();
  } else if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    searchAnything();
  } else if (e.key === "Escape") {
    $("#menu-arrange").hidden = true;
    $("#menu-claudeai").hidden = true;
    if (!document.querySelector(".overlay")) closeDrawers();
  }
});

$("#btn-config").addEventListener("click", () => showConfig());
$("#btn-search").addEventListener("click", () => searchAnything());

/** Ctrl+K: actions, projects, discussions and Claude Code sessions in one list. */
function searchAnything() {
  closeDrawers();
  openPalette({
    tasks: () => [...S.tasks.values()],
    projects,
    projectName: (folder) => projectFor(folder)?.name || "",
    openTask,
    useProject: (p) => { useProject(p); input.focus(); },
    openSession: (pid, sid) => showSession(panelCtx, pid, sid),
    actions: () => [
      { label: "Nouvelle demande", icon: "send", hint: "Barre du bas", keywords: "écrire demander", run: () => input.focus() },
      { label: "Nouveau projet", icon: "book", keywords: "dossier créer", run: newProject },
      { label: "Ouvrir le projet actif", icon: "book", hint: projectFor(currentFolder())?.name || "", keywords: "consignes mémoire fichiers règles", run: () => toggleProject(panelCtx) },
      { label: "Sessions Claude Code", icon: "list", keywords: "desktop cli reprendre", run: () => toggleSessions(panelCtx) },
      { label: "Routines", icon: "clock", keywords: "tâches planifiées programmer", run: () => toggleRoutines(panelCtx) },
      { label: "Historique", icon: "clock", run: () => $("#btn-history").click() },
      { label: "Configuration", icon: "gauge", keywords: "réglages paramètres", run: () => showConfig() },
      { label: "Joindre des fichiers", icon: "clip", keywords: "pièce jointe pdf image", run: () => attacher.picker.click() },
      { label: "Contexte d'autres discussions", icon: "link", run: () => contextPicker.pick() },
      ...profiles().map((p) => ({ label: `Utiliser le compte ${p.name}`, icon: "user", hint: "Pour la prochaine demande",
        run: () => { selectProfile(p.id); input.focus(); } })),
      ...profiles().map((p) => ({ label: `Actualiser les limites · ${p.name}`, icon: "gauge", keywords: "quota usage session semaine",
        run: () => api(`/api/limits/${p.id}/refresh`, { method: "POST" }).then(() => toast(`Lecture des limites de ${p.name}…`)).catch((e) => toast(e.message, "err")) })),
      { label: "Ranger les fenêtres en cascade", icon: "panel", run: () => wm.arrange("cascade") },
      { label: "Ranger les fenêtres en mosaïque", icon: "panel", run: () => wm.arrange("mosaique") },
      { label: "Redémarrer la console", icon: "retry", keywords: "mise à jour", run: restartConsole },
      { label: "Rechercher une mise à jour", icon: "retry", keywords: "github version git pull", run: () => updateConsole() },
      { label: "Assistant de démarrage", icon: "sparkle", keywords: "installation comptes lanceur bienvenue", run: startSetup },
      { label: S.state.emergency_stop ? "Réactiver la console" : "Arrêt d'urgence", icon: "stop", run: () => setEmergency(!S.state.emergency_stop) },
    ],
  });
}
function closeDrawers(except) {
  for (const id of ["history", "sessions", "routines", "project"]) if (id !== except) document.getElementById(id).hidden = true;
}
const panelCtx = {
  profiles, presets: () => S.config.presets, launch, closeDrawers, onTask: (t) => onTask(t, true),
  currentProfile: () => S.profile,
  compose: (pid, text) => {
    closeDrawers();
    selectProfile(pid);
    input.value = text;
    autoGrow(input, 220);
    input.focus();
    input.setSelectionRange(text.length, text.length);
  },
  models: () => [...document.querySelectorAll("#opt-model option")].map((o) => [o.value, o.value ? o.textContent : "défaut du profil"]),
  tasks: () => [...S.tasks.values()],
  openTask: (id) => { closeDrawers(); openTask(id); },
  addContext: (t) => addContext(t),
  fork: (t) => forkTask(t),
  sessions: () => toggleSessions(panelCtx),
  openConfig: (tab) => showConfig(tab),
  mention: (text) => {
    const at = input.selectionStart ?? input.value.length;
    const before = input.value.slice(0, at), after = input.value.slice(at);
    const piece = `${before && !/\s$/.test(before) ? " " : ""}${text} `;
    input.value = before + piece + after;
    autoGrow(input, 220);
    input.focus();
    input.setSelectionRange(before.length + piece.length, before.length + piece.length);
  },
  workdir: () => { const v = $("#opt-workdir").value; return v === "__other__" ? "" : v; },
  workdirOptions: () => [...$("#opt-workdir").options].map((o) => o.value).filter((v) => v && v !== "__other__"),
  setWorkdir: (dir, isDefault) => {
    const sel = $("#opt-workdir");
    if (isDefault) { sel.value = ""; lastWorkdir = ""; updateSummary(); return; }
    if (![...sel.options].some((o) => o.value === dir)) sel.insertBefore(folderOption(dir), sel.lastChild);
    sel.value = dir;
    lastWorkdir = dir;
    updateSummary();
  },
  remember: (dir, pid = S.profile) => rememberWorkdir(pid, dir),
  recentFolders: (pid) => recentFolders(pid),
  project: (folder) => projectFor(folder),
  editProject: async (folder, pid) => {
    const saved = await editProject({ folder, profiles: profiles(), presets: S.config.presets, models: panelCtx.models(), current: { profile: pid } });
    if (saved) { refreshProjectsUI(); if (saved !== "deleted" && dirKey(saved.folder) === dirKey(currentFolder())) applyProject(saved); }
    return saved;
  },
  move: (t) => moveTask(t),
  moveSession: (pid, sid, title, from) => moveSession(pid, sid, title, from),
};
$("#btn-project").addEventListener("click", () => toggleProject(panelCtx));
$("#btn-history").addEventListener("click", () => {
  closeDrawers("history");
  toggleHistory({ tasks: () => [...S.tasks.values()].sort((a, b) => b.created - a.created), profiles, openTask });
});
$("#btn-sessions").addEventListener("click", () => toggleSessions(panelCtx));
$("#btn-routines").addEventListener("click", () => toggleRoutines(panelCtx));
$("#btn-claudeai").addEventListener("click", (e) => { e.stopPropagation(); $("#menu-claudeai").hidden = !$("#menu-claudeai").hidden; });
$("#menu-claudeai").addEventListener("click", (e) => { if (e.target.closest("a")) $("#menu-claudeai").hidden = true; });
$("#btn-emergency").addEventListener("click", () => setEmergency(!S.state.emergency_stop));
$("#btn-arrange").addEventListener("click", (e) => { e.stopPropagation(); $("#menu-arrange").hidden = !$("#menu-arrange").hidden; });
$("#menu-arrange").addEventListener("click", (e) => {
  const mode = e.target.closest("button")?.dataset.arrange;
  if (mode) { wm.arrange(mode); $("#menu-arrange").hidden = true; }
});
document.addEventListener("pointerdown", (e) => {
  if (!e.target.closest(".menu-wrap")) { $("#menu-arrange").hidden = true; $("#menu-claudeai").hidden = true; }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && wm.focused()) S.attention.delete(wm.focused()); });

// ------------------------------------------------------------ installable app
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("#btn-install").hidden = false;
});
window.addEventListener("appinstalled", () => { $("#btn-install").hidden = true; toast("JARVIS est installée : lance-la depuis le menu Démarrer.", "ok"); });
$("#btn-install").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice.catch(() => null);
  installPrompt = null;
  $("#btn-install").hidden = true;
});

const initialPanel = location.hash.replace("#", "");
boot().then(() => {
  // app shortcuts (manifest) open a panel directly
  if (initialPanel === "routines") toggleRoutines(panelCtx);
  if (initialPanel === "sessions") toggleSessions(panelCtx);
  if (["routines", "sessions"].includes(initialPanel)) history.replaceState(null, "", location.pathname);
}).catch((e) => {
  console.error(e);
  toast(`Démarrage impossible : ${e.message}`, "err");
});
