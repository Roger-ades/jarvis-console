// JARVIS desktop app (see docs/electron.md). It starts the console's Python server, or
// reuses the one already running, then shows the interface in one of two ways (Configuration →
// Interface → Affichage):
//   "integre"  no JARVIS window: a floating bar (the command bar, with the JARVIS menu) and windows of the
//              OS for each discussion, preview, display, panel (Historique, Notes…) or modal
//              (configuration, Ctrl+K…), mixed with the other applications;
//   "fenetre"  the whole console in one window, as in the browser.
// The console's page runs in the JARVIS window (hidden with "integre", once the bar is there); the other
// windows are its children (window.open): one JavaScript context, one live stream, one renderer process.
// The page asks for every window operation through the preload (window.jarvis) and the main process
// checks that the request comes from the console's own page.
// Started by `electron .` (start-app.bat), or by the installed application (loader.js), which runs
// this file from the JARVIS folder.
"use strict";
const { app, BrowserWindow, Menu, Notification, Tray, WebContentsView, dialog, globalShortcut, ipcMain, nativeImage, nativeTheme,
  screen, session, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

// the JARVIS folder; the installed application names it (loader.js) when it runs the copy packed with it
const ROOT = app.isPackaged && process.env.JARVIS_ROOT ? path.resolve(process.env.JARVIS_ROOT) : path.resolve(__dirname, "..");
const IS_WIN = process.platform === "win32";
const IS_MAC = process.platform === "darwin";
const HEAD_H = 38;                       // a window's header (.win-head): the native buttons sit in it
const WIN_PREFIX = "jarvis-win:";        // window.open name of a native window: jarvis-win:<id>
const SHORTCUT = "CommandOrControl+Alt+J";
const MODES = ["integre", "fenetre"];
const ICON = path.join(ROOT, "static", "img", IS_WIN ? "jarvis.ico" : "icon-512.png");
const AUMID = "local.jarvis.console";    // the app's identity for Windows (taskbar grouping, notifications)
const APP_ARGS = app.isPackaged ? [] : [__dirname];   // how the OS starts this app again (shortcuts, session start)

let hub = null;                          // the JARVIS window (the console's page)
let tray = null;
let mode = "integre";
let origin = "";
let port = 0;
let token = "";                          // the console's access token (data/token), for the app's own calls
let quitting = false;
let hubAway = false;                     // "Intégré au bureau": the console's page is loading, the JARVIS window stays hidden
const children = new Map();              // id -> native BrowserWindow
const closing = new Set();               // ids the page closes itself (no close request back)
let overlay = { color: "#161b22", symbolColor: "#c9d1d9" };
let cascade = 0;
// the floating JARVIS bar ("Intégré au bureau"): the command bar in a frameless window of its own
let bar = null;
let barPinned = false;                   // stays when the user clicks elsewhere
let barHold = false;                     // a file picker of the bar is open: it does not hide meanwhile
let barH = 190;
const BAR_W = 820;
// started with the session (--demarrage): nothing shows until the user opens JARVIS (notification area,
// shortcut, launcher); the windows the page opens meanwhile wait hidden
let discreet = process.argv.includes("--demarrage");

// ------------------------------------------------------------------ settings, like `python -m console`
function loadEnv() {
  try {
    for (const line of fs.readFileSync(path.join(ROOT, ".env"), "utf8").split(/\r?\n/)) {
      const m = line.trim().match(/^([A-Za-z_][\w]*)\s*=\s*(.*)$/);
      if (m && !line.trim().startsWith("#") && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch { /* no .env */ }
}

function settings() {
  loadEnv();
  const raw = process.env.CONSOLE_DATA_DIR;
  const data = raw ? path.resolve(raw.replace(/^~(?=$|[\\/])/, os.homedir())) : path.join(ROOT, "data");
  let cfg = {};
  try { cfg = JSON.parse(fs.readFileSync(path.join(data, "config.json"), "utf8")); } catch { /* first start */ }
  return { data, port: Number(process.env.CONSOLE_PORT) || cfg.general?.port || 8788 };
}

function call(port, p, { method = "GET", token = "", body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({ host: "127.0.0.1", port, path: p, method, timeout: 3000, headers: {
      "Content-Type": "application/json", ...(token ? { "X-Console-Token": token } : {}),
      ...(data ? { "Content-Length": data.length } : {}) } }, (res) => {
      let buf = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { buf += c; });
      res.on("end", () => {
        if (res.statusCode >= 400) { reject(new Error(`HTTP ${res.statusCode}`)); return; }
        try { resolve(JSON.parse(buf || "{}")); } catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("délai dépassé")));
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

async function serverUp(port) {
  try { return (await call(port, "/api/ping")).app === "jarvis-console"; } catch { return false; }
}

/** The server through the usual launcher (it creates .venv and installs what is missing), without
 * opening a browser. JARVIS_PYTHON: a Python to run the console with directly (tests, development). */
function startServer() {
  let cmd, args;
  if (process.env.JARVIS_PYTHON) [cmd, args] = [process.env.JARVIS_PYTHON, ["-m", "console", "--no-browser", "--background"]];
  else if (IS_WIN) [cmd, args] = ["cmd.exe", ["/d", "/c", path.join(ROOT, "start.bat"), "--no-browser"]];
  else [cmd, args] = ["/bin/bash", [path.join(ROOT, "start.command"), "--no-browser"]];
  spawn(cmd, args, { cwd: ROOT, detached: true, stdio: "ignore", windowsHide: true }).unref();
}

async function waitServer(port, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await serverUp(port)) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

// ------------------------------------------------------------------ remembered places
const stateFile = () => path.join(app.getPath("userData"), "etat.json");
function loadState() { try { return JSON.parse(fs.readFileSync(stateFile(), "utf8")); } catch { return {}; } }
function saveState(patch) {
  try { fs.writeFileSync(stateFile(), JSON.stringify({ ...loadState(), ...patch }, null, 2)); } catch { /* not essential */ }
}

/** A rectangle that still lands on one of the screens (a monitor may have been unplugged). */
function onScreen(r) {
  return r && Number.isFinite(r.x) && Number.isFinite(r.y) && screen.getAllDisplays().some(({ workArea: a }) =>
    r.x < a.x + a.width - 80 && r.x + (r.width || 400) > a.x + 80 && r.y >= a.y - 10 && r.y < a.y + a.height - 60);
}

/** Where a new native window opens: cascading on the screen of the JARVIS window. */
function nextPosition(width, height) {
  const ref = hub && !hub.isDestroyed() && hub.isVisible() ? hub.getBounds() : screen.getCursorScreenPoint();
  const a = screen.getDisplayMatching({ x: ref.x, y: ref.y, width: ref.width || 1, height: ref.height || 1 }).workArea;
  const w = Math.min(width || 820, a.width - 40), h = Math.min(height || 560, a.height - 40);
  const n = cascade++ % 10;
  return { x: a.x + 40 + ((n * 34) % Math.max(1, a.width - w - 80)), y: a.y + 30 + ((n * 30) % Math.max(1, a.height - h - 60)) };
}

function parseFeatures(s) {
  const out = {};
  for (const part of String(s || "").split(",")) {
    const [k, v] = part.split("=").map((x) => x && x.trim());
    if (["left", "top", "width", "height"].includes(k) && Number.isFinite(Number(v))) out[k] = Math.round(Number(v));
  }
  return out;
}

// ------------------------------------------------------------------ windows
const sameOrigin = (url) => { try { return new URL(url).origin === origin; } catch { return false; } };
const openOutside = (url) => { if (/^https?:\/\//i.test(url)) shell.openExternal(url); };
const fromConsole = (e) => hub && !hub.isDestroyed() && e.sender === hub.webContents && sameOrigin(e.senderFrame?.url || "");

function send(id, type, extra = {}) {
  if (hub && !hub.isDestroyed()) hub.webContents.send("jarvis:win-event", { id, type, ...extra });
}

function nativeOptions(f) {
  const pos = f.left !== undefined && f.top !== undefined && onScreen({ x: f.left, y: f.top, width: f.width })
    ? { x: f.left, y: f.top } : nextPosition(f.width, f.height);
  return {
    ...pos, width: f.width || 820, height: f.height || 560, minWidth: 360, minHeight: 200, show: !discreet, title: "JARVIS",
    icon: ICON, backgroundColor: overlay.color, autoHideMenuBar: true,
    ...(IS_MAC ? { titleBarStyle: "hiddenInset" }
      : { titleBarStyle: "hidden", titleBarOverlay: { color: overlay.color, symbolColor: overlay.symbolColor, height: HEAD_H } }),
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false },
  };
}

/** A framed window ("Intégré au bureau"): a panel (Historique, Notes…) or a modal (configuration, Ctrl+K, a
 * question) of the console, with a title strip drawn by the page and the native buttons. A modal's window
 * opens hidden and shows once the page has sized it (frame-show). */
function frameOptions(f, hidden) {
  const a = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  const width = Math.min(f.width || 640, a.width), height = Math.min(f.height || 640, a.height);
  const pos = f.left !== undefined && f.top !== undefined && onScreen({ x: f.left, y: f.top, width })
    ? { x: f.left, y: f.top } : { x: Math.round(a.x + (a.width - width) / 2), y: Math.round(a.y + (a.height - height) / 2) };
  return {
    ...pos, width, height, minWidth: 320, minHeight: 160, show: !hidden && !discreet, title: "JARVIS", icon: ICON,
    backgroundColor: overlay.color, autoHideMenuBar: true,
    ...(IS_MAC ? { titleBarStyle: "hiddenInset" }
      : { titleBarStyle: "hidden", titleBarOverlay: { color: overlay.color, symbolColor: overlay.symbolColor, height: HEAD_H } }),
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false },
  };
}

/** Bottom center of the screen where the mouse is: where the bar shows. */
function barPlace(height) {
  const a = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  return { x: Math.round(a.x + (a.width - BAR_W) / 2), y: Math.round(a.y + a.height - height - 12) };
}

function barOptions() {
  return { ...barPlace(barH), width: BAR_W, height: barH, frame: false, transparent: true, backgroundColor: "#00000000",
    hasShadow: false, resizable: false, maximizable: false, minimizable: false, fullscreenable: false, skipTaskbar: true,
    alwaysOnTop: true, show: false, title: "JARVIS — nouvelle demande", icon: ICON,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false } };
}

function adoptBar(win) {
  bar = win;
  win.webContents.setWindowOpenHandler(({ url }) => { openOutside(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { e.preventDefault(); openOutside(url); });
  win.on("blur", () => { if (!barPinned && !barHold && !win.isDestroyed() && win.isVisible()) win.hide(); });
  win.on("close", (e) => { if (!quitting) { e.preventDefault(); win.hide(); } });   // (Alt+F4: put away)
  win.on("closed", () => { if (bar === win) bar = null; });
}

/** Ctrl+Alt+J, "Nouvelle demande": the bar where the mouse is, ready to type (select: false keeps what
 * is in it); menu: with the JARVIS menu open ("Ouvrir JARVIS"). */
function showBar({ select = true, menu = false } = {}) {
  if (!bar || bar.isDestroyed() || mode !== "integre") { revealHub(true); return; }
  wake();
  if (!bar.isVisible()) bar.setBounds({ ...barPlace(barH), width: BAR_W, height: barH });
  bar.show();
  bar.focus();
  const cmd = menu ? "menu" : select ? "nouvelle-demande" : "";
  if (cmd && hub && !hub.isDestroyed()) hub.webContents.send("jarvis:command", { cmd });
}

function toggleBar() {
  if (bar && !bar.isDestroyed() && mode === "integre" && bar.isVisible() && bar.isFocused()) bar.hide();
  else showBar();
}

/** window.open from the console's page: its native windows; a web address goes to the system browser. */
function openHandler({ url, frameName, features }) {
  if (url === "about:blank" && frameName === `${WIN_PREFIX}barre` && mode === "integre") {
    return { action: "allow", overrideBrowserWindowOptions: barOptions() };
  }
  if (url === "about:blank" && String(frameName).startsWith(`${WIN_PREFIX}cadre-`) && mode === "integre") {
    return { action: "allow", overrideBrowserWindowOptions: frameOptions(parseFeatures(features), /(^|,)\s*hidden=1\b/.test(String(features || ""))) };
  }
  if (url === "about:blank" && String(frameName).startsWith(WIN_PREFIX) && mode === "integre") {
    return { action: "allow", overrideBrowserWindowOptions: nativeOptions(parseFeatures(features)) };
  }
  openOutside(url);
  return { action: "deny" };
}

/** A native window just created by the page: it reports focus, size, place and close requests. */
function adopt(win, details) {
  const id = String(details.frameName || "").slice(WIN_PREFIX.length);
  if (!id) return;
  if (id === "barre") { adoptBar(win); return; }
  children.set(id, win);
  win.webContents.setWindowOpenHandler(({ url }) => { openOutside(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { e.preventDefault(); openOutside(url); });
  // closed by its own button: the page decides (a running discussion asks first), then closes it
  win.on("close", (e) => { if (!quitting && !closing.has(id)) { e.preventDefault(); send(id, "close-request"); } });
  win.on("closed", () => {
    // only a window that went without the page asking (a crash) is reported: the page forgets it
    const asked = closing.delete(id);
    if (children.get(id) === win) children.delete(id);
    if (!asked && !quitting) send(id, "closed");
  });
  win.on("focus", () => send(id, "focus"));
  win.on("minimize", () => send(id, "minimize"));
  win.on("restore", () => send(id, "restore"));
  let t = null;
  const bounds = () => {
    clearTimeout(t);
    t = setTimeout(() => {
      if (!win.isDestroyed() && !win.isMinimized() && !win.isMaximized()) send(id, "bounds", { bounds: win.getBounds() });
    }, 400);
  };
  win.on("move", bounds);
  win.on("resize", bounds);
}

function closeChildren() {
  for (const [id, w] of children) { closing.add(id); if (!w.isDestroyed()) w.destroy(); }
  children.clear();
  if (bar && !bar.isDestroyed()) bar.destroy();
  bar = null;
}

function arrange({ mode: how, ids } = {}) {
  const list = (ids || []).map((id) => children.get(String(id))).filter((w) => w && !w.isDestroyed() && !w.isMinimized());
  if (!list.length) return false;
  const ref = hub && !hub.isDestroyed() && hub.isVisible() ? hub.getBounds() : list[0].getBounds();
  const a = screen.getDisplayMatching(ref).workArea;
  if (how === "mosaique") {
    const n = list.length, cols = Math.max(1, Math.ceil(Math.sqrt(n * (a.width / a.height) / 1.4))), c = Math.min(cols, n);
    const rows = Math.ceil(n / c), gap = 8;
    const cw = Math.floor((a.width - gap * (c + 1)) / c), ch = Math.floor((a.height - gap * (rows + 1)) / rows);
    list.forEach((w, i) => { w.unmaximize(); w.setBounds({ x: a.x + gap + (i % c) * (cw + gap), y: a.y + gap + Math.floor(i / c) * (ch + gap), width: Math.max(360, cw), height: Math.max(200, ch) }); });
  } else {
    const ww = Math.min(820, a.width - 60), hh = Math.min(560, a.height - 60);
    list.forEach((w, i) => { w.unmaximize(); w.setBounds({ x: a.x + 20 + ((i * 34) % Math.max(1, a.width - ww - 40)), y: a.y + 14 + ((i * 30) % Math.max(1, a.height - hh - 30)), width: ww, height: hh }); w.focus(); });
  }
  return true;
}

function splash(text) {
  const html = `<!doctype html><meta charset="utf-8"><title>JARVIS</title><body style="margin:0;height:100vh;display:grid;place-items:center;
    background:#0b0f14;color:#9aa7b4;font:14px system-ui,sans-serif"><div style="text-align:center"><div style="font:600 22px system-ui;
    letter-spacing:.5em;color:#e6edf3;padding-left:.5em">JARVIS</div><p>${text}</p></div></body>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function createHub() {
  const st = (loadState().hub || {})[mode] || {};
  const big = mode === "fenetre";
  hub = new BrowserWindow({
    width: st.width || (big ? 1440 : 1280), height: st.height || (big ? 920 : 760),
    ...(onScreen(st) ? { x: st.x, y: st.y } : {}), minWidth: 720, minHeight: 480, show: false,
    title: "JARVIS", icon: ICON, backgroundColor: "#0b0f14", autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true,
      nodeIntegration: false, backgroundThrottling: false, spellcheck: true },
  });
  if (st.maximized) hub.maximize();
  hub.webContents.setWindowOpenHandler(openHandler);
  hub.webContents.on("did-create-window", adopt);
  hub.webContents.on("will-navigate", (e, url) => { if (!sameOrigin(url)) { e.preventDefault(); openOutside(url); } });
  hub.webContents.on("did-start-navigation", (details, url, inPlace, isMain) => {
    // the page reloads (update, restart): it opens its windows again
    const main = details?.isMainFrame ?? isMain, same = details?.isSameDocument ?? inPlace;
    if (main && !same) closeChildren();
  });
  const remember = () => {
    if (!hub || hub.isDestroyed() || hub.isMinimized()) return;
    const all = loadState().hub || {};
    saveState({ hub: { ...all, [mode]: { ...(all[mode] || {}), ...hub.getNormalBounds(), maximized: hub.isMaximized() } } });
  };
  hub.on("close", (e) => {
    remember();
    if (!quitting && tray) { e.preventDefault(); hub.hide(); }   // the app stays in the notification area
  });
  hub.on("closed", () => { hub = null; if (!quitting) quit(); });
  hub.once("ready-to-show", () => { if (!discreet && !hubAway) hub.show(); });
  hub.loadURL(splash("Démarrage de la console…"));
}

/** The user opens JARVIS: what waited hidden since the session start shows up. */
function wake() {
  if (!discreet) return;
  discreet = false;
  for (const w of [...children.values(), ...sites.map((s) => s.win)]) if (!w.isDestroyed() && !w.isVisible()) w.showInactive();
  if (bar && !bar.isDestroyed() && !bar.isVisible()) bar.showInactive();
}

/** "Ouvrir JARVIS" (notification area, launcher): with "Intégré au bureau", the bar and its menu (there
 * is no JARVIS window to show); with "Une fenêtre JARVIS", the window. */
function showHub(newRequest = false) {
  if (mode === "integre" && bar && !bar.isDestroyed()) showBar({ menu: !newRequest });
  else revealHub(newRequest);
}

/** The JARVIS window: the console's page ("Une fenêtre JARVIS"; with "Intégré au bureau", only while it
 * starts, or when the bar could not open). */
function revealHub(newRequest = false) {
  if (!hub || hub.isDestroyed()) return;
  wake();
  if (hub.isMinimized()) hub.restore();
  hub.show();
  hub.focus();
  if (newRequest) hub.webContents.send("jarvis:command", { cmd: "nouvelle-demande" });
}

function quit() { quitting = true; app.quit(); }

// ------------------------------------------------------------------ connected sites
// A site the console shows (a link, an address Claude shows from an approved domain, one the user agreed
// to open) gets a window of its own: a navigation bar (site.html) above a view of the site, in a session
// of its own (persist:site:<domain>): cookies apart from the console and from the other sites, so the
// user stays signed in to Odoo or SharePoint. The site's view has no preload and no Node; it may go to
// any https address (sign-in pages are often elsewhere); a link opened in a new tab to another site, or
// any plain http address, goes to the system browser.
const SITE_BAR_H = 44;
const sites = [];                        // {win, view, partition, label}
let trusted = [];                        // the approved domains (Configuration → Sécurité), refreshed on each opening
let siteTheme = "sombre";

async function refreshSiteSettings() {
  try {
    const { config } = await call(port, "/api/config", { token });
    trusted = (config?.security?.trusted_domains || []).map((d) => String(d).toLowerCase());
    const t = config?.general?.theme;
    siteTheme = t === "clair" || (t === "systeme" && !nativeTheme.shouldUseDarkColors) ? "clair" : "sombre";
  } catch { /* the last ones known */ }
}

/** The session of an address: its approved domain (subdomains included), else its host. */
function partitionOf(u) {
  const host = u.hostname.toLowerCase();
  const label = trusted.find((d) => host === d || host.endsWith(`.${d}`)) || host;
  return { partition: `persist:site:${label}`, label };
}

function siteSession(partition) {
  const ses = session.fromPartition(partition);
  if (!ses.jarvisReady) {
    ses.jarvisReady = true;
    const ok = ["clipboard-sanitized-write", "fullscreen"];      // never camera, microphone, location, notifications…
    ses.setPermissionRequestHandler((_wc, perm, cb) => cb(ok.includes(perm)));
    ses.setPermissionCheckHandler((_wc, perm) => ok.includes(perm));
    const known = loadState().sites || [];
    if (!known.includes(partition)) saveState({ sites: [...known, partition] });
  }
  return ses;
}

const barColors = () => (siteTheme === "clair" ? { bg: "#f6f8fa", text: "#3f5163" } : { bg: "#1c2430", text: "#aab6c3" });

async function openSite(url, { fresh = false } = {}) {
  let u;
  try { u = new URL(url); } catch { return false; }
  if (u.protocol !== "https:") { openOutside(url); return false; }
  await refreshSiteSettings();
  const { partition, label } = partitionOf(u);
  const live = !fresh && [...sites].reverse().find((s) => s.partition === partition && !s.win.isDestroyed());
  if (live) {
    live.view.webContents.loadURL(u.href);
    if (live.win.isMinimized()) live.win.restore();
    if (!discreet) { live.win.show(); live.win.focus(); }
    return true;
  }
  createSiteWindow(partition, label, u.href);
  return true;
}

function createSiteWindow(partition, label, url) {
  const c = barColors();
  const win = new BrowserWindow({
    ...nextPosition(1180, 820), width: 1180, height: 820, minWidth: 520, minHeight: 320, show: !discreet, title: label,
    icon: ICON, backgroundColor: c.bg, autoHideMenuBar: true,
    ...(IS_MAC ? { titleBarStyle: "hiddenInset" } : { titleBarStyle: "hidden", titleBarOverlay: { color: c.bg, symbolColor: c.text, height: SITE_BAR_H } }),
    webPreferences: { preload: path.join(__dirname, "site-preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  siteSession(partition);
  const view = new WebContentsView({ webPreferences: { partition, sandbox: true, contextIsolation: true, nodeIntegration: false, spellcheck: true } });
  win.contentView.addChildView(view);
  const entry = { win, view, partition, label };
  sites.push(entry);
  const layout = () => {
    if (win.isDestroyed()) return;
    const [w, h] = win.getContentSize();
    view.setBounds({ x: 0, y: SITE_BAR_H, width: w, height: Math.max(0, h - SITE_BAR_H) });
  };
  win.on("resize", layout);
  layout();
  const wc = view.webContents;
  const state = () => {
    if (win.isDestroyed() || wc.isDestroyed()) return;
    const nh = wc.navigationHistory;
    win.webContents.send("site:state", { url: wc.getURL(), title: wc.getTitle(), canBack: nh.canGoBack(), canForward: nh.canGoForward(),
      loading: wc.isLoading(), session: label });
    win.setTitle(`${wc.getTitle() || label} — ${label}`);
  };
  for (const ev of ["did-navigate", "did-navigate-in-page", "page-title-updated", "did-start-loading", "did-stop-loading", "did-fail-load"]) wc.on(ev, state);
  win.webContents.on("did-finish-load", state);
  wc.on("will-navigate", (e, to) => { if (!/^https:/i.test(to)) { e.preventDefault(); openOutside(to); } });
  wc.setWindowOpenHandler(({ url: to, disposition }) => {
    if (!/^https:/i.test(to)) { openOutside(to); return { action: "deny" }; }
    // a pop-up of the site (sign-in, print…) keeps its session
    if (disposition === "new-window") {
      return { action: "allow", overrideBrowserWindowOptions: { width: 640, height: 720, autoHideMenuBar: true, icon: ICON,
        webPreferences: { partition, sandbox: true, contextIsolation: true, nodeIntegration: false } } };
    }
    // a link opened in a new tab: another window of this site, or the browser for another site
    let target;
    try { target = partitionOf(new URL(to)).partition; } catch { return { action: "deny" }; }
    if (target === partition) openSite(to, { fresh: true }); else openOutside(to);
    return { action: "deny" };
  });
  wc.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    const nh = wc.navigationHistory;
    if (input.alt && input.key === "ArrowLeft" && nh.canGoBack()) { nh.goBack(); e.preventDefault(); }
    else if (input.alt && input.key === "ArrowRight" && nh.canGoForward()) { nh.goForward(); e.preventDefault(); }
    else if (input.key === "F5") { wc.reload(); e.preventDefault(); }
  });
  win.on("closed", () => {
    const i = sites.indexOf(entry);
    if (i >= 0) sites.splice(i, 1);
    if (!wc.isDestroyed()) wc.close();
  });
  win.loadFile(path.join(__dirname, "site.html"), { query: { theme: siteTheme, os: process.platform } });
  wc.loadURL(url);
}

/** Signed out of every site: their windows close, their sessions are emptied. */
async function logoutSites() {
  for (const s of [...sites]) if (!s.win.isDestroyed()) s.win.close();
  for (const p of loadState().sites || []) {
    const ses = session.fromPartition(p);
    await ses.clearStorageData();
    await ses.clearCache();
  }
  saveState({ sites: [] });
  return true;
}

ipcMain.on("site:nav", (e, { op, url } = {}) => {
  const s = sites.find((x) => !x.win.isDestroyed() && x.win.webContents === e.sender);
  if (!s) return;
  const wc = s.view.webContents, nh = wc.navigationHistory;
  if (op === "back" && nh.canGoBack()) nh.goBack();
  else if (op === "forward" && nh.canGoForward()) nh.goForward();
  else if (op === "reload") wc.reload();
  else if (op === "stop") wc.stop();
  else if (op === "external") openOutside(wc.getURL());
  else if (op === "go" && typeof url === "string" && url.trim()) {
    const to = /^[a-z][\w+.-]*:/i.test(url.trim()) ? url.trim() : `https://${url.trim()}`;
    if (/^https:\/\//i.test(to)) wc.loadURL(to); else openOutside(to);
  }
});

// ------------------------------------------------------------------ notifications
// An approval waiting (a tool call to allow or refuse) gets a notification of the OS with Approuver and
// Refuser: on Windows through a notification XML whose buttons open jarvis://valider?… (this app is the
// handler of jarvis:), on Mac through the notification's actions. Each notification carries a secret of
// its own: a link forged elsewhere (a web page, another program) decides nothing. A click elsewhere on
// it brings the discussion forward. Not shown while the user looks at that discussion.
const notes = new Map();                 // approval id (or task:kind) -> {n, nonce, tid, aid}
if (process.env.JARVIS_TEST) global.jarvisTest = { notes, file: __filename, root: ROOT };   // (the tests read the secrets)

const clipText = (s, n) => { const t = String(s || ""); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
const xml = (s) => String(s).replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c]));

function lookingAt(tid, pageSays) {
  const f = BrowserWindow.getFocusedWindow();
  if (!f || f.isMinimized() || !f.isVisible()) return false;
  return mode === "integre" ? children.get(tid) === f : f === hub && !!pageSays;
}

function toastXml(p, title, body, nonce) {
  const link = (action, extra = "") => xml(`jarvis://${action}?t=${encodeURIComponent(p.tid)}${p.aid ? `&a=${encodeURIComponent(p.aid)}` : ""}&k=${nonce}${extra}`);
  return `<toast launch="${link("ouvrir")}" activationType="protocol"><visual><binding template="ToastGeneric">`
    + `<text>${xml(title)}</text><text>${xml(body)}</text></binding></visual>`
    + (p.buttons ? `<actions><action content="Approuver" activationType="protocol" arguments="${link("valider", "&amp;d=allow")}"/>`
      + `<action content="Refuser" activationType="protocol" arguments="${link("valider", "&amp;d=deny")}"/></actions>` : "")
    + "</toast>";
}

function notify(p) {
  if (!p?.tid || lookingAt(p.tid, p.looking)) return false;
  const key = p.aid || `${p.tid}:${p.kind}`;
  notes.get(key)?.n.close();
  const nonce = crypto.randomBytes(16).toString("hex");
  const title = clipText(p.title, 120), body = clipText(p.body, 300);
  let n = null;
  if (Notification.isSupported()) {
    n = IS_WIN ? new Notification({ title, body, icon: ICON, toastXml: toastXml(p, title, body, nonce) })
      : new Notification({ title, body, icon: ICON,
        ...(IS_MAC && p.buttons ? { actions: [{ type: "button", text: "Approuver" }, { type: "button", text: "Refuser" }] } : {}) });
    n.on("action", (_e, i) => decideFromOS(p.tid, p.aid, i === 0 ? "allow" : "deny", nonce));
    n.on("click", () => openFromOS(p.tid));
    n.show();
  }
  notes.set(key, { n, nonce, tid: p.tid, aid: p.aid || "" });
  while (notes.size > 50) notes.delete(notes.keys().next().value);
  return !!n;
}

/** The approval was decided (in its window, or from here): its notification goes. */
function notifyDone(aid) {
  const note = notes.get(aid);
  if (!note) return;
  notes.delete(aid);
  try { note.n?.close(); } catch { /* already gone */ }
}

async function decideFromOS(tid, aid, decision, nonce) {
  const note = notes.get(aid);
  if (!note || note.nonce !== nonce || note.tid !== tid || !["allow", "deny"].includes(decision)) return;
  notifyDone(aid);
  try {
    await call(port, `/api/tasks/${encodeURIComponent(tid)}/approvals/${encodeURIComponent(aid)}`, { method: "POST", token, body: { decision } });
  } catch { openFromOS(tid); }   // (already decided, or the console is gone: the window tells)
}

function openFromOS(tid) {
  if (tid === "rappel") { showBar({ select: false }); return; }   // a reminder of the notes: above the bar (or in the JARVIS window)
  wake();
  if (mode === "fenetre") showHub();
  if (hub && !hub.isDestroyed()) hub.webContents.send("jarvis:command", { cmd: "ouvrir", tid });
}

/** jarvis://ouvrir|valider?t=…&a=…&k=…&d=… — from a notification (Windows starts this app with it). */
function handleLink(raw) {
  let u;
  try { u = new URL(raw); } catch { return; }
  if (u.protocol !== "jarvis:") return;
  const action = u.hostname || u.pathname.replace(/^\/+/, "");
  const q = (k) => u.searchParams.get(k) || "";
  const note = [...notes.values()].find((x) => x.nonce === q("k") && x.tid === q("t"));
  if (!note) { showHub(); return; }       // an old notification: nothing to decide
  if (action === "valider" && note.aid && note.aid === q("a")) decideFromOS(note.tid, note.aid, q("d"), q("k"));
  else openFromOS(note.tid);
}

// ------------------------------------------------------------------ the OS: launcher, how to start this app
/** data/app.json: how the console opens this app (start.bat, launcher, session start: console/winsys.py). */
function registerApp(data) {
  try {
    fs.writeFileSync(path.join(data, "app.json"), JSON.stringify({ exe: process.execPath, args: APP_ARGS, aumid: AUMID,
      version: app.getVersion(), electron: process.versions.electron, packaged: app.isPackaged, at: new Date().toISOString() }, null, 2));
  } catch { /* the console keeps opening the browser */ }
  // the installed application finds the JARVIS folder there (loader.js), even after start-app.bat only
  try { fs.writeFileSync(path.join(app.getPath("userData"), "dossier.json"), JSON.stringify({ root: ROOT }, null, 2)); } catch { /* asked */ }
}

const quoteArg = (a) => (/[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
const shortcutDirs = () => ({
  programs: path.join(app.getPath("appData"), "Microsoft", "Windows", "Start Menu", "Programs"),
  desktop: app.getPath("desktop"),
});

/** A Windows shortcut to this app, with its identity: pinned, it groups the app's windows; in the Start
 * menu, it lets Windows show the app's notifications. False when it was already right. */
function writeShortcut(file) {
  const want = { target: process.execPath, args: APP_ARGS.map(quoteArg).join(" "), cwd: path.dirname(process.execPath),
    icon: path.join(ROOT, "static", "img", "jarvis.ico"), iconIndex: 0, appUserModelId: AUMID,
    description: "JARVIS · Console d'agents Claude" };
  try {
    const have = shell.readShortcutLink(file);
    if (have.target === want.target && have.args === want.args && have.appUserModelId === AUMID && have.icon === want.icon) return false;
  } catch { /* none yet */ }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  return shell.writeShortcutLink(file, "create", want);
}

/** The Start menu entry "JARVIS" (and the Desktop one if the user made it) follows this app. Not for an
 * installed app: its installer made them. The start with the session (Configuration → Général, with the
 * desktop app) follows it in both cases: after installing the application, it starts the installed one. */
function ensureShortcuts() {
  if (!IS_WIN) return;
  const { programs, desktop } = shortcutDirs();
  try {
    if (!app.isPackaged) {
      writeShortcut(path.join(programs, "JARVIS.lnk"));
      if (fs.existsSync(path.join(desktop, "JARVIS.lnk"))) writeShortcut(path.join(desktop, "JARVIS.lnk"));
    }
    // console/winsys.py, set_startup: the app's entry has --demarrage; the server's own (pythonw) stays
    const startup = path.join(programs, "Startup", "JARVIS Console.lnk");
    const have = fs.existsSync(startup) ? shell.readShortcutLink(startup) : null;
    const args = [...APP_ARGS, "--demarrage"].map(quoteArg).join(" ");
    if (have && /--demarrage/.test(have.args || "") && (have.target !== process.execPath || have.args !== args)) {
      shell.writeShortcutLink(startup, "replace", { target: process.execPath, args, cwd: path.dirname(process.execPath),
        icon: path.join(ROOT, "static", "img", "jarvis.ico"), iconIndex: 0, appUserModelId: AUMID,
        description: "JARVIS (application de bureau)" });
    }
  } catch { /* not essential */ }
}

/** Configuration → Général → Créer le lanceur, in the app: "JARVIS" in the Start menu and on the Desktop;
 * the browser's "JARVIS Console" shortcuts go, so that one entry remains. */
function createLauncher() {
  if (!IS_WIN) throw new Error("Lanceur de l'application : sous Windows. Sur Mac, garde l'application dans le Dock.");
  const { programs, desktop } = shortcutDirs();
  const paths = [];
  for (const dir of [programs, desktop]) {
    const file = path.join(dir, "JARVIS.lnk");
    writeShortcut(file);
    fs.rmSync(path.join(dir, "JARVIS Console.lnk"), { force: true });
    paths.push(file);
  }
  return paths;
}

// ------------------------------------------------------------------ notification area
/** A small round badge drawn pixel by pixel (Windows: on the taskbar button when something waits). */
function badge(rgb) {
  const n = 16, buf = Buffer.alloc(n * n * 4);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const d = Math.hypot(x - 7.5, y - 7.5), a = Math.max(0, Math.min(1, 7.5 - d)), i = (y * n + x) * 4;
    buf[i] = rgb[2]; buf[i + 1] = rgb[1]; buf[i + 2] = rgb[0]; buf[i + 3] = Math.round(a * 255); // BGRA
  }
  return nativeImage.createFromBitmap(buf, { width: n, height: n });
}

function makeTray() {
  try {
    tray = new Tray(nativeImage.createFromPath(ICON).resize({ width: 16, height: 16 }));
    tray.on("click", () => showHub());
    updateTray({});
  } catch { tray = null; }
}

function updateTray(s = {}) {
  const awaiting = Number(s.awaiting) || 0, running = Number(s.running) || 0, queued = Number(s.queued) || 0;
  // the badge of the taskbar: on every window of JARVIS (with "Intégré au bureau", the JARVIS window is often hidden)
  if (IS_WIN) {
    const icon = awaiting ? badge([240, 180, 92]) : null, label = awaiting ? `${awaiting} à valider` : "";
    for (const w of [hub, ...children.values()]) if (w && !w.isDestroyed()) w.setOverlayIcon(icon, label);
  }
  if (!tray) return;
  tray.setToolTip(`JARVIS · ${running} en cours · ${awaiting} à valider${queued ? ` · ${queued} en file` : ""}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: awaiting ? `${awaiting} à valider` : "Rien à valider", enabled: false },
    { label: `${running} en cours${queued ? ` · ${queued} en file` : ""}`, enabled: false },
    { type: "separator" },
    { label: "Ouvrir JARVIS", click: () => showHub() },
    { label: "Nouvelle demande", accelerator: SHORTCUT, click: () => showBar() },
    { type: "separator" },
    { label: "Se déconnecter des sites", enabled: (loadState().sites || []).length > 0, click: () => logoutSites() },
    { label: "Quitter l'application (la console continue de tourner)", click: quit },
  ]));
}

// ------------------------------------------------------------------ the page's requests
ipcMain.on("jarvis:mode", (e) => { e.returnValue = fromConsole(e) ? mode : null; });

ipcMain.handle("jarvis:win", (e, { op, id, data } = {}) => {
  if (!fromConsole(e)) return false;
  if (op === "overlay") {
    // colors of the native buttons: the theme for new windows, or one window (its account's tint)
    const height = Math.max(28, Math.min(64, Number(data?.height) || HEAD_H));
    const c = { color: String(data?.color || overlay.color), symbolColor: String(data?.symbolColor || overlay.symbolColor), height };
    // (the framed windows keep their own colors)
    const targets = id ? [children.get(String(id))] : [...children].filter(([k]) => !k.startsWith("cadre-")).map(([, w]) => w);
    if (!id) overlay = { color: c.color, symbolColor: c.symbolColor };
    for (const w of targets) if (w && !w.isDestroyed() && !IS_MAC) { try { w.setTitleBarOverlay(c); w.setBackgroundColor(c.color); } catch { /* old platform */ } }
    return true;
  }
  if (op === "arrange") return arrange(data);
  if (op === "theme") {   // the JARVIS theme for what the OS draws (title bars, menus, dialogs)
    nativeTheme.themeSource = data === "clair" ? "light" : data === "sombre" ? "dark" : "system";
    return true;
  }
  if (op === "frame-show") {   // a modal's window, sized by the page: centered on the screen of the mouse
    const w = children.get(String(id));
    if (!w || w.isDestroyed() || !Number.isFinite(data?.width) || !Number.isFinite(data?.height)) return false;
    const a = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
    const width = Math.max(320, Math.min(Math.round(data.width) + 2, a.width - 40));
    const height = Math.max(160, Math.min(Math.round(data.height) + 4, Math.round(a.height * 0.9)));
    w.setContentBounds({ x: Math.round(a.x + (a.width - width) / 2), y: Math.round(a.y + (a.height - height) / 2.4), width, height });
    if (!discreet) { w.show(); w.focus(); }
    return true;
  }
  if (op === "hub") { showHub(); return true; }
  if (op === "hub-reveal") {   // a reminder ("Une fenêtre JARVIS"): the window comes forward without taking the keyboard
    if (hub && !hub.isDestroyed() && !discreet) { if (hub.isMinimized()) hub.restore(); hub.showInactive(); }
    return true;
  }
  if (op.startsWith("bar-")) {
    if (op === "bar-show") showBar({ select: data?.select !== false });
    if (!bar || bar.isDestroyed()) return false;
    if (op === "bar-ready") {
      // the bar is there: no JARVIS window any more (it showed the start of the console)
      if (hub && !hub.isDestroyed()) hub.hide();
      if (!discreet) { bar.setBounds({ ...barPlace(barH), width: BAR_W, height: barH }); bar.showInactive(); }
    }
    else if (op === "bar-reveal" && !discreet && !bar.isVisible()) {   // a reminder above it: without the keyboard
      bar.setBounds({ ...barPlace(barH), width: BAR_W, height: barH });
      bar.showInactive();
    }
    else if (op === "bar-hide") bar.hide();
    else if (op === "bar-sent" && !barPinned) bar.hide();
    else if (op === "bar-pin") barPinned = !!data;
    else if (op === "bar-hold") barHold = !!data;
    else if (op === "bar-fit" && Number.isFinite(data?.height)) {
      // the bar grows upward (a list, a menu, a message above it): its bottom stays where it is
      barH = Math.max(90, Math.min(720, Math.round(data.height)));
      const b = bar.getBounds();
      bar.setBounds({ x: b.x, y: b.y + b.height - barH, width: BAR_W, height: barH });
    }
    return true;
  }
  const w = children.get(String(id));
  if (!w || w.isDestroyed()) return false;
  switch (op) {
    case "focus": case "restore":
      if (w.isMinimized()) w.restore();
      w.show();
      w.focus();
      return true;
    case "minimize": w.minimize(); return true;
    case "max": if (w.isMaximized()) w.unmaximize(); else w.maximize(); return true;
    case "pin": w.setAlwaysOnTop(!!data, "floating"); return true;
    case "title": w.setTitle(String(data || "JARVIS").slice(0, 200)); return true;
    case "size": if (data?.width && data?.height) w.setSize(Math.round(data.width), Math.round(data.height)); return true;
    case "close": closing.add(String(id)); w.close(); return true;
    default: return false;
  }
});

ipcMain.on("jarvis:status", (e, s) => { if (fromConsole(e)) updateTray(s); });

ipcMain.on("jarvis:notify", (e, p) => {
  if (!fromConsole(e) || !p || typeof p !== "object") return;
  notify({ tid: String(p.tid || ""), aid: String(p.aid || ""), kind: String(p.kind || ""), title: String(p.title || ""),
    body: String(p.body || ""), buttons: !!p.buttons && !!p.aid, looking: !!p.looking });
});
ipcMain.on("jarvis:notify-done", (e, aid) => { if (fromConsole(e)) notifyDone(String(aid || "")); });

ipcMain.handle("jarvis:site", (e, url) => (fromConsole(e) ? openSite(String(url || "")) : false));
ipcMain.handle("jarvis:site-logout", (e) => (fromConsole(e) ? logoutSites() : false));

ipcMain.handle("jarvis:launcher", (e) => {
  if (!fromConsole(e)) return { error: "refusé" };
  try { return { paths: createLauncher() }; } catch (err) { return { error: String(err.message || err) }; }
});

ipcMain.handle("jarvis:switch-mode", (e, m) => {
  if (!fromConsole(e) || !MODES.includes(m) || m === mode) return false;
  quitting = true;
  app.relaunch();   // the interface reloads in the other mode; the server and its tasks go on
  app.exit(0);
  return true;
});

// ------------------------------------------------------------------ start
async function main() {
  if (IS_WIN) app.setAppUserModelId(AUMID);
  // the handler of jarvis: links (the buttons of a notification); in development, with the app's folder
  if (IS_MAC || app.isPackaged) app.setAsDefaultProtocolClient("jarvis");
  else app.setAsDefaultProtocolClient("jarvis", process.execPath, APP_ARGS.map((a) => path.resolve(a)));
  const conf = settings();
  const data = conf.data;
  port = conf.port;
  ensureShortcuts();
  origin = `http://127.0.0.1:${port}`;
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb, details) =>
    cb(sameOrigin(details?.requestingUrl || wc.getURL()) && ["notifications", "clipboard-sanitized-write"].includes(permission)));
  makeTray();
  createHub();
  if (!(await serverUp(port))) {
    startServer();
    if (!(await waitServer(port, 180000))) {
      dialog.showErrorBox("JARVIS", `La console ne répond pas sur le port ${port}. Lance start.bat (ou start.command) une fois pour `
        + "voir ce qui manque, ou consulte data/console.log.");
      quit();
      return;
    }
  }
  registerApp(data);
  try { token = fs.readFileSync(path.join(data, "token"), "utf8").trim(); } catch {
    dialog.showErrorBox("JARVIS", `Jeton d'accès introuvable : ${path.join(data, "token")}`);
    quit();
    return;
  }
  try {
    const { config } = await call(port, "/api/config", { token });
    if (MODES.includes(config?.ui?.bureau)) mode = config.ui.bureau;
  } catch { /* default mode */ }
  const { code } = await call(port, "/api/auth/code", { method: "POST", token, body: {} });
  if (hub && !hub.isDestroyed()) {
    hub.setTitle(mode === "integre" ? "JARVIS" : "JARVIS · Console d'agents");
    // "Intégré au bureau": the splash goes, the bar comes (or, if it cannot open, the JARVIS window)
    if (mode === "integre") {
      hubAway = true;
      hub.hide();
      setTimeout(() => { if ((!bar || bar.isDestroyed()) && !discreet) revealHub(); }, 20000);
    }
    hub.loadURL(`${origin}/#code=${code}`);
  }
  globalShortcut.register(SHORTCUT, () => (mode === "integre" ? toggleBar() : showHub(true)));
  const link = process.argv.find((a) => a.startsWith("jarvis://"));   // started by a notification
  if (link) handleLink(link);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // started again (launcher, start.bat with "Application de bureau"): JARVIS comes forward; a second
  // session start (--demarrage) changes nothing
  app.on("second-instance", (_e, argv) => {
    // the installed application starts while this one runs from start-app.bat: it takes over
    const other = path.resolve(String(argv[0] || ""));
    if (!app.isPackaged && other !== process.execPath && /^jarvis(\.exe)?$/i.test(path.basename(other)) && fs.existsSync(other)) {
      app.releaseSingleInstanceLock();
      spawn(other, argv.slice(1).filter((a) => a !== "--demarrage"), { detached: true, stdio: "ignore" }).unref();
      quit();
      return;
    }
    const link = argv.find((a) => a.startsWith("jarvis://"));
    if (link) handleLink(link);
    else if (!argv.includes("--demarrage")) showHub();
  });
  app.on("open-url", (e, url) => { e.preventDefault(); handleLink(url); });   // (Mac)
  app.on("before-quit", () => { quitting = true; });
  app.on("will-quit", () => globalShortcut.unregisterAll());
  app.on("window-all-closed", () => { if (!tray) quit(); });
  app.whenReady().then(main).catch((e) => { dialog.showErrorBox("JARVIS", String(e?.stack || e)); quit(); });
}
