// What the console's page may ask the desktop app (window.jarvis). Only the console's own page gets
// an answer: the main process checks where each request comes from.
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

const mode = ipcRenderer.sendSync("jarvis:mode");
if (mode) {
  contextBridge.exposeInMainWorld("jarvis", {
    mode,                       // "integre": native windows on the desktop; "fenetre": one window
    platform: process.platform,
    /** A native window: focus, restore, minimize, max, pin, title, size, close, overlay, arrange. */
    win: (op, id, data) => ipcRenderer.invoke("jarvis:win", { op, id, data }),
    /** fn({id, type: focus | minimize | restore | bounds | close-request | closed, bounds}) */
    onWin: (fn) => { ipcRenderer.on("jarvis:win-event", (_e, ev) => fn(ev)); },
    /** Counters for the notification area: {running, awaiting, queued}. */
    status: (s) => ipcRenderer.send("jarvis:status", s),
    switchMode: (m) => ipcRenderer.invoke("jarvis:switch-mode", m),
    /** fn({cmd}) — "nouvelle-demande": the global shortcut or the notification area's menu. */
    onCommand: (fn) => { ipcRenderer.on("jarvis:command", (_e, c) => fn(c)); },
  });
}
