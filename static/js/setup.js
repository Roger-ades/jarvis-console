// First-run assistant: Claude Code found, each account connected, working folders, launcher.
// It opens on a fresh install (general.setup_done false) and from Configuration or Ctrl+K.
import { api } from "./api.js";
import { pickFolder } from "./folderpicker.js";
import { h, toast } from "./util.js";

/** ctx: state(), profiles(), probes(), newProject(), recentFolders(pid), onDone() */
export function openSetup(ctx) {
  const STEPS = ["Claude Code", "Comptes", "Dossiers", "Lanceur", "C'est prêt"];
  let step = 0;
  const body = h("div", { class: "su-body" });
  const dots = h("div", { class: "su-steps" });
  const prev = h("button", { type: "button", class: "btn ghost", on: { click: () => go(step - 1) } }, "Précédent");
  const next = h("button", { type: "button", class: "btn primary", on: { click: () => (step === STEPS.length - 1 ? finish() : go(step + 1)) } });
  const skip = h("button", { type: "button", class: "btn ghost", title: "Tu pourras le rouvrir dans Configuration → Général", on: { click: finish } }, "Passer l'assistant");
  const box = h("div", { class: "dialog su", role: "dialog", "aria-modal": "true", "aria-label": "Assistant de démarrage" },
    h("div", { class: "su-head" }, h("img", { src: "/static/img/favicon.svg", alt: "", class: "su-mark" }),
      h("div", {}, h("h3", {}, "Bienvenue dans JARVIS"), h("small", { class: "muted" }, "Quelques réglages pour démarrer, en deux minutes."))),
    dots, body, h("div", { class: "dialog-actions" }, skip, h("span", { class: "grow" }), prev, next));
  const overlay = h("div", { class: "overlay" }, box);
  document.getElementById("modal-root").append(overlay);

  async function finish() {
    try {
      const { config } = await api("/api/config");
      config.general.setup_done = true;
      await api("/api/config", { method: "PUT", body: config });
    } catch (e) { toast(e.message, "err"); }
    overlay.remove();
    ctx.onDone?.();
  }

  function go(i) {
    step = Math.max(0, Math.min(STEPS.length - 1, i));
    dots.replaceChildren(...STEPS.map((s, k) => h("span", { class: `su-dot${k === step ? " on" : k < step ? " done" : ""}` }, `${k + 1}. ${s}`)));
    prev.hidden = step === 0;
    next.textContent = step === STEPS.length - 1 ? "Commencer" : "Suivant";
    body.replaceChildren(...[cli, accounts, folders, launcher, ready][step]());
  }

  const para = (...xs) => h("p", {}, ...xs);
  const okLine = (ok, text) => h("div", { class: `su-line ${ok ? "ok" : "ko"}` }, h("span", { class: "i", svg: ok ? "check" : "alert" }), text);

  function cli() {
    const c = ctx.state().cli || {};
    return [para("La console pilote Claude Code, installé avec Claude Desktop."),
      c.path ? okLine(true, `Claude Code trouvé${c.version ? ` (${c.version})` : ""}.`)
        : okLine(false, "Claude Code introuvable : installe Claude Desktop (ou Claude Code), puis relance la console. "
          + "Tu peux aussi indiquer son chemin dans Configuration → Général.")];
  }

  function accounts() {
    const rows = ctx.profiles().map((p) => {
      const status = h("span", { class: "muted" });
      const show = (pr) => {
        const known = pr && (pr.checked || pr.logged_in !== undefined);
        status.replaceChildren(!known ? "pas encore testé" : pr.logged_in ? h("b", { class: "ok-t" }, `connecté${pr.account?.email ? ` · ${pr.account.email}` : ""}`)
          : h("b", { class: "ko-t" }, pr.error || "non connecté"));
      };
      show(ctx.probes()[p.id]);
      const test = h("button", { type: "button", class: "btn small", on: { click: async () => {
        test.disabled = true; status.textContent = "test…";
        try { show(await api(`/api/profiles/${p.id}/test`, { method: "POST" })); } catch (e) { status.textContent = e.message; }
        test.disabled = false;
      } } }, "Tester");
      const login = h("button", { type: "button", class: "btn small ghost", on: { click: async () => {
        try { await api(`/api/profiles/${p.id}/login`, { method: "POST" }); toast("Une fenêtre s'ouvre : connecte-toi au compte, puis clique sur Tester."); }
        catch (e) { toast(e.message, "err"); }
      } } }, "Se connecter");
      return h("div", { class: "su-acc", style: { "--pc": p.color } }, h("span", { class: "sw" }), h("b", {}, p.name), status, h("span", { class: "grow" }), login, test);
    });
    return [para("Chaque compte a sa propre connexion Claude (par exemple Travail sur le forfait de l'entreprise, Perso sur le tien)."), ...rows,
      h("p", { class: "muted" }, "« Se connecter » ouvre la connexion Claude dans une petite fenêtre ; une seule fois par compte.")];
  }

  function folders() {
    const rows = ctx.profiles().map((p) => {
      const path = h("small", { class: "mono" }, p.workdir);
      const change = h("button", { type: "button", class: "btn small ghost", on: { click: async () => {
        const dir = await pickFolder({ title: `Dossier de travail du compte ${p.name}`, start: p.workdir, recent: ctx.recentFolders(p.id) });
        if (!dir) return;
        try {
          const { config } = await api("/api/config");
          const prof = config.profiles.find((x) => x.id === p.id);
          prof.workdir = dir;
          await api("/api/config", { method: "PUT", body: config });
          p.workdir = dir;
          path.textContent = dir;
          toast(`Dossier de ${p.name} : ${dir}`, "ok");
        } catch (e) { toast(e.message, "err"); }
      } } }, "Changer…");
      return h("div", { class: "su-acc", style: { "--pc": p.color } }, h("span", { class: "sw" }), h("b", {}, p.name), path, h("span", { class: "grow" }), change);
    });
    return [para("Le dossier où chaque compte travaille par défaut. Pour un sujet précis (Visiotech, devis…), crée plutôt un projet : un dossier avec ses consignes et ses réglages."),
      ...rows, h("div", { class: "row" }, h("button", { type: "button", class: "btn small", on: { click: () => ctx.newProject() } }, "+ Créer un projet"))];
  }

  function launcher() {
    const status = h("div", { class: "muted" }, "…");
    const create = h("button", { type: "button", class: "btn small primary", on: { click: async () => {
      try { const r = await api("/api/system/launcher", { method: "POST" }); status.textContent = `Créé : ${r.paths.join(" · ")}`; }
      catch (e) { toast(e.message, "err"); }
    } } }, "Créer le lanceur JARVIS");
    const startup = h("input", { type: "checkbox" });
    startup.addEventListener("change", async () => {
      try { await api("/api/system/startup", { method: "POST", body: { on: startup.checked } }); } catch (e) { toast(e.message, "err"); startup.checked = !startup.checked; }
    });
    api("/api/system").then((s) => {
      startup.checked = !!s.startup;
      status.textContent = s.launcher ? "Lanceur déjà présent." : "";
      if (s.platform === "mac") create.textContent = "Créer l'app JARVIS (dossier Applications)";
    }).catch(() => {});
    return [para("Un lanceur avec l'icône JARVIS, à épingler dans la barre des tâches (ou le Dock) : il démarre la console et l'ouvre."),
      h("div", { class: "row" }, create), status,
      h("label", { class: "check" }, startup, "Démarrer la console avec la session (les routines tournent même sans l'ouvrir)"),
      h("p", { class: "muted" }, "Astuce : le bouton « Installer » en haut installe la console comme une application, avec sa propre fenêtre.")];
  }

  function ready() {
    return [para("C'est prêt. Quelques repères :"),
      h("ul", { class: "su-tips" },
        h("li", {}, h("b", {}, "Ctrl+K"), " : rechercher une discussion, une session, un projet ou une action."),
        h("li", {}, h("b", {}, "Les pastilles en haut"), " : les limites de chaque compte (session de 5 h et semaine)."),
        h("li", {}, h("b", {}, "Projet"), " : consignes, mémoire, fichiers, discussions et règles du dossier."),
        h("li", {}, h("b", {}, "Toujours pour ce projet"), " : dans une validation, pour ne plus être interrompu par la même action."))];
  }

  go(0);
}
