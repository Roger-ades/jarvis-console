// Projects: named working folders with their defaults (account, permissions, model, effort).
import { api } from "./api.js";
import { dialog, h, toast } from "./util.js";

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
  const body = h("div", { class: "pf" }, h("p", { class: "muted pf-path" }, folder),
    row("Nom", name), row("Couleur", color), row("Compte", account), row("Autorisations", preset), row("Modèle", model), row("Effort", effort),
    h("label", { class: "check" }, pinned, "Épingler sur l'accueil"),
    h("p", { class: "muted" }, "Choisir ce projet règle la barre du bas sur ces valeurs ; tu peux toujours les changer pour une demande."));
  const buttons = [{ label: "Annuler", value: null }];
  if (existing) buttons.push({ label: "Retirer le projet", value: "delete", cls: "danger" });
  buttons.push({ label: existing ? "Enregistrer" : "Créer le projet", value: "save", cls: "primary" });
  const v = await dialog({ title: existing ? `Projet ${existing.name}` : "Nouveau projet", body, buttons,
    onOpen: (box) => { box.classList.add("pf-dialog"); name.focus(); name.select(); } });
  if (!v) return null;
  try {
    if (v === "delete") {
      await api(`/api/projects?folder=${encodeURIComponent(folder)}`, { method: "DELETE" });
      toast("Projet retiré (le dossier et ses fichiers ne changent pas).", "ok");
      await loadProjects();
      return "deleted";
    }
    const saved = await api("/api/projects", { method: "PUT", body: { folder, name: name.value.trim() || baseName(folder), color: color.value,
      pinned: pinned.checked, profile: account.value, preset: preset.value, model: model.value, effort: effort.value } });
    await loadProjects();
    return saved;
  } catch (e) { toast(e.message, "err"); return null; }
}
