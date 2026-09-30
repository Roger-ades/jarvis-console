// Existing Claude Code sessions (Claude Desktop Code tab, CLI, console): browse, read, resume.
import { api } from "./api.js";
import { mdElement } from "./md.js";
import { confirmDialog, fmtDate, h, toast, toolIcon, toolLabel } from "./util.js";

const ORIGIN = { desktop: "Claude Desktop", cli: "CLI", console: "Console" };
let ctx = null, rows = [], q = "", profile = "", origin = "", project = "", showArchived = false, loading = false;

const el = () => document.getElementById("sessions");
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

export function toggleSessions(context) {
  ctx = context;
  const d = el();
  if (!d.hidden) { d.hidden = true; return; }
  ctx.closeDrawers?.("sessions");
  d.hidden = false;
  render();
  load();
}

let dups = [];

async function load() {
  loading = true;
  render();
  try { rows = (await api("/api/sessions")).sessions; } catch (e) { toast(e.message, "err"); }
  try { dups = (await api("/api/sessions/duplicates")).duplicates || []; } catch { dups = []; }
  loading = false;
  render();
}

/** Duplicates made by the former "move into a project": back to one session each. */
async function mergeDups() {
  const list = dups.map((d) => `• ${d.title || "(sans titre)"} — ${baseName(d.from)} → ${baseName(d.to)}`).join("\n");
  const ok = await confirmDialog("Réunir les sessions dupliquées ?",
    h("div", {}, h("p", {}, "Chaque session reprend son identifiant d'origine (celui que connaît Claude Desktop), avec toute la suite, dans le dossier du projet ; la copie disparaît."),
      h("pre", { class: "dup-list" }, list)), "Réunir");
  if (!ok) return;
  try {
    const r = await api("/api/sessions/duplicates/merge", { method: "POST", body: {} });
    toast(`${r.merged.length} session(s) réunie(s)${r.skipped.length ? ` · ${r.skipped.length} à trier à la main` : ""}.`, r.skipped.length ? "warn" : "ok");
    r.skipped.forEach((x) => toast(`${x.title || x.original} : ${x.reason}`, "warn"));
  } catch (e) { toast(e.message, "err"); }
  load();
}

function render() {
  const d = el();
  if (d.hidden) return;
  const profiles = ctx.profiles();
  const search = h("input", { type: "text", placeholder: "Rechercher un titre, une demande, un dossier…", value: q });
  search.addEventListener("input", () => { q = search.value; renderList(); });
  const sel = (value, opts, onChange) => {
    const s = h("select", {}, ...opts.map(([v, l]) => h("option", { value: v }, l)));
    s.value = value;
    s.addEventListener("change", () => onChange(s.value));
    return s;
  };
  const projects = [...new Set(rows.map((r) => r.cwd).filter(Boolean))].sort((a, b) => baseName(a).localeCompare(baseName(b)));
  const arch = h("label", { class: "check" }, h("input", { type: "checkbox" }), "archivées");
  arch.firstChild.checked = showArchived;
  arch.firstChild.addEventListener("change", () => { showArchived = arch.firstChild.checked; renderList(); });
  const list = h("div", { class: "drawer-list" });
  const count = h("span", { class: "muted" });
  function renderList() {
    const needle = q.trim().toLowerCase();
    const shown = rows.filter((r) => (!profile || r.profile === profile) && (!origin || r.origin === origin)
      && (!project || r.cwd === project) && (showArchived || !r.archived)
      && (!needle || `${r.title} ${r.first_prompt} ${r.last_prompt} ${r.cwd}`.toLowerCase().includes(needle)));
    count.textContent = loading ? "Lecture des sessions…" : `${shown.length} session${shown.length > 1 ? "s" : ""}`;
    list.replaceChildren(...(shown.length ? shown.map(row) : [h("div", { class: "empty-row" }, loading ? "…" : "Aucune session.")]));
  }
  renderList();
  d.replaceChildren(
    h("div", { class: "drawer-head" }, h("h2", {}, "Sessions Claude Code"), count,
      h("button", { type: "button", class: "icon-btn", title: "Actualiser", svg: "retry", on: { click: load } }),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: () => { d.hidden = true; } } })),
    h("div", { class: "drawer-note" }, "Sessions de l'onglet Code de Claude Desktop, de la CLI et de la console, pour chaque compte. Les conversations claude.ai, les projets et Cowork restent sur claude.ai."),
    dups.length ? h("div", { class: "drawer-note dup-note" }, h("span", {},
      `${dups.length} session${dups.length > 1 ? "s ont" : " a"} été dupliquée${dups.length > 1 ? "s" : ""} par un ancien déplacement vers un projet.`),
    h("div", { class: "row" }, h("button", { type: "button", class: "btn small primary", on: { click: mergeDups } }, "Réunir en une seule session"))) : null,
    h("div", { class: "drawer-filters" }, search,
      sel(profile, [["", "Tous les comptes"], ...profiles.map((p) => [p.id, p.name])], (v) => { profile = v; renderList(); }),
      sel(origin, [["", "Toutes origines"], ...Object.entries(ORIGIN)], (v) => { origin = v; renderList(); }),
      sel(project, [["", "Tous les dossiers"], ...projects.map((p) => [p, baseName(p)])], (v) => { project = v; renderList(); }),
      arch),
    list);
}

function row(r) {
  return h("div", { class: "hrow", style: { "--pc": r.color }, tabindex: "0",
    on: { click: () => openViewer(r), keydown: (e) => { if (e.key === "Enter") openViewer(r); } } },
    h("div", { class: "hm" },
      h("div", { class: "ht", title: r.first_prompt }, r.title),
      h("div", { class: "hs" }, [r.profile_name, ORIGIN[r.origin], baseName(r.cwd), `${r.prompts} demande${r.prompts > 1 ? "s" : ""}`, fmtDate(r.updated)]
        .filter(Boolean).join(" · "))),
    r.archived ? h("span", { class: "tag" }, "archivée") : null,
    h("span", { class: `tag o-${r.origin}` }, ORIGIN[r.origin]));
}

async function openViewer(r) {
  let data;
  try { data = await api(`/api/sessions/${r.profile}/${r.id}`); } catch (e) { toast(e.message, "err"); return; }
  const root = document.getElementById("modal-root");
  const s = data.session;
  const body = h("div", { class: "viewer-body" });
  for (const it of data.items) {
    if (it.role === "user") body.append(h("div", { class: "msg user", style: { "--pc": s.color } }, it.text));
    else if (it.role === "assistant") body.append(mdElement(it.text, `md msg assistant${it.sidechain ? " side" : ""}`));
    else body.append(h("div", { class: `vtool${it.sidechain ? " side" : ""}` }, h("span", { class: "ti", svg: toolIcon(it.name) }),
      h("b", {}, toolLabel(it.name)), h("span", { class: "tt" }, it.target || "")));
  }
  if (!data.items.length) body.append(h("div", { class: "muted" }, "Transcription vide ou illisible."));
  const sub = h("small", {}, [ORIGIN[s.origin], s.cwd, fmtDate(s.updated), `${s.prompts} demande(s)`, s.model].filter(Boolean).join(" · "));
  const presets = ctx.presets().filter((p) => p.enabled);
  const preset = h("select", {}, ...presets.map((p) => h("option", { value: p.id }, p.name)));
  preset.value = ctx.profiles().find((p) => p.id === s.profile)?.default_preset || presets[0]?.id;
  const fork = h("input", { type: "checkbox" });
  fork.checked = false; // the same session continues; tick for a copy (e.g. while it is open in Claude Desktop)
  const msg = h("textarea", { rows: "2", placeholder: "Ton message pour continuer cette session…" });
  // Moving = the same session, filed in another project folder (Claude Desktop follows).
  const folderBtn = h("button", { type: "button", class: "btn small ghost folder-choice", on: { click: async () => {
    const r = await ctx.moveSession(s.profile, s.id, s.title, s.cwd);
    if (!r) return;
    s.cwd = r.folder;
    s.resumable = true;
    sub.textContent = [ORIGIN[s.origin], s.cwd, fmtDate(s.updated), `${s.prompts} demande(s)`, s.model].filter(Boolean).join(" · ");
    refresh();
    load();
  } } });
  const goBtn = h("button", { type: "button", class: "btn primary", on: { click: () => resume() } });
  const termBtn = h("button", { type: "button", class: "btn ghost", title: "Reprend la session d'origine en interactif (ferme-la d'abord dans Claude Desktop)",
    on: { click: () => terminal() } }, "Terminal");
  const note = h("div", { class: "line err", hidden: true });
  const forkLabel = h("label", { class: "check", title: "Coché : la suite part dans une copie et la session d'origine reste intacte (utile si elle est ouverte dans Claude Desktop)." }, fork, "Continuer dans une copie");
  function refresh() {
    folderBtn.replaceChildren(h("span", { class: "i", svg: "folder" }),
      s.resumable ? `Dans ${baseName(s.cwd)} · déplacer…` : "Déplacer dans un projet…");
    folderBtn.title = "Déplacer cette session dans un autre dossier (projet) : la même session continue là";
    goBtn.disabled = !s.resumable;
    goBtn.textContent = "Reprendre dans la console";
    termBtn.disabled = !s.resumable;
    note.hidden = s.resumable;
    note.textContent = `Le dossier d'origine n'existe plus (${s.cwd || "inconnu"}) : déplace la session dans un projet pour la reprendre.`;
  }
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape" && !document.querySelector(".dialog:not(.viewer)")) close(); };
  const resume = async () => {
    const prompt = msg.value.trim();
    if (!prompt) { msg.focus(); toast("Écris d'abord le message qui relance la session.", "warn"); return; }
    const t = await ctx.launch(`/api/sessions/${s.profile}/${s.id}/resume`, { prompt, preset: preset.value, fork: fork.checked });
    if (t) {
      close();
      document.getElementById("sessions").hidden = true;
    }
  };
  msg.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); resume(); } });
  const terminal = async () => {
    try { await api(`/api/sessions/${s.profile}/${s.id}/terminal`, { method: "POST" }); toast("Session ouverte dans un terminal."); }
    catch (e) { toast(e.message, "err"); }
  };
  const box = h("div", { class: "dialog viewer", role: "dialog", "aria-modal": "true", style: { "--pc": s.color } },
    h("div", { class: "viewer-head" },
      h("span", { class: "badge" }, s.profile_name),
      h("div", { class: "vh" }, h("h3", {}, s.title),
        sub),
      h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: close } })),
    body,
    h("div", { class: "viewer-foot" }, note, msg,
      h("div", { class: "row" },
        folderBtn,
        h("label", { class: "chip-select" }, h("span", { class: "i", svg: "shieldq" }), preset),
        forkLabel,
        h("span", { class: "grow" }), termBtn, goBtn)));
  refresh();
  const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) close(); } } }, box);
  root.append(overlay);
  document.addEventListener("keydown", onKey, true);
  body.scrollTop = body.scrollHeight;
  msg.focus();
}

/** Open one session's viewer from elsewhere (search). */
export function showSession(context, pid, sid) {
  ctx = context;
  openViewer({ profile: pid, id: sid });
}
