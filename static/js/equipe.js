// Team projects: the page talks only to this console. The hub token stays on the server.
import { api } from "./api.js";
import { pickFolder } from "./folderpicker.js";
import { dialog, fmtDate, h, toast } from "./util.js";

const pretty = (fp) => {
  const hex = String(fp || "").replace(/[^0-9a-f]/gi, "").toUpperCase();
  return (hex.match(/.{2}/g) || []).join(":") || "";
};

export function renderEquipe(body, ws, ctx) {
  const redraw = () => { body.replaceChildren(); renderEquipe(body, ws, ctx); };
  const head = h("div", { class: "team-sec" }, h("div", { class: "empty-row" }, "Lecture de l'équipe…"));
  const host = h("div");
  body.append(head, host);
  api("/api/hub/status").then((st) => {
    if (!head.isConnected) return;
    if (!st.enabled || !st.has_token || st.error || !st.user) {
      head.replaceChildren(h("p", { class: "pj-lead pad" }, st.error
        ? `Hub injoignable : ${st.error}`
        : "Le hub d'équipe n'est pas encore relié : Réglages › Équipe (adresse, empreinte et jeton)."));
      return;
    }
    head.replaceChildren(...here(ws, ctx, st.user, redraw));
    projectList(host, ws, redraw);
    presence(body, ws, ctx);
  }).catch((e) => { if (head.isConnected) head.replaceChildren(h("div", { class: "line err" }, e.message)); });
}

// What this folder is to the team: a team project (its members), a project to share, or nothing yet.
function here(ws, ctx, user, redraw) {
  const proj = ctx?.project?.(ws.folder);
  const team = proj?.team_id || "";
  if (team) {
    const members = h("div", { class: "drawer-list flat" }, h("div", { class: "empty-row" }, "Lecture des membres…"));
    loadMembers(members, team, user);
    const sync = h("button", { type: "button", class: "btn small ghost", on: { click: async () => {
      sync.disabled = true;
      try {
        const res = await api("/api/hub/syncthing", { method: "POST" });
        const mine = (res.projects || {})[team] || {};
        toast(mine.ok ? "Synchro en place pour ce projet." : (mine.error || "Synchro non relancée."), mine.ok ? "ok" : "warn");
      } catch (e) { toast(e.message, "err"); }
      sync.disabled = false;
    } } }, "Relancer la synchro");
    return [h("div", { class: "team-head" }, h("h4", {}, `Projet d'équipe « ${proj.name} »`), sync), members];
  }
  if (!proj) return [h("p", { class: "pj-lead pad" }, "Ce dossier n'est pas encore un projet JARVIS.")];
  if (!user.admin) return [h("p", { class: "pj-lead pad" }, "Ce projet n'est pas partagé. Rejoins ci-dessous un projet d'équipe, ou demande à l'administrateur d'en créer un.")];
  const create = h("button", { type: "button", class: "btn small primary", on: { click: async () => {
    const name = await dialog({
      title: "Partager ce projet avec l'équipe",
      body: h("div", {}, h("p", {}, "Le hub crée le projet d'équipe et Syncthing synchronise ce dossier avec le serveur."),
        h("p", { class: "muted" }, "Tu en es l'administrateur ; ajoute ensuite les collègues qui y travaillent.")),
      input: { value: proj.name, placeholder: "Nom du projet d'équipe" },
      buttons: [{ label: "Annuler", value: null }, { label: "Créer", value: true, cls: "primary" }],
    });
    if (name == null || !String(name).trim()) return;
    create.disabled = true;
    try {
      const res = await api("/api/hub/create", { method: "POST", body: { folder: ws.folder, name: String(name).trim() } });
      syncToast(String(name).trim(), res.syncthing);
      redraw();
    } catch (e) { toast(e.message, "err"); create.disabled = false; }
  } } }, "Faire de ce projet un projet d'équipe");
  return [h("p", { class: "pj-lead pad" }, "Ce projet n'est pas encore partagé."), h("div", { class: "team-head" }, create)];
}

async function loadMembers(list, team, user) {
  try {
    const [{ members = [] }, { projects = [] }] = await Promise.all([
      api(`/api/hub/members?project=${encodeURIComponent(team)}`), api("/api/hub/projects")]);
    const role = (projects.find((p) => p.id === team) || {}).role;
    const manage = user.admin || role === "administrateur";
    const save = async (next) => {
      try {
        await api("/api/hub/members", { method: "PUT", body: { project: team, members: next } });
      } catch (e) { toast(e.message, "err"); }
      loadMembers(list, team, user);
    };
    const plain = members.map((m) => ({ id: m.id, role: m.role }));
    const rows = members.map((m) => {
      const roleSel = h("select", { "aria-label": `Rôle de ${m.id}`, disabled: !manage, on: { change: () =>
        save(plain.map((x) => x.id === m.id ? { id: x.id, role: roleSel.value } : x)) } },
        ...["administrateur", "membre"].map((r) => h("option", { value: r, selected: r === m.role }, r)));
      const out = manage && h("button", { type: "button", class: "btn small ghost", title: "Retirer du projet", on: { click: () =>
        save(plain.filter((x) => x.id !== m.id)) } }, "Retirer");
      return h("div", { class: "hrow" }, h("div", { class: "hm" }, h("div", { class: "ht" }, m.id),
        h("div", { class: "hs" }, `depuis le ${fmtDate(m.joined)}`)), roleSel, out);
    });
    list.replaceChildren(...rows);
    if (!manage) return;
    const { users = [] } = await api("/api/hub/users");
    const free = users.filter((u) => !members.some((m) => m.id === u.id));
    if (!free.length) return;
    const who = h("select", { "aria-label": "Collègue à ajouter" }, ...free.map((u) => h("option", { value: u.id }, u.name || u.id)));
    list.append(h("div", { class: "team-add" }, who, h("button", { type: "button", class: "btn small", on: { click: () =>
      save([...plain, { id: who.value, role: "membre" }]) } }, "Ajouter au projet")));
  } catch (e) { list.replaceChildren(h("div", { class: "line err" }, e.message)); }
}

function projectList(host, ws, redraw) {
  host.replaceChildren(h("div", { class: "empty-row" }, "Lecture des projets d'équipe…"));
  api("/api/hub/projects").then((res) => {
    if (!host.isConnected) return;
    const projects = res.projects || [];
    if (!projects.length) {
      host.replaceChildren(h("div", { class: "empty-row" }, "Aucun projet d'équipe pour ce compte."));
      return;
    }
    host.replaceChildren(h("h4", { class: "team-title" }, "Projets d'équipe"), h("div", { class: "drawer-list flat" }, ...projects.map((p) => {
      const mine = p.folder && same(p.folder, ws.folder);
      const join = h("button", { type: "button", class: "btn small", disabled: !!p.folder, on: { click: async () => {
        const folder = await pickFolder({ title: `Dossier local de « ${p.name} »`, start: ws.folder || "" });
        if (!folder) return;
        join.disabled = true;
        try {
          const joined = await api("/api/hub/join", { method: "POST", body: { team_id: p.id, folder } });
          syncToast(p.name, joined.syncthing);
          redraw();
        } catch (e) { toast(e.message, "err"); join.disabled = false; }
      } } }, p.folder ? (mine ? "Ce dossier" : "Déjà relié") : "Rejoindre");
      return h("div", { class: "hrow", style: { "--pc": p.color || "var(--accent)" } },
        h("div", { class: "hm" }, h("div", { class: "ht" }, p.name),
          h("div", { class: "hs" }, [p.role, p.preset, p.folder || "pas encore relié sur ce poste"].filter(Boolean).join(" · "))),
        join);
    })));
  }).catch((e) => { if (host.isConnected) host.replaceChildren(h("div", { class: "line err" }, e.message)); });
}

function syncToast(name, sync = {}) {
  toast(sync.ok ? `« ${name} » est partagé, la synchro est lancée.` : `« ${name} » est relié. Synchro : ${sync.error || "non lancée"}.`,
    sync.ok ? "ok" : "warn");
}

function presence(body, ws, ctx) {
  const line = h("p", { class: "pj-lead pad" }, "");
  body.append(line);
  const team = teamId(ws, ctx);
  if (!team) { line.textContent = "Relie ce dossier à un projet d'équipe pour voir qui y travaille."; return; }
  api("/api/hub/presence").then((res) => {
    const people = (res.presence || []).filter((p) => p.project === team);
    if (!people.length) { line.textContent = "Personne d'autre n'est dans ce projet en ce moment."; return; }
    line.textContent = people.map((p) => {
      const files = (p.files || []).slice(0, 2).join(", ");
      const work = p.sessions ? "travaille ici" : "est connecté";
      return files ? `${p.user} ${work} · modifie ${files}` : `${p.user} ${work}`;
    }).join(" — ");
  }).catch(() => { line.textContent = ""; });
}

export function renderPartage(body, ws, ctx) {
  const team = teamId(ws, ctx);
  const host = h("div", { class: "empty-row" }, team ? "Lecture des conversations…" : "Ce dossier n'est pas un projet d'équipe.");
  body.append(host);
  if (!team) return;
  api(`/api/hub/conversations?project=${encodeURIComponent(team)}`).then((res) => {
    if (!host.isConnected) return;
    const rows = res.conversations || [];
    if (!rows.length) { host.textContent = "Aucune conversation partagée."; return; }
    host.replaceWith(h("div", { class: "drawer-list flat" }, ...rows.map((c) => h("div", { class: "hrow" },
      h("div", { class: "hm" }, h("div", { class: "ht" }, c.title),
        h("div", { class: "hs" }, `${c.author} · révision ${c.rev} · ${c.mode === "stripped" ? "sans résultats d'outils" : "complète"} · ${fmtDate(c.updated)}`)),
      h("button", { type: "button", class: "btn small ghost", on: { click: () => resumeShared(c.id, ws.profile, ws.folder, c.title, ctx) } }, "Reprendre")))));
  }).catch((e) => { if (host.isConnected) host.textContent = e.message; });
}

export function renderFil(body, ws, ctx) {
  const team = teamId(ws, ctx);
  const list = h("div", { class: "drawer-list flat" });
  const input = h("textarea", { class: "pj-text", rows: "3", placeholder: "Message pour l'équipe" });
  const send = h("button", { type: "button", class: "btn small primary", disabled: !team, on: { click: async () => {
    const text = input.value.trim();
    if (!text) return;
    try {
      await api("/api/hub/messages", { method: "POST", body: { project: team, text } });
      input.value = "";
      renderFil(body, ws, ctx);
    } catch (e) { toast(e.message, "err"); }
  } } }, "Envoyer");
  body.replaceChildren(list, h("div", { class: "team-add" }, input, send));
  if (!team) { list.append(h("div", { class: "empty-row" }, "Ce dossier n'est pas un projet d'équipe.")); return; }
  // The thread reads itself again while it is on screen.
  let seen = "";
  const read = () => api(`/api/hub/messages?project=${encodeURIComponent(team)}`).then((res) => {
    if (!list.isConnected) return;
    const rows = res.messages || [];
    const stamp = rows.map((m) => `${m.id}:${m.edited || ""}:${m.deleted || ""}`).join();
    if (stamp === seen) return;
    seen = stamp;
    list.replaceChildren(...(rows.length ? rows.map((m) => h("div", { class: "hrow" },
      h("div", { class: "hm" }, h("div", { class: "ht wrap" }, m.deleted ? "Message effacé" : m.text),
        h("div", { class: "hs" }, `${m.author} · ${fmtDate(m.ts)}`)))) : [h("div", { class: "empty-row" }, "Aucun message.")]));
    list.lastElementChild?.scrollIntoView?.({ block: "nearest" });
  }).catch((e) => { if (list.isConnected) list.replaceChildren(h("div", { class: "line err" }, e.message)); });
  read();
  const timer = setInterval(() => {
    if (!list.isConnected) { clearInterval(timer); return; }
    if (!document.hidden) read();
  }, 15000);
}

export async function shareDiscussion(profile, session, folder, title) {
  const preview = await api(`/api/hub/share/preview?${new URLSearchParams({ profile, session, folder })}`);
  const mode = await dialog({
    title: `Partager « ${title || preview.title} »`,
    body: h("div", {}, h("p", {}, preview.summary),
      h("p", { class: "muted" }, "Les chemins de ce dossier deviennent {{PROJET}}. Chaque collègue reprend la discussion avec son propre compte Claude.")),
    buttons: [
      { label: "Annuler", value: null },
      { label: "Sans résultats d'outils", value: "stripped", cls: preview.sensitive ? "primary" : "" },
      { label: "Complète", value: "complete", cls: preview.sensitive ? "" : "primary" },
    ],
  });
  if (mode !== "stripped" && mode !== "complete") return;
  await api("/api/hub/share", { method: "POST", body: { profile, session, folder, mode } });
  toast("Conversation partagée.", "ok");
}

export async function resumeShared(conversation, profile, folder, title, ctx) {
  const text = await dialog({
    title: `Reprendre « ${title || "cette conversation"} »`,
    body: "La discussion arrive dans ce dossier. La suite part avec ton compte Claude.",
    input: { placeholder: "Message pour la continuer" },
    buttons: [{ label: "Annuler", value: null }, { label: "Reprendre", value: true, cls: "primary" }],
  });
  if (text == null) return;
  const task = await api("/api/hub/resume", { method: "POST", body: {
    conversation, profile, folder, prompt: String(text).trim() || "Reprise de la conversation partagée.",
  } });
  toast("Discussion reprise.", "ok");
  if (task?.id) ctx?.openTask?.(task.id);
}

function teamId(ws, ctx) {
  return ctx?.project?.(ws.folder)?.team_id || "";
}

function same(a, b) {
  return String(a || "").replace(/[\\/]+$/, "").toLowerCase() === String(b || "").replace(/[\\/]+$/, "").toLowerCase();
}

export { pretty };
