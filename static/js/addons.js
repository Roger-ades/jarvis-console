// Modules (console/addons.py): tools, applications and panels that Claude develops for JARVIS, one folder of
// addons/ each. The button Modules lists them; each one opens in a window of its own, in a sandboxed frame of the
// preview origin without network. What it does beyond its window comes here as a message (window.jarvis of the
// bridge) and is answered only for a frame of one of these windows: its storage, a text put in the command bar or
// a question to Claude (both after a click in the module), a web page (after a confirmation).
import { api } from "./api.js";
import { colorOf, paint } from "./tint.js";
import { $, confirmDialog, h, toast } from "./util.js";
import { pinButton } from "./viewer.js";
import * as wm from "./wm.js";

let ctx = null, modules = [], folder = "";
const wins = new Map();     // module id -> {id, mod, frame, grant, last, note}
const pending = new Map();  // task id of a question -> {source, msgId}
const early = new Map();    // answers arrived before the reply of /ask
const GAP = 1200;           // ms between two sends (bar, question, notification) of one module
const URL_AGE = 11 * 3600e3;

/** ctx: compose(pid, text) (text in the command bar), currentProfile(), color() (the account's color). */
export async function initAddons(context) {
  ctx = context;
  const btn = $("#btn-addons"), menu = $("#menu-addons");
  btn?.addEventListener("click", async (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    if (!menu.hidden) { renderMenu(); await load(); renderMenu(); }
  });
  wm.onDocument((doc) => doc.addEventListener("pointerdown", (e) => { if (!e.target.closest(".menu-wrap")) menu.hidden = true; }));
  await load();
}

async function load() {
  try { const r = await api("/api/addons"); modules = r.modules || []; folder = r.dossier || ""; } catch { /* the list stays */ }
  for (const [aid, w] of wins) { const m = modules.find((x) => x.id === aid); if (m) { w.mod = m; grantBar(w); } }
}

const emoji = (m) => m.icone || "🧩";

function renderMenu() {
  const menu = $("#menu-addons");
  const rows = modules.map((m) => h("button", { type: "button", class: "ad-row", title: m.problemes.length ? m.problemes.join("\n") : m.description || m.nom,
    disabled: m.problemes.length ? true : undefined, on: { click: () => { menu.hidden = true; openAddon(m.id); } } },
    h("span", { class: "ad-emoji" }, emoji(m)),
    h("span", { class: "ad-t" }, h("b", {}, m.nom), h("small", {}, m.problemes.length ? `⚠ ${m.problemes[0]}` : m.description || m.id))));
  menu.replaceChildren(h("div", { class: "menu-title" }, "Modules de JARVIS"),
    ...(rows.length ? rows : [h("div", { class: "menu-note" }, "Aucun module pour l'instant. Demande à Claude d'en développer un : un outil, un tableau, un panneau qui reste dans JARVIS.")]),
    h("hr"),
    h("button", { type: "button", on: { click: () => { menu.hidden = true; askClaude(); } } }, "Demander un nouveau module à Claude…"),
    h("button", { type: "button", on: { click: () => { menu.hidden = true; reveal(); } } }, "Ouvrir le dossier des modules"),
    h("div", { class: "menu-note" }, "Isolés et sans réseau : un module ne voit ni tes fichiers ni la console, il garde ses propres données."));
}

function askClaude(m = null) {
  ctx.compose(ctx.currentProfile(), m
    ? `Améliore le module JARVIS « ${m.nom} » (${m.id}) : `
    : "Développe un module JARVIS qui ");
}

function reveal(aid = "") {
  api("/api/addons/reveal", { method: "POST", body: { id: aid } }).catch((e) => toast(e.message, "err"));
}

/** The items of Ctrl+K. */
export function addonItems() {
  return [
    ...modules.filter((m) => !m.problemes.length).map((m) => ({ label: `Module ${m.nom}`, icon: "puzzle", hint: m.description || "",
      keywords: `module addon outil ${m.id} ${m.description}`, run: () => openAddon(m.id) })),
    { label: "Demander un nouveau module à Claude", icon: "puzzle", keywords: "module addon outil développer créer application",
      run: () => askClaude() },
  ];
}

/** The server: a module changed (grant), or Claude opened one (tool modules). */
export function addonsChanged() { load(); }
export function addonOpened({ id }) { if (id) load().then(() => openAddon(id, { reload: true })); }

/** Open a module's window (again: brings it forward; reload: a fresh address, its files read again). */
export async function openAddon(aid, { reload = false } = {}) {
  const shown = wins.get(aid);
  if (shown && wm.has(shown.id)) {
    if (wm.isMinimized(shown.id)) wm.restore(shown.id); else wm.focus(shown.id);
    if (reload || Date.now() - shown.at > URL_AGE) await load_(shown);
    return;
  }
  let r;
  try { r = await api(`/api/addons/${encodeURIComponent(aid)}/open`, { method: "POST" }); }
  catch (e) { toast(e.message, "err"); return; }
  const m = r.module;
  const id = `addon-${aid}`;
  const frame = h("iframe", { class: "ad-frame", title: m.nom, referrerpolicy: "no-referrer", allow: "clipboard-write" });
  const w = { id, aid, mod: m, frame, last: 0, at: 0, grant: h("div", { class: "ad-grant", hidden: true }) };
  const close = () => { wm.unregister(id); wins.delete(aid); };
  const act = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  w.title = h("span", { class: "win-title" }, m.nom);
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "ad-emoji" }, emoji(m)),
    h("div", { class: "vh" }, w.title, h("small", {}, `Module · ${m.id}`)),
    h("div", { class: "pv-actions" },
      act("retry", "Recharger le module", () => load_(w)),
      act("edit", "Demander une amélioration à Claude", () => askClaude(w.mod)),
      act("folder", "Afficher le dossier du module", () => reveal(aid))),
    h("div", { class: "win-actions" },
      pinButton(id),
      act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)),
      act("close", "Fermer", close)));
  const win = paint(h("section", { class: "win pv-win ad-win", role: "dialog", "aria-label": m.nom },
    head, h("div", { class: "pv-body ad-body" }, w.grant, frame)), colorOf(ctx.color?.()));
  wins.set(aid, w);
  wm.register(id, win, { handle: head, ephemeral: true, fresh: true, size: { w: m.largeur, h: m.hauteur + 42 },
    meta: { title: m.nom, subtitle: "Module", icon: "puzzle", onClose: close }, title: m.nom });
  frame.src = r.url;
  w.at = Date.now();
  grantBar(w);
}

async function load_(w) {
  try {
    const r = await api(`/api/addons/${encodeURIComponent(w.aid)}/open`, { method: "POST" });
    w.mod = r.module;
    w.title.textContent = r.module.nom;
    w.frame.src = r.url;
    w.at = Date.now();
    grantBar(w);
  } catch (e) { toast(e.message, "err"); }
}

/** A module asking for more than its window: the user grants it here, once (until its permissions change). */
function grantBar(w) {
  const m = w.mod;
  w.grant.hidden = !m.permissions.length || m.autorise;
  if (w.grant.hidden) return;
  w.grant.replaceChildren(
    h("span", {}, `Ce module demande à pouvoir poser des questions à Claude (une courte discussion de ton compte, avec ses autorisations habituelles).`),
    h("button", { type: "button", class: "btn small primary", on: { click: () => grant(w, true) } }, "Autoriser"));
}

async function grant(w, on) {
  try { w.mod = await api(`/api/addons/${encodeURIComponent(w.aid)}/grant`, { method: "POST", body: { on } }); grantBar(w); return true; }
  catch (e) { toast(e.message, "err"); return false; }
}

// ------------------------------------------------------------ the bridge (window.jarvis of the module)
wm.onDocument((doc) => doc.defaultView.addEventListener("message", onMessage));

async function onMessage(ev) {
  const d = ev.data;
  if (!d || d.jarvisAddon !== 1 || ev.origin !== "null" || typeof d.id !== "number") return;
  let w = null;
  for (const x of wins.values()) {
    try { if (ev.source && ev.source === x.frame.contentWindow?.[0]) w = x; } catch { /* not ours */ }
  }
  if (!w) return;
  const reply = (ok, v) => {
    try { ev.source.postMessage(ok ? { jarvisAddon: 1, id: d.id, ok: true, valeur: v ?? null } : { jarvisAddon: 1, id: d.id, ok: false, erreur: String(v) }, "*"); }
    catch { /* the module went away */ }
  };
  const base = `/api/addons/${encodeURIComponent(w.aid)}`;
  const inside = () => w.frame.ownerDocument.activeElement === w.frame; // the user is working in this module
  const paced = () => { const now = Date.now(); if (now - w.last < GAP) return false; w.last = now; return true; };
  try {
    switch (d.type) {
      case "lire": {
        const r = await api(`${base}/data`);
        return reply(true, r.donnees?.[String(d.cle)] ?? null);
      }
      case "cles": return reply(true, Object.keys((await api(`${base}/data`)).donnees || {}));
      case "ecrire":
        await api(`${base}/data`, { method: "PUT", body: { cle: String(d.cle || ""), valeur: d.valeur ?? null } });
        return reply(true, true);
      case "envoyer": {
        const text = String(d.contenu ?? "").slice(0, 20000);
        if (!inside()) return reply(false, "Seulement après un clic de l'utilisateur dans le module.");
        if (!text.trim() || !paced()) return reply(false, "Rien à envoyer.");
        ctx.compose(ctx.currentProfile(), text);
        return reply(true, true);
      }
      case "demander": {
        if (!inside()) return reply(false, "Seulement après un clic de l'utilisateur dans le module.");
        if (!paced()) return reply(false, "Trop de demandes à la suite.");
        if (!w.mod.permissions.includes("claude")) return reply(false, "Le module doit déclarer la permission « claude » dans addon.json.");
        if (!w.mod.autorise) {
          const ok = await confirmDialog(`Autoriser « ${w.mod.nom} » à demander à Claude ?`, h("div", {},
            h("p", {}, "Chaque question devient une courte discussion de ton compte, sans fenêtre, avec ses autorisations et validations habituelles ; la réponse revient au module."),
            h("p", { class: "muted" }, "Tu peux retirer l'autorisation en supprimant le module ou en changeant ses permissions.")), "Autoriser");
          if (!ok || !(await grant(w, true))) return reply(false, "Refusé par l'utilisateur.");
        }
        const r = await api(`${base}/ask`, { method: "POST", body: { consigne: String(d.consigne || ""),
          format: d.format === "json" ? "json" : "texte", modele: String(d.modele || ""), profile: ctx.currentProfile() } });
        const got = early.get(r.task_id);
        if (got) { early.delete(r.task_id); return got.ok ? reply(true, got.valeur) : reply(false, got.erreur); }
        pending.set(r.task_id, { reply });
        return undefined;
      }
      case "ouvrir": {
        let url;
        try { url = new URL(String(d.url || "")); } catch { return reply(false, "Adresse invalide."); }
        if (url.protocol !== "https:") return reply(false, "Seulement une adresse https://.");
        const ok = await confirmDialog("Ouvrir une page web ?", h("div", {},
          h("p", {}, `Le module « ${w.mod.nom} » propose d'ouvrir :`), h("pre", { class: "prop-code" }, url.href)), "Ouvrir");
        if (ok) window.open(url.href, "_blank", "noopener,noreferrer");
        return reply(ok, ok || "Refusé par l'utilisateur.");
      }
      case "notifier": {
        const text = String(d.texte || "").trim().slice(0, 300);
        if (!text || !paced()) return reply(false, "Rien à afficher.");
        toast(`${w.mod.nom} : ${text}`);
        return reply(true, true);
      }
      default: return reply(false, `Inconnu : ${d.type}`);
    }
  } catch (e) { return reply(false, e.message); }
}

/** The server: Claude answered a module's question. */
export function addonAnswered(a) {
  const p = pending.get(a.task_id);
  if (!p) { early.set(a.task_id, a); setTimeout(() => early.delete(a.task_id), 60_000); return; }
  pending.delete(a.task_id);
  if (a.ok) p.reply(true, a.valeur); else p.reply(false, a.erreur || "Claude n'a pas répondu.");
}
