// Plan usage limits of each account (5-hour session, week…), always visible in the top bar.
// The numbers come with every answer of Claude; "Actualiser" sends one tiny Haiku request.
import { api } from "./api.js";
import { h, toast } from "./util.js";

const LABELS = { five_hour: "Session (5 h)", seven_day: "Semaine", seven_day_opus: "Semaine · Opus",
  seven_day_sonnet: "Semaine · Sonnet", seven_day_overage_included: "Semaine, dépassement compris", overage: "Dépassement" };
const ORDER = ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "seven_day_overage_included", "overage"];
const REASONS = { org_level_disabled: "désactivé par l'organisation", out_of_credits: "plus de crédits",
  member_level_disabled: "désactivé pour ce membre" };

let limits = {}, busy = new Set(), onChange = () => {}, onAlert = () => {}, open = null;
const ALERTS_KEY = "jarvis.limitAlerts";

export function initLimits(data, changed, alert = () => {}) {
  limits = data?.limits || {};
  busy = new Set(data?.busy || []);
  onChange = changed;
  onAlert = alert;
}

export function updateLimits(pid, l) {
  limits[pid] = l;
  busy.delete(pid);
  onChange();
  if (open?.pid === pid) open.render();
  checkAlerts(pid);
}

/** One alert per window, per level (80 %, 90 %, reached), per reset period. */
function checkAlerts(pid) {
  const l = limits[pid];
  if (!l?.windows) return;
  let seen = {};
  try { seen = JSON.parse(localStorage.getItem(ALERTS_KEY) || "{}"); } catch { /* private window */ }
  for (const k of ["five_hour", "seven_day"]) {
    const w = current(l.windows[k]);
    if (!w) continue;
    const rejected = l.status === "rejected" && l.type === k;
    const lvl = rejected ? 3 : w.used >= 0.9 ? 2 : w.used >= 0.8 ? 1 : 0;
    const key = `${pid}:${k}:${w.resets_at || 0}`;
    if (lvl && (seen[key] || 0) < lvl) {
      seen[key] = lvl;
      onAlert({ pid, window: k, label: LABELS[k], used: w.used, rejected, resetsAt: w.resets_at, when: when(w.resets_at) });
    }
  }
  const now = Date.now() / 1000;
  for (const key of Object.keys(seen)) if (Number(key.split(":")[2]) && Number(key.split(":")[2]) < now - 86400 * 8) delete seen[key];
  try { localStorage.setItem(ALERTS_KEY, JSON.stringify(seen)); } catch { /* private window */ }
}

/** Where an account stands before a launch: blocked (limit reached) or close to it. */
export function accountState(pid) {
  const l = limits[pid];
  if (!l?.windows) return { known: false };
  const s = current(l.windows.five_hour), wk = current(l.windows.seven_day);
  const now = Date.now() / 1000;
  const typeWin = current(l.windows[l.type]);
  const blocked = l.status === "rejected" && !(typeWin?.reset) && (l.resets_at || 0) > now;
  const full = Object.entries(l.windows).map(([, w]) => current(w))
    .filter((w) => w && !w.reset && (w.used >= 0.999) && (w.resets_at || 0) > now).map((w) => w.resets_at);
  const resetAt = blocked ? Math.max(l.resets_at || 0, ...full) : null;
  return { known: true, blocked, resetAt, resetText: resetAt ? when(resetAt) : "", session: s?.used ?? null, week: wk?.used ?? null,
    near: !blocked && ((s?.used ?? 0) >= 0.9 || (wk?.used ?? 0) >= 0.95) };
}

/** A window as it stands now: past its reset time, it is back to 0. */
function current(w) {
  if (!w) return null;
  const now = Date.now() / 1000;
  const reset = w.resets_at && w.resets_at <= now;
  return { used: reset ? 0 : Math.max(0, Math.min(1, w.used || 0)), resets_at: w.resets_at, reset };
}

const level = (used, rejected) => (rejected || used >= 0.9 ? "hot" : used >= 0.7 ? "warm" : "ok");
const pct = (x) => `${Math.round(x * 100)} %`;

function when(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const mins = Math.max(0, Math.round((d - now) / 60000));
  const rel = mins < 60 ? `dans ${mins} min` : mins < 48 * 60 ? `dans ${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")}` : "";
  const day = d.toDateString() === now.toDateString() ? time
    : `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit" })} ${time}`;
  return rel ? `${day} (${rel})` : day;
}

function ago(ts) {
  if (!ts) return "jamais";
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  return m < 1 ? "à l'instant" : m < 60 ? `il y a ${m} min` : m < 48 * 60 ? `il y a ${Math.floor(m / 60)} h` : `il y a ${Math.floor(m / 1440)} j`;
}

/** Compact gauges for a pill: session and week. */
export function gauges(pid) {
  const l = limits[pid];
  if (!l || !l.windows) return null;
  const rejected = l.status === "rejected";
  const parts = ["five_hour", "seven_day"].map((k) => [k, current(l.windows[k])]).filter(([, w]) => w);
  if (!parts.length) return null;
  const levels = parts.map(([k, w]) => level(w.used, rejected && l.type === k));
  const worst = levels.includes("hot") ? "hot" : levels.includes("warm") ? "warm" : "ok";
  // two thin stacked bars (session above, week below) and "25 · 60 %"
  return h("span", { class: `lg ${worst}${rejected ? " rejected" : ""}` },
    h("span", { class: "lg-bars" }, ...parts.map(([, w], i) =>
      h("span", { class: `lg-bar ${levels[i]}` }, h("i", { style: { width: `${Math.round(w.used * 100)}%` } })))),
    h("span", { class: "lg-txt" }, rejected ? "limite" : `${parts.map(([, w]) => Math.round(w.used * 100)).join(" · ")} %`));
}

/** Share of the 5-hour session used (for the thin line under the account buttons). */
export function sessionUsed(pid) {
  const w = current(limits[pid]?.windows?.five_hour);
  return w ? w.used : null;
}

export function limitsTitle(pid, name) {
  const l = limits[pid];
  if (!l?.windows) return `${name} : limites pas encore connues — cliquer`;
  const bits = ORDER.filter((k) => l.windows[k]).map((k) => `${LABELS[k]} ${pct(current(l.windows[k]).used)}`);
  return `${name} · ${bits.join(" · ")}${l.status === "rejected" ? " · limite atteinte" : ""}`;
}

/** An account's session and week, for the home screen. */
export function limitsBlock(profile, onOpen) {
  const l = limits[profile.id] || {};
  const rejected = l.status === "rejected";
  const rows = ["five_hour", "seven_day"].filter((k) => l.windows?.[k]).map((k) => {
    const w = current(l.windows[k]);
    return h("div", { class: `lp-row ${level(w.used, rejected && l.type === k)}` },
      h("div", { class: "lp-top" }, h("b", {}, LABELS[k]), h("span", { class: "lp-v" }, pct(w.used))),
      h("div", { class: "lp-bar" }, h("i", { style: { width: `${Math.round(w.used * 100)}%` } })),
      h("small", {}, w.reset ? "réinitialisé" : w.resets_at ? `réinit. ${when(w.resets_at)}` : ""));
  });
  return h("button", { type: "button", class: "home-lim", style: { "--pc": profile.color }, on: { click: onOpen } },
    h("div", { class: "lp-head" }, h("span", { class: "sw" }), h("b", {}, profile.name),
      rejected ? h("span", { class: "lp-flag" }, "Limite atteinte") : null),
    ...(rows.length ? rows : [h("small", { class: "muted" }, "Pas encore mesurées")]));
}

async function refresh(pid) {
  busy.add(pid);
  open?.render();
  try { await api(`/api/limits/${pid}/refresh`, { method: "POST" }); }
  catch (e) { busy.delete(pid); toast(e.message, "err"); open?.render(); }
}

/** Details of an account's limits, under its pill. */
export function openLimits(anchor, profile, { onConfig }) {
  closeLimits();
  const box = h("div", { class: "limits-pop", role: "dialog", "aria-label": `Limites ${profile.name}`, style: { "--pc": profile.color } });
  const render = () => {
    const l = limits[profile.id] || {};
    const rejected = l.status === "rejected";
    const rows = ORDER.filter((k) => l.windows?.[k]).map((k) => {
      const w = current(l.windows[k]);
      return h("div", { class: `lp-row ${level(w.used, rejected && l.type === k)}` },
        h("div", { class: "lp-top" }, h("b", {}, LABELS[k]), h("span", { class: "lp-v" }, pct(w.used))),
        h("div", { class: "lp-bar" }, h("i", { style: { width: `${Math.round(w.used * 100)}%` } })),
        h("small", {}, w.reset ? "réinitialisé depuis la dernière mesure" : w.resets_at ? `réinitialisation ${when(w.resets_at)}` : ""));
    });
    const ov = l.overage || {};
    box.replaceChildren(...[
      h("div", { class: "lp-head" }, h("span", { class: "sw" }), h("b", {}, `${profile.name} — limites du forfait`),
        rejected ? h("span", { class: "lp-flag" }, "Limite atteinte") : l.status === "allowed_warning" ? h("span", { class: "lp-flag warn" }, "Bientôt atteinte") : null),
      ...(rows.length ? rows : [h("p", { class: "muted" }, "Pas encore mesurées : lance une tâche avec ce compte ou clique sur Actualiser.")]),
      ov.status ? h("div", { class: "lp-note" }, `Dépassement payant : ${ov.using ? "en cours d'utilisation" : ov.status === "rejected"
        ? (REASONS[ov.reason] || "désactivé") : "possible"}`) : null,
      l.error ? h("div", { class: "lp-note err" }, `Dernière actualisation impossible : ${l.error}`) : null,
      h("div", { class: "lp-foot" },
        h("small", { class: "muted" }, busy.has(profile.id) ? "Actualisation…" : `Mis à jour ${ago(l.updated)}`),
        h("span", { class: "grow" }),
        h("button", { type: "button", class: "btn small ghost", on: { click: () => { closeLimits(); onConfig(); } } }, "Compte…"),
        h("button", { type: "button", class: "btn small", disabled: busy.has(profile.id),
          title: "Envoie une toute petite requête (Haiku) pour relire les limites", on: { click: () => refresh(profile.id) } }, "Actualiser")),
      h("p", { class: "lp-help" }, "Mesuré par Claude à chaque réponse ; les mêmes chiffres que /usage dans Claude Code."),
    ].filter(Boolean));
  };
  render();
  document.body.append(box);
  const r = anchor.getBoundingClientRect();
  box.style.top = `${r.bottom + 8}px`;
  box.style.left = `${Math.max(8, Math.min(window.innerWidth - box.offsetWidth - 8, r.left + r.width / 2 - box.offsetWidth / 2))}px`;
  const away = (e) => { if (!box.contains(e.target) && !anchor.contains(e.target)) closeLimits(); };
  const esc = (e) => { if (e.key === "Escape") closeLimits(); };
  setTimeout(() => document.addEventListener("pointerdown", away), 0);
  document.addEventListener("keydown", esc);
  open = { pid: profile.id, render, close: () => { box.remove(); document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); } };
}

export function closeLimits() { open?.close(); open = null; }
export const limitsOpenFor = () => open?.pid || null;
