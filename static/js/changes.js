// What Claude changed in files (console/changes.py): the differences of one change, the list of a
// discussion's changes, undo and redo. Undoing puts the file back as it was before the change (a file
// Claude created is removed); the console refuses when the file changed since, unless the user forces
// it, and keeps what it overwrote so that "Rétablir" can bring it back.
import { api } from "./api.js";
import { dialog, fmtDate, h, toast } from "./util.js";
import { pinButton, openPreview } from "./viewer.js";
import { colorOf, paint } from "./tint.js";
import * as wm from "./wm.js";

const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const icon = (name) => h("span", { class: "i", svg: name });
const open = new Map();   // `${taskId}|${changeId | "*"}` -> {id, reload}
let seq = 0;

/** "+3 −1", or what happened to the file. */
export function counts(c) {
  if (c.binary) return c.created ? "créé" : "binaire";
  if (c.deleted) return "supprimé";
  return c.created ? `créé · +${c.added}` : `+${c.added} −${c.removed}`;
}

const STATUS = {
  annule: ["Annulée", "La modification est annulée : le fichier est revenu à son état d'avant."],
  actuel: ["", ""],
  avant: ["Comme avant", "Le fichier est déjà revenu à son état d'avant cette modification."],
  modifie: ["Modifié depuis", "Le fichier a changé depuis cette modification (une autre modification, de Claude ou de toi)."],
  suivie: ["Modifiée ensuite", "Une modification plus récente de Claude suit sur ce fichier : annule-la d'abord, ou « Tout annuler » dans Fichiers modifiés (menu ⋯ de la discussion)."],
  illisible: ["Illisible", "Le fichier est illisible ou trop gros pour être comparé."],
};

/** The open windows of a task follow its changes (a new one, an undo from elsewhere). */
export function changesUpdated(taskId) {
  for (const [k, w] of open) if (k.startsWith(`${taskId}|`)) w.reload();
}

function frame(key, { title, subtitle, iconName, color, size, actions = [], regard = null }) {
  const shown = open.get(key);
  if (shown && wm.has(shown.id)) { if (wm.isMinimized(shown.id)) wm.restore(shown.id); else wm.focus(shown.id); shown.reload(); return null; }
  const id = `chg-${++seq}`;
  const body = h("div", { class: "pv-body chg-body" }, h("div", { class: "muted pv-wait" }, "Chargement…"));
  const titleEl = h("span", { class: "win-title" }, title);
  const sub = h("small", { title: subtitle }, subtitle);
  const bar = h("div", { class: "pv-actions" }, ...actions);
  const close = () => { wm.unregister(id); open.delete(key); };
  const act = (ic, label, fn) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, svg: ic, on: { click: fn } });
  const head = h("header", { class: "win-head pv-head" },
    h("span", { class: "pv-ic", svg: iconName }), h("div", { class: "vh" }, titleEl, sub), bar,
    h("div", { class: "win-actions" }, pinButton(id), act("min", "Réduire", () => wm.minimize(id)),
      act("max", "Agrandir / rétablir (double-clic sur la barre)", () => wm.toggleMax(id)), act("close", "Fermer", close)));
  const el = paint(h("section", { class: "win pv-win chg-win", role: "dialog", "aria-label": title }, head, body), color);
  wm.register(id, el, { handle: head, ephemeral: true, size, fresh: true,
    meta: { title, subtitle, color: colorOf(color), icon: iconName, onClose: close, ...(regard ? { regard } : {}) } });
  const w = { id, body, bar, titleEl, sub, close, reload: () => {} };
  open.set(key, w);
  return w;
}

/** Undo (or redo) one change, after the user confirms; a file changed since asks again before forcing. */
export async function undoChange(taskId, c, { redo = false, running = false, color = null } = {}) {
  const name = baseName(c.path);
  const what = redo
    ? `Le fichier reprend l'état qu'il avait quand tu as annulé la modification.`
    : c.created ? "Claude avait créé ce fichier : il sera supprimé." : "Le fichier revient à son état d'avant cette modification de Claude.";
  const ok = await dialog({
    title: redo ? `Rétablir la modification de ${name} ?` : `Annuler la modification de ${name} ?`,
    body: h("div", {}, h("p", {}, what), h("p", { class: "muted" }, c.path),
      redo ? null : h("p", { class: "muted" }, "Tu pourras la rétablir. Claude en sera informé avec ton prochain message."),
      running ? h("p", { class: "warn-text" }, "La discussion est en cours : Claude pourrait réécrire ce fichier.") : null),
    buttons: [{ label: "Fermer", value: false }, { label: redo ? "Rétablir" : "Annuler la modification", value: true, cls: "primary" }],
    tint: color,
  });
  if (!ok) return null;
  const path = `/api/tasks/${taskId}/changes/${c.id}/${redo ? "redo" : "undo"}`;
  try {
    const r = await api(path, { method: "POST", body: {} });
    toast(redo ? `Modification rétablie : ${name}` : `Modification annulée : ${name}`, "ok");
    return r;
  } catch (e) {
    if (e.status !== 409 || !e.data.conflict) { toast(e.message, "err"); return null; }
    const force = await dialog({
      title: "Le fichier a changé depuis", body: h("div", {}, h("p", {}, e.message), h("p", { class: "muted" }, c.path)),
      buttons: [{ label: "Laisser tel quel", value: false }, { label: "Forcer", value: true, cls: "danger" }], tint: color,
    });
    if (!force) return null;
    try {
      const r = await api(path, { method: "POST", body: { force: true } });
      toast(redo ? `Modification rétablie : ${name}` : `Modification annulée : ${name} (version précédente gardée pour « Rétablir »)`, "ok");
      return r;
    } catch (e2) { toast(e2.message, "err"); return null; }
  }
}

function diffTable(d) {
  if (d.binary) return h("p", { class: "muted chg-note" }, "Fichier binaire : pas de différences ligne à ligne.");
  if (d.only_endings) return h("p", { class: "muted chg-note" }, "Seules les fins de ligne ou l'encodage ont changé.");
  const rows = d.lines.map((l) => h("tr", { class: l.k === "+" ? "add" : l.k === "-" ? "del" : l.k === "@" ? "hunk" : "" },
    h("td", { class: "ln" }, l.k === "@" ? "" : l.a ?? ""), h("td", { class: "ln" }, l.k === "@" ? "" : l.b ?? ""),
    h("td", { class: "sg" }, l.k === "@" ? "" : l.k), h("td", { class: "tx" }, l.t)));
  return h("div", { class: "chg-diff" }, h("table", {}, h("tbody", {}, ...rows)),
    d.truncated ? h("p", { class: "muted chg-note" }, "Différences tronquées : le fichier change sur trop de lignes.") : null);
}

/** One change: its differences, undo / redo, the file's preview. ctx: {task(): the task, color}. */
export async function openDiff(taskId, change, ctx = {}) {
  const color = ctx.color || null;
  const w = frame(`${taskId}|${change.id}`, { title: `Différences · ${baseName(change.path)}`, subtitle: change.path,
    iconName: "diff", color, size: { w: 860, h: 640 }, regard: { type: "fichier", path: change.path, task: taskId } });
  if (!w) return;
  w.reload = async () => {
    let d;
    try { d = await api(`/api/tasks/${taskId}/changes/${change.id}`); } catch (e) { w.body.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
    const note = (STATUS[d.status] || ["", ""])[1];
    const undone = d.state === "annule";
    const running = () => ["queued", "running", "awaiting"].includes(ctx.task?.()?.status);
    const act = async () => { if (await undoChange(taskId, d, { redo: undone, running: running(), color })) changesUpdated(taskId); };
    w.bar.replaceChildren(
      h("button", { type: "button", class: "icon-btn", title: `Aperçu : ${d.path}`, "aria-label": "Aperçu du fichier", svg: "eye",
        on: { click: () => openPreview({ taskId, path: d.path, color }) } }),
      h("button", { type: "button", class: `btn small ${undone ? "" : "primary"}`, on: { click: act } },
        icon(undone ? "retry" : "undo"), undone ? "Rétablir" : "Annuler la modification"));
    w.sub.textContent = `${d.tool} · ${fmtDate(d.ts)} · ${counts(d)} · ${d.path}`;
    w.body.replaceChildren(...[
      note ? h("div", { class: `pv-note chg-status s-${d.status}` }, icon(d.status === "annule" ? "undo" : "info"), h("span", {}, note)) : null,
      d.created && !d.binary ? h("div", { class: "pv-note" }, icon("info"), h("span", {}, "Fichier créé par Claude.")) : null,
      diffTable(d),
    ].filter(Boolean));
  };
  w.reload();
}

/** Every file the discussion changed, its changes newest first, undo one or the whole file. */
export async function openChanges(taskId, ctx = {}) {
  const color = ctx.color || null;
  const title = ctx.title ? `Fichiers modifiés · ${ctx.title}` : "Fichiers modifiés";
  const w = frame(`${taskId}|*`, { title, subtitle: "Modifications faites par Claude dans cette discussion", iconName: "diff",
    color, size: { w: 720, h: 560 } });
  if (!w) return;
  const running = () => ["queued", "running", "awaiting"].includes(ctx.task?.()?.status);
  w.reload = async () => {
    let list;
    try { list = (await api(`/api/tasks/${taskId}/changes`)).changes || []; } catch (e) { w.body.replaceChildren(h("div", { class: "line err pv-err" }, e.message)); return; }
    if (!list.length) {
      w.body.replaceChildren(h("p", { class: "muted chg-note" },
        "Aucune modification de fichier suivie dans cette discussion. Seules les écritures des outils de fichiers de Claude "
        + "(Write, Edit…) sont suivies, pas celles des commandes."));
      return;
    }
    const files = new Map();
    for (const c of list) { if (!files.has(c.path)) files.set(c.path, []); files.get(c.path).push(c); }
    const groups = [...files.entries()].sort((a, b) => Math.max(...b[1].map((c) => c.ts)) - Math.max(...a[1].map((c) => c.ts)));
    w.sub.textContent = `${groups.length} fichier${groups.length > 1 ? "s" : ""} · ${list.length} modification${list.length > 1 ? "s" : ""}`;
    w.body.replaceChildren(...groups.map(([path, cs]) => {
      const live = cs.filter((c) => c.state !== "annule");
      const undoAll = async () => {
        const ok = await dialog({ title: `Tout annuler pour ${baseName(path)} ?`,
          body: h("div", {}, h("p", {}, `Les ${live.length} modifications de Claude encore en place, de la plus récente à la plus ancienne : le fichier revient à son état d'avant la discussion.`),
            h("p", { class: "muted" }, path),
            running() ? h("p", { class: "warn-text" }, "La discussion est en cours : Claude pourrait réécrire ce fichier.") : null),
          buttons: [{ label: "Fermer", value: false }, { label: "Tout annuler", value: true, cls: "primary" }], tint: color });
        if (!ok) return;
        try {
          const r = await api(`/api/tasks/${taskId}/changes-file/undo`, { method: "POST", body: { path } });
          toast(r.stopped ? `${r.undone.length} modification(s) annulée(s), puis arrêt : ${r.stopped}` : `Modifications annulées : ${baseName(path)}`,
            r.stopped ? "warn" : "ok");
        } catch (e) { toast(e.message, "err"); }
        changesUpdated(taskId);
      };
      return h("section", { class: "chg-file" },
        h("div", { class: "chg-fhead" },
          h("a", { href: "#", class: "chg-fname", title: `Aperçu : ${path}`, on: { click: (e) => { e.preventDefault(); openPreview({ taskId, path, color }); } } },
            icon("file"), h("b", {}, baseName(path))),
          h("small", { class: "muted", title: path }, path),
          live.length > 1 ? h("button", { type: "button", class: "btn small ghost", on: { click: undoAll } }, icon("undo"), "Tout annuler") : null),
        h("div", { class: "chg-rows" }, ...[...cs].reverse().map((c) => {
          const undone = c.state === "annule";
          const [label] = STATUS[c.status] || [""];
          return h("div", { class: `chg-row${undone ? " undone" : ""}` },
            h("span", { class: "chg-when" }, fmtDate(c.ts)), h("span", { class: "chg-tool" }, c.tool),
            h("span", { class: "chg-n" }, counts(c)), label ? h("span", { class: `chg-badge s-${c.status}` }, label) : h("span"),
            h("span", { class: "grow" }),
            h("button", { type: "button", class: "btn small ghost", on: { click: () => openDiff(taskId, c, ctx) } }, icon("diff"), "Différences"),
            h("button", { type: "button", class: "btn small", on: { click: async () => {
              if (await undoChange(taskId, c, { redo: undone, running: running(), color })) changesUpdated(taskId);
            } } }, icon(undone ? "retry" : "undo"), undone ? "Rétablir" : "Annuler"));
        })));
    }));
  };
  w.reload();
}
