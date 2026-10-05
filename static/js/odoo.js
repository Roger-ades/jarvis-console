// Odoo inside the project's Suivi tab: the Odoo projects linked to this project, and their tasks and subtasks
// (console/odoo_link.py). The console reads Odoo through a short read-only discussion of the project's account;
// a click edits its text and its status (Claude writes them in Odoo, under the account's preset), a right click
// shows it to Claude (Regard).
import { api } from "./api.js";
import { dialog, fmtDate, h, toast } from "./util.js";
import { openPreview } from "./viewer.js";

const shown = new Map();   // folder -> the state last read
let showDone = false;

/** The tab's content. rerender(): draw the tab again (the panel's render). */
export function renderOdoo(body, folder, { isProject, makeProject, tint, rerender }) {
  if (!isProject) {
    body.append(h("p", { class: "pj-lead pad" }, "Les projets Odoo se lient à un projet de la console. Donne d'abord un nom à ce dossier."),
      h("div", { class: "row pad" }, h("button", { type: "button", class: "btn small primary", on: { click: makeProject } }, "En faire un projet")));
    return;
  }
  const box = h("div", { class: "odoo-tab" }, h("div", { class: "empty-row" }, "Lecture…"));
  body.append(box);
  const draw = (st) => {
    shown.set(folder, st);
    box.replaceChildren(...view(st, folder, tint, rerender));
  };
  const cached = shown.get(folder);
  if (cached) draw(cached);
  api(`/api/projects/odoo?${new URLSearchParams({ folder })}`).then((st) => {
    draw(st);
    if (st.links.length && !st.running && (!st.updated || st.stale) && !st.error) refresh(folder).then(() => { if (box.isConnected) draw(shown.get(folder)); });
  }).catch((e) => box.replaceChildren(h("div", { class: "line err" }, e.message)));
}

async function refresh(folder) {
  try { shown.set(folder, await api("/api/projects/odoo/refresh", { method: "POST", body: { folder } })); }
  catch (e) { toast(e.message, "err"); }
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return fmtDate(ts);
}

function view(st, folder, tint, rerender) {
  const btn = (label, title, fn, cls = "btn small ghost") => h("button", { type: "button", class: cls, title,
    on: { click: (e) => { e.stopPropagation(); fn(); } } }, label);
  const links = h("section", { class: "pj-card" }, h("h3", {}, "Projets Odoo liés"),
    h("p", { class: "pj-lead" }, "Leurs tâches et sous-tâches s'affichent ici. Tu peux aussi demander à Claude, dans une "
      + "discussion du projet : « lie le projet Odoo … » ; il le propose, ton clic l'enregistre."),
    st.links.length ? h("div", { class: "odoo-links" }, ...st.links.map((l) => h("span", { class: "odoo-link" },
      h("b", {}, l.name), h("small", { class: "muted" }, `n° ${l.id}${l.app ? ` · ${l.app}` : ""}`),
      l.url ? btn("Ouvrir", "Ouvrir le projet dans Odoo", () => openPreview({ url: l.url, kind: "web" })) : null,
      btn("Délier", "Ne plus suivre ce projet Odoo ici (rien ne change dans Odoo)", async () => {
        try { await api("/api/projects/odoo/unlink", { method: "POST", body: { folder, id: l.id, app: l.app } }); shown.delete(folder); rerender(); }
        catch (e) { toast(e.message, "err"); }
      })))) : h("p", { class: "muted" }, "Aucun projet Odoo lié."),
    h("div", { class: "row" }, btn("Lier un projet Odoo…", "Chercher un projet dans Odoo", () => linkDialog(folder, tint, rerender), "btn small primary")));
  if (!st.links.length) return [links];
  const done = st.tasks.filter((t) => t.done).length;
  const toggle = h("input", { type: "checkbox" });
  toggle.checked = showDone;
  toggle.addEventListener("change", () => { showDone = toggle.checked; rerender(); });
  const state = st.running ? "Lecture d'Odoo en cours…" : st.updated ? `À jour ${ago(st.updated)}` : "Pas encore lues";
  const head = h("div", { class: "row odoo-bar" }, h("b", {}, `${st.tasks.length - done} tâche${st.tasks.length - done > 1 ? "s" : ""} ouverte${st.tasks.length - done > 1 ? "s" : ""}`),
    h("span", { class: "muted" }, state),
    h("span", { class: "grow" }),
    done ? h("label", { class: "check" }, toggle, `Terminées (${done})`) : null,
    h("button", { type: "button", class: "btn small", disabled: st.running, title: "Relire les tâches dans Odoo (une courte lecture par Claude, en lecture seule)",
      on: { click: async () => { await refresh(folder); rerender(); } } }, "Actualiser"));
  const err = st.error ? h("div", { class: "line err pad" }, st.error) : null;
  return [links, h("section", { class: "pj-card odoo-tasks" }, head, err, tree(st, showDone, folder, tint, rerender))];
}

function tree(st, withDone, folder, tint, rerender) {
  const tasks = st.tasks.filter((t) => withDone || !t.done);
  if (!tasks.length) return h("div", { class: "empty-row" }, st.running ? "Lecture d'Odoo en cours…" : st.tasks.length ? "Toutes les tâches sont terminées." : "Aucune tâche lue.");
  const byParent = new Map();
  const ids = new Set(tasks.map((t) => t.id));
  for (const t of tasks) {
    const p = t.parent && ids.has(t.parent) ? t.parent : 0;
    if (!byParent.has(p)) byParent.set(p, []);
    byParent.get(p).push(t);
  }
  const order = (a, b) => (a.done - b.done) || (b.priority - a.priority) || ((a.deadline || "9999") < (b.deadline || "9999") ? -1 : 1);
  const names = new Map(st.links.map((l) => [l.id, l.name]));
  const many = st.links.length > 1;
  const today = new Date().toISOString().slice(0, 10);
  const rows = [];
  const walk = (parent, depth) => {
    for (const t of (byParent.get(parent) || []).sort(order)) {
      rows.push(row(t, depth, { today, project: many ? names.get(t.project) : "", folder, tint, rerender }));
      if (depth < 4) walk(t.id, depth + 1);
    }
  };
  walk(0, 0);
  return h("div", { class: "drawer-list flat odoo-tree" }, ...rows);
}

const DONE = /termin|annul|fait|cl[oô]tur|clos/i;

function row(t, depth, { today, project, folder, tint, rerender }) {
  const late = !t.done && t.deadline && t.deadline < today;
  const when = t.deadline ? `échéance ${t.deadline.split("-").reverse().join("/")}` : "";
  const sub = [project, t.stage, when, t.users.join(", ")].filter(Boolean).join(" · ");
  const detail = [`Tâche Odoo n° ${t.id} « ${t.name} »${project ? ` (projet ${project})` : ""}`,
    t.stage ? `étape : ${t.stage}` : "", t.done ? "terminée" : "", t.deadline ? `échéance : ${t.deadline}` : "",
    t.users.length ? `responsables : ${t.users.join(", ")}` : "", t.priority ? "prioritaire" : "",
    t.parent ? `sous-tâche de la tâche n° ${t.parent}` : ""].filter(Boolean).join("\n");
  return h("div", {
    class: `hrow odoo-task${t.done ? " done" : ""}${late ? " late" : ""}`, style: { "--pc": t.done ? "var(--border-3)" : late ? "var(--err)" : t.priority ? "var(--warn)" : "var(--accent)", "--depth": depth },
    title: "Modifier le contenu et le statut · clic droit : demander à Claude à propos de cette tâche",
    "data-pick": detail, "data-pick-label": `tâche « ${t.name} »`,
    on: { click: () => editTask(folder, t, tint, rerender) } },
    h("span", { class: "i", svg: t.done ? "check" : t.priority ? "alert" : "list" }),
    h("div", { class: "hm" }, h("div", { class: "ht" }, t.name), sub ? h("div", { class: "hs" }, sub) : null));
}

function stageOptions(stages, current, done) {
  const opts = [];
  for (const s of [...(stages || []), current, "Terminée"]) {
    const label = String(s || "").trim();
    if (label && !opts.includes(label)) opts.push(label);
  }
  return opts.length ? opts : ["En cours", "Terminée"];
}

/** Click on a task: edit its text and its status. The write goes through the account's Odoo connector. */
async function editTask(folder, t, tint, rerender) {
  const name = h("input", { type: "text", value: t.name, maxlength: "200" });
  const text = h("textarea", { class: "pj-text", rows: "8", placeholder: "Lecture du contenu…", disabled: true });
  const status = h("select");
  let description = null;
  let token = 0;
  const showStages = (stages, current, done) => {
    const opts = stageOptions(stages, current, done);
    status.replaceChildren(...opts.map((s) => h("option", { value: s }, s)));
    const picked = done ? (opts.find((s) => DONE.test(s)) || "Terminée") : (current || opts[0]);
    status.value = opts.includes(picked) ? picked : opts[0];
  };
  showStages([t.stage], t.stage, t.done);
  const mine = ++token;
  (async () => {
    try {
      const { task_id: rid } = await api("/api/projects/odoo/task/read", { method: "POST", body: { folder, id: t.id } });
      let r = { status: "running" };
      for (let i = 0; i < 80 && r.status === "running" && mine === token; i++) {
        await new Promise((ok) => setTimeout(ok, 1500));
        r = await api(`/api/projects/odoo/task/read/${rid}?${new URLSearchParams({ folder })}`);
      }
      if (mine !== token) return;
      if (r.status !== "done") throw new Error(r.error || "Odoo ne répond pas : réessaie dans un moment.");
      description = r.task.description || "";
      text.value = description;
      text.disabled = false;
      text.placeholder = "";
      if (name.value === t.name && r.task.name) name.value = r.task.name;
      showStages(r.task.stages, r.task.stage || t.stage, r.task.done);
    } catch (e) {
      if (mine === token) text.placeholder = e.message;
    }
  })();
  const open = t.url ? h("button", { type: "button", class: "btn small ghost",
    on: { click: () => openPreview({ url: t.url, kind: "web" }) } }, "Ouvrir dans Odoo") : null;
  const ok = await dialog({
    title: `Tâche n° ${t.id}`,
    body: h("div", { class: "odoo-edit" },
      h("label", {}, "Titre", name),
      h("label", {}, "Contenu", text),
      h("label", {}, "Statut", status),
      open),
    buttons: [{ label: "Annuler", value: false }, { label: "Enregistrer", value: true, cls: "primary" }],
    onOpen: () => name.focus(), tint,
  });
  token++;
  if (!ok) return;
  const stage = status.value;
  const done = DONE.test(stage);
  const sendDescription = description !== null && text.value !== description;
  const sameName = name.value.trim() === t.name;
  const sameStage = stage === (t.done ? (DONE.test(t.stage) ? t.stage : "Terminée") : (t.stage || stage));
  if (sameName && sameStage && done === !!t.done && !sendDescription) return;
  try {
    await api("/api/projects/odoo/task", { method: "POST", body: {
      folder, id: t.id, name: name.value.trim(), stage, done, ...(sendDescription ? { description: text.value } : {}),
    }});
    toast("Modification envoyée. Si le compte demande une validation, confirme-la dans la discussion.", "ok");
    rerender();
  } catch (e) { toast(e.message, "err"); }
}

/** Search a project in Odoo and link the ones ticked. */
async function linkDialog(folder, tint, rerender) {
  const q = h("input", { type: "text", placeholder: "Nom du projet dans Odoo (vide : les plus récents)" });
  const go = h("button", { type: "button", class: "btn small primary" }, "Chercher dans Odoo");
  const list = h("div", { class: "ctx-list" }, h("div", { class: "muted" }, "Cherche un projet : Claude lit Odoo en lecture seule, sans fenêtre."));
  const picked = new Map();
  let token = 0;
  const search = async () => {
    const mine = ++token;
    go.disabled = true;
    picked.clear();
    list.replaceChildren(h("div", { class: "muted" }, "Recherche dans Odoo…"));
    try {
      const { task_id: tid } = await api("/api/projects/odoo/search", { method: "POST", body: { folder, q: q.value.trim() } });
      let r = { status: "running" };
      for (let i = 0; i < 120 && r.status === "running" && mine === token; i++) {
        await new Promise((ok) => setTimeout(ok, 1500));
        r = await api(`/api/projects/odoo/search/${tid}?${new URLSearchParams({ folder })}`);
      }
      if (mine !== token) return;
      if (r.status === "running") throw new Error("Odoo ne répond pas : réessaie dans un moment.");
      if (r.error && !(r.projects || []).length) throw new Error(r.error);
      list.replaceChildren(...r.projects.map((p) => {
        const c = h("input", { type: "checkbox" });
        c.addEventListener("change", () => { if (c.checked) picked.set(p.id, p); else picked.delete(p.id); });
        return h("label", { class: "ctx-row" }, c, h("span", { class: "ctx-t" }, h("b", {}, p.name),
          h("small", {}, [`n° ${p.id}`, p.client, p.tasks ? `${p.tasks} tâche${p.tasks > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · "))));
      }));
    } catch (e) { if (mine === token) list.replaceChildren(h("div", { class: "line err" }, e.message)); }
    finally { if (mine === token) go.disabled = false; }
  };
  go.addEventListener("click", search);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });
  const ok = await dialog({
    title: "Lier un projet Odoo",
    body: h("div", { class: "ctx-pick" }, h("div", { class: "row" }, q, go), list),
    buttons: [{ label: "Annuler", value: false }, { label: "Lier", value: true, cls: "primary" }],
    onOpen: (b) => { b.classList.add("ctx-dialog"); q.focus(); }, tint,
  });
  token++;
  if (!ok) return;
  if (!picked.size) { toast("Coche au moins un projet Odoo.", "warn"); return; }
  try {
    await api("/api/projects/odoo/link", { method: "POST", body: { folder, links: [...picked.values()].map((p) => ({ id: p.id, name: p.name })) } });
    shown.delete(folder);
    toast("Projet Odoo lié : ses tâches arrivent.", "ok");
    rerender();
  } catch (e) { toast(e.message, "err"); }
}

/** The console read the tasks again (event odoo_tasks): the tab follows. */
export function odooChanged(folder, isShown, rerender) {
  shown.delete(folder);
  if (isShown) rerender();
}
