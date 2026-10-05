// Widgets: displays of Claude pinned to the desktop (console/widgets.py). A column on the right of the
// desktop, under the windows; each widget follows the display it came from (same id, same account).
import { api } from "./api.js";
import { openDisplayWindow, renderDisplay, setDoc, unmount } from "./display.js";
import { popupMenu } from "./taskwin.js";
import { taskTint } from "./tint.js";
import { $, fmtDate, h, toast } from "./util.js";
import * as wm from "./wm.js";

let ctx = { profiles: () => [], openTask: () => {} };
let list = [];
const views = new Set();
const AUTO = [["", "Jamais"], ["heure", "Toutes les heures"], ["matin", "Chaque matin à 8:00"], ["semaine", "En semaine à 8:00"]];

export function initWidgets(context) {
  ctx = { ...ctx, ...context };
  api("/api/widgets").then(widgetsChanged).catch(() => {});
}

export function widgetsChanged(payload) {
  list = payload?.widgets || [];
  render();
}

/** A display of a discussion goes on the desktop (pinned again, it is only brought up to date). */
export async function pinDisplay(taskId, key) {
  try {
    await api("/api/widgets", { method: "POST", body: { task: taskId, key } });
    toast("Épinglé sur le bureau JARVIS.", "ok");
  } catch (e) { toast(e.message, "err"); }
}

async function call(path, method = "POST", body = undefined, done = "") {
  try {
    const r = await api(path, { method, ...(body ? { body } : {}) });
    if (done) toast(done, "ok");
    return r;
  } catch (e) { toast(e.message, "err"); return null; }
}

function tintOfWidget(w) {
  const p = ctx.profiles().find((x) => x.id === w.profile);
  return taskTint({ color: p?.color, workdir: w.workdir });
}

const detach = (w) => call(`/api/widgets/${w.id}`, "DELETE", undefined, "Widget retiré du bureau.");

function card(w) {
  setDoc(w.task, { key: w.key, rev: w.rev, ...w.doc });
  const color = tintOfWidget(w);
  const view = renderDisplay(w.task, w.key, { mode: "widget", color });
  views.add(view);
  const btn = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  const account = ctx.profiles().find((p) => p.id === w.profile);
  const more = btn("more", "Options du widget", () => popupMenu(more, [
    { label: "Ouvrir dans une fenêtre", run: () => openDisplayWindow(w.task, w.key, color) },
    { label: "Ouvrir la discussion", run: () => ctx.openTask(w.task) },
    "-",
    { title: `Actualisation automatique · ${w.auto_label}` },
    ...AUTO.filter(([k]) => k !== (w.auto || "")).map(([k, label]) => ({
      label, run: () => call(`/api/widgets/${w.id}/auto`, "POST", { every: k },
        k ? `Claude l'actualisera ${label.toLowerCase()} (Routines).` : "Actualisation automatique arrêtée."),
    })),
    "-",
    { label: "Détacher du bureau", danger: true, run: () => detach(w) },
  ]));
  const when = [account && ctx.profiles().length > 1 ? account.name : "", `à jour ${fmtDate(w.updated)}`,
    w.auto ? w.auto_label : ""].filter(Boolean).join(" · ");
  return h("article", { class: "wg", "aria-label": w.doc.titre, style: { "--pc": color?.color || "" } },
    h("header", { class: "wg-head" },
      h("span", { class: "i", svg: "sparkle" }),
      h("div", { class: "wg-t" }, h("b", { title: w.doc.titre }, w.doc.titre), h("small", {}, when)),
      btn("retry", "Actualiser maintenant : Claude refait l'affichage avec les données du moment",
        () => call(`/api/widgets/${w.id}/refresh`, "POST", {}, "Claude actualise le widget…")),
      more,
      btn("close", "Retirer du bureau", () => detach(w))),
    h("div", { class: "wg-body" }, view));
}

function render() {
  for (const v of views) unmount(v);
  views.clear();
  if (wm.isNative()) { renderNative(); return; }
  const box = $("#widgets");
  if (!box) return;
  box.replaceChildren(...list.map(card));
  box.hidden = !list.length;
  $("#desktop")?.classList.toggle("has-widgets", list.length > 0);
}

/** "Intégré au bureau": the JARVIS page is hidden; each widget is a window of its own on the desktop. */
function renderNative() {
  const ids = new Set(list.map((w) => w.id));
  for (const id of wm.widgetWindowIds()) if (!ids.has(id)) wm.closeWidgetWindow(id);
  let below = 0;
  for (const w of list) {
    wm.openWidgetWindow(w.id, card(w), { below, onClose: () => detach(w), onGone: () => setTimeout(render, 500) });
    below += 432;
  }
}
