// Server version and restart: the page is read from disk on every load, the server's code
// only when it starts. After an update, a banner offers to restart it in place.
import { api } from "./api.js";
import { confirmDialog, h, toast } from "./util.js";

let bar = null;

function showBar(restartable) {
  if (bar) return;
  bar = h("div", { class: "update-bar", role: "status" },
    h("span", { class: "i", svg: "retry" }),
    h("span", {}, restartable ? "Mise à jour installée : redémarre la console pour l'activer."
      : "Mise à jour installée : arrête la console (Configuration → Général) puis relance start.bat."),
    restartable ? h("button", { type: "button", class: "btn small primary", on: { click: restartConsole } }, "Redémarrer") : null);
  document.body.append(bar);
}

export async function checkVersion() {
  try {
    const v = await api("/api/system/version");
    if (v.stale) showBar(v.restartable);
  } catch (e) {
    if (e.status === 404) showBar(false); // a server older than this page: it has no such route
  }
}

export async function restartConsole() {
  let st = {};
  try { st = await api("/api/state"); } catch { /* ask anyway */ }
  const running = (st.running || 0) + (st.awaiting || 0);
  if (running && !(await confirmDialog("Redémarrer la console ?",
    `${running} tâche(s) en cours seront interrompues ; tu pourras les relancer ou les reprendre.`, "Redémarrer", "danger"))) return;
  let old;
  try { old = (await api("/api/system/restart", { method: "POST" })).boot; }
  catch (e) { toast(e.message, "err"); return; }
  const note = h("div", { class: "dialog-body" }, "La page se recharge dès que la nouvelle version répond.");
  document.getElementById("modal-root").append(h("div", { class: "overlay" },
    h("div", { class: "dialog", role: "alertdialog", "aria-live": "polite" }, h("h3", {}, "Redémarrage de la console…"), note)));
  const end = Date.now() + 90_000;
  while (Date.now() < end) {
    await new Promise((r) => setTimeout(r, 800));
    try {
      const p = await (await fetch("/api/ping", { cache: "no-store" })).json();
      if (p.boot && p.boot !== old) { location.reload(); return; }
    } catch { /* not up yet */ }
  }
  note.textContent = "La console ne répond pas : relance start.bat.";
}
