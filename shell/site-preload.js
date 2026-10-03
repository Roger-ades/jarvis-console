// The navigation bar of a site window (site.html) talks to the main process; the site itself never
// gets a preload.
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("site", {
  /** back | forward | reload | stop | external | go (url) */
  nav: (op, url) => ipcRenderer.send("site:nav", { op, url }),
  /** fn({url, title, canBack, canForward, loading, session}) */
  onState: (fn) => { ipcRenderer.on("site:state", (_e, s) => fn(s)); },
});
