// Projects: named working folders with their defaults (account, permissions, model, effort).
import { api } from "./api.js";
import { dialog, h, paint, toast } from "./util.js";
import { tintOf } from "./tint.js";

let list = [];
const key = (p) => String(p || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
const COLORS = ["#72c9ff", "#ffb347", "#4fd3a0", "#b69cff", "#f26b80", "#f0d35c", "#5ce0e6", "#ff8fd1"];

export const projects = () => list;
export function setProjects(p) { list = p || []; }
export async function loadProjects() {
  try { list = (await api("/api/projects")).projects || []; } catch { /* keep the last list */ }
  return list;
}
export function projectFor(folder) {
  const k = key(folder);
  return k ? list.find((p) => key(p.folder) === k) || null : null;
}
export const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const lines = (t) => t.value.split("\n").map((x) => x.trim()).filter(Boolean);

/** The settings of a project (or of a folder about to become one). Resolves with the saved project, "deleted" or null. */
export async function editProject({ folder, profiles, presets, models, current = {} }) {
  const existing = projectFor(folder);
  const p = { name: baseName(folder), color: COLORS[list.length % COLORS.length], pinned: true, profile: current.profile || "",
    preset: "", model: "", effort: "", ...(existing || {}) };
  const name = h("input", { type: "text", value: p.name, maxlength: "60" });
  const color = h("input", { type: "color", value: p.color });
  const sel = (value, options) => { const s = h("select", {}, ...options.map(([v, l]) => h("option", { value: v }, l))); s.value = value; return s; };
  const account = sel(p.profile, [["", "Le compte choisi dans la barre"], ...profiles.map((x) => [x.id, x.name])]);
  const preset = sel(p.preset, [["", "Celui du compte"], ...presets.filter((x) => x.enabled).map((x) => [x.id, x.name])]);
  const model = sel(p.model, [["", "Celui du compte"], ...models.filter(([v]) => v).map(([v, l]) => [v, l])]);
  const effort = sel(p.effort, [["", "Par défaut"], ["low", "Faible"], ["medium", "Moyen"], ["high", "Élevé"], ["xhigh", "Très élevé"], ["max", "Max"]]);
  const pinned = h("input", { type: "checkbox" });
  pinned.checked = p.pinned;
  const row = (label, control) => h("label", { class: "pf-row" }, h("span", {}, label), control);
  // "Mails à suivre": written by the user. They count in the account brief while followed, and in the project brief
  // when that brief's Mails source is on.
  const mails = { follow: true, senders: [], subjects: [], bodies: [], folders: [], instructions: "", ...(p.mails || {}) };
  const follow = h("input", { type: "checkbox" });
  follow.checked = mails.follow !== false;
  const listArea = (values, placeholder) => { const t = h("textarea", { rows: "2", spellcheck: "false", placeholder }); t.value = values.join("\n"); return t; };
  const senders = listArea(mails.senders, "dupont@client.fr\nclient.fr");
  const subjects = listArea(mails.subjects, "déménagement\nlocaux");
  const bodies = listArea(mails.bodies, "bail\nétat des lieux");
  const instr = h("textarea", { rows: "2", placeholder: "Ex. les relances du propriétaire, les devis des déménageurs" });
  instr.value = mails.instructions || "";
  const mailBox = h("details", { class: "pf-mails", open: !!(mails.senders.length || mails.subjects.length || mails.bodies.length || mails.instructions) },
    h("summary", {}, "Mails à suivre"),
    h("p", { class: "muted" }, "Une valeur par ligne. « Suivre dans le brief du compte » les fait compter dans le brief du matin. "
      + "Le brief de ce projet les reprend quand sa source Mails est cochée."),
    h("label", { class: "check" }, follow, "Suivre dans le brief du compte"),
    row("Correspondants", senders), row("Mots de l'objet", subjects), row("Mots du corps", bodies), row("Consigne", instr));
  // The project's own brief: off until turned on, launched on its own (docs/ihm.md).
  const pb = { enabled: false, time: "08:00", days: [0, 1, 2, 3, 4], mails: true, office_tasks: true, calendar: true,
    odoo: true, odoo_projects: [], show: "", model: "", effort: "low", preset: "lecture", ...(p.brief || {}) };
  const briefOn = h("input", { type: "checkbox" });
  briefOn.checked = !!pb.enabled;
  const briefTime = h("input", { type: "time", value: pb.time || "08:00" });
  const briefDays = new Set(pb.days || []);
  const dayBtns = h("div", { class: "days" }, ...["L", "M", "M", "J", "V", "S", "D"].map((l, d) => {
    const btn = h("button", { type: "button", class: `day${briefDays.has(d) ? " on" : ""}` }, l);
    btn.addEventListener("click", () => { if (!briefDays.delete(d)) briefDays.add(d); btn.classList.toggle("on", briefDays.has(d)); });
    return btn;
  }));
  const src = (label, on) => { const c = h("input", { type: "checkbox" }); c.checked = on; return h("label", { class: "check" }, c, label); };
  const srcMails = src("Mails de ce projet", pb.mails !== false);
  const srcOffice = src("Tâches Office 365", pb.office_tasks !== false);
  const srcCal = src("Calendrier", pb.calendar !== false);
  const srcOdoo = src("Projets et tâches Odoo", pb.odoo !== false);
  const odooNames = listArea(pb.odoo_projects, "Déménagement locaux\nUn nom de projet Odoo par ligne");
  const show = sel(pb.show || "", [["", "Comme le réglage général"], ["oui", "Oui, ouvrir la fenêtre"], ["non", "Non, rester dans la boîte"]]);
  const briefChoices = presets.filter((x) => (x.enabled && !x.require_confirm) || x.id === (pb.preset || "lecture"));
  const briefPreset = sel(pb.preset || "lecture", briefChoices.map((x) => [x.id, x.name]));
  const briefModel = sel(pb.model || "", [["", "Celui du projet"], ...models.filter(([v]) => v).map(([v, l]) => [v, l])]);
  const briefEffort = sel(pb.effort || "low", [["low", "Faible"], ["medium", "Moyen"], ["high", "Élevé"], ["xhigh", "Très élevé"], ["max", "Max"]]);
  const launch = h("button", { type: "button", class: "btn small" }, "Lancer maintenant");
  const briefBox = h("details", { class: "pf-mails", open: !!pb.enabled },
    h("summary", {}, "Brief du projet"),
    h("p", { class: "muted" }, "Un point de ce dossier, lancé seul. Les autorisations sont celles choisies ci-dessous "
      + "(Lecture seule par défaut). Le rapport lit les sources cochées et le fichier BRIEF.md à la racine. "
      + "Une ligne n'y est ajoutée que si tu la coches dans le rapport."),
    h("label", { class: "check" }, briefOn, "Activer le brief de ce projet"),
    h("div", { class: "pf-row" }, h("span", {}, "Quand"), h("div", { class: "row" }, briefTime, dayBtns)),
    srcMails, srcOffice, srcCal, srcOdoo,
    row("Projets Odoo", odooNames),
    row("À l'écran", show),
    row("Autorisations", briefPreset),
    row("Modèle", briefModel), row("Effort", briefEffort),
    launch);
  const payload = () => ({ folder, name: name.value.trim() || baseName(folder), color: color.value,
    pinned: pinned.checked, profile: account.value, preset: preset.value, model: model.value, effort: effort.value,
    mails: { follow: follow.checked, senders: lines(senders), subjects: lines(subjects), bodies: lines(bodies),
      folders: mails.folders || [], instructions: instr.value.trim() },
    brief: { enabled: briefOn.checked, time: briefTime.value || "08:00", days: [...briefDays].sort(),
      mails: srcMails.querySelector("input").checked, office_tasks: srcOffice.querySelector("input").checked,
      calendar: srcCal.querySelector("input").checked, odoo: srcOdoo.querySelector("input").checked,
      odoo_projects: lines(odooNames), show: show.value, preset: briefPreset.value || "lecture",
      model: briefModel.value, effort: briefEffort.value } });
  const persist = async () => {
    const saved = await api("/api/projects", { method: "PUT", body: payload() });
    await loadProjects();
    return saved;
  };
  launch.addEventListener("click", async () => {
    if (!briefOn.checked) { toast("Active d'abord le brief de ce projet.", "err"); return; }
    launch.disabled = true;
    try {
      await persist();
      await api("/api/projects/brief/run", { method: "POST", body: { folder } });
      toast("Brief du projet lancé. Le rapport arrive en tête de la boîte, et dans une fenêtre si l'affichage automatique est actif.", "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { launch.disabled = false; }
  });
  const body = h("div", { class: "pf" }, h("p", { class: "muted pf-path" }, folder),
    row("Nom", name), row("Couleur", color), row("Compte", account), row("Autorisations", preset), row("Modèle", model), row("Effort", effort),
    h("label", { class: "check" }, pinned, "Épingler sur l'accueil"),
    mailBox, briefBox,
    h("p", { class: "muted" }, "Choisir ce projet règle la barre du bas sur ces valeurs ; tu peux toujours les changer pour une demande."));
  const buttons = [{ label: "Annuler", value: null }];
  if (existing) buttons.push({ label: "Retirer le projet", value: "delete", cls: "danger" });
  buttons.push({ label: existing ? "Enregistrer" : "Créer le projet", value: "save", cls: "primary" });
  // the dialog takes the colors of the account and of the project, and follows them while they are chosen
  const accountColor = () => profiles.find((x) => x.id === (account.value || current.profile))?.color || profiles[0]?.color;
  let box = null;
  const repaint = () => box && paint(box, tintOf(accountColor(), color.value));
  color.addEventListener("input", repaint);
  account.addEventListener("change", repaint);
  const v = await dialog({ title: existing ? `Projet ${existing.name}` : "Nouveau projet", body, buttons,
    tint: tintOf(accountColor(), p.color),
    onOpen: (b) => { box = b; b.classList.add("pf-dialog"); name.focus(); name.select(); } });
  if (!v) return null;
  try {
    if (v === "delete") {
      await api(`/api/projects?folder=${encodeURIComponent(folder)}`, { method: "DELETE" });
      toast("Projet retiré (le dossier et ses fichiers ne changent pas).", "ok");
      await loadProjects();
      return "deleted";
    }
    return await persist();
  } catch (e) { toast(e.message, "err"); return null; }
}
