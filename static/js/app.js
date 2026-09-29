// Main: command bar, task windows, live stream, shortcuts, notifications.
import { ApiError, api, bootstrapAuth, openStream } from "./api.js";
import { openConfig, updateProbe } from "./config.js";
import { renderHistory, toggleHistory } from "./history.js";
import { toggleSessions } from "./library.js";
import { routinesChanged, toggleRoutines } from "./routines.js";
import { IMG_EXT } from "./md.js";
import { TaskWindow, autoGrow } from "./taskwin.js";
import { $, STATUS, confirmDialog, copyText, dialog, h, store, toast } from "./util.js";
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
  openStream({
    hello: resync, task: onTask, ev: onEvent, state: (st) => { S.state = { ...S.state, ...st }; renderState(); },
    probe: ({ profile: pid, probe }) => { S.probes[pid] = { ...(S.probes[pid] || {}), ...probe }; updateProbe(pid, probe); renderPills(); },
    config: reloadConfig, deleted: ({ id }) => { S.tasks.delete(id); closeWindow(id); renderHistory(); },
    reload: resync, routines: routinesChanged,
  }, () => $("#banner").dataset.down === "1" && renderBanner(false), () => renderBanner(true));
  input.focus();
}

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
  } catch { /* keep the old one */ }
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
    const label = !known ? "état inconnu — cliquer pour tester" : pr.logged_in ? "connecté" : "non connecté";
    return h("button", { type: "button", class: "ppill", style: { "--pc": p.color }, title: `${p.name} : ${label}`,
      on: { click: () => showConfig("profiles") } }, h("span", { class: "sw" }), p.name, h("span", { class: `st ${cls}` }));
  }));
}

async function setEmergency(on) {
  if (on && !(await confirmDialog("Arrêt d'urgence", "Toutes les tâches en cours sont stoppées et les nouvelles bloquées jusqu'à réactivation.", "Tout arrêter", "danger"))) return;
  try { S.state = { ...S.state, ...(await api("/api/emergency", { method: "POST", body: { on } })) }; renderState(); }
  catch (e) { toast(e.message, "err"); }
}

function showConfig(tabName = null) {
  openConfig({
    onSaved: (config, meta) => { applyConfig(config, meta); renderProfiles(); selectProfile(S.profile, false); renderPills(); },
    state: () => S.state, setEmergency, theme,
  }, tabName).catch((e) => toast(e.message, "err"));
}

// ------------------------------------------------------------ command bar
function renderProfiles() {
  $("#cmd-profiles").replaceChildren(...profiles().map((p, i) => h("button", {
    type: "button", class: "pbtn", role: "radio", "aria-checked": String(p.id === S.profile), style: { "--pc": p.color },
    title: `Profil ${p.name}${i < 9 ? ` (Alt+${i + 1})` : ""}`, on: { click: () => { selectProfile(p.id); input.focus(); } },
  }, h("span", { class: "sw" }), p.name)));
}

function option(value, label) { return h("option", { value }, label); }

function selectProfile(id, remember = true) {
  const p = profile(id);
  if (!p) return;
  S.profile = p.id;
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
  wd.replaceChildren(option("", `${S.meta?.profiles?.[p.id]?.workdir || p.workdir} (défaut)`),
    ...recent.map((d) => option(d, d)), option("__other__", "Autre dossier…"));
  wd.value = "";
}
const $$pbtn = () => Array.from(document.querySelectorAll("#cmd-profiles .pbtn"));

$("#opt-workdir").addEventListener("change", async (e) => {
  if (e.target.value !== "__other__") return;
  const v = await dialog({ title: "Dossier de travail", body: "Chemin complet d'un dossier existant (il doit être autorisé par la configuration de sécurité).",
    input: { placeholder: "C:\\Users\\…\\MonProjet" }, buttons: [{ label: "Annuler", value: null }, { label: "Utiliser", value: true, cls: "primary" }] });
  const sel = $("#opt-workdir");
  if (!v || !v.trim()) { sel.value = ""; return; }
  const path = v.trim();
  if (![...sel.options].some((o) => o.value === path)) sel.insertBefore(option(path, path), sel.lastChild);
  sel.value = path;
});

function rememberWorkdir(pid, dir) {
  if (!dir) return;
  const all = { ...(wm.prefs().workdirs || {}) };
  all[pid] = [dir, ...(all[pid] || []).filter((d) => d !== dir)].slice(0, 8);
  wm.savePrefs({ workdirs: all });
}

function history() { return store.get("jarvis.prompts", []); }

async function submit(extra = {}) {
  const prompt = input.value.trim();
  if (!prompt) return;
  const wd = $("#opt-workdir").value;
  const body = {
    prompt, profile: S.profile, model: $("#opt-model").value || null, preset: $("#opt-preset").value || null,
    effort: $("#opt-effort").value || null, workdir: wd && wd !== "__other__" ? wd : null,
    team: $("#opt-team").getAttribute("aria-pressed") === "true", ...extra,
  };
  // Clear at once so the next request can be typed while this one is sent.
  input.value = "";
  autoGrow(input, 220);
  hideSuggest();
  S.lastRecall = -1;
  const hist = history().filter((x) => x !== prompt);
  hist.unshift(prompt);
  store.set("jarvis.prompts", hist.slice(0, MAX_HISTORY));
  const t = await launch("/api/tasks", body);
  if (t) rememberWorkdir(body.profile, body.workdir);
  else if (!input.value.trim()) { input.value = prompt; autoGrow(input, 220); }
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
    if (taskId) openPreview({ taskId, path: ref.dataset.path });
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
  } else if (e.key === "Escape") {
    $("#menu-arrange").hidden = true;
    $("#menu-claudeai").hidden = true;
    if (!document.querySelector(".overlay")) closeDrawers();
  }
});

$("#btn-config").addEventListener("click", () => showConfig());
function closeDrawers(except) {
  for (const id of ["history", "sessions", "routines"]) if (id !== except) document.getElementById(id).hidden = true;
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
};
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
