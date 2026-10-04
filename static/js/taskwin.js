// One task = one window: compact header, timeline, inspector panel, approvals, follow-up.
import { api } from "./api.js";
import { Attacher } from "./attach.js";
import { mdElement } from "./md.js";
import {
  ACTIVE, STATUS, confirmDialog, planProgress, statusLabel, copyText, dialog, fmtCost, fmtDuration, fmtTokens, h, iconBtn, modelName, toast, toolIcon, toolLabel,
} from "./util.js";
import { changesUpdated, counts, openChanges, openDiff } from "./changes.js";
import { openDisplayModal, openDisplayWindow, renderDisplay, setAnswer, setDoc } from "./display.js";
import * as regard from "./regard.js";
import { paint, taskTint } from "./tint.js";
import { openPreview, revealImage, thumbnail } from "./viewer.js";
import * as wm from "./wm.js";

const SUBAGENT_TOOLS = new Set(["Task", "Agent"]);
const FILE_KEYS = { Read: "file_path", Write: "file_path", Edit: "file_path", MultiEdit: "file_path", NotebookEdit: "notebook_path" };
const IMG_FILE = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
const AGENT_COLORS = ["var(--violet)", "#4fc8c0", "#f39ac0", "#a6d46e", "#f0b45c", "#8fb3ff"];
const GROUP_FOLD = 3; // consecutive actions folded into one line once the model moves on

export function autoGrow(el, max) {
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight + 2, max)}px`;
}

/** A menu or popover next to its anchor, in the anchor's document (a native window's, in the app). */
function place(el, anchor, width = 220) {
  const doc = anchor.ownerDocument, view = doc.defaultView;
  doc.body.append(el);
  const r = anchor.getBoundingClientRect();
  const w = Math.max(width, el.offsetWidth);
  Object.assign(el.style, {
    top: `${Math.max(8, Math.min(r.bottom + 6, view.innerHeight - el.offsetHeight - 8))}px`,
    left: `${Math.max(8, Math.min(r.right - w, view.innerWidth - w - 8))}px`,
  });
}

// The prompt cache lasts about an hour on Claude plans (measured on real sessions): after that, the
// next action sends the whole context again.
const CACHE_TTL = 55 * 60;
const BIG_CONTEXT = 80000;
const ROLE = { eclaireur: "éclaireur", executant: "exécutant", expert: "expert" };
const agentLabel = (type) => (type === "chef" ? "Chef" : ROLE[type] ? ROLE[type][0].toUpperCase() + ROLE[type].slice(1) : type || "Sous-agent");
const serverKey = (name) => String(name || "").replace(/[^A-Za-z0-9_-]/g, "_");
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 10 ? "à l'instant" : `il y a ${fmtDuration(s * 1000)}`;
};

/** A chip per tool with its count; MCP tools stand out (plug icon, accent). */
function toolChips(tools, max = 99) {
  const list = Object.entries(tools || {}).sort((a, b) => b[1] - a[1]);
  return h("div", { class: "tchips" },
    ...list.slice(0, max).map(([name, n]) => h("span", { class: `tchip${name.startsWith("mcp__") ? " mcp" : ""}`, title: name },
      name.startsWith("mcp__") ? svg("plug") : null, h("span", {}, toolLabel(name)), h("b", {}, String(n)))),
    list.length > max ? h("span", { class: "tchip more" }, `+${list.length - max}`) : null);
}

function dismissable(el, onClose) {
  const doc = el.ownerDocument;
  const off = (e) => { if (!el.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  function close() {
    el.remove();
    doc.removeEventListener("pointerdown", off, true);
    doc.removeEventListener("keydown", esc, true);
    onClose?.();
  }
  setTimeout(() => { doc.addEventListener("pointerdown", off, true); doc.addEventListener("keydown", esc, true); });
  return close;
}

export function popupMenu(anchor, items) {
  anchor.ownerDocument.querySelectorAll(".menu.popup, .popover").forEach((m) => m.remove());
  const menu = h("div", { class: "menu popup" });
  // (the base .menu is anchored right: 0 for the top bar; here it must size to its content)
  Object.assign(menu.style, { position: "fixed", zIndex: "9000", right: "auto", bottom: "auto", maxWidth: "360px" });
  let close = () => {};
  for (const it of items) {
    if (it === "-") { menu.append(h("hr")); continue; }
    if (it.title) { menu.append(h("div", { class: "menu-title" }, it.title)); continue; }
    menu.append(h("button", { type: "button", class: it.danger ? "danger" : "", disabled: it.disabled,
      on: { click: () => { close(); it.run(); } } }, it.label));
  }
  place(menu, anchor, 210);
  close = dismissable(menu);
}

export function popover(anchor, ...content) {
  anchor.ownerDocument.querySelectorAll(".menu.popup, .popover").forEach((m) => m.remove());
  const el = h("div", { class: "popover" }, ...content);
  place(el, anchor, 260);
  dismissable(el);
}

const svg = (name, cls = "") => h("span", { class: `i ${cls}`, svg: name });

function pretty(v) {
  if (v && typeof v === "object" && v["_tronqué"]) return String(v["aperçu"] || "");
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

/** A tool call's input, readable (the approval cards, the inbox's Détail). */
export function inputView(tool, inp) {
  const box = h("div");
  if (!inp || typeof inp !== "object") return box;
  if (inp["_tronqué"]) { box.append(h("pre", {}, pretty(inp))); return box; }
  const pre = (label, text) => { if (text) box.append(h("div", { class: "fl" }, label), h("pre", {}, String(text).slice(0, 20000))); };
  if (tool === "Bash" || tool === "PowerShell") {
    pre("Commande", inp.command);
    if (inp.description) box.append(h("div", { class: "muted" }, inp.description));
  } else if (tool === "Write") {
    pre("Fichier", inp.file_path); pre("Contenu", inp.content);
  } else if (tool === "Edit") {
    pre("Fichier", inp.file_path); pre("Remplacer", inp.old_string); pre("Par", inp.new_string);
  } else if (tool === "MultiEdit") {
    pre("Fichier", inp.file_path);
    (inp.edits || []).forEach((e, i) => { pre(`Remplacer (${i + 1})`, e.old_string); pre("Par", e.new_string); });
  } else if (SUBAGENT_TOOLS.has(tool)) {
    pre("Consigne donnée au sous-agent", inp.prompt);
  } else {
    box.append(h("pre", {}, pretty(inp)));
  }
  return box;
}

const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const mcpCls = (s) => (s === "connected" ? "" : s === "pending" ? "wait" : "ko");

export class TaskWindow {
  constructor(task, ctx) {
    this.id = task.id;
    this.task = task;
    this.ctx = ctx;
    this.lastSeq = 0;
    this.live = new Map();
    this.tools = new Map();
    this.agents = new Map();
    this.toolCounts = new Map();
    this.apprEls = new Map();
    this.changes = new Map(); // file changes of the discussion (console/changes.py), by id
    this.lastText = "";
    this.build();
  }

  // ------------------------------------------------------------ layout
  build() {
    const t = this.task;
    this.el = paint(h("section", { class: "win", "data-id": t.id, "aria-label": t.title }), taskTint(t));
    this.whoName = h("span", { class: "nm" });
    this.titleEl = h("span", { class: "win-title" });
    this.statusEl = h("span", { class: "status" });
    this.metaEl = h("span", { class: "win-meta" });
    this.stopBtn = iconBtn("stop", "Annuler la tâche", () => this.cancel(), "danger");
    this.inspBtn = iconBtn("panel", "Panneau agents et activité", () => this.toggleInspector());
    this.pinBtn = iconBtn("pin", "Épingler au premier plan", () => this.pin());
    this.head = h("header", { class: "win-head" },
      h("span", { class: "who" }, h("span", { class: "sw" }), this.whoName), h("span", { class: "slash" }, "/"),
      this.titleEl, this.statusEl, this.metaEl,
      h("div", { class: "win-actions" }, this.stopBtn, this.inspBtn,
        iconBtn("more", "Actions", (e) => this.menu(e.currentTarget)), this.pinBtn,
        iconBtn("min", "Réduire", () => wm.minimize(this.id)), iconBtn("close", "Fermer la fenêtre", () => this.close())));
    this.sub = h("div", { class: "win-sub" });
    this.plan = h("div", { class: "win-plan", hidden: true });
    this.body = h("div", { class: "win-body" });
    this.insp = h("aside", { class: "insp", "aria-label": "Inspecteur" });
    this.main = h("div", { class: "win-main" }, this.body, this.insp);
    this.approvals = h("div", { class: "win-approvals", "aria-live": "polite" });
    this.input = h("textarea", { rows: "1", placeholder: "Message de suite…" });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.sendFollowup(); }
    });
    this.input.addEventListener("input", () => autoGrow(this.input, 120));
    this.attList = h("div", { class: "att-list win-att" });
    this.att = new Attacher(this.attList);
    this.att.bindDrop(this.el);
    this.att.bindPaste(this.input);
    // "Ce que je regarde": shown while this window has the focus, sent with the follow-up
    this.regardList = h("div", { class: "att-list win-att win-regard" });
    this.regardChip = regard.chip(this.regardList, { visible: () => wm.focused() === this.id, here: this.id });
    this.foot = h("footer", { class: "win-foot" }, this.input, this.att.button("icon-btn"),
      h("button", { type: "button", class: "send-mini", title: "Envoyer (Entrée)", "aria-label": "Envoyer", svg: "send",
        on: { click: () => this.sendFollowup() } }));
    this.el.append(this.head, this.sub, this.plan, this.main, this.approvals, this.regardList, this.attList, this.foot);
    this.timer = setInterval(() => this.tick(), 1000);
    // What the session does, from its transcripts: every agent's tools, background sub-agents included
    this.activity = null;
    this.actTimer = setInterval(() => { if (ACTIVE.has(this.task.status) && !wm.isMinimized(this.id)) this.refreshActivity(); }, 4000);
    this.update(t);
  }

  async refreshActivity() {
    if (!this.task.resumable || this.actBusy) return;
    this.actBusy = true;
    try {
      const a = await api(`/api/tasks/${this.id}/activity`);
      this.activity = a.available ? a : null;
      this.updateAgentCards();
      if (this.inspOpen) this.renderInspector();
    } catch { /* the conversation still shows what the stream brought */ } finally {
      this.actBusy = false;
    }
  }

  /** Sub-agent cards of the conversation: their tools, live (the stream shows none for background ones). */
  updateAgentCards() {
    for (const g of this.activity?.agents || []) {
      const a = g.tool_use_id && this.agents.get(g.tool_use_id);
      if (!a) continue;
      const n = Object.values(g.tools).reduce((x, y) => x + y, 0);
      a.live.replaceChildren(toolChips(g.tools, 6));
      if (g.background) {
        const busy = ACTIVE.has(this.task.status) && Date.now() / 1000 - (g.last || 0) < 30;
        a.sm.replaceChildren(`${n} action${n > 1 ? "s" : ""} `, busy ? h("span", { class: "spin" }) : svg("check", "ok-ic"));
      }
    }
  }

  mount(fresh) {
    wm.register(this.id, this.el, { handle: this.head, fresh, onFocus: () => this.ctx.onFocus?.(this.id),
      onClose: () => this.close(), title: this.windowTitle() });
    let open = wm.flag(this.id, "insp");
    if (open === undefined) open = wm.width(this.id) >= 720;
    this.setInspector(open, false);
    this.renderPlan();
  }

  destroy() {
    clearInterval(this.timer);
    clearInterval(this.actTimer);
    this.att.picker.remove();
    this.regardChip.dispose();
    wm.unregister(this.id);
  }

  setInspector(open, save = true) {
    this.inspOpen = open;
    this.insp.hidden = !open;
    this.inspBtn.classList.toggle("on", open);
    if (save) wm.flag(this.id, "insp", open);
    if (open) { this.renderInspector(); this.refreshActivity(); }
  }

  toggleInspector() { this.setInspector(!this.inspOpen); }

  elapsed() {
    const t = this.task;
    if (!t.started) return 0;
    const end = t.ended && !ACTIVE.has(t.status) ? t.ended : Date.now() / 1000;
    return (end - t.started) * 1000;
  }

  tick() {
    const t = this.task;
    this.metaEl.textContent = t.started ? fmtDuration(this.elapsed()) : "";
    if (this.inspOpen && (ACTIVE.has(t.status) || [...this.agents.values()].some((a) => a.status === "running"))) this.renderInspector();
  }

  modelLabel() {
    const m = this.task.model_resolved || this.task.model || "";
    return m.replace(/^claude-/, "").replace(/-\d{8}$/, "");
  }

  tokens() {
    const mu = Object.values(this.task.model_usage || {});
    if (mu.length) {  // every model of the session, sub-agents included
      return mu.reduce((n, u) => n + (u.inputTokens || 0) + (u.outputTokens || 0) + (u.cacheReadInputTokens || 0) + (u.cacheCreationInputTokens || 0), 0);
    }
    const u = this.task.usage || {};
    return (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  }

  /** The lead's context: size, compaction threshold, and whether the prompt cache is still warm. */
  contextInfo() {
    const t = this.task;
    const size = t.context_tokens || 0, limit = t.context_limit || 0;
    const now = Date.now() / 1000;
    const idle = t.context_at ? Math.max(0, now - t.context_at) : 0;
    const touched = Math.max(t.context_at || 0, t.warm?.last || 0);   // a keep-warm read restarts the cache's hour
    const warmUntil = (t.keep_warm_until || 0) > now ? t.keep_warm_until : 0;
    return { size, limit, ratio: limit ? size / limit : 0, idle, cold: touched ? now - touched > CACHE_TTL : false, warmUntil };
  }

  contextChip() {
    const c = this.contextInfo();
    if (!c.size) return null;
    const lvl = c.ratio >= 0.85 ? " hot" : c.ratio >= 0.6 ? " warn" : "";
    const w = this.task.warm || {};
    const tip = [`Contexte de la session : ${fmtTokens(c.size)} tokens, relus à chaque action de Claude.`,
      c.limit ? `Compactage automatique vers ${fmtTokens(c.limit)}.` : "",
      c.warmUntil ? `Cache gardé au chaud jusqu'à ${hhmm(c.warmUntil)}${w.pings ? ` · ${w.pings} maintien${w.pings > 1 ? "s" : ""} (${fmtTokens(w.read)} relus)` : ""}.` : "",
      c.cold ? `Dernière activité il y a ${fmtDuration(c.idle * 1000)} : le cache a expiré, la prochaine action renverra tout le contexte.` : "",
      "Cliquer : compacter, garder au chaud ou repartir sur une nouvelle demande."].filter(Boolean).join("\n");
    return h("button", { type: "button", class: `meta-chip ctx${lvl}${c.cold ? " cold" : ""}${c.warmUntil ? " warm" : ""}`, title: tip,
      on: { click: (e) => this.contextMenu(e.currentTarget) } },
    svg(c.warmUntil ? "flame" : "gauge"), h("span", {}, `Contexte ${fmtTokens(c.size)}${c.warmUntil ? " · chaud" : ""}`),
    c.limit ? h("span", { class: "ctx-bar" }, h("i", { style: { width: `${Math.min(100, Math.round(c.ratio * 100))}%` } })) : null);
  }

  contextMenu(anchor) {
    const t = this.task, c = this.contextInfo();
    popupMenu(anchor, [
      { title: `Contexte : ${fmtTokens(c.size)}${c.limit ? ` sur ${fmtTokens(c.limit)}` : ""} tokens` },
      { label: "Compacter maintenant", disabled: !t.resumable, run: () => this.compact() },
      c.warmUntil
        ? { label: `Arrêter le maintien au chaud (prévu jusqu'à ${hhmm(c.warmUntil)})`, run: () => this.keepWarm(0) }
        : { label: "Garder au chaud…", disabled: !t.resumable, run: () => this.keepWarmDialog() },
      { label: "Utiliser comme contexte d'une nouvelle demande", disabled: !t.resumable, run: () => this.ctx.addContext(t) },
    ]);
  }

  /** What compacting costs: one full read of the context by the session's model, to write the summary. */
  compactCost(c) {
    return `Compacter fait relire une fois tout le contexte (${fmtTokens(c.size)} tokens) par ${this.modelLabel() || "le modèle de la session"} pour le résumer. `
      + (c.cold ? "Le cache a expiré : cette relecture coûte autant que de renvoyer toute la discussion. "
        : "Le cache est encore chaud : ça coûte à peu près une action de plus. ")
      + "C'est rentable si tu continues à travailler ici (chaque action relira ensuite le résumé au lieu de tout), pas pour une seule question.";
  }

  /** Keep the prompt cache from expiring while the discussion rests (a read every 50 min). */
  async keepWarmDialog() {
    const c = this.contextInfo();
    const hours = await dialog({
      title: "Garder le cache au chaud ?",
      body: `Toutes les 50 minutes, tant que la discussion est inactive, une copie jetable de la session relit le contexte depuis le cache `
        + `(${fmtTokens(c.size)} tokens, avec ${this.modelLabel() || "le modèle de la session"}) pour qu'il n'expire pas. Rien n'est écrit dans la conversation. `
        + "Chaque maintien coûte environ 1/20e d'une reprise à froid : c'est rentable si tu reviens dans cette discussion, perdu sinon. "
        + (c.cold ? "Le cache a déjà expiré : le premier maintien le recréera, ce qui coûte comme une reprise. " : "")
        + "Arrêt automatique à la fin de la durée, à la fermeture de la fenêtre ou si le quota du compte approche de sa limite.",
      buttons: [{ label: "Annuler", value: null }, { label: "1 h", value: 1 }, { label: "2 h", value: 2 },
        { label: "4 h", value: 4, cls: "primary" }, { label: "8 h", value: 8 }],
    });
    if (hours) this.keepWarm(hours);
  }

  async keepWarm(hours) {
    try {
      await api(`/api/tasks/${this.id}/keep-warm`, { method: "POST", body: { hours } });
      toast(hours ? `Cache gardé au chaud pendant ${hours} h.` : "Maintien au chaud arrêté.");
    } catch (e) { toast(e.message, "err"); }
  }

  /** Claude Code summarizes the conversation (/compact): the next actions re-read a few thousand tokens. */
  async compact() {
    const c = this.contextInfo();
    if (!(await confirmDialog("Compacter la discussion ?", this.compactCost(c), "Compacter"))) return;
    try {
      await api(`/api/tasks/${this.id}/message`, { method: "POST", body: { text: "", compact: true } });
      toast("Compactage demandé à Claude Code…");
    } catch (e) { toast(e.message, "err"); }
  }

  /** Which member of the team a model is: the lead, a team role, or Claude Code's own sub-agents. */
  modelRole(id) {
    const t = this.task, out = [];
    if (id === t.model_resolved) out.push("chef");
    for (const [name, alias] of Object.entries(t.team_agents || {})) {
      if (alias && id !== t.model_resolved && id.toLowerCase().includes(String(alias).toLowerCase())) out.push(ROLE[name] || name);
    }
    if (!out.length && t.model_resolved) out.push("sous-agents");
    return [...new Set(out)].join(", ");
  }

  /** The account's color, mixed with the project's when the discussion is in a project. */
  tint() { return taskTint(this.task); }
  repaint() { paint(this.el, this.tint()); wm.colorize(this.id); }

  update(t) {
    const before = this.task?.status;
    this.task = t;
    if (before && before !== t.status && !ACTIVE.has(t.status)) this.refreshActivity();  // the final figures
    this.repaint();
    this.el.setAttribute("aria-label", t.title);
    wm.setTitle(this.id, this.windowTitle());
    this.whoName.textContent = t.profile_name;
    this.titleEl.textContent = t.title;
    this.titleEl.title = t.prompt;
    this.statusEl.className = `status s-${t.status}`;
    this.statusEl.textContent = statusLabel(t);
    this.stopBtn.hidden = !ACTIVE.has(t.status);
    this.pinBtn.classList.toggle("on", !!t.pinned);
    this.tick();
    this.renderSub();
    this.renderPlan();
    this.renderApprovals(t.pending || []);
    if (this.inspOpen) this.renderInspector();
  }

  /** The native window's title (taskbar, Alt+Tab): the status first when it calls for the user. */
  windowTitle() {
    const t = this.task, p = planProgress(t);
    const step = t.status === "running" && p.total ? `${p.done}/${p.total} · ` : "";
    return `${t.status === "awaiting" ? "À valider · " : step}${t.title} — ${t.profile_name || "JARVIS"}`;
  }

  renderSub() {
    const t = this.task;
    const chip = (icon, text, title, opt = false) =>
      h("span", { class: `meta-chip${opt ? " opt" : ""}`, title }, svg(icon), h("span", {}, text));
    const bits = [];
    if (t.team) {
      const roles = Object.entries(t.team_agents || {}).map(([k, m]) => `${k} ${m}`).join(", ");
      bits.push(h("span", { class: "meta-chip team", title: `Mode équipe : ${this.modelLabel()} dirige ; ${roles}` },
        svg("bot"), h("span", {}, `Équipe · ${Object.values(t.team_agents || {}).join(" / ")}`)));
    }
    if (t.routine) bits.push(chip("clock", `Routine · ${t.routine.name}`, "Lancée par une routine"));
    if (t.action) bits.push(chip("bolt", `Action · /${t.action}`, "Exécution d'une action du projet : elle figure dans son historique (onglet Actions du projet)"));
    if (t.resumed_from) bits.push(chip(t.origin === "copie" ? "branch" : "retry",
      t.origin === "reprise desktop" ? "Reprise Claude Desktop" : t.origin === "copie" ? "Copie d'une discussion"
        : t.origin === "déplacée" ? "Déplacée dans ce projet" : "Reprise",
      `Suite de la session ${t.resumed_from}${t.fork_next === false ? " (copie)" : ""}`));
    bits.push(
      chip("shieldq", t.preset_name, `Autorisations : ${t.preset_name}${t.preset_mode ? ` (${t.preset_mode})` : ""}`),
      chip("model", this.modelLabel() || "modèle", `Modèle : ${t.model_resolved || t.model}`),
    );
    if (t.effort) bits.push(chip("gauge", t.effort, `Effort : ${t.effort}`, true));
    bits.push(chip("folder", this.ctx.projectName?.(t.workdir) || baseName(t.workdir), `Dossier de travail : ${t.workdir}`, true));
    const mcp = t.mcp || [];
    if (mcp.length) {
      const ok = mcp.filter((s) => s.status === "connected").length;
      const dots = h("span", { class: "dots" }, ...mcp.slice(0, 8).map((s) => h("i", { class: mcpCls(s.status) })));
      bits.push(h("button", { type: "button", class: "meta-chip", title: "Serveurs MCP de la session",
        on: { click: (e) => this.mcpPopover(e.currentTarget) } },
        svg("plug"), h("span", {}, `${mcp.length} MCP${ok < mcp.length ? ` · ${ok} actifs` : ""}`), dots));
    }
    if (t.queued_messages) bits.push(chip("clock", `${t.queued_messages} en attente`, "Messages de suite en attente"));
    const ctxChip = this.contextChip();
    if (ctxChip) bits.push(ctxChip);
    this.sub.replaceChildren(...bits);
  }

  /** Claude's plan above the conversation: progress and the step under way; a click shows every step. */
  renderPlan() {
    const t = this.task, p = planProgress(t);
    this.plan.hidden = !p.total;
    if (!p.total) return;
    const open = !!wm.flag(this.id, "plan");
    const finished = p.done === p.total;
    const running = ACTIVE.has(t.status) && !finished;
    const head = h("button", { type: "button", class: "plan-head", "aria-expanded": String(open),
      title: open ? "Replier le plan" : "Voir toutes les étapes du plan de Claude",
      on: { click: () => { wm.flag(this.id, "plan", !open); this.renderPlan(); } } },
    svg("list"), h("b", {}, "Plan"), h("span", { class: "plan-n" }, `${p.done}/${p.total}`),
    h("span", { class: "plan-bar" }, h("i", { style: { width: `${Math.round((p.done / p.total) * 100)}%` } })),
    h("span", { class: "plan-cur" }, finished ? "Toutes les étapes sont faites" : p.current),
    running ? h("span", { class: "spin" }) : null, svg("chev", "plan-chev"));
    const list = open ? h("div", { class: "plan-list" }, ...p.todos.map((x) => h("div", { class: `todo ${x.status}` },
      h("span", { class: "b", svg: x.status === "completed" ? "check" : "" }),
      h("span", {}, x.status === "in_progress" && x.active ? x.active : x.content)))) : null;
    this.plan.classList.toggle("done", finished);
    this.plan.classList.toggle("open", open);
    this.plan.replaceChildren(...[head, list].filter(Boolean));
  }

  mcpPopover(anchor) {
    const mcp = this.task.mcp || [];
    popover(anchor, h("h5", {}, `Serveurs MCP · ${mcp.length}`),
      h("div", { class: "mcp-list" }, ...mcp.map((s) => h("div", { class: "mcp-row" },
        h("i", { class: mcpCls(s.status) }), h("span", {}, String(s.name || "").replace(/^claude\.ai /, "")),
        h("em", {}, s.status === "connected" ? (String(s.name).startsWith("claude.ai") ? "connecteur" : "actif") : s.status)))),
      h("div", { class: "note" }, "Réglages : Configuration → Intégrations."));
  }

  // ------------------------------------------------------------ inspector
  renderInspector() {
    const t = this.task;
    const secs = [];
    // Agents
    const kids = (pid) => [...this.agents.values()].filter((a) => a.parent === pid);
    const stIcon = (status) => status === "running" ? h("span", { class: "spin" })
      : status === "error" ? svg("x", "ko-ic") : svg("check", "ok-ic");
    const rootStatus = ACTIVE.has(t.status) ? "running" : t.status === "done" ? "done" : "error";
    const node = (a) => {
      const children = kids(a.id);
      const dur = a.started ? fmtDuration(((a.ended || Date.now() / 1000) - a.started) * 1000) : "";
      return h("li", {},
        h("div", { class: "agent", style: { "--ac": a.color }, title: a.desc || a.type, on: { click: () => this.focusAgent(a.id) } },
          h("span", { class: "ic", svg: "bot" }),
          h("span", { class: "nm" }, h("b", {}, a.type || "Sous-agent", a.model ? h("span", { class: "mdl" }, a.model) : null),
            h("small", {}, [a.desc, `${a.tools} action${a.tools > 1 ? "s" : ""}`, dur].filter(Boolean).join(" · "))),
          h("span", { class: "st" }, stIcon(a.status))),
        children.length ? h("ul", {}, ...children.map(node)) : null);
    };
    const top = kids(null);
    const act = this.activity;
    if (act) secs.push(this.agentsSection(act));
    else secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Agents", h("span", { class: "n" }, String(1 + this.agents.size))),
      h("ul", { class: "tree" }, h("li", {},
        h("div", { class: "agent", style: { "--ac": t.color }, on: { click: () => { this.body.scrollTop = 0; } } },
          h("span", { class: "ic", svg: "bot" }),
          h("span", { class: "nm" }, h("b", {}, t.team ? "Chef d'équipe" : "Agent principal"),
            h("small", {}, [this.modelLabel(), `${this.rootTools()} action(s)`].filter(Boolean).join(" · "))),
          h("span", { class: "st" }, stIcon(rootStatus))),
        top.length ? h("ul", {}, ...top.map(node)) : null)),
      this.agents.size ? null : h("div", { class: "note" }, "Les sous-agents lancés par Claude apparaîtront ici.")));
    if (act) secs.push(this.mcpSection(act));
    // Activity (all agents when the transcripts are readable, else what the stream brought)
    const counts = new Map(this.toolCounts);
    if (act) {
      counts.clear();
      for (const g of act.agents) for (const [name, n] of Object.entries(g.tools)) counts.set(toolLabel(name), (counts.get(toolLabel(name)) || 0) + n);
    }
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    const top6 = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
    const max = top6[0]?.[1] || 1;
    secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Activité"),
      h("div", { class: "stats" },
        h("div", { class: "stat" }, h("b", {}, fmtDuration(this.elapsed())), h("span", {}, "durée")),
        h("div", { class: "stat" }, h("b", {}, String(total)), h("span", {}, "actions")),
        h("div", { class: "stat" }, h("b", {}, String(t.turns || 0)), h("span", {}, "tours")),
        h("div", { class: "stat", title: "Tokens traités (entrée, sortie et cache) : décomptés de ton quota" },
          h("b", {}, fmtTokens(this.tokens())), h("span", {}, "tokens"))),
      top6.length ? h("div", { class: "bars" }, ...top6.map(([name, n]) => h("div", { class: "bar", style: { "--w": `${Math.round((n / max) * 100)}%` } },
        h("span", {}, name), h("span", {}, String(n)), h("i")))) : null,
      t.cost_usd ? h("div", { class: "note", title: "Estimation calculée par Claude Code au tarif API" },
        `Équivalent API estimé : ${fmtCost(t.cost_usd)}. Avec un abonnement Claude, l'usage est décompté du quota du forfait ; il n'est facturé en plus que si les crédits d'usage sont activés et le quota dépassé.`) : null));
    if (act) secs.push(this.recentSection(act));
    // Usage per model (lead and sub-agents): what weighs on the quota
    const fromLog = act && Object.keys(act.models || {}).length;
    const mu = (fromLog
      ? Object.entries(act.models).map(([id, u]) => ({ id, calls: u.calls, u: { cacheCreationInputTokens: u.cache_creation_input_tokens, outputTokens: u.output_tokens },
        read: (u.cache_read_input_tokens || 0) + (u.input_tokens || 0) }))
      : Object.entries(t.model_usage || {}).map(([id, u]) => ({ id, u, read: (u.cacheReadInputTokens || 0) + (u.inputTokens || 0) })))
      .sort((a, b) => b.read - a.read);
    if (mu.length) {
      const c = this.contextInfo();
      const all = mu.reduce((n, m) => n + m.read, 0) || 1;
      secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Consommation"),
        h("table", { class: "usage" },
          h("thead", {}, h("tr", {}, h("th", {}, "Modèle"), fromLog ? h("th", { title: "Appels à l'API" }, "Appels") : null,
            h("th", { title: "Tokens relus (cache et entrée) : le gros du quota, chaque action relit tout le contexte" }, "Relus"),
            h("th", { title: "Tokens mis en cache : le contexte envoyé la première fois ou après expiration du cache" }, "Écrits"),
            h("th", { title: "Tokens produits par le modèle" }, "Produits"))),
          h("tbody", {}, ...mu.map(({ id, u, read, calls }) => h("tr", {},
            h("td", { title: `${id} · ${Math.round((read / all) * 100)} % des tokens relus` }, h("b", {}, modelName(id)),
              h("small", {}, this.modelRole(id)), h("i", { class: "share", style: { width: `${Math.max(2, Math.round((read / all) * 100))}%` } })),
            fromLog ? h("td", {}, String(calls || 0)) : null,
            h("td", {}, fmtTokens(read)), h("td", {}, fmtTokens(u.cacheCreationInputTokens || 0)),
            h("td", {}, fmtTokens(u.outputTokens || 0)))))),
        c.size ? h("div", { class: "note" }, `Contexte actuel du chef : ${fmtTokens(c.size)} tokens${c.limit ? `, compacté automatiquement vers ${fmtTokens(c.limit)}` : ""}. Chacune de ses actions le relit.`) : null,
        t.warm?.pings ? h("div", { class: "note" }, `Maintien du cache : ${t.warm.pings} fois · ${fmtTokens(t.warm.read)} relus en cache`
          + `${t.warm.written >= 1000 ? ` · ${fmtTokens(t.warm.written)} réécrits` : ""}${c.warmUntil ? ` · actif jusqu'à ${hhmm(c.warmUntil)}` : ""}.`) : null));
    }
    // Session
    const mcp = t.mcp || [];
    secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Session"),
      h("dl", { class: "kvs" },
        h("dt", {}, "Compte"), h("dd", {}, t.profile_name),
        h("dt", {}, "Preset"), h("dd", {}, `${t.preset_name}${t.preset_mode ? ` · ${t.preset_mode}` : ""}`),
        h("dt", {}, "Dossier"), h("dd", {}, t.workdir),
        t.resumable ? h("dt", {}, "Session") : null, t.resumable ? h("dd", { title: "claude --resume" }, t.session_id) : null),
      mcp.length ? h("div", { class: "mcp-list" }, ...mcp.map((s) => h("div", { class: "mcp-row" },
        h("i", { class: mcpCls(s.status) }), h("span", {}, String(s.name || "").replace(/^claude\.ai /, "")), h("em", {}, s.status)))) : null));
    this.insp.replaceChildren(...secs.filter(Boolean));
  }

  /** Lead and sub-agents (background ones too) with their model, calls and tools. */
  agentsSection(act) {
    const t = this.task;
    const rows = act.agents.map((g) => {
      const lead = g.type === "chef";
      const busy = ACTIVE.has(t.status) && Date.now() / 1000 - (g.last || 0) < 30;
      const card = !lead && g.tool_use_id && this.agents.get(g.tool_use_id);
      return h("div", { class: `agent${lead ? " lead" : ""}`, style: { "--ac": card?.color || (lead ? t.color : "var(--violet)") },
        title: g.description || agentLabel(g.type), on: { click: () => (card ? this.focusAgent(g.tool_use_id) : lead && (this.body.scrollTop = 0)) } },
      h("span", { class: "ic", svg: "bot" }),
      h("span", { class: "nm" },
        h("b", {}, lead ? (t.team ? "Chef d'équipe" : "Agent principal") : agentLabel(g.type),
          g.models[0] ? h("span", { class: "mdl" }, g.models.map(modelName).join(", ")) : null),
        h("small", {}, [!lead && g.description, `${g.calls} appel${g.calls > 1 ? "s" : ""}`, `${fmtTokens(g.read)} relus`,
          g.last ? ago(g.last) : ""].filter(Boolean).join(" · ")),
        Object.keys(g.tools).length ? toolChips(g.tools) : h("small", { class: "muted" }, "aucun outil utilisé")),
      h("span", { class: "st" }, busy ? h("span", { class: "spin" }) : svg("check", "ok-ic")));
    });
    return h("section", { class: "insp-sec" }, h("h4", {}, "Agents", h("span", { class: "n" }, String(act.agents.length))), ...rows);
  }

  /** MCP servers Claude really used in the session (the full list sits behind the MCP chip). */
  mcpSection(act) {
    const status = new Map((this.task.mcp || []).map((s) => [serverKey(s.name), s.status]));
    const names = Object.keys(act.mcp || {}).filter((n) => act.mcp[n] > 0).sort();
    if (!names.length) return null;
    return h("section", { class: "insp-sec" }, h("h4", {}, "Serveurs MCP"),
      ...names.map((n) => {
        const calls = act.mcp[n];
        return h("div", { class: "mcp-use" }, h("i", { class: mcpCls(status.get(n) || "connected") }),
          h("span", {}, n.replace(/^claude_ai_/, "claude.ai · ")),
          h("em", {}, `${calls} appel${calls > 1 ? "s" : ""}`));
      }));
  }

  /** The latest tool calls, all agents together. */
  recentSection(act) {
    if (!act.recent?.length) return null;
    return h("section", { class: "insp-sec" }, h("h4", {}, "Derniers appels d'outils"),
      h("div", { class: "recent" }, ...act.recent.slice(0, 18).map((r) => h("div", { class: `rc${r.tool.startsWith("mcp__") ? " mcp" : ""}`, title: r.target || r.tool },
        h("span", { class: "tm" }, new Date(r.ts * 1000).toLocaleTimeString("fr-FR")),
        h("span", { class: `who${r.agent === "chef" ? " lead" : ""}` }, agentLabel(r.agent)),
        h("span", { class: "tl" }, toolLabel(r.tool)),
        h("span", { class: "tg" }, r.target || "")))));
  }

  rootTools() {
    return [...this.tools.values()].filter((x) => !x.parent).length + [...this.agents.values()].filter((a) => !a.parent).length;
  }

  focusAgent(id) {
    const a = this.agents.get(id);
    if (!a) return;
    let p = a;
    while (p) { p.card.open = true; p = this.agents.get(p.parent); }
    a.card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    a.card.classList.remove("flash");
    void a.card.offsetWidth;
    a.card.classList.add("flash");
  }

  // ------------------------------------------------------------ timeline
  nearBottom() { return this.body.scrollHeight - this.body.scrollTop - this.body.clientHeight < 80; }

  container(parent) { return (parent && this.agents.get(parent)?.body) || this.body; }

  /** Append a node; the action group it follows folds once the model moves on. */
  push(node, parent) {
    const stick = this.nearBottom();
    const box = this.container(parent);
    const last = box.lastElementChild;
    if (last?.classList.contains("tgroup") && !last.dataset.touched && last._n >= GROUP_FOLD && !last._pending) last.open = false;
    box.append(node);
    if (stick) this.body.scrollTop = this.body.scrollHeight;
  }

  /** The user's message, with its attachments (click: preview). */
  userMsg(d) {
    const box = h("div", { class: "msg user" }, d.text || "");
    if (d.files?.length) {
      const list = h("div", { class: "msg-files" }, ...d.files.map((f) => h("a", {
        href: "#", class: `fileref att-ref${f.context ? " ctx" : ""}${IMG_FILE.test(f.name) ? " img" : ""}`, "data-path": f.path,
        title: f.context ? `Contexte : transcription de « ${f.title} »` : `Aperçu : ${f.path}`,
      }, svg(f.context ? "link" : "clip"), h("span", {}, f.context ? f.title : f.name))));
      box.append(list);
      list.querySelectorAll(".fileref.img").forEach((el) => thumbnail(el, this.id, this.tint()));
    }
    if (d.regard) {
      const r = d.regard;
      const what = r.path ? h("a", { href: "#", class: "fileref", "data-path": r.path, title: `Aperçu : ${r.path}` }, r.label) : h("span", {}, r.label);
      box.append(h("div", { class: "msg-regard", title: r.selection ? `Texte sélectionné :\n${r.selection}` : "" },
        svg("eye"), h("span", {}, "Regard : ", what), r.selection ? h("q", {}, r.selection) : null));
    }
    return box;
  }

  line(text, cls = "", ico = "") {
    return h("div", { class: `line ${cls}` }, ico ? svg(ico) : null, h("span", {}, text));
  }

  /** A web page Claude wants to show, on a site outside the approved domains: opened only by the user. */
  askUrl(u) {
    let host = u;
    try { host = new URL(u).hostname; } catch { /* shown as is */ }
    const open = () => openPreview({ url: u, kind: "web", color: this.tint() });
    const trust = async () => {
      try {
        const r = await api("/api/security/trust", { method: "POST", body: { domain: host } });
        toast(`${r.domain} ajouté aux domaines approuvés (Configuration › Sécurité).`);
        open();
      } catch (e) { toast(e.message, "err"); }
    };
    const btn = (label, fn, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, on: { click: fn } }, label);
    return h("div", { class: "askurl" },
      h("div", { class: "askurl-head" }, svg("globe"), h("span", {}, "Claude veut ouvrir ", h("b", {}, host))),
      h("div", { class: "askurl-url", title: u }, u),
      h("div", { class: "askurl-actions" },
        btn("Ouvrir", open, "primary"),
        btn(`Toujours autoriser ${host}`, trust),
        btn("Copier le lien", () => copyText(u), "ghost")));
  }

  /** Markdown with previews: thumbnails for local images, web images on demand. */
  md(text, cls = "md") {
    const node = mdElement(text, cls);
    node.querySelectorAll(".fileref.img").forEach((el) => thumbnail(el, this.id, this.tint()));
    if (this.ctx.autoImages?.()) node.querySelectorAll(".ext-img").forEach(revealImage);
    return node;
  }

  async load() {
    if (this.loading) return;
    this.loading = true;
    this.buffer = [];
    try {
      const { events } = await api(`/api/tasks/${this.id}/events?after=${this.lastSeq}`);
      for (const ev of events) this.apply(ev, false);
    } catch (e) {
      toast(`Historique de la tâche indisponible : ${e.message}`, "err");
    } finally {
      this.loading = false;
      const buf = this.buffer;
      this.buffer = [];
      for (const ev of buf) this.apply(ev, true);
      this.body.scrollTop = this.body.scrollHeight;
      if (this.inspOpen) this.renderInspector();
      this.refreshActivity();  // context and tools of a session that has not answered since the console started
    }
  }

  addEvent(ev) {
    if (this.loading) { this.buffer.push(ev); return; }
    this.apply(ev, true);
  }

  apply(ev, live) {
    if (ev.seq != null) {
      if (ev.seq <= this.lastSeq) return;
      this.lastSeq = ev.seq;
    }
    this.isLive = live;
    const d = ev.data || {};
    const parent = d.parent || null;
    switch (ev.kind) {
      case "history": {
        const items = d.items || [];
        const list = h("div", { class: "hist-list" }, ...items.map((i) => i.role === "user"
          ? this.userMsg(i)
          : i.role === "tool" ? h("div", { class: "line" }, svg("dot"), i.text) : this.md(i.text, "md msg assistant")));
        this.push(h("details", { class: "hist" },
          h("summary", {}, svg("clock"), `Session reprise · ${d.total || items.length} messages précédents`,
            d.total > items.length ? h("span", { class: "muted" }, ` (les ${items.length} derniers affichés)`) : null), list));
        break;
      }
      case "user":
        this.push(this.userMsg(d));
        break;
      case "info":
        this.push(this.line(d.text, "", "info"), parent);
        break;
      case "init": {
        const n = (d.mcp || []).length;
        this.push(this.line(`Session prête · ${d.model || ""} · ${d.tools || 0} outils${n ? ` · ${n} serveurs MCP` : ""}`, "", "sparkle"));
        break;
      }
      case "delta": {
        let el = this.live.get(parent);
        if (!el) { el = h("div", { class: "msg assistant live" }); this.live.set(parent, el); this.push(el, parent); }
        const stick = this.nearBottom();
        el.textContent += d.text;
        if (stick) this.body.scrollTop = this.body.scrollHeight;
        break;
      }
      case "text": {
        const live = this.live.get(parent);
        const node = this.md(d.text, "md msg assistant");
        if (live) { live.replaceWith(node); this.live.delete(parent); } else this.push(node, parent);
        if (!parent) this.lastText = d.text;
        break;
      }
      case "thinking":
        this.push(h("details", { class: "thinking" }, h("summary", {}, svg("brain"), "Réflexion"), h("div", {}, d.text)), parent);
        break;
      case "tool":
        if (SUBAGENT_TOOLS.has(d.name)) this.addAgent(d, parent, ev.ts);
        else this.addTool(d, parent);
        break;
      case "tool_result":
        this.toolDone(d, ev.ts);
        break;
      case "show": {
        // Claude opened files in preview windows (app.js opens them): they stay reachable from here.
        const refs = [...(d.files || []).map((p) => h("a", { href: "#", class: `fileref${IMG_FILE.test(p) ? " img" : ""}`, "data-path": p, title: `Aperçu : ${p}` },
          p.split(/[\\/]/).pop())),
        ...(d.urls || []).map((u) => h("a", { href: "#", class: "showurl", title: u,
          on: { click: (e) => { e.preventDefault(); openPreview({ url: u, kind: "web", color: this.tint() }); } } }, u.replace(/^https:\/\//, ""))),
        ...(d.results || []).map((r) => h("a", { href: "#", class: "showurl", title: `Résultat de ${r.tool}`,
          on: { click: (e) => { e.preventDefault(); openPreview({ taskId: this.id, result: r, color: this.tint() }); } } },
          `${r.kind === "mail" ? "mail" : "résultat"} « ${r.title} »`))];
        const list = [];
        refs.forEach((r, i) => list.push(...(i ? [", ", r] : [r])));
        if (refs.length) this.push(h("div", { class: "line shown" }, svg("eye"), h("span", {}, "Affiché : ", ...list)), parent);
        for (const u of d.ask || []) this.push(this.askUrl(u), parent);
        break;
      }
      case "display": {
        // a display composed by Claude (tool presenter): drawn in the conversation, or a line that reopens
        // its window or modal. An update (same key) redraws it where it already is.
        setDoc(this.id, d);
        this.displayCards = this.displayCards || new Map();
        if (this.displayCards.get(d.key)?.isConnected) break;
        const color = this.tint();
        const card = d.ou === "conversation"
          ? renderDisplay(this.id, d.key, { mode: "conversation", color })
          : h("div", { class: "line shown" }, svg("sparkle"), h("span", {}, d.ou === "modale" ? "Affiché au premier plan : " : "Affiché dans une fenêtre : ",
            h("a", { href: "#", class: "showurl", on: { click: (e) => {
              e.preventDefault();
              if (d.ou === "modale") openDisplayModal(this.id, d.key, this.tint()); else openDisplayWindow(this.id, d.key, this.tint());
            } } }, `« ${d.titre} »`)));
        this.displayCards.set(d.key, card);
        this.push(card, parent);
        break;
      }
      case "display_answer":
        setAnswer(this.id, d);
        break;
      case "change":
        this.addChange(d);
        if (live) changesUpdated(this.id);
        break;
      case "change_state":
        this.changeState(d);
        if (live) changesUpdated(this.id);
        break;
      case "policy":
        this.push(this.line(`Refusé : ${toolLabel(d.tool)}${d.target ? ` — ${d.target}` : ""}. ${d.reason || ""}`, "policy", "shield"), parent);
        break;
      case "approval":
        this.push(this.line(`Validation demandée : ${toolLabel(d.tool)}${d.target ? ` — ${d.target}` : ""}`, "warn", "help"));
        if (live) this.ctx.onAttention?.(this.id, "awaiting");
        break;
      case "approval_done":
        this.push(d.decision === "allow"
          ? this.line(`Approuvé : ${toolLabel(d.tool)}`, "approved", "check")
          : this.line(`Refusé : ${toolLabel(d.tool)}${d.message ? ` — ${d.message}` : ""}${d.by === "console" ? " (console)" : ""}`, "denied", "x"));
        break;
      case "result":
        this.addResult(d);
        break;
      case "status":
        if (d.text) this.push(this.line(d.text, d.status === "error" ? "err" : "", "info"));
        else if (d.status === "queued") this.push(this.line("En file d'attente…", "", "clock"));
        break;
      case "error":
        this.push(this.line(d.text, "err", "alert"));
        break;
      default:
        break;
    }
    if (live && this.inspOpen && ["tool", "tool_result", "result", "init"].includes(ev.kind)) this.renderInspector();
  }

  group(parent) {
    const box = this.container(parent);
    const last = box.lastElementChild;
    if (last?.classList.contains("tgroup")) return last;
    const label = h("span", {});
    const g = h("details", { class: "tgroup single", open: true },
      h("summary", { on: { click: () => { g.dataset.touched = "1"; } } }, h("span", { class: "chev", svg: "chev" }), label),
      h("div", { class: "tlist" }));
    g._n = 0;
    g._pending = 0;
    g._names = new Map();
    g._label = label;
    this.push(g, parent);
    return g;
  }

  refreshGroup(g) {
    g.classList.toggle("single", g._n < GROUP_FOLD);
    const names = [...g._names.entries()].map(([n, c]) => (c > 1 ? `${n} ×${c}` : n)).join(", ");
    g._label.textContent = `${g._n} actions · ${names}${g._pending ? " · en cours" : ""}`;
  }

  addTool(d, parent) {
    const label = toolLabel(d.name);
    this.toolCounts.set(label, (this.toolCounts.get(label) || 0) + 1);
    if (parent && this.agents.has(parent)) this.agents.get(parent).tools += 1;
    const status = h("span", { class: "ts wait" });
    const details = h("div", { class: "td" }, inputView(d.name, d.input));
    const path = FILE_KEYS[d.name] && d.input?.[FILE_KEYS[d.name]];
    const url = d.name === "WebFetch" && /^https:\/\//i.test(d.input?.url || "") ? d.input.url : "";
    const peek = path || url ? h("button", {
      type: "button", class: "pv-btn", title: path ? `Aperçu : ${path}` : `Aperçu : ${url}`, svg: "eye",
      on: { click: (e) => { e.preventDefault(); e.stopPropagation(); openPreview(path ? { taskId: this.id, path, color: this.tint() } : { url, kind: "web" }); } },
    }) : null;
    const row = h("details", { class: `tool${d.name?.startsWith("mcp__") ? " mcp" : ""}` },
      h("summary", {}, h("span", { class: "ti", svg: toolIcon(d.name) }), h("span", { class: "tn" }, label),
        h("span", { class: "tt", title: d.target || "" }, d.target || ""), peek, status),
      details);
    const g = this.group(parent);
    g.querySelector(".tlist").append(row);
    g._n += 1;
    g._pending += 1;
    g._names.set(label, (g._names.get(label) || 0) + 1);
    this.refreshGroup(g);
    this.tools.set(d.id, { row, status, details, group: g, parent, name: d.name, path });
    if (this.nearBottom()) this.body.scrollTop = this.body.scrollHeight;
  }

  /** A file a tool changed: a button on its action opens the differences (and undo). */
  addChange(d) {
    this.changes.set(d.id, d);
    const t = this.tools.get(d.tool_use_id);
    if (!t) return;
    const btn = h("button", { type: "button", class: "pv-btn chg-btn", title: `Différences de ${d.path} (annuler possible)`,
      on: { click: (e) => { e.preventDefault(); e.stopPropagation(); this.showChange(d.id); } } },
    svg("diff"), h("span", {}, counts(d)));
    t.status.before(btn);
    d.btn = btn;
  }

  changeState(d) {
    const c = this.changes.get(d.id);
    if (c) {
      c.state = d.state;
      c.btn?.classList.toggle("undone", d.state === "annule");
    }
    const name = baseName(d.path);
    this.push(d.state === "annule"
      ? this.line(`Modification annulée : ${name}${d.forced ? " (forcée)" : ""} — Claude le saura avec ton prochain message.`, "denied", "undo")
      : this.line(`Modification rétablie : ${name}`, "approved", "retry"));
  }

  showChange(id) {
    const c = this.changes.get(id);
    if (c) openDiff(this.id, c, { color: this.tint(), task: () => this.task });
  }

  addAgent(d, parent, ts) {
    const color = AGENT_COLORS[this.agents.size % AGENT_COLORS.length];
    const inp = d.input || {};
    const type = inp.subagent_type || "Sous-agent";
    const desc = inp.description || d.target || "";
    const model = (this.task.team_agents || {})[type] || inp.model || "";
    if (parent && this.agents.has(parent)) this.agents.get(parent).tools += 1;
    this.toolCounts.set("Sous-agent", (this.toolCounts.get("Sous-agent") || 0) + 1);
    const sm = h("span", { class: "sm" }, h("span", { class: "spin" }));
    const live = h("span", { class: "alive" });
    const body = h("div", { class: "ab" });
    const card = h("details", { class: "agentcard", open: true, style: { "--ac": color } },
      h("summary", { on: { click: () => { card.dataset.touched = "1"; } } },
        h("span", { class: "ic", svg: "bot" }),
        h("span", { class: "hd" }, h("b", {}, `Sous-agent · ${type}`, model ? h("span", { class: "mdl" }, model) : null), h("small", { title: desc }, desc), live), sm),
      body);
    if (inp.prompt) body.append(h("details", { class: "thinking" }, h("summary", {}, svg("list"), "Consigne reçue"), h("div", {}, String(inp.prompt).slice(0, 4000))));
    this.agents.set(d.id, { id: d.id, parent, type, desc, model, color, status: "running", tools: 0, started: ts, ended: null, card, body, sm, live });
    this.push(card, parent);
    // The first subagent opens the side panel, unless the user chose to keep it closed.
    if (this.isLive && !this.inspOpen && wm.flag(this.id, "insp") === undefined) this.setInspector(true, false);
  }

  toolDone(d, ts) {
    const a = this.agents.get(d.id);
    if (a) {
      a.status = d.is_error ? "error" : "done";
      a.ended = ts;
      a.sm.replaceChildren(`${a.tools} action${a.tools > 1 ? "s" : ""} · ${fmtDuration((a.ended - (a.started || a.ended)) * 1000)} `,
        svg(d.is_error ? "x" : "check", d.is_error ? "ko-ic" : "ok-ic"));
      if (d.preview) a.body.append(h("div", { class: "fl" }, "Réponse du sous-agent"), this.md(d.preview));
      if (!a.card.dataset.touched && this.isLive) a.card.open = false;
      if (!this.isLive) a.card.open = false;
      return;
    }
    const t = this.tools.get(d.id);
    if (!t) return;
    t.status.className = `ts ${d.is_error ? "ko" : "ok"}`;
    t.status.replaceChildren(svg(d.is_error ? "x" : "check"));
    if (d.preview) t.details.append(h("div", { class: "fl" }, "Résultat"), h("pre", {}, d.preview));
    // An image Claude just created shows up in the conversation, not only in the tool list.
    if (!d.is_error && t.path && t.name !== "Read" && IMG_FILE.test(t.path)) {
      const ref = h("a", { href: "#", class: "fileref img", "data-path": t.path, title: `Aperçu : ${t.path}` }, t.path);
      this.push(h("div", { class: "md msg assistant created" }, h("p", {}, "Image créée : ", ref)), t.parent);
      thumbnail(ref, this.id, this.tint());
    }
    t.group._pending = Math.max(0, t.group._pending - 1);
    this.refreshGroup(t.group);
  }

  addResult(d) {
    const box = h("div", { class: `result${d.is_error ? " is-error" : ""}` });
    const text = (d.text || "").trim();
    if (text && text !== this.lastText.trim()) box.append(this.md(text));
    if (d.is_error && d.hint) box.append(h("div", { class: "line err" }, d.hint));
    const bits = [];
    if (d.duration_ms) bits.push(fmtDuration(d.duration_ms));
    if (d.turns) bits.push(`${d.turns} tour${d.turns > 1 ? "s" : ""}`);
    const tok = this.tokens();
    if (tok) bits.push(`${fmtTokens(tok)} tokens`);
    if (d.denials?.length) bits.push(`${d.denials.length} action(s) refusée(s)`);
    box.append(h("div", { class: "result-foot" },
      h("span", { class: "grow" }, svg(d.is_error ? "alert" : "check"), d.is_error ? "Terminé avec une erreur" : "Terminé", bits.length ? ` · ${bits.join(" · ")}` : ""),
      text ? h("button", { type: "button", class: "btn small ghost", on: { click: () => copyText(text) } }, h("span", { class: "i", svg: "copy" }), "Copier") : null));
    this.push(box);
    if (this.isLive) this.ctx.onAttention?.(this.id, d.is_error ? "error" : "done");
  }

  // ------------------------------------------------------------ approvals
  renderApprovals(pending) {
    const ids = new Set(pending.map((p) => p.id));
    for (const [id, el] of this.apprEls) if (!ids.has(id)) { el.remove(); this.apprEls.delete(id); }
    for (const p of pending) {
      if (this.apprEls.has(p.id)) continue;
      const el = p.kind === "question" ? this.questionCard(p) : p.kind === "plan" ? this.planCard(p)
        : p.kind === "proposal" ? this.proposalCard(p) : this.approvalCard(p);
      this.apprEls.set(p.id, el);
      this.approvals.append(el);
    }
  }

  async decide(p, decision, message = "", answers = null, el = null, remember = []) {
    el?.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const r = await api(`/api/tasks/${this.id}/approvals/${p.id}`, { method: "POST", body: { decision, message, answers, remember } });
      if (r?.note) toast(r.note, "ok");
      if (r?.remembered?.length) toast(`Mémorisé pour ce projet : ${r.remembered.join(", ")}`, "ok");
    } catch (e) {
      toast(e.message, "err");
      el?.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  }

  apprHead(icon, title, sub) {
    return h("div", { class: "appr-head" }, h("span", { class: "ai", svg: icon }), h("b", {}, title), sub ? h("span", { class: "tl" }, sub) : null);
  }

  approvalCard(p) {
    const msg = h("input", { type: "text", placeholder: "Message pour Claude (facultatif)" });
    const el = h("div", { class: "appr" });
    const mcpWrite = p.tool.startsWith("mcp__");
    // "Toujours pour ce projet": the proposed rules, editable, one per line
    const rules = h("textarea", { class: "appr-rules", rows: String(Math.max(1, (p.suggest || []).length)), spellcheck: "false" });
    rules.value = (p.suggest || []).join("\n");
    const folder = String(this.task.workdir || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop();
    const always = h("div", { class: "appr-always", hidden: true },
      h("div", { class: "muted" }, `Règle mémorisée pour les discussions du dossier ${folder}. Les chemins protégés et les refus permanents restent appliqués.`),
      rules,
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn small ghost", on: { click: () => { always.hidden = true; } } }, "Annuler"),
        h("button", { type: "button", class: "btn small ok", on: { click: () => {
          const list = rules.value.split("\n").map((x) => x.trim()).filter(Boolean);
          if (!list.length) { rules.focus(); return; }
          this.decide(p, "allow", msg.value, null, el, list);
        } } }, "Mémoriser et approuver")));
    // Actions come first so they stay visible in a small window; the detail follows.
    el.append(...[
      this.apprHead(toolIcon(p.tool), "Validation requise", toolLabel(p.tool)),
      p.target ? h("div", { class: "appr-target" }, p.target) : null,
      h("div", { class: "appr-reason" }, p.reason),
      h("div", { class: "appr-actions" }, msg,
        h("button", { type: "button", class: "btn danger", on: { click: () => this.decide(p, "deny", msg.value, null, el) } }, "Refuser"),
        p.suggest?.length ? h("button", { type: "button", class: "btn", title: "Approuver et ne plus demander pour ce type d'action dans ce projet",
          on: { click: () => { always.hidden = false; rules.focus(); } } }, "Toujours pour ce projet") : null,
        h("button", { type: "button", class: "btn ok", on: { click: () => this.decide(p, "allow", msg.value, null, el) } }, "Approuver")),
      always,
      h("details", { open: mcpWrite || p.tool === "Edit" || p.tool === "Write" },
        h("summary", { class: "muted" }, "Détail de l'action proposée"), inputView(p.tool, p.input)),
    ].filter(Boolean));
    return el;
  }

  /** Claude proposes an action or a routine for the project: shown in full, written or saved only on a click. */
  proposalCard(p) {
    const x = p.input || {};
    const action = x.quoi === "action";
    const consigne = x.quoi === "consigne";
    const msg = h("input", { type: "text", placeholder: "Message pour Claude (facultatif)" });
    const el = h("div", { class: "appr proposal" });
    const field = (k, v) => (v ? h("div", { class: "prop-field" }, h("span", { class: "muted" }, k), h("span", {}, v)) : null);
    const go = (answers) => this.decide(p, "allow", msg.value, answers, el);
    const buttons = action
      ? [h("button", { type: "button", class: "btn ok", on: { click: () => go(null) } }, x.remplace != null ? "Remplacer l'action" : "Ajouter l'action")]
      : consigne
      ? [h("button", { type: "button", class: "btn ok", on: { click: () => go(null) } },
          x.remplace === true ? "Remplacer la consigne" : "Ajouter la consigne")]
      : [h("button", { type: "button", class: "btn", on: { click: () => go({ activer: "non" }) } }, "Ajouter désactivée"),
        h("button", { type: "button", class: "btn ok", on: { click: () => go({ activer: "oui" }) } }, "Ajouter et activer")];
    const fields = action
      ? [field("Commande", `/${x.nom}`), field("Bouton", x.libelle || x.nom), field("À saisir", x.parametre), field("Fichier", x.fichier)]
      : consigne
      ? [field("Projet", x.projet)]
      : [field("Nom", x.nom), field("Quand", x.planification), field("Compte", x.compte), field("Autorisations", x.preset),
        field("Modèle", x.modele), field("Dossier", x.dossier)];
    const title = action ? "Nouvelle action proposée" : consigne ? (x.remplace === true ? "Consigne à remplacer" : "Consigne proposée") : "Nouvelle routine proposée";
    el.append(...[
      this.apprHead(action ? "bolt" : consigne ? "mail" : "clock", title, `Projet « ${x.projet || ""} »`),
      h("div", { class: "appr-reason" }, x.description || p.reason),
      h("div", { class: "prop-fields" }, ...fields.filter(Boolean)),
      h("div", { class: "prop-note muted" }, action
        ? "Ces consignes seront suivies à chaque clic sur le bouton, avec les autorisations du projet. Relis-les : rien n'est écrit avant ton accord."
        : consigne
        ? (x.remplace === true
          ? "Ce texte remplace la consigne actuelle des mails du projet. Rien n'est enregistré avant ton accord."
          : "Ce texte sera ajouté aux consignes des mails du projet. Rien n'est enregistré avant ton accord.")
        : "Elle tournera seule, avec ces autorisations ; une validation demandée attendra ton retour. Rien n'est enregistré avant ton accord."),
      h("div", { class: "appr-actions" }, msg,
        h("button", { type: "button", class: "btn danger", on: { click: () => this.decide(p, "deny", msg.value, null, el) } }, "Refuser"),
        ...buttons),
      h("details", { open: true }, h("summary", { class: "muted" }, action ? "Contenu du fichier" : consigne ? (x.remplace === true ? "Nouvelle consigne" : "Texte ajouté") : "Demande envoyée à chaque exécution"),
        h("pre", { class: "prop-code" }, action ? x.contenu || "" : x.consigne || "")),
      action && x.remplace != null ? h("details", {}, h("summary", { class: "muted" }, "Contenu actuel, qui sera remplacé"),
        h("pre", { class: "prop-code" }, x.remplace)) : null,
      consigne && x.remplace === true && x.actuelle ? h("details", {}, h("summary", { class: "muted" }, "Consigne actuelle, qui sera remplacée"),
        h("pre", { class: "prop-code" }, x.actuelle)) : null,
    ].filter(Boolean));
    return el;
  }

  planCard(p) {
    const msg = h("input", { type: "text", placeholder: "Remarque (facultatif)" });
    const el = h("div", { class: "appr" });
    el.append(this.apprHead("list", "Plan proposé", "Claude attend ton accord pour l'exécuter"),
      this.md((p.input && p.input.plan) || pretty(p.input)),
      h("div", { class: "appr-actions" }, msg,
        h("button", { type: "button", class: "btn danger", on: { click: () => this.decide(p, "deny", msg.value, null, el) } }, "Refuser"),
        h("button", { type: "button", class: "btn ok", on: { click: () => this.decide(p, "allow", msg.value, null, el) } }, "Approuver le plan")));
    return el;
  }

  /** Several questions: one at a time, with tabs, so none is missed in a small window; all must be answered. */
  questionCard(p) {
    const qs = (p.input && p.input.questions) || [];
    const state = qs.map(() => ({ sel: new Set(), other: "" }));
    const answered = (i) => state[i].sel.size > 0 || state[i].other.trim() !== "";
    const many = qs.length > 1;
    let cur = 0;
    const tabs = many ? h("div", { class: "q-tabs", role: "tablist" }) : null;
    const counter = many ? h("span", { class: "q-count" }) : null;
    const prev = h("button", { type: "button", class: "btn", on: { click: () => show(cur - 1) } }, "Précédent");
    const next = h("button", { type: "button", class: "btn primary", on: { click: () => (cur < qs.length - 1 ? show(cur + 1) : answer()) } });
    const pages = qs.map((q, i) => {
      const opts = h("div", { class: "q-opts" });
      for (const o of q.options || []) {
        const b = h("button", { type: "button", class: "btn q-opt" }, o.label, o.description ? h("small", {}, o.description) : null);
        b.addEventListener("click", () => {
          const s = state[i].sel;
          if (q.multiSelect) { s.has(o.label) ? s.delete(o.label) : s.add(o.label); }
          else { s.clear(); s.add(o.label); opts.querySelectorAll(".q-opt").forEach((x) => x.classList.remove("sel")); }
          b.classList.toggle("sel", s.has(o.label));
          refresh();
          // a single choice moves on to the next question still without an answer
          if (!q.multiSelect && many) {
            const todo = qs.findIndex((_, j) => j > i && !answered(j));
            if (todo >= 0) setTimeout(() => show(todo), 180);
          }
        });
        opts.append(b);
      }
      const other = h("input", { type: "text", placeholder: "Autre réponse…" });
      other.addEventListener("input", () => { state[i].other = other.value; refresh(); });
      other.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); next.click(); } });
      return h("div", { class: "q" }, h("div", { class: "q-title" }, q.question || q.header || ""),
        q.multiSelect ? h("div", { class: "q-hint" }, "Plusieurs choix possibles.") : null, opts, other);
    });
    const refresh = () => {
      if (many) {
        tabs.replaceChildren(...qs.map((q, i) => h("button", {
          type: "button", role: "tab", class: `q-tab${i === cur ? " on" : ""}${answered(i) ? " done" : ""}`,
          "aria-selected": i === cur ? "true" : "false", on: { click: () => show(i) },
        }, answered(i) ? h("span", { svg: "check" }) : h("span", { class: "q-num" }, String(i + 1)), q.header || `Question ${i + 1}`)));
        counter.textContent = `${qs.filter((_, i) => answered(i)).length} / ${qs.length} répondue(s)`;
      }
      prev.hidden = !many || cur === 0;
      next.textContent = many && cur < qs.length - 1 ? "Suivant" : "Répondre";
    };
    const show = (i) => {
      cur = Math.max(0, Math.min(qs.length - 1, i));
      pages.forEach((pg, j) => { pg.hidden = j !== cur; });
      refresh();
      const box = el.closest(".win-approvals");  // back to the top of the card: tabs and title in view
      if (box) box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top;
    };
    const answer = () => {
      const missing = qs.findIndex((_, i) => !answered(i));
      if (missing >= 0) {
        show(missing);
        toast(many ? `Il reste ${qs.filter((_, i) => !answered(i)).length} question(s) sans réponse.` : "Choisis au moins une réponse.", "warn");
        return;
      }
      const answers = {};
      qs.forEach((q, i) => {
        const vals = [...state[i].sel];
        if (state[i].other.trim()) vals.push(state[i].other.trim());
        answers[q.question] = vals.join(", ");
      });
      this.decide(p, "allow", "", answers, el);
    };
    const el = h("div", { class: "appr q-card" },
      h("div", { class: "appr-head" }, h("span", { class: "ai", svg: "help" }),
        h("b", {}, many ? `${qs.length} questions de Claude` : "Question de Claude"), counter),
      tabs, ...pages,
      h("div", { class: "appr-actions q-actions" },
        h("button", { type: "button", class: "btn ghost", on: { click: () => this.decide(p, "deny", "Pas de réponse.", null, el) } }, "Ignorer"),
        h("span", { class: "grow" }), prev, next));
    pages.forEach((pg, j) => { pg.hidden = j !== 0; });
    refresh();
    return el;
  }

  // ------------------------------------------------------------ actions
  async sendFollowup() {
    const text = this.input.value.trim();
    const files = this.att.ids();
    if (!text && !files.length && !this.att.busy()) return;
    if (!this.att.ready()) return;
    // A big session idle for more than an hour: its cache has expired, the follow-up re-sends it all.
    let compact = false;
    const c = this.contextInfo();
    if (c.size >= BIG_CONTEXT && c.cold && this.task.resumable && !ACTIVE.has(this.task.status)) {
      const v = await dialog({
        title: "Reprendre une grosse discussion ?",
        body: `Cette discussion porte ${fmtTokens(c.size)} tokens de contexte et sa dernière activité date de ${fmtDuration(c.idle * 1000)} : `
          + "le cache a expiré, la suite renverra tout ce contexte, puis chaque action de Claude le relira. "
          + this.compactCost(c)
          + (c.limit && c.size > c.limit ? ` Au-delà de ${fmtTokens(c.limit)}, Claude Code compactera de toute façon au premier message.` : ""),
        buttons: [{ label: "Annuler", value: null }, { label: "Envoyer tel quel", value: "send" },
          { label: "Compacter puis envoyer", value: "compact", cls: "primary" }],
      });
      if (!v) return;
      compact = v === "compact";
    }
    this.input.value = "";
    autoGrow(this.input, 120);
    // only what this window's bar showed (it shows the chip while the window has the focus)
    const seen = wm.focused() === this.id ? regard.get() : null;
    try {
      await api(`/api/tasks/${this.id}/message`, { method: "POST", body: { text, attachments: files, compact, regard: regard.payload(seen) } });
      this.att.sent(files);
      regard.sent(seen);
    } catch (e) {
      toast(e.message, "err");
      if (!this.input.value.trim()) this.input.value = text;
    }
  }

  async cancel() {
    try { await api(`/api/tasks/${this.id}/cancel`, { method: "POST" }); } catch (e) { toast(e.message, "err"); }
  }

  async pin() {
    const on = wm.togglePin(this.id);
    this.pinBtn.classList.toggle("on", on);
    try { await api(`/api/tasks/${this.id}`, { method: "PATCH", body: { pinned: on } }); } catch { /* local state is enough */ }
  }

  /** Claude reads the session (first request, latest exchanges, files) and gives it a title. */
  async aiRename() {
    if (this.renaming) return;
    const before = this.task.title;
    this.renaming = true;
    this.el.classList.add("renaming");
    toast("Claude cherche un titre…");
    try {
      const t = await api(`/api/tasks/${this.id}/ai-title`, { method: "POST" });
      if (t.title !== before) toast(`Renommée : « ${t.title} »`);
      else toast("Claude garde le même titre.");
    } catch (e) {
      toast(e.message, "err");
    } finally {
      this.renaming = false;
      this.el.classList.remove("renaming");
    }
  }

  async close() {
    if (ACTIVE.has(this.task.status)) {
      const v = await dialog({
        title: "Fermer la fenêtre ?", body: "La tâche tourne encore. Elle reste accessible depuis l'historique.",
        buttons: [{ label: "Rester", value: null }, { label: "Annuler la tâche", value: "cancel", cls: "danger" },
          { label: "Fermer, la tâche continue", value: "close", cls: "primary" }],
      });
      if (!v) return;
      if (v === "cancel") await this.cancel();
    }
    try { await api(`/api/tasks/${this.id}`, { method: "PATCH", body: { closed: true } }); } catch { /* ignore */ }
    this.ctx.onClosed?.(this.id);
  }

  menu(anchor) {
    const t = this.task;
    const others = this.ctx.profiles().filter((p) => p.id !== t.profile);
    popupMenu(anchor, [
      { label: "Nouvelle discussion à partir d'ici", disabled: !t.resumable, run: () => this.ctx.fork(t) },
      { label: "Utiliser comme contexte d'une demande", disabled: !t.resumable, run: () => this.ctx.addContext(t) },
      { label: "Déplacer vers un projet…", disabled: !t.resumable, run: () => this.ctx.move(t) },
      "-",
      { label: "Relancer", run: () => this.ctx.retry(this.id) },
      ...others.map((p) => ({ label: `Dupliquer avec ${p.name}`, run: () => this.ctx.duplicate(this.id, p.id) })),
      "-",
      { label: t.changed_files ? `Fichiers modifiés (${t.changed_files})…` : "Fichiers modifiés…",
        run: () => openChanges(this.id, { color: this.tint(), task: () => this.task, title: this.task.title }) },
      "-",
      { label: "Copier le résultat", disabled: !t.result, run: () => copyText(t.result || "") },
      { label: "Ouvrir dans un terminal", disabled: !t.resumable, run: async () => {
        try { await api(`/api/tasks/${this.id}/terminal`, { method: "POST" }); toast("Session ouverte dans un terminal."); }
        catch (e) { toast(e.message, "err"); } } },
      { label: "Renommer", run: async () => {
        const v = await dialog({ title: "Renommer la tâche", body: "", input: { value: t.title },
          buttons: [{ label: "Annuler", value: null }, { label: "Renommer", value: true, cls: "primary" }] });
        if (v && v.trim()) api(`/api/tasks/${this.id}`, { method: "PATCH", body: { title: v.trim() } }).catch((e) => toast(e.message, "err"));
      } },
      { label: "Renommer avec l'IA", disabled: this.renaming, run: () => this.aiRename() },
      { label: this.inspOpen ? "Masquer le panneau" : "Afficher le panneau", run: () => this.toggleInspector() },
      "-",
      { label: "Supprimer de l'historique", danger: true, disabled: ACTIVE.has(t.status), run: async () => {
        if (!(await confirmDialog("Supprimer la tâche ?", "Sa sortie et son flux seront effacés. Le journal d'audit est conservé.", "Supprimer", "danger"))) return;
        try { await api(`/api/tasks/${this.id}`, { method: "DELETE" }); } catch (e) { toast(e.message, "err"); }
      } },
    ]);
  }
}
