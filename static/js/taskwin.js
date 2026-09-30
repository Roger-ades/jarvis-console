// One task = one window: compact header, timeline, inspector panel, approvals, follow-up.
import { api } from "./api.js";
import { Attacher } from "./attach.js";
import { mdElement } from "./md.js";
import {
  ACTIVE, STATUS, confirmDialog, copyText, dialog, fmtCost, fmtDuration, fmtTokens, h, iconBtn, toast, toolIcon, toolLabel,
} from "./util.js";
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

function place(el, anchor, width = 220) {
  document.body.append(el);
  const r = anchor.getBoundingClientRect();
  const w = Math.max(width, el.offsetWidth);
  Object.assign(el.style, {
    top: `${Math.min(r.bottom + 6, innerHeight - el.offsetHeight - 8)}px`,
    left: `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`,
  });
}

function dismissable(el, onClose) {
  const off = (e) => { if (!el.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  function close() {
    el.remove();
    document.removeEventListener("pointerdown", off, true);
    document.removeEventListener("keydown", esc, true);
    onClose?.();
  }
  setTimeout(() => { document.addEventListener("pointerdown", off, true); document.addEventListener("keydown", esc, true); });
  return close;
}

export function popupMenu(anchor, items) {
  document.querySelectorAll(".menu.popup, .popover").forEach((m) => m.remove());
  const menu = h("div", { class: "menu popup" });
  Object.assign(menu.style, { position: "fixed", zIndex: "9000" });
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
  document.querySelectorAll(".menu.popup, .popover").forEach((m) => m.remove());
  const el = h("div", { class: "popover" }, ...content);
  place(el, anchor, 260);
  dismissable(el);
}

const svg = (name, cls = "") => h("span", { class: `i ${cls}`, svg: name });

function pretty(v) {
  if (v && typeof v === "object" && v["_tronqué"]) return String(v["aperçu"] || "");
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function inputView(tool, inp) {
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
    this.lastText = "";
    this.build();
  }

  // ------------------------------------------------------------ layout
  build() {
    const t = this.task;
    this.el = h("section", { class: "win", "data-id": t.id, style: { "--pc": t.color }, "aria-label": t.title });
    this.whoName = h("span", { class: "nm" });
    this.titleEl = h("span", { class: "win-title" });
    this.statusEl = h("span", { class: "status" });
    this.metaEl = h("span", { class: "win-meta" });
    this.stopBtn = iconBtn("stop", "Annuler la tâche", () => this.cancel(), "danger");
    this.inspBtn = iconBtn("panel", "Panneau agents, plan et activité", () => this.toggleInspector());
    this.pinBtn = iconBtn("pin", "Épingler au premier plan", () => this.pin());
    this.head = h("header", { class: "win-head" },
      h("span", { class: "who" }, h("span", { class: "sw" }), this.whoName), h("span", { class: "slash" }, "/"),
      this.titleEl, this.statusEl, this.metaEl,
      h("div", { class: "win-actions" }, this.stopBtn, this.inspBtn,
        iconBtn("more", "Actions", (e) => this.menu(e.currentTarget)), this.pinBtn,
        iconBtn("min", "Réduire", () => wm.minimize(this.id)), iconBtn("close", "Fermer la fenêtre", () => this.close())));
    this.sub = h("div", { class: "win-sub" });
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
    this.foot = h("footer", { class: "win-foot" }, this.input, this.att.button("icon-btn"),
      h("button", { type: "button", class: "send-mini", title: "Envoyer (Entrée)", "aria-label": "Envoyer", svg: "send",
        on: { click: () => this.sendFollowup() } }));
    this.el.append(this.head, this.sub, this.main, this.approvals, this.attList, this.foot);
    this.timer = setInterval(() => this.tick(), 1000);
    this.update(t);
  }

  mount(fresh) {
    wm.register(this.id, this.el, { handle: this.head, fresh, onFocus: () => this.ctx.onFocus?.(this.id) });
    let open = wm.flag(this.id, "insp");
    if (open === undefined) open = wm.width(this.id) >= 720;
    this.setInspector(open, false);
  }

  destroy() {
    clearInterval(this.timer);
    this.att.picker.remove();
    wm.unregister(this.id);
  }

  setInspector(open, save = true) {
    this.inspOpen = open;
    this.insp.hidden = !open;
    this.inspBtn.classList.toggle("on", open);
    if (save) wm.flag(this.id, "insp", open);
    if (open) this.renderInspector();
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
    const u = this.task.usage || {};
    return (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  }

  update(t) {
    this.task = t;
    this.el.style.setProperty("--pc", t.color);
    this.el.setAttribute("aria-label", t.title);
    this.whoName.textContent = t.profile_name;
    this.titleEl.textContent = t.title;
    this.titleEl.title = t.prompt;
    this.statusEl.className = `status s-${t.status}`;
    this.statusEl.textContent = STATUS[t.status] || t.status;
    this.stopBtn.hidden = !ACTIVE.has(t.status);
    this.pinBtn.classList.toggle("on", !!t.pinned);
    this.tick();
    this.renderSub();
    this.renderApprovals(t.pending || []);
    if (this.inspOpen) this.renderInspector();
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
    if (t.resumed_from) bits.push(chip(t.origin === "copie" ? "branch" : "retry",
      t.origin === "reprise desktop" ? "Reprise Claude Desktop" : t.origin === "copie" ? "Copie d'une discussion"
        : t.origin === "déplacée" ? "Déplacée dans ce projet" : "Reprise",
      `Suite de la session ${t.resumed_from}${t.fork_next === false ? " (copie)" : ""}`));
    bits.push(
      chip("shieldq", t.preset_name, `Autorisations : ${t.preset_name}${t.preset_mode ? ` (${t.preset_mode})` : ""}`),
      chip("model", this.modelLabel() || "modèle", `Modèle : ${t.model_resolved || t.model}`),
    );
    if (t.effort) bits.push(chip("gauge", t.effort, `Effort : ${t.effort}`, true));
    bits.push(chip("folder", baseName(t.workdir), `Dossier de travail : ${t.workdir}`, true));
    const mcp = t.mcp || [];
    if (mcp.length) {
      const ok = mcp.filter((s) => s.status === "connected").length;
      const dots = h("span", { class: "dots" }, ...mcp.slice(0, 8).map((s) => h("i", { class: mcpCls(s.status) })));
      bits.push(h("button", { type: "button", class: "meta-chip", title: "Serveurs MCP de la session",
        on: { click: (e) => this.mcpPopover(e.currentTarget) } },
        svg("plug"), h("span", {}, `${mcp.length} MCP${ok < mcp.length ? ` · ${ok} actifs` : ""}`), dots));
    }
    if (t.queued_messages) bits.push(chip("clock", `${t.queued_messages} en attente`, "Messages de suite en attente"));
    this.sub.replaceChildren(...bits);
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
    secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Agents", h("span", { class: "n" }, String(1 + this.agents.size))),
      h("ul", { class: "tree" }, h("li", {},
        h("div", { class: "agent", style: { "--ac": t.color }, on: { click: () => { this.body.scrollTop = 0; } } },
          h("span", { class: "ic", svg: "bot" }),
          h("span", { class: "nm" }, h("b", {}, t.team ? "Chef d'équipe" : "Agent principal"),
            h("small", {}, [this.modelLabel(), `${this.rootTools()} action(s)`].filter(Boolean).join(" · "))),
          h("span", { class: "st" }, stIcon(rootStatus))),
        top.length ? h("ul", {}, ...top.map(node)) : null)),
      this.agents.size ? null : h("div", { class: "note" }, "Les sous-agents lancés par Claude apparaîtront ici.")));
    // Plan
    if (t.todos?.length) {
      const doneN = t.todos.filter((x) => x.status === "completed").length;
      secs.push(h("section", { class: "insp-sec" }, h("h4", {}, "Plan", h("span", { class: "n" }, `${doneN}/${t.todos.length}`)),
        ...t.todos.map((x) => h("div", { class: `todo ${x.status}` },
          h("span", { class: "b", svg: x.status === "completed" ? "check" : "" }), h("span", {}, x.content)))));
    }
    // Activity
    const total = [...this.toolCounts.values()].reduce((a, b) => a + b, 0);
    const top6 = [...this.toolCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
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
    this.insp.replaceChildren(...secs);
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
      list.querySelectorAll(".fileref.img").forEach((el) => thumbnail(el, this.id));
    }
    return box;
  }

  line(text, cls = "", ico = "") {
    return h("div", { class: `line ${cls}` }, ico ? svg(ico) : null, h("span", {}, text));
  }

  /** Markdown with previews: thumbnails for local images, web images on demand. */
  md(text, cls = "md") {
    const node = mdElement(text, cls);
    node.querySelectorAll(".fileref.img").forEach((el) => thumbnail(el, this.id));
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
      on: { click: (e) => { e.preventDefault(); e.stopPropagation(); openPreview(path ? { taskId: this.id, path } : { url, kind: "web" }); } },
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

  addAgent(d, parent, ts) {
    const color = AGENT_COLORS[this.agents.size % AGENT_COLORS.length];
    const inp = d.input || {};
    const type = inp.subagent_type || "Sous-agent";
    const desc = inp.description || d.target || "";
    const model = (this.task.team_agents || {})[type] || inp.model || "";
    if (parent && this.agents.has(parent)) this.agents.get(parent).tools += 1;
    this.toolCounts.set("Sous-agent", (this.toolCounts.get("Sous-agent") || 0) + 1);
    const sm = h("span", { class: "sm" }, h("span", { class: "spin" }));
    const body = h("div", { class: "ab" });
    const card = h("details", { class: "agentcard", open: true, style: { "--ac": color } },
      h("summary", { on: { click: () => { card.dataset.touched = "1"; } } },
        h("span", { class: "ic", svg: "bot" }),
        h("span", { class: "hd" }, h("b", {}, `Sous-agent · ${type}`, model ? h("span", { class: "mdl" }, model) : null), h("small", { title: desc }, desc)), sm),
      body);
    if (inp.prompt) body.append(h("details", { class: "thinking" }, h("summary", {}, svg("list"), "Consigne reçue"), h("div", {}, String(inp.prompt).slice(0, 4000))));
    this.agents.set(d.id, { id: d.id, parent, type, desc, model, color, status: "running", tools: 0, started: ts, ended: null, card, body, sm });
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
      thumbnail(ref, this.id);
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
      const el = p.kind === "question" ? this.questionCard(p) : p.kind === "plan" ? this.planCard(p) : this.approvalCard(p);
      this.apprEls.set(p.id, el);
      this.approvals.append(el);
    }
  }

  async decide(p, decision, message = "", answers = null, el = null) {
    el?.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      await api(`/api/tasks/${this.id}/approvals/${p.id}`, { method: "POST", body: { decision, message, answers } });
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
    // Actions come first so they stay visible in a small window; the detail follows.
    el.append(...[
      this.apprHead(toolIcon(p.tool), "Validation requise", toolLabel(p.tool)),
      p.target ? h("div", { class: "appr-target" }, p.target) : null,
      h("div", { class: "appr-reason" }, p.reason),
      h("div", { class: "appr-actions" }, msg,
        h("button", { type: "button", class: "btn danger", on: { click: () => this.decide(p, "deny", msg.value, null, el) } }, "Refuser"),
        h("button", { type: "button", class: "btn ok", on: { click: () => this.decide(p, "allow", msg.value, null, el) } }, "Approuver")),
      h("details", { open: mcpWrite || p.tool === "Edit" || p.tool === "Write" },
        h("summary", { class: "muted" }, "Détail de l'action proposée"), inputView(p.tool, p.input)),
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

  questionCard(p) {
    const el = h("div", { class: "appr" }, this.apprHead("help", "Question de Claude"));
    const qs = (p.input && p.input.questions) || [];
    const state = qs.map(() => ({ sel: new Set(), other: "" }));
    qs.forEach((q, i) => {
      const opts = h("div", { class: "q-opts" });
      for (const o of q.options || []) {
        const b = h("button", { type: "button", class: "btn q-opt" }, o.label, o.description ? h("small", {}, o.description) : null);
        b.addEventListener("click", () => {
          const s = state[i].sel;
          if (q.multiSelect) { s.has(o.label) ? s.delete(o.label) : s.add(o.label); }
          else { s.clear(); s.add(o.label); opts.querySelectorAll(".q-opt").forEach((x) => x.classList.remove("sel")); }
          b.classList.toggle("sel", s.has(o.label));
        });
        opts.append(b);
      }
      const other = h("input", { type: "text", placeholder: "Autre réponse…" });
      other.addEventListener("input", () => { state[i].other = other.value; });
      el.append(h("div", { class: "q" }, h("div", { class: "q-title" }, q.question || q.header || ""), opts, other));
    });
    const answer = () => {
      const answers = {};
      qs.forEach((q, i) => {
        const vals = [...state[i].sel];
        if (state[i].other.trim()) vals.push(state[i].other.trim());
        if (vals.length) answers[q.question] = vals.join(", ");
      });
      if (!Object.keys(answers).length) { toast("Choisis au moins une réponse.", "warn"); return; }
      this.decide(p, "allow", "", answers, el);
    };
    el.append(h("div", { class: "appr-actions" },
      h("button", { type: "button", class: "btn ghost", on: { click: () => this.decide(p, "deny", "Pas de réponse.", null, el) } }, "Ignorer"),
      h("button", { type: "button", class: "btn primary", on: { click: answer } }, "Répondre")));
    return el;
  }

  // ------------------------------------------------------------ actions
  async sendFollowup() {
    const text = this.input.value.trim();
    const files = this.att.ids();
    if (!text && !files.length && !this.att.busy()) return;
    if (!this.att.ready()) return;
    this.input.value = "";
    autoGrow(this.input, 120);
    try {
      await api(`/api/tasks/${this.id}/message`, { method: "POST", body: { text, attachments: files } });
      this.att.sent(files);
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
      { label: "Copier le résultat", disabled: !t.result, run: () => copyText(t.result || "") },
      { label: "Ouvrir dans un terminal", disabled: !t.resumable, run: async () => {
        try { await api(`/api/tasks/${this.id}/terminal`, { method: "POST" }); toast("Session ouverte dans un terminal."); }
        catch (e) { toast(e.message, "err"); } } },
      { label: "Renommer", run: async () => {
        const v = await dialog({ title: "Renommer la tâche", body: "", input: { value: t.title },
          buttons: [{ label: "Annuler", value: null }, { label: "Renommer", value: true, cls: "primary" }] });
        if (v && v.trim()) api(`/api/tasks/${this.id}`, { method: "PATCH", body: { title: v.trim() } }).catch((e) => toast(e.message, "err"));
      } },
      { label: this.inspOpen ? "Masquer le panneau" : "Afficher le panneau", run: () => this.toggleInspector() },
      "-",
      { label: "Supprimer de l'historique", danger: true, disabled: ACTIVE.has(t.status), run: async () => {
        if (!(await confirmDialog("Supprimer la tâche ?", "Sa sortie et son flux seront effacés. Le journal d'audit est conservé.", "Supprimer", "danger"))) return;
        try { await api(`/api/tasks/${this.id}`, { method: "DELETE" }); } catch (e) { toast(e.message, "err"); }
      } },
    ]);
  }
}
