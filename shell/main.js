// JARVIS desktop app (prototype, see docs/electron.md). It starts the console's Python server, or
// reuses the one already running, then shows the interface in one of two ways (Configuration →
// Interface → Affichage):
//   "integre"  the JARVIS window (top bar, home, command bar, panels) plus one native window of the
//              OS per discussion, preview or display, mixed with the other applications;
//   "fenetre"  the whole console in one window, as in the browser.
// Native windows are children of the JARVIS window's page (window.open): one JavaScript context, one
// live stream, one renderer process. The page asks for every window operation through the preload
// (window.jarvis) and the main process checks that the request comes from the console's own page.
"use strict";
const { app, BrowserWindow, Menu, Tray, dialog, globalShortcut, ipcMain, nativeImage, screen, session, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const IS_WIN = process.platform === "win32";
const IS_MAC = process.platform === "darwin";
const HEAD_H = 38;                       // a window's header (.win-head): the native buttons sit in it
const WIN_PREFIX = "jarvis-win:";        // window.open name of a native window: jarvis-win:<id>
const SHORTCUT = "CommandOrControl+Alt+J";
const MODES = ["integre", "fenetre"];
const ICON = path.join(ROOT, "static", "img", IS_WIN ? "jarvis.ico" : "icon-512.png");

let hub = null;                          // the JARVIS window (the console's page)
let tray = null;
let mode = "integre";
let origin = "";
let quitting = false;
const children = new Map();              // id -> native BrowserWindow
const closing = new Set();               // ids the page closes itself (no close request back)
let overlay = { color: "#161b22", symbolColor: "#c9d1d9" };
let cascade = 0;

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
    ...pos, width: f.width || 820, height: f.height || 560, minWidth: 360, minHeight: 200, show: true, title: "JARVIS",
    icon: ICON, backgroundColor: overlay.color, autoHideMenuBar: true,
    ...(IS_MAC ? { titleBarStyle: "hiddenInset" }
      : { titleBarStyle: "hidden", titleBarOverlay: { color: overlay.color, symbolColor: overlay.symbolColor, height: HEAD_H } }),
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false },
  };
}

/** window.open from the console's page: its native windows; a web address goes to the system browser. */
function openHandler({ url, frameName, features }) {
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
}

function arrange({ mode: how, ids } = {}) {
  const list = (ids || []).map((id) => children.get(String(id))).filter((w) => w && !w.isDestroyed() && !w.isMinimized());
  if (!list.length) return false;
  const ref = hub && !hub.isDestroyed() ? hub.getBounds() : list[0].getBounds();
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
    saveState({ hub: { ...all, [mode]: { ...hub.getNormalBounds(), maximized: hub.isMaximized() } } });
  };
  hub.on("close", (e) => {
    remember();
    if (!quitting && tray) { e.preventDefault(); hub.hide(); }   // the app stays in the notification area
  });
  hub.on("closed", () => { hub = null; if (!quitting) quit(); });
  hub.once("ready-to-show", () => hub.show());
  hub.loadURL(splash("Démarrage de la console…"));
}

function showHub(newRequest = false) {
  if (!hub || hub.isDestroyed()) return;
  if (hub.isMinimized()) hub.restore();
  hub.show();
  hub.focus();
  if (newRequest) hub.webContents.send("jarvis:command", { cmd: "nouvelle-demande" });
}

function quit() { quitting = true; app.quit(); }

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
  if (hub && !hub.isDestroyed() && IS_WIN) hub.setOverlayIcon(awaiting ? badge([240, 180, 92]) : null, awaiting ? `${awaiting} à valider` : "");
  if (!tray) return;
  tray.setToolTip(`JARVIS · ${running} en cours · ${awaiting} à valider${queued ? ` · ${queued} en file` : ""}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: awaiting ? `${awaiting} à valider` : "Rien à valider", enabled: false },
    { label: `${running} en cours${queued ? ` · ${queued} en file` : ""}`, enabled: false },
    { type: "separator" },
    { label: "Ouvrir JARVIS", click: () => showHub() },
    { label: "Nouvelle demande", accelerator: SHORTCUT, click: () => showHub(true) },
    { type: "separator" },
    { label: "Quitter l'application (la console continue de tourner)", click: quit },
  ]));
}

// ------------------------------------------------------------------ the page's requests
ipcMain.on("jarvis:mode", (e) => { e.returnValue = fromConsole(e) ? mode : null; });

ipcMain.handle("jarvis:win", (e, { op, id, data } = {}) => {
  if (!fromConsole(e)) return false;
  if (op === "overlay") {
    // colors of the native buttons: the theme for new windows, or one window (its account's tint)
    const c = { color: String(data?.color || overlay.color), symbolColor: String(data?.symbolColor || overlay.symbolColor), height: HEAD_H };
    const targets = id ? [children.get(String(id))] : [...children.values()];
    if (!id) overlay = { color: c.color, symbolColor: c.symbolColor };
    for (const w of targets) if (w && !w.isDestroyed() && !IS_MAC) { try { w.setTitleBarOverlay(c); w.setBackgroundColor(c.color); } catch { /* old platform */ } }
    return true;
  }
  if (op === "arrange") return arrange(data);
  if (op === "hub") { showHub(); return true; }
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

ipcMain.handle("jarvis:switch-mode", (e, m) => {
  if (!fromConsole(e) || !MODES.includes(m) || m === mode) return false;
  quitting = true;
  app.relaunch();   // the interface reloads in the other mode; the server and its tasks go on
  app.exit(0);
  return true;
});

// ------------------------------------------------------------------ start
async function main() {
  if (IS_WIN) app.setAppUserModelId("local.jarvis.console");
  const { data, port } = settings();
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
  let token;
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
    hub.loadURL(`${origin}/#code=${code}`);
  }
  globalShortcut.register(SHORTCUT, () => showHub(true));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => showHub());
  app.on("before-quit", () => { quitting = true; });
  app.on("will-quit", () => globalShortcut.unregisterAll());
  app.on("window-all-closed", () => { if (!tray) quit(); });
  app.whenReady().then(main).catch((e) => { dialog.showErrorBox("JARVIS", String(e?.stack || e)); quit(); });
}
