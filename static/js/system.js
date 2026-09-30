// Server version, updates and restart: the page is read from disk on every load, the server's
// code only when it starts. A banner offers to restart after an update, or to install a new
// version published on GitHub (git pull, dependencies, restart).
import { api } from "./api.js";
import { confirmDialog, h, toast } from "./util.js";

let bar = null, barKind = "";

function showBar(kind, text, button) {
  if (bar && barKind === kind) return;
  bar?.remove();
  barKind = kind;
  bar = h("div", { class: "update-bar", role: "status" }, h("span", { class: "i", svg: "retry" }), h("span", {}, text), button,
    h("button", { type: "button", class: "icon-btn", title: "Masquer", svg: "close", on: { click: () => { bar?.remove(); bar = null; } } }));
  document.body.append(bar);
}

export async function checkVersion() {
  try {
    const v = await api("/api/system/version");
    if (v.stale) {
      showBar("stale", v.restartable ? "Mise à jour installée : redémarre la console pour l'activer."
        : "Mise à jour installée : arrête la console (Configuration → Général) puis relance start.bat.",
      v.restartable ? h("button", { type: "button", class: "btn small primary", on: { click: restartConsole } }, "Redémarrer") : null);
      return;
    }
  } catch (e) {
    if (e.status === 404) { showBar("stale", "Mise à jour installée : arrête la console (Configuration → Général) puis relance start.bat.", null); return; }
  }
  try {
    const u = await api("/api/system/update");
    if (u.behind > 0) {
      showBar("update", `Nouvelle version disponible (${u.behind} changement${u.behind > 1 ? "s" : ""}).`,
        h("button", { type: "button", class: "btn small primary", on: { click: () => updateConsole(u) } }, "Mettre à jour"));
    }
  } catch { /* no update information: nothing to show */ }
}

async function running() {
  try { const st = await api("/api/state"); return (st.running || 0) + (st.awaiting || 0); } catch { return 0; }
}

/** After a restart: wait for the new server (another boot id), then reload the page. */
async function waitForNew(old, title) {
  const note = h("div", { class: "dialog-body" }, "La page se recharge dès que la nouvelle version répond.");
  document.getElementById("modal-root").append(h("div", { class: "overlay" },
    h("div", { class: "dialog", role: "alertdialog", "aria-live": "polite" }, h("h3", {}, title), note)));
  const end = Date.now() + 120_000;
  while (Date.now() < end) {
    await new Promise((r) => setTimeout(r, 800));
    try {
      const p = await (await fetch("/api/ping", { cache: "no-store" })).json();
      if (p.boot && p.boot !== old) { location.reload(); return; }
    } catch { /* not up yet */ }
  }
  note.textContent = "La console ne répond pas : relance start.bat.";
}

export async function restartConsole() {
  const n = await running();
  if (n && !(await confirmDialog("Redémarrer la console ?",
    `${n} tâche(s) en cours seront interrompues ; tu pourras les relancer ou les reprendre.`, "Redémarrer", "danger"))) return;
  let old;
  try { old = (await api("/api/system/restart", { method: "POST" })).boot; }
  catch (e) { toast(e.message, "err"); return; }
  waitForNew(old, "Redémarrage de la console…");
}

/** Install the version published on GitHub, then restart on it. */
export async function updateConsole(u = null) {
  const info = u || await api("/api/system/update").catch(() => null);
  const n = await running();
  const list = (info?.commits || []).slice(0, 12);
  const body = h("div", {},
    h("p", {}, "La console récupère la nouvelle version (git pull), réinstalle les dépendances si besoin, puis redémarre."),
    list.length ? h("ul", { class: "upd-list" }, ...list.map((c) => h("li", {}, c))) : null,
    n ? h("p", { class: "line warn" }, `${n} tâche(s) en cours seront interrompues.`) : null);
  if (!(await confirmDialog("Mettre à jour la console ?", body, "Mettre à jour", "primary"))) return;
  toast("Mise à jour en cours…");
  let r;
  try { r = await api("/api/system/update", { method: "POST" }); }
  catch (e) { toast(e.message, "err"); return; }
  if (!r.updated) { toast("La console est déjà à jour.", "ok"); bar?.remove(); bar = null; return; }
  waitForNew(r.boot, "Mise à jour installée, redémarrage…");
}

/** "Rechercher une mise à jour" (Configuration): asks GitHub now. */
export async function checkUpdateNow() {
  const u = await api("/api/system/update/check", { method: "POST" });
  if (u.behind > 0) checkVersion();
  return u;
}
