// Main: command bar, task windows, live stream, shortcuts, notifications.
import { runAction } from "./actions.js";
import { ApiError, api, bootstrapAuth, openStream } from "./api.js";
import { Attacher, hasFiles } from "./attach.js";
import { ContextPicker } from "./context.js";
import { pickFolder } from "./folderpicker.js";
import { baseName as pname, editProject, loadProjects, projectFor, projects, setProjects } from "./projects.js";
import { accountState, gauges, initLimits, limitsTitle, openLimits, sessionUsed, updateLimits } from "./limits.js";
import { projectActionsChanged, projectFollow, toggleProject } from "./project.js";
import { editNote, initNotes, notesChanged, notesRecolor, toggleNotes } from "./notes.js";
import { checkVersion, restartConsole, updateConsole } from "./system.js";
import { openPalette } from "./palette.js";
import { openSetup } from "./setup.js";
import { openConfig, updateProbe } from "./config.js";
import { renderHistory, toggleHistory } from "./history.js";
import { showSession, toggleSessions } from "./library.js";
import { routinesChanged, toggleRoutines } from "./routines.js";
import { IMG_EXT } from "./md.js";
import { mountLogo, setLogoActivity } from "./logo.js";
import { setAccounts, taskTint } from "./tint.js";
import { TaskWindow, autoGrow } from "./taskwin.js";
import { barSent, setupBar } from "./bar.js";
import { $, STATUS, confirmDialog, copyText, createLauncher, debounce, dialog, fmtDate, h, modelName, statusLabel, store, toast, toolLabel } from "./util.js";
import { configure as configureDisplays, displayTitle, isWindowOpen, openDisplayModal, openDisplayWindow, setAnswer, setDoc } from "./display.js";
import * as regard from "./regard.js";
import { openPreview, refreshPreviews, revealImage } from "./viewer.js";
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
setAccounts({ color: (pid) => profile(pid)?.color, current: () => S.profile });

document.querySelectorAll("[data-logo]").forEach(mountLogo);

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
  initNotes(notesCtx);
  setInterval(renderPills, 60_000); // a window past its reset time goes back to 0
  openStream({
    hello: resync, task: onTask, ev: onEvent, state: (st) => { S.state = { ...S.state, ...st }; renderState(); },
    probe: ({ profile: pid, probe }) => { S.probes[pid] = { ...(S.probes[pid] || {}), ...probe }; updateProbe(pid, probe); renderPills(); },
    config: reloadConfig, deleted: ({ id }) => { S.tasks.delete(id); closeWindow(id); renderHistory(); },
    reload: resync, routines: (p) => { routinesChanged(p); projectActionsChanged(); }, limits: ({ profile: pid, limits: l }) => updateLimits(pid, l),
    projects: ({ projects: list }) => { setProjects(list); refreshProjectsUI(); projectActionsChanged(); },
    notes: notesChanged,
  }, () => $("#banner").dataset.down === "1" && renderBanner(false), () => renderBanner(true));
  input.focus();
  checkVersion();
  setInterval(checkVersion, 60_000);
  if (S.config.general.setup_done === false) startSetup(); // fresh install: the first-run assistant
  else setTimeout(proposeDesktopApp, 2500);
}

/** In the desktop app, once: should start.bat, the launcher and the session start open it rather than the
 * browser? (Configuration → Général → Ouverture au démarrage; the choice can be changed there.) */
async function proposeDesktopApp() {
  if (!window.jarvis || S.config?.general?.open_as === "bureau" || store.get("jarvis.app-proposed")
    || document.querySelector("#modal-root .overlay")) return;
  const v = await dialog({
    title: "Ouvrir JARVIS avec l'application de bureau ?",
    body: "start.bat, le lanceur et le démarrage avec la session ouvriront cette application au lieu du navigateur, "
      + "et un raccourci « JARVIS » va dans le menu Démarrer et sur le Bureau. Tu pourras revenir au navigateur dans Configuration → Général.",
    buttons: [{ label: "Plus tard", value: null }, { label: "Garder le navigateur", value: "non" },
      { label: "Utiliser l'application", value: "oui", cls: "primary" }],
  });
  if (!v) return;
  store.set("jarvis.app-proposed", v);
  if (v !== "oui") return;
  try {
    await api("/api/config", { method: "PUT", body: { ...S.config, general: { ...S.config.general, open_as: "bureau" } } });
    if (window.jarvis.platform === "win32") await createLauncher(api);
    toast("JARVIS s'ouvrira avec l'application de bureau.", "ok");
  } catch (e) { toast(e.message, "err"); }
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
  wm.retheme();         // (native windows of the desktop app follow the theme)
  regard.configure({}); // (the chips follow the "Joindre ce que je regarde" setting)
}

/** Desktop app: "Affichage" changed in the configuration, the interface reloads in the other mode. */
async function switchDisplay() {
  const want = S.config?.ui?.bureau;
  if (!window.jarvis || !want || want === window.jarvis.mode || S.switching) return;
  S.switching = true;
  const ok = await confirmDialog("Changer l'affichage ?", want === "integre"
    ? "Chaque discussion, aperçu ou affichage deviendra une fenêtre du bureau. L'interface se recharge ; les tâches continuent."
    : "Toute la console revient dans une seule fenêtre. L'interface se recharge ; les tâches continuent.", "Recharger maintenant");
  S.switching = false;
  if (ok) window.jarvis.switchMode(want);
  else toast("L'affichage changera au prochain lancement de l'application.");
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
    switchDisplay();
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
  for (const w of S.windows.values()) w.repaint();
  renderTaskbar();
  renderHistory();
  renderHome();
  notesRecolor();
}

// ------------------------------------------------------------ top bar & state
function renderState() {
  const st = S.state;
  const counter = (cls, n, label) => h("span", { class: `counter ${cls}${n ? " live" : ""}` },
    h("span", { class: "d" }), h("b", {}, String(n || 0)), label);
  $("#counters").replaceChildren(
    counter("run", st.running, "en cours"), counter("await", st.awaiting, "à valider"), counter("queue", st.queued, "en file"));
  setLogoActivity(st.running, st.awaiting);
  const btn = $("#btn-emergency");
  btn.textContent = st.emergency_stop ? "Réactiver" : "Arrêt d'urgence";
  btn.className = `btn ${st.emergency_stop ? "ok" : "danger"}`;
  renderBanner();
  renderPills();
  document.title = `${st.awaiting ? `(${st.awaiting}) ` : ""}JARVIS · Console d'agents`;
  if (!st.cli?.path) renderBanner();
  window.jarvis?.status({ running: st.running || 0, awaiting: st.awaiting || 0, queued: st.queued || 0 });
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
// (in every document: the command bar may float in a window of its own, bar.js)
wm.onDocument((doc) => doc.addEventListener("pointerdown", (e) => {
  if (!$("#opt-panel").hidden && !e.target.closest("#opt-panel, #opt-summary")) toggleOptions(false);
}));
wm.onDocument((doc) => doc.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#opt-panel").hidden) { e.preventDefault(); e.stopPropagation(); toggleOptions(false); $("#opt-summary").focus(); }
}, true));

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

  const dm = p.default_model || "default";
  const dres = (S.probes[p.id]?.models || []).find((m) => m.value === dm)?.resolved;
  const models = new Map([["", `défaut du compte ${p.name} (${dres ? modelName(dres) : dm === "default" ? "celui de Claude Code" : dm})`]]);
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
const $$pbtn = () => Array.from($("#cmd-profiles").querySelectorAll(".pbtn"));

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
  for (const w of S.windows.values()) w.repaint();
  renderTaskbar();
  notesRecolor();
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
  const touched = (t) => Math.max(t.created || 0, t.started || 0, t.ended || 0);
  const recent = [...S.tasks.values()].sort((a, b) => touched(b) - touched(a)).slice(0, 6);
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
        h("small", {}, [projectFor(t.workdir)?.name || pname(t.workdir), statusLabel(t), fmtDate(touched(t))].filter(Boolean).join(" · ")))))) : null,
    // (the accounts' limits are in the top bar)
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
// "Ce que je regarde": the preview, display or text the user looks at goes with the next request
regard.configure({ enabled: () => S.config?.ui?.regard !== false, displayTitle, taskTitle: (id) => S.tasks.get(id)?.title || "" });
regard.chip($("#cmd-regard"));
// desktop app, "Intégré au bureau": the command bar floats in a window of its own (bar.js)
setupBar({ dock: $("#dock"), popups: [suggest, $("#opt-panel")], callButton: $("#bar-call") });
// desktop app: its global shortcut (or its notification area) asks for a new request
window.jarvis?.onCommand(({ cmd, tid } = {}) => {
  if (cmd === "nouvelle-demande") { input.focus(); input.select(); }
  else if (cmd === "ouvrir" && S.tasks.has(tid)) openTask(tid);   // a click on a notification
});
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
// Files dropped anywhere else on the page (or in a native window) join the next request and never replace the app.
wm.onDocument((doc) => doc.defaultView.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }));
wm.onDocument((doc) => doc.defaultView.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  attacher.add([...e.dataTransfer.files]);
  input.focus();
}));

async function submit(extra = {}) {
  const prompt = input.value.trim();
  const files = attacher.ids();
  const context = contextPicker.sources();
  if (!prompt && !files.length && !context.length && !attacher.busy()) return;
  if (!attacher.ready()) return;
  const wd = $("#opt-workdir").value;
  const seen = regard.get();
  const body = {
    prompt, profile: S.profile, model: $("#opt-model").value || null, preset: $("#opt-preset").value || null,
    effort: $("#opt-effort").value || null, workdir: wd && wd !== "__other__" ? wd : null,
    team: $("#opt-team").getAttribute("aria-pressed") === "true", attachments: files, context, regard: regard.payload(seen), ...extra,
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
  if (t) { rememberWorkdir(body.profile, body.workdir); attacher.sent(files); contextPicker.clear(); regard.sent(seen); barSent(); }
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
configureDisplays({
  autoImages: winCtx.autoImages,
  // jarvis.action() of an application, confirmed by the user: the action of the discussion's project
  appAction: (taskId, name, args) => {
    const t = S.tasks.get(taskId);
    const proj = projectFor(t?.workdir);
    const a = proj?.actions?.find((x) => x.name === name);
    if (!a) { toast(proj ? `Action introuvable dans « ${proj.name} » : /${name}` : "Cette discussion n'est pas dans un projet.", "err"); return; }
    runAction(panelCtx, proj.folder, a, { args });
  },
});

function openWindow(t, fresh) {
  let w = S.windows.get(t.id);
  if (!w) {
    w = new TaskWindow(t, winCtx);
    S.windows.set(t.id, w);
    w.mount(fresh);
    w.load();
  } else if (fresh) wm.restore(t.id);
  $("#empty-hint").hidden = S.windows.size > 0 && !wm.isNative();
  return w;
}

function closeWindow(id) {
  const w = S.windows.get(id);
  if (w) { w.destroy(); S.windows.delete(id); }
  S.attention.delete(id);
  $("#empty-hint").hidden = S.windows.size > 0 && !wm.isNative();
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
  if (t.action && (!prev || prev.status !== t.status || prev.title !== t.title)) actionRunsChanged(); // its line in the project's Actions tab
}

const actionRunsChanged = debounce(() => projectActionsChanged(), 400);

function onEvent(ev) {
  const w = S.windows.get(ev.task_id);
  if (w) w.addEvent(ev);
  if (window.jarvis && (ev.kind === "approval" || ev.kind === "approval_done")) nativeNotify(ev);
  if (ev.kind === "show") showFiles(ev);
  if (ev.kind === "display") showDisplay(ev);
  if (ev.kind === "tool_result") followFiles();
  if (ev.kind === "display_answer") setAnswer(ev.task_id, ev.data || {});
}

/** A tool just ran (maybe a file written): open file previews check their file at once, not at the next tick. */
let followTimer = 0;
function followFiles() {
  clearTimeout(followTimer);
  followTimer = setTimeout(refreshPreviews, 300);
}

/** Claude composed a display (tool presenter). In the conversation it is drawn by the task's window; a
    window or a modal opens here, live only, and once (an update redraws it where it is). A modal never
    covers the screen for a task the user is not looking at: it opens as a window instead. */
function showDisplay(ev) {
  const d = ev.data || {};
  const e = setDoc(ev.task_id, d);
  if (ev.ts && Date.now() / 1000 - ev.ts > 120) return;
  const first = (d.rev || 1) === 1 || e.movedFrom;
  const color = taskTint(S.tasks.get(ev.task_id));
  const w = S.windows.get(ev.task_id);
  const looking = w && wm.has(ev.task_id) && !wm.isMinimized(ev.task_id);
  if (d.ou === "fenetre" || (d.ou === "modale" && !looking)) {
    if (first && !isWindowOpen(ev.task_id, d.key)) openDisplayWindow(ev.task_id, d.key, color);
  } else if (d.ou === "modale") {
    if (first) openDisplayModal(ev.task_id, d.key, color);
  } else if (!w && first) {
    const t = S.tasks.get(ev.task_id);
    toast(`Claude a affiché « ${d.titre} » dans la tâche « ${t?.title || "…"} ».`);
  }
}

/** Claude asked to show files, pages of approved sites or a tool's result (tools mcp__jarvis__afficher
    and afficher_resultat): one preview window each, even if the task's window is minimized or closed.
    Live events only, never when replaying a conversation. Other sites wait for a click in the task. */
function showFiles(ev) {
  if (ev.ts && Date.now() / 1000 - ev.ts > 120) return;
  const color = taskTint(S.tasks.get(ev.task_id));
  for (const path of ev.data?.files || []) openPreview({ taskId: ev.task_id, path, color });
  for (const url of ev.data?.urls || []) {
    let image = false;
    try { image = IMG_EXT.test(new URL(url).pathname); } catch { /* shown as a page */ }
    openPreview({ url, kind: image ? "image" : "web", color });
  }
  for (const result of ev.data?.results || []) openPreview({ taskId: ev.task_id, result, color });
  const ask = ev.data?.ask || [];
  if (ask.length && !S.windows.get(ev.task_id)) {
    const t = S.tasks.get(ev.task_id);
    toast(`Claude propose d'ouvrir ${ask.length > 1 ? `${ask.length} pages web` : "une page web"} : ouvre la tâche « ${t?.title || "…"} » pour décider.`);
  }
}

function renderTaskbar() {
  const docked = [], floating = [];
  const d = $("#desktop").getBoundingClientRect();
  for (const id of wm.minimizedIds()) {
    const pill = taskPill(id);
    if (!pill) continue;
    const at = wm.flag(id, "mini");
    if (at) {
      // moved onto the desktop: kept inside it, above the command bar
      const x = Math.max(0, Math.min(at.x, d.width - 140)), y = Math.max(0, Math.min(at.y, d.height - wm.reservedHeight() - 34));
      Object.assign(pill.style, { left: `${Math.round(d.left + x)}px`, top: `${Math.round(d.top + y)}px` });
      floating.push(pill);
    } else docked.push(pill);
  }
  const bar = $("#taskbar");
  bar.replaceChildren(...docked);
  bar.hidden = !docked.length;
  $("#minis").replaceChildren(...floating);
  $("#brand").title = wm.visibleCount() ? "Réduire toutes les fenêtres"
    : wm.minimizedIds().length ? "Rétablir les fenêtres" : "Aucune fenêtre ouverte";
  $("#empty-hint").hidden = S.windows.size > 0 && !wm.isNative();
}

/** A minimized window (task or preview): click to restore, drag to move it, × to close it. */
function taskPill(id) {
  const t = S.tasks.get(id), m = wm.meta(id);
  if (!t && !m) return null;
  const n = S.attention.get(id) || 0;
  const name = t ? t.title : m.title;
  const close = () => (t ? S.windows.get(id)?.close() : m.onClose?.());
  const pill = h("div", {
    class: "tpill", role: "button", tabindex: "0", style: { "--pc": (t ? taskTint(t).color : m.color) || "var(--accent)" },
    title: `${t ? `${t.profile_name} · ${STATUS[t.status]} · ${t.prompt}` : `Aperçu · ${m.subtitle || name}`}\nClic : rétablir · glisser : déplacer`,
    on: { keydown: (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); wm.restore(id); } else if (e.key === "Delete") close();
    } },
  }, t ? h("span", { class: "sw" }) : h("span", { class: "pic", svg: m.icon || "file" }),
  h("span", { class: "tt" }, name),
  t ? h("span", { class: `dot s-${t.status}` }) : null,
  t?.status === "awaiting" ? h("span", { class: "cnt" }, "!") : n ? h("span", { class: "cnt" }, String(n)) : null,
  h("button", { type: "button", class: "tp-x", title: "Fermer", "aria-label": `Fermer ${name}`, svg: "close",
    on: { click: (e) => { e.stopPropagation(); close(); } } }));
  dragPill(pill, id);
  return pill;
}

function dragPill(pill, id) {
  pill.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest(".tp-x")) return;
    const sx = e.clientX, sy = e.clientY, r = pill.getBoundingClientRect();
    let moved = false;
    pill.setPointerCapture(e.pointerId);
    const at = (ev) => ({ left: r.left + ev.clientX - sx, top: r.top + ev.clientY - sy });
    const move = (ev) => {
      if (!moved && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) return;
      if (!moved) { moved = true; pill.classList.add("dragging"); document.body.classList.add("dragging"); }
      const p = at(ev);
      Object.assign(pill.style, { left: `${p.left}px`, top: `${p.top}px` });
    };
    const up = (ev) => {
      pill.removeEventListener("pointermove", move);
      pill.removeEventListener("pointerup", up);
      pill.removeEventListener("pointercancel", up);
      document.body.classList.remove("dragging");
      if (!moved) { if (ev.type === "pointerup") wm.restore(id); return; }
      const d = $("#desktop").getBoundingClientRect(), p = at(ev);
      // dropped on the command bar: back in the taskbar
      const docked = ev.clientY >= d.bottom - wm.reservedHeight();
      wm.flag(id, "mini", docked ? null : { x: Math.round(p.left - d.left), y: Math.round(p.top - d.top) });
      renderTaskbar();
    };
    pill.addEventListener("pointermove", move);
    pill.addEventListener("pointerup", up);
    pill.addEventListener("pointercancel", up);
  });
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

/** Desktop app: an approval waiting gets a notification of the OS, with Approuver and Refuser for a tool
 * call (a question, a plan or a proposal is read in its window: a click opens it). */
function nativeNotify(ev) {
  const d = ev.data || {};
  if (ev.kind === "approval_done") { window.jarvis.notifyDone(d.id); return; }
  if (!S.config?.general?.notifications || (ev.ts && Date.now() / 1000 - ev.ts > 120)) return;
  const t = S.tasks.get(ev.task_id);
  const what = { question: "Question de Claude", plan: "Plan à approuver", proposal: "Proposition de Claude" }[d.kind] || toolLabel(d.tool);
  window.jarvis.notify({ tid: ev.task_id, aid: d.id, kind: d.kind, title: `À valider · ${t?.title || "discussion"}`,
    body: `${[what, d.target].filter(Boolean).join(" — ")}${t?.profile_name ? ` · ${t.profile_name}` : ""}`,
    buttons: d.kind === "hook" || d.kind === "permission", looking: wm.focused() === ev.task_id && !wm.isMinimized(ev.task_id) });
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
  if (window.jarvis) {
    // desktop app: the OS's notifications (an approval has its own, with its buttons: nativeNotify)
    if (kind !== "awaiting" && S.config.general.notifications) {
      window.jarvis.notify({ tid: id, kind, title, body: t.title, looking: wm.focused() === id && !wm.isMinimized(id) });
    }
    return;
  }
  if (S.config.general.notifications && document.hidden && "Notification" in window && Notification.permission === "granted") {
    const n = new Notification(title, { body: t.title, tag: `${id}-${kind}`, icon: "/static/img/favicon.svg" });
    n.onclick = () => { window.focus(); openTask(id); n.close(); };
  }
}

// ------------------------------------------------------------ global UI
const isLoopback = (host) => /^(localhost|127\.|\[::1\]|0\.0\.0\.0|.*\.localhost$)/i.test(host);

wm.onDocument((doc) => doc.addEventListener("click", onDocumentClick));
function onDocumentClick(e) {
  const b = e.target.closest(".copy-code");
  if (b) { copyText(b.parentElement.querySelector("code")?.textContent || ""); return; }
  const ref = e.target.closest(".fileref");
  if (ref) {
    e.preventDefault();
    const taskId = ref.closest("[data-task]")?.dataset.task || ref.closest(".win")?.dataset.id;
    if (taskId) openPreview({ taskId, path: ref.dataset.path, color: taskTint(S.tasks.get(taskId)) });
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
}

wm.onDocument((doc) => doc.addEventListener("keydown", onShortcut));
function onShortcut(e) {
  // from a native window (desktop app), the shortcuts that open something of the JARVIS window bring it forward
  const elsewhere = (e.target?.ownerDocument || e.target) !== document && !!window.jarvis;
  if (e.altKey && /^[1-9]$/.test(e.key)) {
    const p = profiles()[Number(e.key) - 1];
    if (p) { e.preventDefault(); if (elsewhere) window.jarvis.win("hub"); selectProfile(p.id); input.focus(); }
  } else if ((e.ctrlKey || e.metaKey) && e.key === ",") {
    e.preventDefault();
    if (elsewhere) window.jarvis.win("hub");
    showConfig();
  } else if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (elsewhere) window.jarvis.win("hub");
    searchAnything();
  } else if (e.ctrlKey && e.altKey && !e.shiftKey && e.code === "KeyW") {
    e.preventDefault();
    closeAll();
  } else if (e.key === "Escape" && !elsewhere) {
    $("#menu-arrange").hidden = true;
    $("#menu-claudeai").hidden = true;
    if (!document.querySelector(".overlay")) closeDrawers();
  }
}

/** Ctrl+Alt+W / Ranger → Tout fermer: every session and preview window, then every open modal (Échap each). */
function closeAll() {
  for (const id of wm.ids()) {
    if (S.tasks.has(id)) S.windows.get(id)?.close();
    else wm.meta(id)?.onClose?.();
  }
  for (let i = 0; i < 8 && document.querySelector("#modal-root .overlay"); i++) {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  }
  closeDrawers();
}
$("#btn-config").addEventListener("click", () => showConfig());
$("#brand").addEventListener("click", () => wm.toggleDesktop());
window.addEventListener("resize", debounce(() => { if ($("#minis").childElementCount) renderTaskbar(); }, 150));
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
      ...projectActions(),
      { label: "Nouvelle demande", icon: "send", hint: "Barre du bas", keywords: "écrire demander", run: () => input.focus() },
      { label: "Nouveau projet", icon: "book", keywords: "dossier créer", run: newProject },
      { label: "Ouvrir le projet actif", icon: "book", hint: projectFor(currentFolder())?.name || "", keywords: "consignes mémoire fichiers règles", run: () => toggleProject(panelCtx) },
      { label: "Nouvelle note", icon: "note", hint: "Générale", keywords: "noter rappel mémo pense-bête", run: () => editNote(null, { folder: "" }) },
      ...(projectFor(currentFolder()) ? [{ label: `Nouvelle note du projet ${projectFor(currentFolder()).name}`, icon: "note",
        keywords: "noter rappel mémo pense-bête", run: () => editNote(null, { folder: projectFor(currentFolder()).folder }) }] : []),
      { label: "Notes", icon: "note", keywords: "rappels mémos pense-bête", run: () => toggleNotes(notesCtx) },
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
      { label: "Tout fermer (fenêtres et modales)", icon: "close", hint: "Ctrl+Alt+W", run: closeAll },
      { label: "Redémarrer la console", icon: "retry", keywords: "mise à jour", run: restartConsole },
      { label: "Rechercher une mise à jour", icon: "retry", keywords: "github version git pull", run: () => updateConsole() },
      { label: "Assistant de démarrage", icon: "sparkle", keywords: "installation comptes lanceur bienvenue", run: startSetup },
      { label: S.state.emergency_stop ? "Réactiver la console" : "Arrêt d'urgence", icon: "stop", run: () => setEmergency(!S.state.emergency_stop) },
    ],
  });
}
/** The buttons of the active project in Ctrl+K: a launch is a normal discussion of the project. */
function projectActions() {
  const proj = projectFor(currentFolder());
  if (!proj) return [];
  return (proj.actions || []).map((a) => ({ label: a.label, icon: "bolt", keywords: `${a.name} ${a.description || ""} action projet ${proj.name}`,
    hint: `${proj.name} · /${a.name}${a.status === "ok" ? "" : " · à valider"}`, run: () => runAction(panelCtx, proj.folder, a) }));
}

function closeDrawers(except) {
  for (const id of ["history", "sessions", "routines", "project", "notes"]) if (id !== except) document.getElementById(id).hidden = true;
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
  models: () => [...$("#opt-model").options].map((o) => [o.value, o.value ? o.textContent : "défaut du profil"]),
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

/** What the notes need: accounts, the active folder, the project panel, the sound and system notification. */
const notesCtx = {
  profiles, currentProfile: () => S.profile, currentFolder, closeDrawers,
  openProject: (folder) => {
    const p = projectFor(folder);
    if (!p) return;
    useProject(p);
    if ($("#project").hidden) toggleProject(panelCtx);
  },
  alert: ({ title, body, tag, onClick }) => {
    if (S.config?.ui?.sounds) beep(660);
    if (window.jarvis) {
      // desktop app: the reminder is in the JARVIS window (often hidden), which comes forward without taking
      // the keyboard; and a notification of the OS (a click opens the JARVIS window)
      window.jarvis.win("hub-reveal");
      if (S.config?.general?.notifications) window.jarvis.notify({ tid: "rappel", kind: tag, title, body });
      return;
    }
    if (S.config?.general?.notifications && document.hidden && "Notification" in window && Notification.permission === "granted") {
      const n = new Notification(title, { body, tag, icon: "/static/img/favicon.svg", requireInteraction: true });
      n.onclick = () => { window.focus(); onClick?.(); n.close(); };
    }
  },
};
$("#btn-notes").addEventListener("click", () => toggleNotes(notesCtx));
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
  if (mode === "close") { closeAll(); $("#menu-arrange").hidden = true; }
  else if (mode) { wm.arrange(mode); $("#menu-arrange").hidden = true; }
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
