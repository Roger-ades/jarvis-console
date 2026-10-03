// Configuration window: a working copy of the config edited through tabs,
// validated by the server on save (errors are listed in the footer).
import { api, download, setToken } from "./api.js";
import { checkUpdateNow, restartConsole, updateConsole } from "./system.js";
import { confirmDialog, fmtDate, h, toast } from "./util.js";

const TABS = [
  ["general", "Général"], ["profiles", "Profils"], ["permissions", "Autorisations"],
  ["integrations", "Intégrations"], ["models", "Modèles et consignes"], ["security", "Sécurité"],
  ["interface", "Interface"], ["history", "Historique"],
];
const EFFORTS = [["", "défaut du profil"], ["low", "faible"], ["medium", "moyen"], ["high", "élevé"], ["xhigh", "très élevé"], ["max", "max"]];

let draft = null, meta = null, probes = {}, tab = "general", dirty = false, ctx = null;
let overlay = null, bodyEl = null, msgEl = null, navEl = null, presetSel = null;

const clone = (o) => JSON.parse(JSON.stringify(o));

function getPath(path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), draft);
}
function setPath(path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const obj = keys.reduce((o, k) => o[k], draft);
  obj[last] = value;
  markDirty();
}
function markDirty() {
  dirty = true;
  msgEl.className = "msg-area";
  msgEl.textContent = "Modifications non enregistrées.";
}

// ------------------------------------------------------------ field builders
function field(label, control, help, cls = "") {
  return h("label", { class: `field ${cls}` }, h("span", {}, label), control, help ? h("small", {}, help) : null);
}
function text(label, path, { help, placeholder, cls, type = "text" } = {}) {
  const el = h("input", { type, value: getPath(path) ?? "", placeholder });
  el.addEventListener("input", () => setPath(path, el.value));
  return field(label, el, help, cls);
}
function num(label, path, { help, min, max, optional } = {}) {
  const v = getPath(path);
  const el = h("input", { type: "number", value: v ?? "", min, max, placeholder: optional ? "défaut" : "" });
  el.addEventListener("input", () => {
    if (el.value === "" && optional) return setPath(path, null);
    const n = Number(el.value);
    if (!Number.isNaN(n)) setPath(path, n);
  });
  return field(label, el, help);
}
function check(label, path, { help, onChange } = {}) {
  const el = h("input", { type: "checkbox" });
  el.checked = !!getPath(path);
  el.addEventListener("change", () => { setPath(path, el.checked); onChange?.(el.checked); });
  return h("div", { class: "field" }, h("label", { class: "check" }, el, h("span", {}, label)), help ? h("small", {}, help) : null);
}
function select(label, path, options, { help, onChange } = {}) {
  const el = h("select", {}, options.map(([v, l]) => h("option", { value: v }, l)));
  el.value = getPath(path) ?? "";
  el.addEventListener("change", () => { setPath(path, el.value); onChange?.(el.value); });
  return field(label, el, help);
}
function lines(label, path, { help, cls = "wide", rows = 5 } = {}) {
  const el = h("textarea", { rows: String(rows), spellcheck: "false" });
  el.value = (getPath(path) || []).join("\n");
  el.addEventListener("input", () => setPath(path, el.value.split("\n").map((s) => s.trim()).filter(Boolean)));
  return field(label, el, help || "Une valeur par ligne.", cls);
}
function area(label, path, { help, rows = 6 } = {}) {
  const el = h("textarea", { rows: String(rows), class: "prose" });
  el.value = getPath(path) || "";
  el.addEventListener("input", () => setPath(path, el.value));
  return field(label, el, help, "wide");
}
function color(label, path, onChange) {
  const el = h("input", { type: "color", value: getPath(path) || "#40dcff" });
  el.addEventListener("input", () => { setPath(path, el.value); onChange?.(el.value); });
  return field(label, el);
}
function kv(label, path, help) {
  const el = h("textarea", { rows: "3", spellcheck: "false" });
  el.value = Object.entries(getPath(path) || {}).map(([k, v]) => `${k}=${v}`).join("\n");
  el.addEventListener("input", () => {
    const o = {};
    for (const line of el.value.split("\n")) {
      const i = line.indexOf("=");
      if (i > 0) o[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    setPath(path, o);
  });
  return field(label, el, help || "Une variable par ligne : NOM=valeur (pas de secrets).", "wide");
}
function json(label, path, help) {
  const el = h("textarea", { rows: "5", spellcheck: "false" });
  el.value = JSON.stringify(getPath(path) || {}, null, 2);
  const note = h("small", {}, help);
  el.addEventListener("input", () => {
    try { setPath(path, JSON.parse(el.value || "{}")); note.textContent = help; note.classList.remove("err"); }
    catch { note.textContent = "JSON invalide : la valeur précédente est conservée."; }
  });
  return h("label", { class: "field wide" }, h("span", {}, label), el, note);
}
const section = (title, lead, ...children) =>
  h("div", { class: "section" }, h("h3", {}, title), lead ? h("p", { class: "lead" }, lead) : null, ...children);
const grid = (...children) => h("div", { class: "grid" }, ...children);

function modelOptions(extra = []) {
  const seen = new Set();
  const out = [];
  const add = (v, l) => { if (v != null && !seen.has(v)) { seen.add(v); out.push([v, l || v]); } };
  extra.forEach(([v, l]) => add(v, l));
  (meta.base_models || []).forEach((m) => add(m, m === "default" ? "défaut du compte" : m));
  for (const p of Object.values(probes)) (p.models || []).forEach((m) => add(m.value, m.label ? `${m.value} — ${m.label}` : m.value));
  return out;
}
const presetOptions = () => draft.presets.map((p) => [p.id, p.name]);
const profileOptions = () => draft.profiles.map((p) => [p.id, p.name]);

// ------------------------------------------------------------ probes
function probeView(pid) {
  const p = probes[pid];
  if (!p || !p.checked) return h("div", { class: "probe" }, "Pas encore testé.");
  const acc = p.account || {};
  const who = acc.email || acc.emailAddress || acc.organization || acc.organizationName || "";
  const box = h("div", { class: `probe ${p.ok && p.logged_in ? "ok" : "ko"}` },
    h("div", {}, h("b", {}, !p.ok ? "Échec du test" : p.logged_in ? "Connecté" : "Non connecté"),
      who ? ` · ${who}` : "", ` · testé à ${fmtDate(p.checked)}`,
      p.cli?.version ? ` · ${p.cli.version}` : ""));
  if (p.error) box.append(h("div", { class: "line err" }, p.error));
  if (p.ok && !p.logged_in) box.append(h("div", { class: "muted" }, "Clique « Se connecter » : un terminal s'ouvre pour la connexion à ce compte (une seule fois)."));
  const kvs = [];
  for (const [k, v] of Object.entries(acc)) if (typeof v !== "object") kvs.push(h("span", { class: "tag" }, `${k} : ${v}`));
  if (p.commands) kvs.push(h("span", { class: "tag" }, `${p.commands.length} skills et commandes`));
  if (p.agents) kvs.push(h("span", { class: "tag" }, `${p.agents.length} sous-agents`));
  if (p.models) kvs.push(h("span", { class: "tag" }, `${p.models.length} modèles`));
  if (kvs.length) box.append(h("div", { class: "kv" }, kvs));
  if (p.mcp?.length) {
    box.append(h("div", { class: "kv" }, ...p.mcp.map((s) => h("span", { class: `mcp-chip ${s.status === "connected" ? "ok" : s.status === "pending" ? "wait" : "ko"}`,
      title: `${s.scope || ""} ${s.type || ""} ${s.error || ""}` }, `${s.name} · ${s.status}`))));
  }
  return box;
}

async function runProbe(pid, target) {
  if (dirty && !(await save(true))) return;
  target.replaceChildren(h("div", { class: "probe" }, "Test en cours… (initialisation de Claude Code, sans consommer de tokens)"));
  try {
    probes[pid] = await api(`/api/profiles/${pid}/test`, { method: "POST" });
  } catch (e) {
    probes[pid] = { ok: false, error: e.message, checked: Date.now() / 1000 };
  }
  target.replaceChildren(probeView(pid));
}

async function openLogin(pid) {
  if (dirty && !(await save(true))) return;
  try {
    await api(`/api/profiles/${pid}/login`, { method: "POST" });
    toast("Terminal de connexion ouvert. Une fois connecté, relance le test.");
  } catch (e) { toast(e.message, "err"); }
}

// ------------------------------------------------------------ tabs
function tabGeneral() {
  const g = "general";
  const versions = h("div", {}, h("div", { class: "muted" }, "Chargement…"));
  api("/api/config/history").then(({ versions: list }) => {
    if (!list.length) { versions.replaceChildren(h("div", { class: "muted" }, "Aucune version précédente.")); return; }
    versions.replaceChildren(h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Version du"), h("th", {}, "Remplacée par"), h("th", {}))),
      h("tbody", {}, list.slice(0, 15).map((v) => h("tr", {}, h("td", {}, fmtDate(v.ts)), h("td", {}, v.reason || "—"),
        h("td", { class: "narrow" }, h("button", { type: "button", class: "btn small", on: { click: () => rollback(v.id) } }, "Restaurer")))))));
  }).catch(() => versions.replaceChildren(h("div", { class: "muted" }, "Historique indisponible.")));
  const fileIn = h("input", { type: "file", accept: "application/json,.json", hidden: true });
  fileIn.addEventListener("change", async () => {
    const f = fileIn.files[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      const res = await api("/api/config/import", { method: "POST", body: data });
      afterSave(res, "Configuration importée.");
    } catch (e) { showErrors(e); }
    fileIn.value = "";
  });
  return [
    section("Général", null, grid(
      select("Langue", `${g}.language`, [["fr", "Français"]]),
      select("Thème", `${g}.theme`, [["sombre", "Sombre"], ["clair", "Clair"], ["systeme", "Système"]],
        { onChange: (v) => document.documentElement.setAttribute("data-theme", v) }),
      num("Port", `${g}.port`, { min: 1024, max: 65535, help: "Pris en compte au prochain démarrage." }),
      field("Dossier de données", h("input", { type: "text", value: meta.data_dir, readonly: true }), "Variable CONSOLE_DATA_DIR pour le changer."),
      select("Profil par défaut", `${g}.default_profile`, profileOptions()),
      select("Ouverture au démarrage", `${g}.open_as`, [["app", "Fenêtre d'application (Chrome ou Edge)"], ["navigateur", "Onglet du navigateur"]],
        { help: "start.bat ouvre la console dans sa propre fenêtre. Pour l'installer : bouton Installer ou menu ⋮ de Chrome." }),
      num("Tâches simultanées (total)", `${g}.max_concurrent`, { min: 1, max: 16, help: "Au-delà, les demandes attendent en file." }),
      num("Durée max d'une tâche (min)", `${g}.task_timeout_min`, { min: 1, max: 1440, help: "Le temps passé à attendre ta validation n'est pas compté." }),
      num("Délai de validation (min)", `${g}.approval_timeout_min`, { min: 1, max: 1440, help: "Passé ce délai, l'action est refusée." }),
      num("Taille max de sortie (Ko)", `${g}.max_output_kb`, { min: 16, max: 65536 }),
    ), h("div", { class: "row" }, check("Notifications du navigateur", `${g}.notifications`),
      h("button", { type: "button", class: "btn small", on: { click: askNotify } }, "Autoriser les notifications"))),
    section("Claude Code", "La console pilote la CLI Claude Code installée sur ce poste (celle des apps Claude Desktop est détectée automatiquement).", grid(
      text("Chemin de la CLI", `${g}.cli_path`, { placeholder: meta.cli_detected || "détection automatique", help: `Détectée : ${meta.cli_detected || "aucune"}`, cls: "wide" }),
      text("Dossier des pièces jointes", `${g}.attachments_dir`, { placeholder: "~/ClaudeConsole/pieces-jointes", cls: "wide",
        help: "Chaque tâche y a son sous-dossier (date_identifiant), ajouté à ses dossiers de travail. Rien n'y est effacé automatiquement." }),
      check("Questions interactives (AskUserQuestion)", `${g}.ask_user_questions`, { help: "Claude peut te poser des questions dans la fenêtre." }),
      check("Lire les limites des comptes au démarrage", `${g}.limits_on_start`, { help: "Si la dernière mesure date de plus de 3 h : une toute petite requête (Haiku) par compte. Sinon elles se mettent à jour à chaque tâche." }),
      lines("Variables d'environnement retirées", `${g}.env_strip`, { help: "Motifs retirés de l'environnement des tâches (ANTHROPIC_* évite toute facturation par clé API)." }),
    )),
    launcherSection(),
    serverSection(),
    section("Sauvegarde de la configuration", "Export et import en JSON, validés par le schéma ; chaque enregistrement garde la version précédente.",
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", on: { click: () => download("/api/config/export", "jarvis-config.json") } }, "Exporter"),
        h("button", { type: "button", class: "btn", on: { click: () => fileIn.click() } }, "Importer…"), fileIn,
        h("button", { type: "button", class: "btn ghost", on: { click: () => download("/api/config/schema", "jarvis-config.schema.json") } }, "Schéma JSON"),
        h("button", { type: "button", class: "btn danger", on: { click: resetDefaults } }, "Valeurs par défaut")),
      h("div", { class: "section" }, versions)),
  ];
}

function tabProfiles() {
  const out = [section("Comptes Claude", "Chaque profil a son propre dossier de configuration Claude Code : connexion, MCP, mémoire, skills et CLAUDE.md restent séparés. Les consignes additionnelles sont dans l'onglet « Modèles et consignes ».")];
  draft.profiles.forEach((p, i) => {
    const base = `profiles.${i}`;
    const probeBox = h("div", {}, probeView(p.id));
    const m = meta.profiles[p.id] || {};
    const card = h("div", { class: "card profile", style: { "--pc": p.color } },
      h("div", { class: "card-head" }, h("span", { class: "badge" }, p.name), h("h4", {}, h("span", { class: "tag" }, p.id)),
        h("button", { type: "button", class: "btn small", on: { click: () => runProbe(p.id, probeBox) } }, "Tester la connexion"),
        h("button", { type: "button", class: "btn small", on: { click: () => openLogin(p.id) } }, "Se connecter"),
        draft.profiles.length > 1 ? h("button", { type: "button", class: "btn small danger", on: { click: () => removeProfile(i) } }, "Supprimer") : null),
      grid(
        text("Nom", `${base}.name`),
        color("Couleur", `${base}.color`, (v) => card.style.setProperty("--pc", v)),
        text("Dossier de configuration Claude", `${base}.config_dir`, { help: `Vide = dossier par défaut. Actuel : ${m.config_dir || "?"}` }),
        text("Dossier de travail", `${base}.workdir`, { help: `Créé au besoin. Actuel : ${m.workdir || "?"}` }),
        select("Modèle par défaut", `${base}.default_model`, modelOptions([[p.default_model, p.default_model]])),
        select("Effort", `${base}.effort`, EFFORTS),
        select("Preset par défaut", `${base}.default_preset`, presetOptions()),
        num("Tâches simultanées (profil)", `${base}.max_concurrent`, { min: 1, max: 16 }),
        check("Confirmer chaque lancement", `${base}.confirm_launch`),
        check("Claude in Chrome", `${base}.chrome`, { help: "--chrome" }),
        lines("Dossiers supplémentaires autorisés", `${base}.add_dirs`, { rows: 3, help: "Passés avec --add-dir. Un par ligne." }),
        kv("Variables d'environnement", `${base}.env`),
      ), probeBox);
    out.push(card);
  });
  out.push(h("button", { type: "button", class: "btn", on: { click: addProfile } }, "Ajouter un profil"));
  return out;
}

function tabPermissions() {
  const list = h("div", { class: "list" });
  const detail = h("div", { class: "detail" });
  const idx = Math.max(0, draft.presets.findIndex((p) => p.id === presetSel));
  presetSel = draft.presets[idx]?.id;
  const renderList = () => list.replaceChildren(...draft.presets.map((p) => h("button", { type: "button", class: p.id === presetSel ? "active" : "",
    on: { click: () => { presetSel = p.id; render(); } } }, p.name, h("small", {}, `${meta.mode_labels[p.mode] || p.mode}${p.enabled ? "" : " · désactivé"}${p.builtin ? " · fourni" : ""}`))),
    h("button", { type: "button", on: { click: newPreset } }, "+ Nouveau preset"));
  renderList();
  const i = draft.presets.findIndex((p) => p.id === presetSel);
  const p = draft.presets[i];
  const b = `presets.${i}`;
  detail.append(...[
    p.id === "complet" || p.mode === "bypassPermissions"
      ? h("div", { class: "warnbox" }, "Mode sans aucune demande d'autorisation : Claude peut écrire, supprimer et exécuter sans te consulter. Les refus de la console (chemins interdits, règles verrouillées, contraintes) restent appliqués. Garde-le désactivé sauf besoin ponctuel, dans un dossier dédié.") : null,
    grid(
      text("Nom", `${b}.name`), field("Identifiant", h("input", { type: "text", value: p.id, readonly: true })),
      text("Description", `${b}.description`, { cls: "wide" }),
      select("Mode de permission", `${b}.mode`, Object.entries(meta.mode_labels)),
      select("Outils non listés", `${b}.unlisted`, [["ask", "demander ma validation"], ["deny", "refuser"]]),
      select("Confinement au dossier de travail", `${b}.confine`, [["all", "lecture et écriture"], ["writes", "écritures seulement"], ["none", "aucun"]]),
      select("Modèle", `${b}.model`, modelOptions([["", "celui du profil"]])),
      num("Tours max", `${b}.max_turns`, { min: 1, max: 1000, optional: true }),
      check("Preset activé", `${b}.enabled`, { onChange: renderList }),
      check("Valider toute écriture ou commande", `${b}.validate_writes`, { help: "sauf règle d'autorisation explicite" }),
      check("Confirmer chaque lancement", `${b}.require_confirm`),
      check("Dossier de travail dédié obligatoire", `${b}.require_dedicated_workdir`),
      lines("Outils disponibles", `${b}.tools`, { cls: "", help: "Outils intégrés proposés à Claude (--tools). Vide = tous." }),
      lines("Autorisés sans demande", `${b}.allow`, { cls: "", help: "Ex. Read, Bash(git status:*), mcp__odoo__search_records, mcp__*__get*. Un motif générique n'autorise jamais un outil d'écriture (create, delete, send…) dont il ne cite pas le verbe : nomme l'outil exactement." }),
      lines("Refusés", `${b}.deny`, { cls: "", help: "Un refus l'emporte toujours. Pour les commandes, alias PowerShell, espaces et cmd /c sont pris en compte, mais une liste de refus reste une protection d'appoint : préfère « Outils non listés : refuser » ou la validation humaine." }),
      lines("Validation humaine", `${b}.approval`, { cls: "", help: "Ces appels s'arrêtent dans la fenêtre pour approbation." }),
    ),
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn", on: { click: () => duplicatePreset(i) } }, "Dupliquer"),
      !p.builtin ? h("button", { type: "button", class: "btn danger", on: { click: () => removePreset(i) } }, "Supprimer") : null),
  ].filter(Boolean));
  return [
    section("Presets d'autorisations", "Ordre d'application, pour chaque appel d'outil (sous-agents compris) : chemins interdits et règles verrouillées → refus → contraintes et dossiers → validation humaine → autorisations → écritures à valider → outils non listés."),
    h("div", { class: "split" }, list, detail),
  ];
}

function tabIntegrations() {
  const out = [section("Serveurs MCP par profil", "Claude Code charge déjà les MCP de son dossier de configuration et les connecteurs du compte claude.ai. La console peut y ajouter ceux déclarés dans l'app Claude Desktop du profil, et les tiens.")];
  draft.profiles.forEach((p, i) => {
    const base = `profiles.${i}.mcp`;
    const table = h("div", {}, h("div", { class: "muted" }, "Lecture des serveurs…"));
    const status = h("div", {});
    const loadServers = () => api(`/api/profiles/${p.id}/mcp`).then((res) => {
      const rows = res.servers.map((s) => {
        const cb = h("input", { type: "checkbox" });
        cb.checked = !(getPath(`${base}.disabled_servers`) || []).includes(s.name);
        cb.addEventListener("change", () => {
          const set = new Set(getPath(`${base}.disabled_servers`) || []);
          cb.checked ? set.delete(s.name) : set.add(s.name);
          setPath(`${base}.disabled_servers`, [...set]);
        });
        return h("tr", {}, h("td", { class: "narrow" }, cb), h("td", {}, h("b", {}, s.name)), h("td", {}, s.origin), h("td", {}, s.type),
          h("td", { class: "mono" }, s.command || s.url));
      });
      table.replaceChildren(...[
        res.error ? h("div", { class: "line warn" }, res.error) : null,
        rows.length ? h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Actif"), h("th", {}, "Serveur"), h("th", {}, "Origine"), h("th", {}, "Type"), h("th", {}, "Commande"))), h("tbody", {}, rows))
          : h("div", { class: "muted" }, "Aucun serveur importé ni ajouté (ceux du dossier Claude et du compte claude.ai restent actifs)."),
      ].filter(Boolean));
      if (res.status?.length) status.replaceChildren(h("div", { class: "kv" }, ...res.status.map((s) => h("span", { class: `mcp-chip ${s.status === "connected" ? "ok" : "ko"}`, title: s.error || s.scope }, `${s.name} · ${s.status}`))));
    }).catch((e) => table.replaceChildren(h("div", { class: "line err" }, e.message)));
    loadServers();
    const probeBox = h("div", {});
    out.push(h("div", { class: "card profile", style: { "--pc": p.color } },
      h("div", { class: "card-head" }, h("span", { class: "badge" }, p.name), h("h4", {}, "MCP"),
        h("button", { type: "button", class: "btn small", on: { click: async () => { await runProbe(p.id, probeBox); loadServers(); } } }, "Tester les MCP")),
      grid(
        check("Importer les MCP de l'app Claude Desktop", `${base}.import_desktop`),
        check("Uniquement ces serveurs (--strict-mcp-config)", `${base}.strict`, { help: "ignore ceux du dossier Claude et du compte" }),
        text("Fichier claude_desktop_config.json", `${base}.desktop_config`, { cls: "wide",
          help: meta.profiles[p.id]?.desktop_config_exists ? `Trouvé : ${meta.profiles[p.id].desktop_config}` : "Fichier introuvable pour l'instant." }),
      ), table, status,
      json("Serveurs ajoutés (JSON)", `${base}.extra_servers`, "Même format que mcpServers : { \"nom\": { \"command\": \"…\", \"args\": [] } } ou { \"url\": \"…\", \"type\": \"http\" }."),
      probeBox));
  });

  const rulesBody = h("tbody");
  const renderRules = () => rulesBody.replaceChildren(...draft.tool_rules.map((r, i) => {
    const pat = h("input", { type: "text", value: r.pattern, readonly: r.locked });
    pat.addEventListener("input", () => setPath(`tool_rules.${i}.pattern`, pat.value));
    const dec = h("select", { disabled: r.locked }, [["allow", "autoriser"], ["ask", "valider"], ["deny", "refuser"]].map(([v, l]) => h("option", { value: v }, l)));
    dec.value = r.decision;
    dec.addEventListener("change", () => setPath(`tool_rules.${i}.decision`, dec.value));
    const note = h("input", { type: "text", value: r.note || "" });
    note.addEventListener("input", () => setPath(`tool_rules.${i}.note`, note.value));
    return h("tr", {}, h("td", {}, pat), h("td", { class: "narrow" }, dec), h("td", {}, note),
      h("td", { class: "narrow" }, r.locked ? h("span", { class: "tag lock" }, "verrouillée")
        : h("button", { type: "button", class: "btn small ghost", on: { click: () => { draft.tool_rules.splice(i, 1); markDirty(); renderRules(); } } }, "Retirer")));
  }));
  renderRules();
  const consBody = h("tbody");
  const csv = (a) => (a || []).join(", ");
  const parseCsv = (s) => (s.trim() ? s.split(",").map((x) => x.trim()).filter(Boolean) : null);
  const renderCons = () => consBody.replaceChildren(...draft.constraints.map((c, i) => {
    const mk = (key, val, parse = (x) => x) => {
      const el = h("input", { type: "text", value: val ?? "" });
      el.addEventListener("input", () => setPath(`constraints.${i}.${key}`, parse(el.value)));
      return h("td", {}, el);
    };
    return h("tr", {}, mk("tool", c.tool), mk("path", c.path), mk("allowed", csv(c.allowed), parseCsv), mk("forbidden", csv(c.forbidden), parseCsv), mk("note", c.note),
      h("td", { class: "narrow" }, h("button", { type: "button", class: "btn small ghost", on: { click: () => { draft.constraints.splice(i, 1); markDirty(); renderCons(); } } }, "Retirer")));
  }));
  renderCons();
  out.push(
    section("Règles par outil (tous presets)", "S'ajoutent aux règles du preset. Motifs : nom exact, * pour plusieurs outils (mcp__odoo__*), Outil(motif) pour Bash, Read, WebFetch(domain:…).",
      h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Motif"), h("th", {}, "Décision"), h("th", {}, "Note"), h("th", {}))), rulesBody),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn small", on: { click: () => { draft.tool_rules.push({ pattern: "mcp__serveur__outil", decision: "ask", locked: false, note: "" }); markDirty(); renderRules(); } } }, "Ajouter une règle")),
      h("p", { class: "lead" }, "Toujours appliqué par la console, même si la règle est retirée ici : ", ...meta.locked_rules.map((r) => h("span", { class: "tag lock" }, `${r.pattern} → refus`)))),
    section("Contraintes sur les paramètres", "Refuse un appel si un paramètre sort des valeurs permises. Chemin pointé dans les paramètres de l'outil (ex. model, values.state). Valeurs séparées par des virgules.",
      h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Outil"), h("th", {}, "Paramètre"), h("th", {}, "Valeurs permises"), h("th", {}, "Valeurs interdites"), h("th", {}, "Note"), h("th", {}))), consBody),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn small", on: { click: () => { draft.constraints.push({ tool: "mcp__odoo__create_record", path: "model", allowed: ["sale.order"], forbidden: null, note: "" }); markDirty(); renderCons(); } } }, "Ajouter une contrainte"))),
  );
  return out;
}

function tabModels() {
  const rows = draft.presets.map((p, i) => {
    const sel = h("select", {}, modelOptions([["", "celui du profil"]]).map(([v, l]) => h("option", { value: v }, l)));
    sel.value = p.model || "";
    sel.addEventListener("change", () => setPath(`presets.${i}.model`, sel.value));
    return h("tr", {}, h("td", {}, p.name), h("td", {}, sel));
  });
  return [
    section("Modèles", "Le modèle se choisit aussi à chaque demande dans la barre de commande.", grid(
      num("Nombre max de tours par tâche", "general.max_turns", { min: 1, max: 1000, help: "Chaque preset peut le réduire." }),
      num("Compacter le contexte vers (milliers de tokens)", "general.compact_at_k", { min: 0, max: 1000,
        help: "Chaque action relit tout le contexte de la session. Opus a une fenêtre d'un million de tokens : sans ce réglage, Claude Code ne compacte presque jamais. 0 : seuil de Claude Code." })),
      h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, h("th", {}, "Type de tâche (preset)"), h("th", {}, "Modèle"))), h("tbody", {}, rows))),
    section("Mode équipe", "Case « Équipe » de la barre du bas : le modèle choisi dirige ; il délègue le travail de volume à des sous-agents moins chers et, s'il est lui-même moins puissant, les points difficiles à l'expert. Un rôle n'est proposé que s'il est utile à côté du chef.",
      grid(
        select("Éclaireur (recherche, lecture)", "team.scout_model", modelOptions([[draft.team.scout_model, draft.team.scout_model]])),
        select("Exécutant (fichiers, commandes)", "team.worker_model", modelOptions([[draft.team.worker_model, draft.team.worker_model]])),
        select("Expert (points difficiles)", "team.expert_model", modelOptions([[draft.team.expert_model, draft.team.expert_model]])),
        select("Autres sous-agents de Claude Code", "team.subagent_default", modelOptions([[draft.team.subagent_default, draft.team.subagent_default]]),
          { help: "Explore, general-purpose… n'héritent plus du modèle du chef." })),
      area("Règles données au chef", "team.instructions", { rows: 7 })),
    section("Consignes système", "Ajoutées au prompt système de Claude Code (--append-system-prompt-file), pour chaque tâche, après le contexte JARVIS que la console envoie toujours (la console, son fonctionnement, le compte, le projet, les dossiers et les autorisations de la discussion).",
      area("Consignes de sécurité (tous profils)", "general.security_instructions", { rows: 5 }),
      ...draft.profiles.map((p, i) => area(`Consignes additionnelles · ${p.name}`, `profiles.${i}.instructions`, { rows: 4 }))),
  ];
}

function tabSecurity(state) {
  const audit = h("div", {});
  let offset = 0;
  const kindSel = h("select", {}, ["", "appel d'outil", "validation", "tâche créée", "tâche terminée", "appel refusé", "configuration modifiée", "arrêt d'urgence"].map((k) => h("option", { value: k }, k || "tous les types")));
  const loadAudit = async () => {
    try {
      const q = new URLSearchParams({ limit: "60", offset: String(offset) });
      if (kindSel.value) q.set("kind", kindSel.value);
      const { rows, total } = await api(`/api/audit?${q}`);
      audit.replaceChildren(h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", {}, "Tâche"), h("th", {}, "Profil"), h("th", {}, "Type"), h("th", {}, "Détail"))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "narrow" }, fmtDate(r.ts)), h("td", { class: "mono narrow" }, r.task_id || ""),
          h("td", { class: "narrow" }, r.profile || ""), h("td", { class: "narrow" }, r.kind),
          h("td", {}, h("div", { class: "audit-detail", title: JSON.stringify(r.detail, null, 1) }, JSON.stringify(r.detail))))))),
      h("div", { class: "row" }, h("span", { class: "muted" }, `${Math.min(total, offset + 1)}–${Math.min(total, offset + rows.length)} sur ${total}`),
        h("button", { type: "button", class: "btn small", disabled: offset === 0, on: { click: () => { offset = Math.max(0, offset - 60); loadAudit(); } } }, "Plus récents"),
        h("button", { type: "button", class: "btn small", disabled: offset + 60 >= total, on: { click: () => { offset += 60; loadAudit(); } } }, "Plus anciens")));
    } catch (e) { audit.replaceChildren(h("div", { class: "line err" }, e.message)); }
  };
  kindSel.addEventListener("change", () => { offset = 0; loadAudit(); });
  loadAudit();
  const emergencyOn = state().emergency_stop;
  return [
    section("Arrêt d'urgence", "Stoppe immédiatement toutes les tâches et bloque les nouvelles jusqu'à réactivation.",
      h("div", { class: "row" }, h("span", {}, emergencyOn ? "Actif : la console est bloquée." : "Inactif."),
        h("button", { type: "button", class: `btn big-stop ${emergencyOn ? "ok" : "danger"}`, on: { click: async () => { await ctx.setEmergency(!emergencyOn); render(); } } },
          emergencyOn ? "Réactiver la console" : "Déclencher l'arrêt d'urgence"))),
    section("Accès à la console", "Chaque appel au serveur porte un jeton ; l'en-tête Origin et l'hôte sont contrôlés ; le serveur n'écoute que 127.0.0.1.",
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", on: { click: rotate } }, "Régénérer le jeton d'accès"),
        h("span", { class: "muted" }, "Les autres onglets ouverts devront être rouverts via start.bat.")),
      grid(lines("Origines supplémentaires autorisées", "security.extra_origins", { cls: "", rows: 3, help: "Rarement utile. Ex. http://127.0.0.1:8788" }))),
    section("Pages web montrées par Claude", "Quand Claude veut afficher une page web (outil « afficher »), elle s'ouvre seule si son site est approuvé ; sinon la tâche te propose de l'ouvrir, et rien n'est chargé tant que tu ne cliques pas.",
      grid(lines("Domaines approuvés", "security.trusted_domains", { rows: 4,
        help: "Un domaine par ligne, sous-domaines compris (ex. monentreprise.odoo.com, monentreprise.sharepoint.com). Adresses https:// seulement." }))),
    section("Dossiers et fichiers interdits", "Jamais lus ni écrits par un agent, quel que soit le preset. Le dossier de données de la console est toujours protégé.",
      grid(lines("Chemins interdits", "security.forbidden_paths", { rows: 8, help: "~/ = dossier utilisateur ; **/ = n'importe où ; *.pem = motif de nom." }))),
    section("Journal d'audit", "Demandes, profils, presets, appels d'outils avec leur décision, validations et résultats.",
      h("div", { class: "row" }, check("Journal activé", "security.audit"), kindSel,
        h("button", { type: "button", class: "btn small", on: { click: () => download("/api/audit/export?format=csv", "audit.csv") } }, "Exporter CSV"),
        h("button", { type: "button", class: "btn small", on: { click: () => download("/api/audit/export?format=json", "audit.json") } }, "Exporter JSON")),
      audit),
  ];
}

function tabInterface() {
  const keys = [["Entrée", "envoyer la demande"], ["Maj + Entrée", "nouvelle ligne"], ["↑ / ↓", "rappeler une demande précédente"],
    ["Alt + 1, 2…", "choisir le profil"], ["Ctrl + ,", "ouvrir la configuration"], ["/", "skills et commandes du profil"],
    ["@work, @perso", "choisir le compte dans le texte"], ["Double-clic sur l'en-tête", "agrandir une fenêtre"], ["Échap", "fermer une fenêtre de dialogue"]];
  return [
    section("Fenêtres", null, grid(
      num("Largeur par défaut (px)", "ui.default_width", { min: 320, max: 4000 }),
      num("Hauteur par défaut (px)", "ui.default_height", { min: 200, max: 4000 }),
      select("Rangement par défaut", "ui.arrange", [["cascade", "Cascade"], ["mosaique", "Mosaïque"]]),
      check("Sons de notification", "ui.sounds"),
      check("Joindre ce que je regarde", "ui.regard",
        { help: "L'aperçu ou l'affichage au premier plan et le texte sélectionné partent avec ton message (une puce « Regard » le montre, sa croix le retire)." }))),
    section("Aperçus", "Images, PDF, pages web et fichiers des dossiers de la tâche s'affichent dans la console.", grid(
      check("Ouvrir les liens web dans l'aperçu intégré", "ui.link_preview",
        { help: "Ctrl/⌘ + clic ouvre toujours le vrai navigateur. Certains sites refusent l'affichage intégré." }),
      check("Charger automatiquement les images web", "ui.auto_images",
        { help: "Sinon un bouton « afficher » : le site qui héberge l'image voit ta requête seulement quand tu cliques." }))),
    section("Raccourcis", null, h("table", { class: "tbl" }, h("tbody", {}, keys.map(([k, v]) => h("tr", {}, h("td", { class: "narrow" }, h("kbd", {}, k)), h("td", {}, v)))))),
  ];
}

function tabHistory() {
  return [
    section("Historique des tâches", "Tâches, flux et journal d'audit sont conservés sur ce poste (data/console.db).", grid(
      num("Durée de conservation (jours)", "history.retention_days", { min: 1, max: 3650, help: "Purge automatique au démarrage." })),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", on: { click: () => download("/api/history/export", "historique.json") } }, "Exporter l'historique (JSON)"),
        h("button", { type: "button", class: "btn", on: { click: () => purge(draft.history.retention_days) } }, "Purger au-delà de la durée"),
        h("button", { type: "button", class: "btn danger", on: { click: () => purge(null) } }, "Tout purger"))),
  ];
}

function launcherSection() {
  const state = h("div", { class: "muted" }, "…");
  const help = h("p", { class: "muted" });
  const btn = h("button", { type: "button", class: "btn primary", disabled: true, on: { click: async () => {
    btn.disabled = true;
    try {
      const { paths } = await api("/api/system/launcher", { method: "POST" });
      state.textContent = `Lanceur créé : ${paths.join(" · ")}`;
      btn.textContent = "Recréer le lanceur";
      toast("Lanceur JARVIS créé.", "ok");
    } catch (e) { toast(e.message, "err"); }
    btn.disabled = false;
  } } }, "Créer le lanceur");
  api("/api/system").then((s) => {
    const mac = s.platform === "mac";
    btn.disabled = !(mac || s.platform === "nt");
    btn.textContent = s.launcher ? "Recréer le lanceur" : "Créer le lanceur";
    state.textContent = s.launcher ? "Lanceur présent." : "Pas encore de lanceur.";
    help.textContent = mac
      ? "Crée « JARVIS Console » dans ton dossier Applications : glisse-le dans le Dock. Il démarre la console si besoin et l'ouvre."
      : "Crée « JARVIS Console » dans le menu Démarrer et sur le Bureau, avec l'icône JARVIS. Clic droit dessus → Épingler à la barre des tâches. "
        + "Il démarre la console si besoin et l'ouvre. Astuce : installe aussi l'app (bouton Installer en haut) ; le lanceur ouvre alors l'app installée "
        + "et sa fenêtre porte l'icône JARVIS au lieu de celle de Chrome.";
  }).catch(() => { state.textContent = ""; });
  const setup = h("button", { type: "button", class: "btn", on: { click: async () => { const c = ctx; await close(); if (!overlay) c.openSetup?.(); } } }, "Assistant de démarrage");
  return section("Lanceur", null, help, h("div", { class: "row" }, btn, setup), state);
}

function serverSection() {
  const info = h("div", { class: "muted" }, "…");
  const stop = h("button", { type: "button", class: "btn danger", disabled: true, on: { click: async () => {
    const st = ctx.state();
    const running = (st.running || 0) + (st.awaiting || 0);
    const msg = running ? `${running} tâche(s) en cours seront interrompues. Les routines ne tourneront plus jusqu'au prochain lancement.`
      : "Les routines ne tourneront plus jusqu'au prochain lancement (start.bat ou démarrage de Windows).";
    if (!(await confirmDialog("Arrêter la console ?", msg, "Arrêter", "danger"))) return;
    try { await api("/api/system/shutdown", { method: "POST" }); toast("Console arrêtée. Relance start.bat pour la rouvrir.", "ok"); }
    catch (e) { toast(e.message, "err"); }
  } } }, "Arrêter la console");
  const restart = h("button", { type: "button", class: "btn", disabled: true, on: { click: restartConsole } }, "Redémarrer la console");
  api("/api/system").then((s) => {
    stop.disabled = !s.stoppable;
    restart.disabled = !s.stoppable;
    info.textContent = s.stoppable
      ? `Le serveur tourne en arrière-plan, sans fenêtre. Journal : ${s.log}`
      : "Cette console n'a pas été lancée par start.bat / start.command : arrête-la depuis le terminal qui l'a lancée (Ctrl+C).";
  }).catch(() => { info.textContent = ""; });
  // updates from GitHub
  const upd = h("div", { class: "muted" }, "");
  const install = h("button", { type: "button", class: "btn primary", hidden: true, on: { click: () => updateConsole() } }, "Mettre à jour");
  const describe = (u) => {
    install.hidden = !(u.behind > 0);
    upd.textContent = u.error && !u.behind ? u.error
      : u.behind ? `${u.behind} changement${u.behind > 1 ? "s" : ""} disponible${u.behind > 1 ? "s" : ""} : ${(u.commits || []).slice(0, 3).join(" · ")}${u.commits?.length > 3 ? "…" : ""}`
        : `À jour${u.branch ? ` (branche ${u.branch})` : ""}${u.dirty ? " · des fichiers ont été modifiés sur ce poste : la mise à jour automatique est bloquée" : ""}.`;
  };
  const checkBtn = h("button", { type: "button", class: "btn", on: { click: async () => {
    checkBtn.disabled = true;
    upd.textContent = "Recherche sur GitHub…";
    try { describe(await checkUpdateNow()); } catch (e) { upd.textContent = e.message; }
    checkBtn.disabled = false;
  } } }, "Rechercher une mise à jour");
  api("/api/system/update").then(describe).catch(() => {});
  return section("Serveur", "Redémarrer active une mise à jour du code, sans relancer start.bat.",
    h("div", { class: "row" }, restart, stop), info,
    h("div", { class: "row upd-row" }, checkBtn, install), upd,
    check("Rechercher les mises à jour au démarrage, puis toutes les 6 h", "general.update_check"));
}

// ------------------------------------------------------------ actions
function askNotify() {
  if (!("Notification" in window)) return toast("Notifications non disponibles dans ce navigateur.", "warn");
  Notification.requestPermission().then((p) => toast(p === "granted" ? "Notifications autorisées." : "Notifications refusées par le navigateur.", p === "granted" ? "ok" : "warn"));
}

function addProfile() {
  const n = draft.profiles.length + 1;
  const base = clone(draft.profiles[0]);
  Object.assign(base, { id: `profil${n}`, name: `Profil ${n}`, color: "#3ddc97", config_dir: `~/.claude-profil${n}`,
    workdir: `~/ClaudeConsole/profil${n}`, instructions: "", env: {} });
  base.mcp = { import_desktop: false, desktop_config: "", disabled_servers: [], extra_servers: {}, strict: false };
  draft.profiles.push(base);
  markDirty();
  render();
}

async function removeProfile(i) {
  const p = draft.profiles[i];
  if (!(await confirmDialog("Supprimer ce profil ?", `Le profil « ${p.name} » sera retiré de la console (son dossier Claude n'est pas touché).`, "Supprimer", "danger"))) return;
  draft.profiles.splice(i, 1);
  if (draft.general.default_profile === p.id) draft.general.default_profile = draft.profiles[0].id;
  markDirty();
  render();
}

function uniqueId(base) {
  let id = base.toLowerCase().normalize("NFKD").replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24) || "preset";
  let k = 2;
  const taken = new Set(draft.presets.map((p) => p.id));
  const root = id;
  while (taken.has(id)) id = `${root}-${k++}`;
  return id;
}

function duplicatePreset(i) {
  const p = clone(draft.presets[i]);
  p.id = uniqueId(`${p.id}-copie`);
  p.name = `${p.name} (copie)`.slice(0, 40);
  p.builtin = false;
  draft.presets.splice(i + 1, 0, p);
  presetSel = p.id;
  markDirty();
  render();
}

function newPreset() {
  const p = { id: uniqueId("perso"), name: "Nouveau preset", description: "", mode: "manual", tools: [], allow: ["Read", "Glob", "Grep"],
    deny: [], approval: [], validate_writes: true, unlisted: "ask", confine: "all", model: "", max_turns: null, enabled: true,
    require_confirm: false, require_dedicated_workdir: false, builtin: false };
  draft.presets.push(p);
  presetSel = p.id;
  markDirty();
  render();
}

async function removePreset(i) {
  const p = draft.presets[i];
  const users = draft.profiles.filter((x) => x.default_preset === p.id);
  if (users.length) return toast(`Preset utilisé par défaut par : ${users.map((x) => x.name).join(", ")}.`, "warn");
  if (!(await confirmDialog("Supprimer ce preset ?", `« ${p.name} » sera supprimé.`, "Supprimer", "danger"))) return;
  draft.presets.splice(i, 1);
  presetSel = draft.presets[0]?.id;
  markDirty();
  render();
}

async function rotate() {
  if (!(await confirmDialog("Régénérer le jeton ?", "L'ancien jeton cesse de fonctionner immédiatement.", "Régénérer"))) return;
  try { const { token } = await api("/api/security/rotate-token", { method: "POST" }); setToken(token); toast("Nouveau jeton actif.", "ok"); }
  catch (e) { toast(e.message, "err"); }
}

async function purge(days) {
  const msg = days == null ? "Toutes les tâches terminées et leur journal seront supprimés." : `Les tâches terminées depuis plus de ${days} jours seront supprimées.`;
  if (!(await confirmDialog("Purger l'historique ?", msg, "Purger", "danger"))) return;
  try { const { deleted } = await api("/api/history/purge", { method: "POST", body: { days } }); toast(`${deleted} tâche(s) supprimée(s).`, "ok"); }
  catch (e) { toast(e.message, "err"); }
}

async function rollback(id) {
  if (dirty && !(await confirmDialog("Abandonner les modifications ?", "Tes modifications non enregistrées seront perdues.", "Continuer"))) return;
  try { afterSave(await api("/api/config/rollback", { method: "POST", body: { id } }), "Version restaurée."); }
  catch (e) { showErrors(e); }
}

async function resetDefaults() {
  if (!(await confirmDialog("Revenir aux valeurs par défaut ?", "La configuration actuelle est gardée dans les versions précédentes.", "Réinitialiser", "danger"))) return;
  try { afterSave(await api("/api/config/reset", { method: "POST" }), "Valeurs par défaut restaurées."); }
  catch (e) { showErrors(e); }
}

function showErrors(e) {
  msgEl.className = "msg-area err";
  const errs = e.data?.errors;
  msgEl.replaceChildren(h("div", {}, e.message, errs?.length ? h("ul", { class: "cfg-errors" }, errs.map((x) => h("li", {}, x))) : null));
}

function afterSave(res, message) {
  draft = clone(res.config);
  meta = res.meta;
  dirty = false;
  msgEl.className = "msg-area";
  msgEl.textContent = res.restart_needed ? "Enregistré. Le nouveau port sera utilisé au prochain démarrage." : message;
  ctx.onSaved(res.config, res.meta);
  render();
}

async function save(quiet = false) {
  try {
    const res = await api("/api/config", { method: "PUT", body: draft });
    afterSave(res, "Enregistré : s'applique aux nouvelles tâches, jamais à celles en cours.");
    if (!quiet) toast("Configuration enregistrée.", "ok");
    return true;
  } catch (e) {
    showErrors(e);
    return false;
  }
}

async function close() {
  if (dirty && !(await confirmDialog("Fermer sans enregistrer ?", "Tes modifications seront perdues.", "Fermer", "danger"))) return;
  overlay.remove();
  overlay = null;
  document.documentElement.setAttribute("data-theme", ctx.theme());
  document.removeEventListener("keydown", onKey, true);
}

function onKey(e) {
  // Escape belongs to a confirmation dialog opened on top of the configuration, if any.
  if (e.key === "Escape" && !document.querySelector(".dialog:not(.cfg)")) { e.preventDefault(); close(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
}

function render() {
  navEl.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  const scroll = bodyEl.scrollTop;
  const content = { general: tabGeneral, profiles: tabProfiles, permissions: tabPermissions, integrations: tabIntegrations,
    models: tabModels, security: () => tabSecurity(ctx.state), interface: tabInterface, history: tabHistory }[tab]();
  bodyEl.replaceChildren(...content);
  bodyEl.scrollTop = scroll;
}

export async function openConfig(context, startTab = null) {
  ctx = context;
  if (overlay) { if (startTab) { tab = startTab; render(); } return; }
  const [{ config, meta: m }, pr] = await Promise.all([api("/api/config"), api("/api/probes").catch(() => ({ probes: {} }))]);
  draft = clone(config);
  meta = m;
  probes = pr.probes || {};
  dirty = false;
  if (startTab) tab = startTab;
  navEl = h("nav", { class: "cfg-nav" }, TABS.map(([id, label]) => h("button", { type: "button", dataset: { tab: id }, on: { click: () => { tab = id; bodyEl.scrollTop = 0; render(); } } }, label)));
  bodyEl = h("div", { class: "cfg-body" });
  msgEl = h("div", { class: "msg-area" }, "Les changements s'appliquent aux nouvelles tâches.");
  const box = h("div", { class: "dialog cfg", role: "dialog", "aria-modal": "true", "aria-label": "Configuration" },
    h("div", { class: "cfg-head" }, h("h2", {}, "Configuration"), h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close", on: { click: close } })),
    h("div", { class: "cfg-main" }, navEl, bodyEl),
    h("div", { class: "cfg-foot" }, msgEl,
      h("button", { type: "button", class: "btn ghost", on: { click: close } }, "Fermer"),
      h("button", { type: "button", class: "btn primary", on: { click: () => save() } }, "Enregistrer")));
  overlay = h("div", { class: "overlay" }, box);
  document.getElementById("modal-root").append(overlay);
  document.addEventListener("keydown", onKey, true);
  render();
}

export function updateProbe(pid, probe) {
  probes[pid] = { ...(probes[pid] || {}), ...probe };
}
