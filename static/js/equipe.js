// Team projects: the page talks only to this console. The hub token stays on the server.
import { api } from "./api.js";
import { pickFolder } from "./folderpicker.js";
import { dialog, fmtDate, h, toast } from "./util.js";

const pretty = (fp) => {
  const hex = String(fp || "").replace(/[^0-9a-f]/gi, "").toUpperCase();
  return (hex.match(/.{2}/g) || []).join(":") || "";
};

export function renderEquipe(body, ws, ctx) {
  const host = h("div", { class: "empty-row" }, "Lecture de l'équipe…");
  body.append(host);
  api("/api/hub/projects").then((res) => {
    if (!host.isConnected) return;
    const projects = res.projects || [];
    if (!projects.length) {
      host.replaceWith(h("div", { class: "empty-row" }, "Aucun projet d'équipe pour ce compte."));
      return;
    }
    host.replaceWith(h("div", { class: "drawer-list flat" }, ...projects.map((p) => {
      const here = p.folder && same(p.folder, ws.folder);
      const join = h("button", { type: "button", class: "btn small", disabled: !!p.folder, on: { click: async () => {
        const folder = await pickFolder({ title: "Dossier local de ce projet" });
        if (!folder) return;
        try {
          const joined = await api("/api/hub/join", { method: "POST", body: { team_id: p.id, folder } });
          const sync = joined.syncthing || {};
          toast(sync.ok ? `« ${p.name} » rejoint, synchro lancée.` : `« ${p.name} » rejoint. ${sync.error || "Synchro non lancée."}`, sync.ok ? "ok" : "warn");
        } catch (e) { toast(e.message, "err"); }
      } } }, p.folder ? (here ? "Ce dossier" : "Déjà relié") : "Rejoindre");
      return h("div", { class: "hrow", style: { "--pc": p.color || "var(--accent)" } },
        h("div", { class: "hm" }, h("div", { class: "ht" }, p.name),
          h("div", { class: "hs" }, [p.role, p.preset, p.folder || "pas encore relié"].filter(Boolean).join(" · "))),
        join);
    })));
  }).catch((e) => { if (host.isConnected) host.textContent = e.message; });
  presence(body, ws, ctx);
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
  body.replaceChildren(list, h("div", { class: "row" }, input, send));
  if (!team) { list.append(h("div", { class: "empty-row" }, "Ce dossier n'est pas un projet d'équipe.")); return; }
  api(`/api/hub/messages?project=${encodeURIComponent(team)}`).then((res) => {
    if (!list.isConnected) return;
    const rows = res.messages || [];
    list.replaceChildren(...(rows.length ? rows.map((m) => h("div", { class: "hrow" },
      h("div", { class: "hm" }, h("div", { class: "ht" }, m.deleted ? "Message effacé" : m.text),
        h("div", { class: "hs" }, `${m.author} · ${fmtDate(m.ts)}`)))) : [h("div", { class: "empty-row" }, "Aucun message.")]));
  }).catch((e) => { if (list.isConnected) list.replaceChildren(h("div", { class: "line err" }, e.message)); });
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
