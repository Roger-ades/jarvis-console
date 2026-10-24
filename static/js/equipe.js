// Team projects: the page talks only to this console. The hub token stays on the server.
import { api } from "./api.js";
import { pickFolder } from "./folderpicker.js";
import { offInbox, onInbox, teamNews } from "./inbox.js";
import { confirmDialog, dialog, fmtDate, h, modalHost, toast } from "./util.js";

const pretty = (fp) => {
  const hex = String(fp || "").replace(/[^0-9a-f]/gi, "").toUpperCase();
  return (hex.match(/.{2}/g) || []).join(":") || "";
};

// The hub's colours for its accounts (hub/db.py, COLORS).
const PEOPLE_COLORS = ["#40dcff", "#7dffb3", "#ffb020", "#ff7ad9", "#c9a6ff", "#ff8f6b"];
const PSEUDO = /^[a-z0-9][a-z0-9-]{0,40}$/;

/** At start: a poste without a hub account gets one, the hub's address being written in JARVIS (docs/equipe.md).
 * Out of reach (no VPN), nothing opens: the next start asks again. Resolves true once registered. */
export async function offerSignup() {
  let st;
  try { st = await api("/api/hub/signup"); } catch { return false; }
  if (!st.needed || !st.reachable) return false;
  return new Promise((resolve) => {
    const { root } = modalHost("dialog");
    const toSlug = (v) => v.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 41);
    const name = h("input", { type: "text", value: st.suggest?.name || "", maxlength: "40", placeholder: "Paul Martin", "aria-label": "Nom affiché" });
    const pseudo = h("input", { type: "text", value: st.suggest?.id || "", maxlength: "41", placeholder: "paul", "aria-label": "Pseudo",
      autocapitalize: "off", spellcheck: "false" });
    let typed = false;   // the pseudo follows the name until the person writes their own
    let color = PEOPLE_COLORS[0];
    const dots = PEOPLE_COLORS.map((c) => h("button", { type: "button", class: "su-color", style: { "--c": c }, title: c, "aria-label": `Couleur ${c}`,
      on: { click: () => { color = c; paintDots(); } } }));
    const paintDots = () => dots.forEach((d, i) => d.setAttribute("aria-pressed", String(PEOPLE_COLORS[i] === color)));
    paintDots();
    const error = h("p", { class: "su-error", role: "alert", hidden: true });
    const later = h("button", { type: "button", class: "btn ghost", on: { click: () => close(false) } }, "Plus tard");
    const ok = h("button", { type: "button", class: "btn primary", on: { click: send } }, "M'inscrire");
    const box = h("div", { class: "dialog su-signup", role: "dialog", "aria-modal": "true", "aria-label": "Inscription à l'équipe" },
      h("h3", {}, "Rejoindre l'équipe"),
      h("div", { class: "dialog-body team-join" },
        h("p", { class: "muted" }, "JARVIS t'inscrit sur le serveur de l'équipe : projets partagés, fil de discussion, fichiers synchronisés. C'est tout ce qu'il y a à faire."),
        h("label", { class: "team-field" }, h("span", {}, "Nom affiché"), name),
        h("label", { class: "team-field" }, h("span", {}, "Pseudo"), pseudo,
          h("small", { class: "muted" }, "Minuscules, chiffres et tirets. Tes collègues te mentionnent avec @pseudo.")),
        h("div", { class: "team-field" }, h("span", {}, "Couleur"), h("div", { class: "su-colors" }, dots)),
        error),
      h("div", { class: "dialog-actions" }, later, ok));
    const overlay = h("div", { class: "overlay" }, box);
    const check = () => {
      const bad = pseudo.value && !PSEUDO.test(pseudo.value) ? "Pseudo : lettres minuscules, chiffres et tirets, sans accent ni espace." : "";
      error.textContent = bad;
      error.hidden = !bad;
      ok.disabled = !name.value.trim() || !PSEUDO.test(pseudo.value) || pseudo.value === "me";
    };
    name.addEventListener("input", () => { if (!typed) pseudo.value = toSlug(name.value); check(); });
    pseudo.addEventListener("input", () => { typed = true; pseudo.value = pseudo.value.toLowerCase(); check(); });
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(false); }
      if (e.key === "Enter" && !ok.disabled && (e.target === name || e.target === pseudo)) { e.preventDefault(); send(); }
    };
    root.ownerDocument.addEventListener("keydown", onKey, true);
    function close(v) { overlay.remove(); root.ownerDocument.removeEventListener("keydown", onKey, true); resolve(v); }
    async function send() {
      if (ok.disabled) return;
      ok.disabled = later.disabled = true;
      ok.textContent = "Inscription…";
      try {
        const res = await api("/api/hub/signup", { method: "POST", body: { id: pseudo.value, name: name.value.trim(), color } });
        close(true);
        toast(`Bienvenue dans l'équipe, ${res.user?.name || name.value.trim()} ! Tes projets d'équipe arriveront dans ta boîte de réception.`, "ok");
      } catch (e) {
        ok.textContent = "M'inscrire";
        later.disabled = false;
        check();
        error.textContent = e.message;
        error.hidden = false;
        if (/pseudo/i.test(e.message)) { pseudo.focus(); pseudo.select(); }
      }
    }
    root.append(overlay);
    check();
    (name.value ? pseudo : name).focus();
  });
}

/** The Membres screen of a team project (who is here, who belongs, the sync), or how a personal project becomes one.
 * The other team projects are not listed here: they are joined from the home screen (joinTeamProject). */
export function renderEquipe(body, ws, ctx) {
  const redraw = () => { body.replaceChildren(); renderEquipe(body, ws, ctx); };
  const head = h("div", { class: "team-sec" }, h("div", { class: "empty-row" }, "Lecture de l'équipe…"));
  body.append(head);
  api("/api/hub/status").then((st) => {
    if (!head.isConnected) return;
    if (!st.enabled || !st.has_token || st.error || !st.user) {
      head.replaceChildren(invite(st.error ? "Hub injoignable" : "Hub pas encore relié", st.error
        ? `${st.error}. Le projet reste utilisable sur ce poste.`
        : "Réglages › Équipe : adresse du hub, empreinte et jeton."));
      return;
    }
    head.replaceChildren(...here(ws, ctx, st.user, redraw, (nodes) => head.replaceChildren(...nodes)));
  }).catch((e) => { if (head.isConnected) head.replaceChildren(h("div", { class: "line err" }, e.message)); });
}

const invite = (title, text, ...actions) => h("div", { class: "team-invite" }, h("span", { class: "i", svg: "team" }),
  h("div", { class: "hm" }, h("b", {}, title), h("p", { class: "pj-lead" }, text),
    actions.length ? h("div", { class: "row" }, ...actions) : null));

// What this folder is to the team: a team project (who is here, its members, the sync), a project to share, or nothing yet.
function here(ws, ctx, user, redraw, replace) {
  const proj = ctx?.project?.(ws.folder);
  const team = proj?.team_id || "";
  if (team) {
    if (teamNews(team).removed) return lost(ws, proj);
    const who = h("div", { class: "team-who" }, h("span", { class: "muted" }, "Lecture de la présence…"));
    presence(who, team, user);
    const members = h("div", { class: "drawer-list flat team-members" }, h("div", { class: "empty-row" }, "Lecture des membres…"));
    const foot = h("div", { class: "team-foot" }, h("span", { class: "muted" }, "Les fichiers de ce dossier sont synchronisés avec le serveur d'équipe."));
    loadMembers({ list: members, foot, team, user, ws, proj, gone: () => replace(lost(ws, proj)) });
    // removed while the screen is open: the inbox learns it first
    const onNews = () => {
      if (!members.isConnected) { offInbox(onNews); return; }
      if (teamNews(team).removed) { offInbox(onNews); replace(lost(ws, proj)); }
    };
    onInbox(onNews);
    return [
      h("h4", { class: "team-title" }, "En ce moment"), who,
      h("h4", { class: "team-title" }, "Membres"), members, foot,
    ];
  }
  if (!proj) return [invite("Pas encore un projet", "Donne d'abord un nom à ce dossier (« En faire un projet », en haut) ; il pourra ensuite être partagé.")];
  const join = h("button", { type: "button", class: "btn small", on: { click: () => joinTeamProject(ctx, { start: ws.folder }) } },
    "Rejoindre un projet d'équipe…");
  if (!user.admin) {
    return [invite("Projet personnel", "Ce projet reste sur ce poste. Seul l'administrateur du hub en fait un projet d'équipe ; "
      + "un projet déjà partagé se rejoint ici ou depuis l'accueil.", join)];
  }
  const create = h("button", { type: "button", class: "btn small primary", on: { click: async () => {
    create.disabled = true;
    try {
      const made = await shareProject(ws, proj, user);
      if (made) redraw();
    } finally { create.disabled = false; }
  } } }, "Partager avec l'équipe…");
  return [invite("Projet personnel", "Ce projet reste sur ce poste. Partagé, ses fichiers sont synchronisés avec le serveur "
    + "et ses membres y ont un fil et des conversations. Tu choisis qui en fait partie.", create, join)];
}

/** The project becomes a team project: its name on the hub, and who works on it (not necessarily everyone). */
async function shareProject(ws, proj, user) {
  let users = [], mine = [];
  try {
    [users, mine] = await Promise.all([people(true).then((w) => w.list), api("/api/hub/projects").then((r) => r.projects || [])]);
  } catch (e) { toast(e.message, "err"); return false; }
  // a team project of the same name is already there for this account (this folder was detached from it, say):
  // joining it again beats a second project of the same name
  const key = (s) => String(s || "").trim().toLowerCase();
  const same = mine.find((p) => !p.folder && key(p.name) === key(proj.name));
  if (same) {
    const pick = await dialog({
      title: `« ${same.name} » existe déjà`,
      body: h("p", {}, `Tu fais déjà partie du projet d'équipe « ${same.name} » (${same.member_count || 1} membre${(same.member_count || 1) > 1 ? "s" : ""}). `
        + "Ce dossier peut le rejoindre : ses fichiers se synchronisent avec ceux de l'équipe."),
      buttons: [{ label: "Annuler", value: null }, { label: "Créer un autre projet", value: "new" }, { label: "Rejoindre ce projet", value: "join", cls: "primary" }],
    });
    if (!pick) return false;
    if (pick === "join") {
      try {
        const joined = await api("/api/hub/join", { method: "POST", body: { team_id: same.id, folder: ws.folder } });
        syncToast(same.name, joined.syncthing);
        return true;
      } catch (e) { toast(e.message, "err"); return false; }
    }
  }
  const picker = memberPicker(users.filter((u) => u.id !== user.id));
  const name = h("input", { type: "text", value: proj.name, placeholder: "Nom du projet d'équipe", "aria-label": "Nom du projet d'équipe", maxlength: "80" });
  let ok = null;
  const value = await dialog({
    title: "Partager ce projet avec l'équipe",
    body: h("div", { class: "team-join" },
      h("p", { class: "muted" }, "Le hub crée le projet d'équipe et Syncthing synchronise ce dossier avec le serveur. Tu en es l'administrateur."),
      h("label", { class: "team-field" }, h("span", {}, "Nom"), name),
      h("div", { class: "team-field" }, h("span", {}, "Membres"), picker.node),
      h("p", { class: "muted team-note" }, "Chacun le trouve dans sa boîte de réception et le rejoint d'un clic. La liste se modifie ensuite dans l'onglet Membres.")),
    buttons: [{ label: "Annuler", value: null }, { label: "Créer le projet d'équipe", value: "create", cls: "primary" }],
    onOpen: (box) => {
      ok = box.querySelector(".dialog-actions .primary");
      const check = () => { ok.disabled = !name.value.trim(); };
      name.addEventListener("input", check);
      name.addEventListener("keydown", (e) => { if (e.key === "Enter" && !ok.disabled) { e.preventDefault(); ok.click(); } });
      check();
      name.focus();
      name.select();
    },
  });
  if (value !== "create" || !name.value.trim()) return false;
  try {
    const res = await api("/api/hub/create", { method: "POST", body: { folder: ws.folder, name: name.value.trim(), members: picker.value() } });
    syncToast(name.value.trim(), res.syncthing);
    return true;
  } catch (e) { toast(e.message, "err"); return false; }
}

/** The hub's accounts, to pick members and to put names on ids (kept a minute). */
let peopleAsk = null, peopleAt = 0;
function people(fresh = false) {
  if (fresh || !peopleAsk || Date.now() - peopleAt > 60_000) {
    peopleAt = Date.now();
    peopleAsk = api("/api/hub/users").then((res) => {
      const list = res.users || [];
      return { list, byId: new Map(list.map((u) => [u.id, u])) };
    });
    peopleAsk.catch(() => { peopleAsk = null; });
  }
  return peopleAsk;
}
const named = (who, id) => who?.byId?.get(id)?.name || id || "";

const avatar = (u) => h("span", { class: "team-av", "aria-hidden": "true", style: u?.color ? { "--uc": u.color } : {} },
  initials(u?.name || u?.id));

/** Colleagues to pick: a box each and the role beside it, "Toute l'équipe" at the top, a search past 8 accounts. */
function memberPicker(users) {
  const picked = new Map();   // id -> role
  const search = h("input", { type: "search", placeholder: "Chercher un collègue", "aria-label": "Chercher un collègue",
    on: { input: () => paint() } });
  const all = h("input", { type: "checkbox", on: { change: () => {
    for (const u of shown()) { if (all.checked) picked.set(u.id, picked.get(u.id) || "membre"); else picked.delete(u.id); }
    paint();
  } } });
  const allText = h("span", {}, "Toute l'équipe");
  const count = h("span", { class: "muted" });
  const list = h("div", { class: "team-pick team-people", role: "group", "aria-label": "Collègues" });
  const shown = () => {
    const f = search.value.trim().toLowerCase();
    return users.filter((u) => !f || `${u.name} ${u.id}`.toLowerCase().includes(f));
  };
  function paint() {
    const rows = shown();
    all.checked = rows.length > 0 && rows.every((u) => picked.has(u.id));
    all.indeterminate = !all.checked && rows.some((u) => picked.has(u.id));
    allText.textContent = search.value.trim() ? "Tous ceux-ci" : "Toute l'équipe";
    count.textContent = picked.size ? `${picked.size} choisi${picked.size > 1 ? "s" : ""}` : "Personne pour l'instant";
    list.replaceChildren(...(rows.length ? rows.map((u) => {
      const on = picked.has(u.id);
      const toggle = (v) => { if (v) picked.set(u.id, role.value); else picked.delete(u.id); paint(); };
      const box = h("input", { type: "checkbox", checked: on, "aria-label": u.name || u.id, on: { change: () => toggle(box.checked) } });
      const role = h("select", { "aria-label": `Rôle de ${u.name || u.id}`, disabled: !on, on: { change: () => picked.set(u.id, role.value) } },
        ...ROLES.map(([r, label]) => h("option", { value: r, selected: (picked.get(u.id) || "membre") === r }, label)));
      return h("div", { class: `hrow team-person${on ? " on" : ""}`,
        on: { click: (e) => { if (!e.target.closest("input, select")) toggle(!picked.has(u.id)); } } },
      box, avatar(u), h("div", { class: "hm" }, h("div", { class: "ht" }, u.name || u.id),
        u.name && u.name !== u.id ? h("div", { class: "hs" }, u.id) : null), role);
    }) : [h("div", { class: "empty-row" }, users.length ? "Personne ne correspond." : "Aucun autre compte sur le hub.")]));
  }
  paint();
  const node = h("div", { class: "team-picker" },
    users.length ? h("div", { class: "team-pick-head" }, h("label", { class: "team-all" }, all, allText), count) : null,
    users.length > 8 ? search : null, list);
  return { node, value: () => [...picked].map(([id, role]) => ({ id, role })) };
}
const ROLES = [["membre", "membre"], ["administrateur", "administrateur"]];

const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "");

/** The Membres list: names, roles, who added whom; the project's administrators (and the hub's) change it.
 * The project always keeps an administrator: the hub refuses otherwise, the page says so first. */
async function loadMembers(o) {
  const { list, foot, team, user, ws, proj, gone } = o;
  let members, who;
  try {
    [{ members = [] }, who] = await Promise.all([
      api(`/api/hub/members?project=${encodeURIComponent(team)}`), people().catch(() => null)]);
  } catch (e) {
    if (e.status === 404) { gone(); return; }
    list.replaceChildren(h("div", { class: "line err" }, e.message));
    return;
  }
  if (!list.isConnected) return;
  const mine = members.find((m) => m.id === user.id);
  if (!mine) { gone(); return; }
  const manage = user.admin || mine.role === "administrateur";
  const admins = members.filter((m) => m.role === "administrateur");
  const reload = () => loadMembers(o);
  const save = async (next, done) => {
    list.classList.add("busy");
    try {
      const res = await api("/api/hub/members", { method: "PUT", body: { project: team, members: next } });
      if (res.sync_errors?.length) toast(`${done || "Membres enregistrés"}. Synchro du serveur : ${res.sync_errors.join(" ; ")}`, "warn");
      else if (done) toast(done, "ok");
    } catch (e) { toast(e.message, "err"); }
    list.classList.remove("busy");
    reload();
  };
  const plain = members.map((m) => ({ id: m.id, role: m.role }));
  const nameOf = (m) => m.name || named(who, m.id);
  const rows = members.map((m) => {
    const me = m.id === user.id;
    const lastAdmin = m.role === "administrateur" && admins.length === 1;
    let roleSel;
    if (manage) {
      roleSel = h("select", { "aria-label": `Rôle de ${nameOf(m)}`, disabled: lastAdmin,
        title: lastAdmin ? "Seul administrateur du projet : nomme d'abord quelqu'un d'autre" : "",
        on: { change: async () => {
          if (me && !user.admin && roleSel.value !== "administrateur"
            && !(await confirmDialog("Ne plus être administrateur ?", "Tu ne pourras plus gérer les membres de ce projet.", "Confirmer"))) {
            roleSel.value = m.role;
            return;
          }
          save(plain.map((x) => (x.id === m.id ? { id: x.id, role: roleSel.value } : x)));
        } } }, ...ROLES.map(([r, label]) => h("option", { value: r, selected: r === m.role }, label)));
    } else roleSel = h("span", { class: `team-role${m.role === "administrateur" ? " admin" : ""}` }, m.role);
    const out = manage && !me ? h("button", { type: "button", class: "btn small ghost", disabled: lastAdmin,
      title: lastAdmin ? "Seul administrateur du projet" : "Retirer du projet", on: { click: async () => {
        if (!(await confirmDialog(`Retirer ${nameOf(m)} ?`,
          `${nameOf(m)} ne verra plus le fil ni les conversations de « ${proj.name} » et ses postes cessent de synchroniser le dossier (les fichiers déjà chez lui restent).`,
          "Retirer", "danger"))) return;
        save(plain.filter((x) => x.id !== m.id), `${nameOf(m)} ne fait plus partie du projet.`);
      } } }, "Retirer")
      : manage ? h("span", { class: "btn small ghost team-gap", "aria-hidden": "true" }, "Retirer") : null;  // my row stays aligned
    const by = m.added_by && m.added_by !== m.id ? named(who, m.added_by) : "";
    return h("div", { class: `hrow team-member${me ? " me" : ""}` }, avatar({ ...m, name: nameOf(m) }),
      h("div", { class: "hm" }, h("div", { class: "ht" }, nameOf(m), me ? h("span", { class: "muted" }, " · toi") : null),
        h("div", { class: "hs" }, by ? `ajouté par ${m.added_by === user.id ? "toi" : by} le ${day(m.joined)}` : `depuis le ${day(m.joined)}`)),
      roleSel, out);
  });
  const free = (who?.list || []).filter((u) => !members.some((m) => m.id === u.id));
  const add = manage ? h("button", { type: "button", class: "btn small", disabled: who && !free.length,
    title: who && !free.length ? "Tous les comptes du hub sont déjà membres" : "", on: { click: async () => {
      const picker = memberPicker(free);
      const go = await dialog({
        title: `Ajouter à « ${proj.name} »`,
        body: h("div", { class: "team-join" }, picker.node,
          h("p", { class: "muted team-note" }, "Ils le trouvent dans leur boîte de réception et le rejoignent d'un clic.")),
        buttons: [{ label: "Annuler", value: null }, { label: "Ajouter", value: "add", cls: "primary" }],
      });
      const picked = picker.value();
      if (go !== "add" || !picked.length) return;
      save([...plain, ...picked], picked.length > 1 ? `${picked.length} collègues ajoutés.` : `${named(who, picked[0].id)} fait partie du projet.`);
    } } }, "Ajouter des collègues…") : null;
  list.replaceChildren(...rows, add ? h("div", { class: "team-add" }, add,
    h("span", { class: "muted" }, `${members.length} membre${members.length > 1 ? "s" : ""}`)) : null);

  // the foot: the sync, and leaving (the hub) or detaching (this computer only)
  const sync = h("button", { type: "button", class: "btn small ghost", title: "Relier à nouveau ce dossier au serveur (Syncthing)", on: { click: async () => {
    sync.disabled = true;
    try {
      const res = await api("/api/hub/syncthing", { method: "POST" });
      const it = (res.projects || {})[team] || {};
      toast(it.ok ? "Synchro en place pour ce projet." : (it.error || "Synchro non relancée."), it.ok ? "ok" : "warn");
    } catch (e) { toast(e.message, "err"); }
    sync.disabled = false;
  } } }, "Relancer la synchro");
  const leave = h("button", { type: "button", class: "btn small danger", on: { click: async () => {
    if (mine.role === "administrateur" && admins.length === 1 && members.length > 1) {
      toast("Tu es le seul administrateur : nomme d'abord quelqu'un d'autre administrateur, puis quitte le projet.", "warn");
      return;
    }
    if (members.length === 1) {
      toast("Tu es le seul membre : l'administrateur du hub peut supprimer le projet, ou détache-le de ce poste.", "warn");
      return;
    }
    if (!(await confirmDialog(`Quitter « ${proj.name} » ?`,
      "Tu ne verras plus son fil ni ses conversations, et ce poste arrête de synchroniser le dossier. Les fichiers déjà là restent : le dossier redevient un projet personnel.",
      "Quitter le projet", "danger"))) return;
    leave.disabled = true;
    try {
      const res = await api("/api/hub/leave", { method: "POST", body: { folder: ws.folder } });
      toast(res.warning ? `Tu as quitté « ${proj.name} ». ${res.warning}` : `Tu as quitté « ${proj.name} ».`, res.warning ? "warn" : "ok");
    } catch (e) { toast(e.message, "err"); leave.disabled = false; }
  } } }, "Quitter le projet…");
  foot.replaceChildren(h("span", { class: "muted" }, "Les fichiers de ce dossier sont synchronisés avec le serveur d'équipe."),
    h("span", { class: "row" }, sync, detachButton(ws, proj, "Détacher de ce poste…", "ghost"), leave));
}

/** This computer stops syncing the folder; the account stays a member (or no longer is): the files stay. */
function detachButton(ws, proj, label, cls, removed = false) {
  const b = h("button", { type: "button", class: `btn small ${cls}`, title: "Ce poste arrête de synchroniser le dossier ; ses fichiers restent", on: { click: async () => {
    if (!(await confirmDialog("Détacher le dossier ?",
      `« ${proj.name} » redevient un projet personnel sur ce poste : ses fichiers restent, ils ne se synchronisent plus avec l'équipe.`
      + (removed ? "" : " Tant que tu en es membre, tu peux le rejoindre à nouveau depuis l'accueil."), "Détacher"))) return;
    b.disabled = true;
    try {
      const res = await api("/api/hub/unlink", { method: "POST", body: { folder: ws.folder } });
      toast(res.warning ? `« ${proj.name} » est détaché. ${res.warning}` : `« ${proj.name} » est redevenu un projet personnel.`, res.warning ? "warn" : "ok");
    } catch (e) { toast(e.message, "err"); b.disabled = false; }
  } } }, label);
  return b;
}

/** The account was removed from the project: nothing syncs any more, the folder can be detached. */
function lost(ws, proj) {
  return [invite("Tu ne fais plus partie de ce projet", "Un administrateur t'a retiré de ce projet d'équipe : ses fichiers ne se synchronisent "
    + "plus avec ce poste, son fil et ses conversations ne te sont plus ouverts. Détache le dossier pour en refaire un projet personnel (les fichiers restent).",
  detachButton(ws, proj, "Détacher le dossier…", "primary", true))];
}

const initials = (name) => String(name || "?").split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

/** The team projects of this account not yet on this computer: pick one, then its local folder.
 * start: where the folder picker opens. */
export async function joinTeamProject(ctx, { start = "", select = "" } = {}) {
  let projects, who;
  try { [projects, who] = await Promise.all([api("/api/hub/projects").then((r) => r.projects || []), people().catch(() => null)]); }
  catch (e) { toast(e.message, "err"); return; }
  const free = projects.filter((p) => !p.folder);
  if (!free.length) {
    toast(projects.length ? "Tous tes projets d'équipe sont déjà sur ce poste." : "Aucun projet d'équipe pour ce compte.", "ok");
    return;
  }
  if (select && !free.some((p) => p.id === select)) {
    toast(projects.some((p) => p.id === select) ? "Ce projet d'équipe est déjà sur ce poste." : "Tu ne fais plus partie de ce projet d'équipe.", "warn");
    return;
  }
  let chosen = select || free[0].id;
  let joinBtn = null;
  const pick = (id) => { chosen = id; paintList(search.value); };
  const list = h("div", { class: "team-pick", role: "listbox", "aria-label": "Projets d'équipe" });
  const search = h("input", { type: "text", placeholder: "Chercher un projet", "aria-label": "Chercher un projet d'équipe",
    on: { input: () => paintList(search.value) } });
  function paintList(q = "") {
    const f = q.trim().toLowerCase();
    const shown = free.filter((p) => !f || `${p.name} ${p.id}`.toLowerCase().includes(f));
    if (!shown.some((p) => p.id === chosen)) chosen = shown[0]?.id || "";
    if (joinBtn) joinBtn.disabled = !chosen;
    list.replaceChildren(...(shown.length ? shown.map((p) => h("div", {
      class: `hrow${p.id === chosen ? " on" : ""}`, role: "option", "aria-selected": String(p.id === chosen), tabindex: "0",
      style: { "--pc": p.color || "var(--accent)" },
      on: { click: () => pick(p.id), dblclick: () => { pick(p.id); joinBtn?.click(); },
        keydown: (e) => { if (e.key === " ") { e.preventDefault(); pick(p.id); } } } },
      h("div", { class: "hm" }, h("div", { class: "ht" }, p.name),
        h("div", { class: "hs" }, [p.added_by ? `ajouté par ${named(who, p.added_by)}` : "",
          p.member_count ? `${p.member_count} membre${p.member_count > 1 ? "s" : ""}` : "", p.role].filter(Boolean).join(" · ") || p.id)))) : [h("div", { class: "empty-row" }, "Aucun projet ne correspond.")]));
  }
  paintList();
  const id = await dialog({
    title: "Rejoindre un projet d'équipe",
    body: h("div", { class: "team-join" },
      h("p", { class: "muted" }, "Choisis le projet, puis le dossier de ce poste où ses fichiers arrivent (un dossier vide, ou celui que Syncthing remplit déjà)."),
      free.length > 6 ? search : null, list),
    buttons: [{ label: "Annuler", value: null }, { label: "Choisir le dossier…", value: "join", cls: "primary" }],
    onOpen: (box) => {
      joinBtn = box.querySelector(".dialog-actions .primary");
      joinBtn.disabled = !chosen;
      if (free.length > 6) search.focus();
    },
  });
  const p = id === "join" && free.find((x) => x.id === chosen);
  if (!p) return;
  const folder = await pickFolder({ title: `Dossier local de « ${p.name} »`, start });
  if (!folder) return;
  try {
    const joined = await api("/api/hub/join", { method: "POST", body: { team_id: p.id, folder } });
    syncToast(p.name, joined.syncthing);
    ctx?.joinedTeam?.(joined.folder || folder);
  } catch (e) { toast(e.message, "err"); }
}

function syncToast(name, sync = {}) {
  toast(sync.ok ? `« ${name} » est partagé, la synchro est lancée.` : `« ${name} » est relié. Synchro : ${sync.error || "non lancée"}.`,
    sync.ok ? "ok" : "warn");
}

/** Who is in this team project right now, and what they change. */
function presence(box, team, user) {
  Promise.all([api("/api/hub/presence"), people().catch(() => null)]).then(([res, who]) => {
    if (!box.isConnected) return;
    const here = (res.presence || []).filter((p) => p.project === team && p.user !== user?.id);
    if (!here.length) { box.replaceChildren(h("span", { class: "muted" }, "Personne d'autre n'est dans ce projet en ce moment.")); return; }
    box.replaceChildren(...here.map((p) => {
      const files = (p.files || []).slice(0, 2).join(", ");
      const u = who?.byId.get(p.user) || { id: p.user, name: p.user };
      return h("span", { class: `team-here${p.sessions ? " busy" : ""}`, title: files ? `Modifie ${files}` : "" },
        avatar(u), h("span", { class: "hm" }, h("b", {}, u.name || p.user), ` ${p.sessions ? "travaille ici" : "est connecté"}`,
          files ? h("small", {}, ` · ${files}`) : null));
    }));
  }).catch(() => { if (box.isConnected) box.replaceChildren(); });
}

export function renderPartage(body, ws, ctx) {
  const team = teamId(ws, ctx);
  const host = h("div", { class: "empty-row" }, team ? "Lecture des conversations…" : "Ce dossier n'est pas un projet d'équipe.");
  body.append(host);
  if (!team) return;
  if (teamNews(team).removed) { host.replaceWith(...lost(ws, ctx?.project?.(ws.folder))); return; }
  Promise.all([api(`/api/hub/conversations?project=${encodeURIComponent(team)}`), people().catch(() => null)]).then(([res, who]) => {
    if (!host.isConnected) return;
    const rows = res.conversations || [];
    if (!rows.length) { host.textContent = "Aucune conversation partagée. « Partager » dans une discussion du projet la publie ici."; return; }
    const latest = Math.max(...rows.map((c) => c.updated || 0));
    if (teamNews(team).partage) seen(team, { conv: latest });
    host.replaceWith(h("div", { class: "drawer-list flat" }, ...rows.map((c) => h("div", { class: "hrow" },
      h("div", { class: "hm" }, h("div", { class: "ht" }, c.title),
        h("div", { class: "hs" }, `${named(who, c.author)} · révision ${c.rev} · ${c.mode === "stripped" ? "sans résultats d'outils" : "complète"} · ${fmtDate(c.updated)}`)),
      h("button", { type: "button", class: "btn small ghost", on: { click: () => resumeShared(c.id, ws.profile, ws.folder, c.title, ctx) } }, "Reprendre")))));
  }).catch((e) => {
    if (!host.isConnected) return;
    if (e.status === 403 || e.status === 404) host.replaceWith(...lost(ws, ctx?.project?.(ws.folder)));
    else host.textContent = e.message;
  });
}

export function renderFil(body, ws, ctx) {
  const team = teamId(ws, ctx);
  const list = h("div", { class: "drawer-list flat" });
  const input = h("textarea", { class: "pj-text", rows: "3", placeholder: "Message pour l'équipe (@nom pour mentionner quelqu'un)",
    on: { keydown: (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send.click(); } } } });
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
  const gone = () => body.replaceChildren(...lost(ws, ctx?.project?.(ws.folder)));
  if (teamNews(team).removed) { gone(); return; }
  // The thread reads itself again while it is on screen; what it shows is read (the inbox and the tab's count follow).
  let stampShown = "", last = 0;
  const markRead = () => { if (last && !document.hidden && teamNews(team).fil) seen(team, { msg: last }); };
  const read = () => Promise.all([api(`/api/hub/messages?project=${encodeURIComponent(team)}`), people().catch(() => null)]).then(([res, who]) => {
    if (!list.isConnected) return;
    const rows = res.messages || [];
    last = Math.max(0, ...rows.map((m) => m.ts || 0));
    markRead();
    const stamp = rows.map((m) => `${m.id}:${m.edited || ""}:${m.deleted || ""}`).join();
    if (stamp === stampShown) return;
    stampShown = stamp;
    list.replaceChildren(...(rows.length ? rows.map((m) => {
      const u = who?.byId.get(m.author) || { id: m.author, name: m.author };
      return h("div", { class: "hrow team-msg" }, avatar(u),
        h("div", { class: "hm" }, h("div", { class: "ht wrap" }, m.deleted ? h("i", { class: "muted" }, "Message effacé") : withMentions(m.text)),
          h("div", { class: "hs" }, `${u.name || m.author} · ${fmtDate(m.ts)}${m.edited ? " · modifié" : ""}`)));
    }) : [h("div", { class: "empty-row" }, "Aucun message. Le fil est partagé avec les membres du projet.")]));
    list.lastElementChild?.scrollIntoView?.({ block: "nearest" });
  }).catch((e) => {
    if (!list.isConnected) return;
    if (e.status === 403 || e.status === 404) gone();
    else list.replaceChildren(h("div", { class: "line err" }, e.message));
  });
  read();
  // the inbox may learn of a message after this page showed it
  const onNews = () => { if (!list.isConnected) { offInbox(onNews); return; } markRead(); };
  onInbox(onNews);
  const timer = setInterval(() => {
    if (!list.isConnected) { clearInterval(timer); offInbox(onNews); return; }
    if (!document.hidden) read();
  }, 15000);
}

/** @name in a message stands out. */
function withMentions(text) {
  const parts = String(text || "").split(/(@[\w.-]*\w)/u);
  return h("span", {}, ...parts.map((p, i) => (i % 2 ? h("b", { class: "team-at" }, p) : p)));
}

/** The page showed the Fil (msg) or the conversations (conv) up to there: read, in the inbox too. */
function seen(team, upto) {
  api("/api/hub/seen", { method: "POST", body: { project: team, ...upto } }).catch(() => {});
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

export { pretty };
