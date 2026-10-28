// Skills are competences, not actions: Claude Code loads one by itself when a task calls for it. The project
// panel only lists the folder's own (.claude/skills); they are read and written in the page Compétences
// (competences.js), with the account's. An action (a command) may ask for a skill in its text.
import { api } from "./api.js";
import { slashDescription } from "./slash-fr.js";
import { h } from "./util.js";

/** The "Compétences" tab of the project panel: the folder's skills, and the way to the page Compétences. */
export async function renderSkills(body, ctx, ws) {
  const proj = ctx.project(ws.folder);
  const open = (skill = "") => ctx.openCompetences?.(proj ? { folder: proj.folder, skill } : { profile: ws.profile || ctx.currentProfile?.() || "" });
  body.append(h("p", { class: "pj-lead pad" }, "Ce que Claude sait faire dans ce dossier : il charge une compétence de lui-même quand la tâche s'y prête. ",
    "Ce ne sont pas des boutons ; une action peut en demander une dans son texte."),
  h("div", { class: "pad" }, h("button", { type: "button", class: "btn small", on: { click: () => open() } },
    proj ? "Gérer les compétences" : "Gérer les compétences du compte")));
  const here = h("div", { class: "drawer-list flat" }, h("div", { class: "empty-row" }, "Lecture des compétences…"));
  body.append(h("div", { class: "sec-title pad" }, "Du dossier"), here);
  let res;
  try { res = await api(`/api/skills?${new URLSearchParams({ folder: ws.folder, profile: proj?.profile || ws.profile || ctx.currentProfile?.() || "" })}`); }
  catch (e) { if (here.isConnected) here.replaceChildren(h("div", { class: "line err" }, e.message)); return; }
  if (!here.isConnected) return;
  const color = proj?.color || "var(--accent)";
  if (!res.projet.length) {
    here.replaceChildren(h("div", { class: "empty-row" }, "Aucune compétence propre à ce dossier (.claude/skills). "
      + `Celles du compte ${res.profile_name || ""} servent aussi ici.`.replace("  ", " ")));
    return;
  }
  here.replaceChildren(...res.projet.map((s) => h("div", { class: "hrow file", tabindex: "0", role: "button", style: { "--pc": color },
    title: proj ? "Ouvrir dans la page Compétences" : s.path,
    on: { click: () => proj && open(s.name), keydown: (e) => { if (e.key === "Enter" && proj) open(s.name); } } },
  h("span", { class: "i", svg: "sparkle" }),
  h("div", { class: "hm" }, h("div", { class: "ht" }, s.name),
    h("div", { class: "hs" }, slashDescription(s.name, s.description) || "")))));
}
