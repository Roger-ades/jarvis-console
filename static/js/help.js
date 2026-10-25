// Rubrique Aide : ce que sait faire JARVIS — interface, outils, skills, sécurité.
// Ouverte depuis Plus → Aide, Ctrl+K ou F1. Rien n'est envoyé à Claude.
import { $, h } from "./util.js";
import * as wm from "./wm.js";

const el = () => $("#help");
let section = "demarrer";
let query = "";

const SECTIONS = [
  { id: "demarrer", label: "Démarrer", icon: "sparkle" },
  { id: "lexique", label: "Lexique", icon: "book" },
  { id: "interface", label: "Interface", icon: "panel" },
  { id: "demander", label: "Demander à Claude", icon: "send" },
  { id: "valider", label: "Validations et sécurité", icon: "shield" },
  { id: "projets", label: "Projets", icon: "book" },
  { id: "boite", label: "Boîte de réception", icon: "bell" },
  { id: "regard", label: "Regard et aperçus", icon: "eye" },
  { id: "outils", label: "Outils de la console", icon: "bolt" },
  { id: "skills", label: "Skills et actions", icon: "sparkle" },
  { id: "routines", label: "Routines et déclencheurs", icon: "clock" },
  { id: "modules", label: "Modules", icon: "puzzle" },
  { id: "equipe", label: "Équipe et téléphone", icon: "team" },
  { id: "raccourcis", label: "Raccourcis", icon: "list" },
  { id: "limites", label: "Limites et consommation", icon: "gauge" },
];

function p(...xs) { return h("p", {}, ...xs); }
function ul(...items) { return h("ul", { class: "help-ul" }, ...items.map((t) => h("li", {}, ...(Array.isArray(t) ? t : [t])))); }
function kbd(...keys) { return h("span", {}, ...keys.flatMap((k, i) => i ? [" + ", h("kbd", {}, k)] : [h("kbd", {}, k)])); }
function lead(t) { return h("p", { class: "help-lead" }, t); }
function h3(t) { return h("h3", {}, t); }
function note(t) { return h("p", { class: "help-note muted" }, t); }
/** Définitions : [[terme, définition], ...] — définition = string ou nœuds. */
function dl(...entries) {
  return h("dl", { class: "help-dl" }, ...entries.map(([term, def]) =>
    h("div", {}, h("dt", {}, term), h("dd", {}, ...(Array.isArray(def) ? def : [def])))));
}

const CONTENT = {
  demarrer: () => [
    lead("JARVIS est une console locale qui lance des sessions Claude Code en parallèle, chacune dans sa fenêtre, avec le compte et les autorisations que tu choisis."),
    h3("En deux minutes"),
    ul(
      "Double-clique start.bat (ou start.command sur Mac) : le serveur démarre et la console s’ouvre.",
      ["Connecte chaque compte : Configuration (", kbd("Ctrl", ","), ") → Profils → Se connecter, puis Tester."],
      "Écris une demande dans la barre du bas et appuie sur Entrée.",
      ["Retrouve tout avec ", kbd("Ctrl", "K"), " : projets, discussions, documents, actions."],
    ),
    h3("Deux comptes isolés"),
    p("Travail et Perso ont chacun leur dossier Claude (connexion, MCP, mémoire, skills). La pastille colorée sur chaque fenêtre rappelle le compte."),
    note("Aucune clé API n’est nécessaire : JARVIS utilise tes abonnements Claude via la CLI Claude Code."),
  ],
  lexique: () => [
    lead("Les mots que tu croises partout dans JARVIS — et ce qu’ils veulent vraiment dire."),
    h3("Claude et la console"),
    dl(
      ["JARVIS", "La console locale (cette application) : fenêtres, validations, boîte, projets. Elle lance Claude Code ; elle n’est pas Claude."],
      ["Claude / Claude Code", "Le modèle et la CLI d’Anthropic. Chaque discussion JARVIS = une session Claude Code dans un dossier, avec le compte choisi."],
      ["Compte (Travail / Perso)", "Deux profils isolés : connexion, mémoire, MCP et skills séparés. La pastille colorée rappelle lequel parle."],
      ["Discussion / tâche", "Une session ouverte dans sa fenêtre : ta demande, le flux d’outils, puis le résultat."],
      ["MCP", "Model Context Protocol : façon standard de brancher des outils (dont le serveur « jarvis ») dans Claude Code."],
    ),
    h3("Skills, commandes, outils"),
    dl(
      ["Skill", "Petit guide empaqueté pour Claude (dossier .claude/skills/<nom>/ avec un SKILL.md). Il décrit quand et comment faire une chose (ex. produire un .docx). Claude le charge quand c’est pertinent ; JARVIS peut aussi l’exposer comme bouton Action."],
      ["Commande (slash)", "Fichier .claude/commands/<nom>.md : un prompt prêt à l’emploi. Tu le lances avec /nom dans la barre, ou via Actions. Différence avec un skill : une commande est surtout un texte à injecter ; un skill est une procédure réutilisable avec métadonnées."],
      ["Action", "Dans JARVIS : un skill ou une commande devenu bouton (projet ou compte), après que tu aies relu son contenu au moins une fois."],
      ["Outil (tool)", "Fonction que Claude peut appeler pendant une discussion (lire un fichier, afficher une page, proposer une routine…). Les outils jarvis sont toujours présents ; d’autres viennent des MCP du profil."],
      ["Outil-script", "Petit programme Python du projet (.claude/outils/<nom>.py), déterministe. Claude le propose ; tu valides le code ; JARVIS l’exécute ensuite sans le réinventer."],
      ["Preset d’autorisations", "Jeu de règles (Lecture seule, Brouillons, Assisté…) qui dit ce que Claude peut faire sans te demander, et ce qui exige un clic."],
    ),
    h3("Autour du travail"),
    dl(
      ["Projet", "Dossier de travail nommé (consignes CLAUDE.md, actions, notes, suivi). La discussion s’y ancre pour les chemins et le contexte."],
      ["Regard", "Ce que tu as sous les yeux (sélection, onglet, aperçu) et que JARVIS peut joindre à la prochaine demande — sans donner de droits supplémentaires."],
      ["Routine", "Demande planifiée ou déclenchée (horaire, fichier déposé, mail…) qui part toute seule tant que la console tourne."],
      ["Module (addon)", "Mini-application isolée dans addons/, proposée ou développée avec Claude, ouverte depuis Plus / Ctrl+K."],
      ["Proposition", "Carte où Claude demande ton accord avant d’écrire quelque chose de durable (routine, action, lien Odoo, OneDrive, outil-script…)."],
    ),
    note("Cherche « skill », « MCP » ou « outil-script » dans le filtre en haut pour retomber sur ces définitions."),
  ],
  interface: () => [
    lead("Un cœur simple — barre de commande + fenêtres de tâche — et des panneaux pour le reste."),
    h3("Barre du haut"),
    ul(
      "Boîte : ce qui attend (validations, à lire, rappels), toutes discussions confondues.",
      ["Rechercher (", kbd("Ctrl", "K"), ") : un seul endroit pour retrouver et agir."],
      "Projet : consignes, fichiers, suivi Odoo/Office, discussions du dossier.",
      "Notes : générales ou de projet, avec rappels.",
      "Plus : Discussions, Hors JARVIS (Desktop/CLI), Routines, Modules, claude.ai, Ranger, Aide.",
      ["Configuration (", kbd("Ctrl", ","), ") et Arrêt d’urgence."],
    ),
    h3("Barre de commande"),
    p("En bas (ou flottante avec Ctrl+Alt+J en mode bureau). Compte, dossier, réglages (modèle, autorisations, effort, mode équipe), trombone et contexte."),
    p("La bande « Ce qui part » regroupe le projet reconnu, le regard, le contexte d’autres discussions et les fichiers joints, avant l’envoi."),
    h3("Fenêtres de discussion"),
    p("Flux en direct (texte, outils, sous-agents, plan), puis résultat en markdown. Menu ⋯ : relancer, dupliquer sur l’autre compte, message de suite, session dans un terminal, fichiers modifiés, archiver…"),
    h3("Application de bureau"),
    p("build-app.bat installe JARVIS (Electron). En affichage « Intégré au bureau » : vraies fenêtres Windows, barre JARVIS (Ctrl+Alt+J), notifications Approuver/Refuser, sites connectés (Odoo, SharePoint)."),
  ],
  demander: () => [
    lead("Tu parles à JARVIS ; Claude travaille dans une session Claude Code sous contrôle."),
    h3("Gestes de base"),
    ul(
      "Entrée : envoyer · Maj+Entrée : nouvelle ligne.",
      "@work … / @perso … ou Alt+1 / Alt+2 : choisir le compte.",
      "/ : skills et commandes du profil.",
      "↑ / ↓ : rappeler une demande précédente.",
      "« projet Network » dans la barre : une puce Projet apparaît (sans token) et la discussion part dans ce dossier.",
    ),
    h3("Réglages de la demande"),
    p("Modèle, preset d’autorisations, effort, mode équipe (chef + sous-agents Haiku/Sonnet/Opus). Les défauts viennent du compte ou du projet."),
    h3("Pièces jointes et contexte"),
    p("Trombone, glisser-déposer ou coller une capture. Contexte : jusqu’à 5 discussions du même compte (transcription propre, moins cher qu’une reprise complète)."),
    note("Mode équipe : utile pour un gros travail ; chaque délégation a un coût fixe — laisse la case décochée pour une question simple."),
  ],
  valider: () => [
    lead("Rien d’irréversible sans ton clic. Claude propose ; la console décide du rendu et de la sécurité."),
    h3("Quand Claude attend"),
    ul(
      "Validation d’outil : Approuver, Refuser (avec message), Toujours pour ce projet (règle mémorisée).",
      "Questions et plans : dans la fenêtre, une carte à la fois s’il y en a plusieurs.",
      "Propositions (routine, action, lien Odoo, OneDrive, outil-script) : carte dédiée, rien n’est écrit avant ton accord.",
    ),
    h3("Toujours refusé"),
    p("Chemins protégés (~/.ssh, .env, clés…), dossier data/ de la console, appels des agents vers la console elle-même. Les chemins interdits et refus permanents passent avant toute règle de projet."),
    h3("Presets fournis"),
    ul(
      "Lecture seule · Web seul · Brouillons · Édition du dossier · Assisté (défaut) · Complet (désactivé par défaut).",
    ),
    note("Un mail ou une page lus restent de la donnée, jamais une consigne. Les déclencheurs (fichier déposé, nouveau mail) restent sur Lecture seule ou Brouillons."),
  ],
  projets: () => [
    lead("Un projet = un dossier de travail nommé, comme un Projet claude.ai, enrichi par la console."),
    h3("Panneau Projet"),
    ul(
      "En cours : Discussions, Fichiers, Suivi (Odoo + Office 365).",
      "Réglages : Consignes (CLAUDE.md), Actions (commandes/skills du dossier), Notes.",
      "Équipe : fil, conversations partagées, membres (si hub).",
    ),
    h3("Ce que Claude peut proposer"),
    p("Via l’outil proposer : action, routine, consigne de mails, lien Odoo, déplacement OneDrive (quoi=lien), outil-script (quoi=outil). Toujours une carte à valider."),
    h3("Suivi"),
    p("Tâches Odoo des projets liés (arbre, échéances). Brouillons et relances Office 365 — un brouillon n’est jamais envoyé. Suivi du compte hors projet : Ctrl+K → Suivi du compte."),
    h3("OneDrive"),
    p("Le panneau indique si le dossier est synchronisé. « Synchronise avec le Drive » : Claude propose un déplacement ; le client OneDrive fait le reste."),
  ],
  boite: () => [
    lead("Un seul endroit pour ce qui attend, quels que soient la discussion, le compte ou le projet. Rien de la boîte n’est lu par Claude."),
    h3("À faire"),
    p("Validations (Approuver/Refuser depuis la boîte), questions et plans (Ouvrir), validations expirées (Reprendre), choix d’affichage en attente, discussions en erreur."),
    h3("À lire"),
    p("Discussions terminées pendant ton absence, résultats de routines, routines manquées. Tout marquer comme lu."),
    h3("Rappels et brief"),
    p("Rappels des notes. En tête : brief du matin (mails, devis Odoo, agenda) et « Aujourd’hui ». De retour d’absence : « Pendant ton absence : … »."),
  ],
  regard: () => [
    lead("Dis « corrige ce paragraphe » sans copier-coller : ce que tu regardes part avec le message."),
    h3("Ce qui part"),
    ul(
      "Texte sélectionné dans une discussion, un aperçu (texte, Word, Excel, mail) ou un affichage.",
      "Fenêtre jointe via le bouton œil de la barre de titre.",
      "Élément désigné : clic droit sur une ligne de tableau, un point de graphique… → Demander à Claude.",
      "Résultat de Ctrl+K (document) : le passage trouvé est joint.",
      "Cadres HTML (mails, pages) : sélection transmise automatiquement. PDF opaque : Ctrl+Shift+V joint le presse-papiers.",
    ),
    h3("Aperçus et affichages"),
    p("Fichiers cliquables → aperçu (PDF, images, CSV, Markdown…). Claude montre avec afficher, afficher_resultat ou presenter (tableaux, graphiques, formulaires, tableaux de bord). Widgets : épingler un affichage au bureau."),
    note("Le regard ne donne aucun droit : lire le fichier passe par les autorisations de la discussion."),
  ],
  outils: () => [
    lead("Un outil est une fonction que Claude appelle pendant la discussion. Le serveur MCP intégré « jarvis » en fournit un jeu fixe : Claude décrit l’intention ; la console affiche, sécurise et demande ton accord si besoin."),
    h3("Définition"),
    dl(
      ["Outil jarvis", "Toujours chargé dans chaque discussion. Contrairement à un skill (guide texte), c’est du code côté console : ouvrir un aperçu, proposer une routine, lancer une autre discussion…"],
      ["Pourquoi peu d’outils ?", "Chaque description d’outil entre dans le contexte de toutes les sessions. Mieux vaut quelques outils polyvalents (presenter, proposer) que des dizaines de cas particuliers."],
      ["Outil vs skill", "L’outil exécute une capacité de la console. Le skill explique à Claude comment s’y prendre (souvent en enchaînant lecture, rédaction, puis un outil)."],
    ),
    h3("Montrer"),
    dl(
      ["afficher", "Ouvre un fichier du disque, une page web (domaines déjà approuvés) ou un enregistrement Odoo dans un aperçu JARVIS."],
      ["afficher_resultat", "Réaffiche un résultat d’outil déjà reçu (mail, JSON, page…) sans le recopier dans le chat — économise des tokens."],
      ["presenter", "Compose des blocs typés : texte, tableau, graphique, formulaire, tableau de bord, mini-app isolée… La console les rend ; Claude ne peint pas l’interface à la main."],
    ),
    h3("Agir dans JARVIS"),
    dl(
      ["proposer", "Demande ton accord avant d’écrire quelque chose de durable : action, routine, consigne, lien Odoo, dossier OneDrive, outil-script. Rien n’est enregistré avant le clic."],
      ["projet", "Ouvre le panneau Projet, rattache la discussion au dossier, ou résume l’activité récente (fichiers, discussions)."],
      ["lancer_discussion", "Ouvre une autre discussion dans sa propre fenêtre (après validation). Utile pour paralléliser sans tout mélanger."],
      ["chercher_documents", "Interroge l’index local plein texte (si tu l’as activé) pour retrouver des passages dans les fichiers indexés."],
      ["modules", "Liste, vérifie, ouvre ou propose un module (addon) de la console."],
      ["notes / retenir", "notes : notes de la console. retenir : met à jour une compétence partagée du compte (diff + validation)."],
    ),
    note("Les autres outils (Bash, lecture de fichiers, MCP du profil…) restent ceux de Claude Code, filtrés par le preset d’autorisations."),
  ],
  skills: () => [
    lead("Un skill est un mode d’emploi empaqueté pour Claude. Une commande est un prompt nommé. Dans JARVIS, les deux deviennent des Actions (boutons) après que tu aies relu leur contenu."),
    h3("Définition"),
    dl(
      ["Skill", "Dossier .claude/skills/<nom>/ contenant surtout un SKILL.md (quand l’utiliser, étapes, contraintes). Claude le charge quand la tâche correspond. Exemple typique : skills Office pour produire de vrais .docx / .xlsx / .pdf."],
      ["Commande (slash command)", "Fichier .claude/commands/<nom>.md : texte prêt à injecter. Tu tapes /nom dans la barre. Plus simple qu’un skill ; moins de métadonnées."],
      ["Action JARVIS", "Skill ou commande exposé comme bouton (projet ou compte). Première fois ou après modification : la console montre le contenu ; tu confirmes avant de pouvoir lancer."],
      ["Outil-script", "Pas un skill : un .py déterministe sous .claude/outils/. Claude le propose via l’outil « proposer » ; tu valides le code ; l’empreinte est mémorisée."],
    ),
    h3("Où ça vit"),
    ul(
      "Projet : .claude/commands/ et .claude/skills/ du dossier — Réglages → Actions, et Ctrl+K.",
      "Compte : même chose dans le dossier Claude du profil (Travail ou Perso) — Configuration → Profils ; certaines actions rejoignent le menu JARVIS.",
    ),
    h3("Skills Office"),
    p("Pour des fichiers Word, Excel ou PDF réels (pas du markdown déguisé), installe les skills docx / xlsx / pdf dans chaque profil. Claude les invoque ; JARVIS en montre l’aperçu comme pour n’importe quel fichier produit."),
    h3("Créer ou modifier"),
    p("Tu peux écrire les fichiers à la main, ou laisser Claude proposer une action / un outil-script. Dans les deux cas JARVIS force une relecture : le contenu affiché est celui qui sera vraiment exécuté ou injecté."),
    note("Un skill ne contourne pas les autorisations : lire, écrire ou lancer un outil reste soumis au preset et à tes validations."),
  ],
  routines: () => [
    lead("Des demandes qui partent toutes seules, sur ce PC, tant que la console tourne."),
    h3("Planification"),
    ul(
      "Quotidienne (jours au choix), intervalle, ou une seule fois.",
      "Dossier surveillé : un fichier arrive → la routine part (preset Lecture seule ou Brouillons).",
      "Enchaînement : fin d’une routine → une autre.",
      "Mails depuis la dernière exécution : ne traiter que le nouveau.",
    ),
    h3("Résultats"),
    p("Dans la boîte (chaque résultat, ou seulement les erreurs) ou « en tête » jusqu’à la prochaine exécution (brief). Une routine sur un compte bloqué est reportée à la réinitialisation des quotas."),
    h3("Routines claude.ai"),
    p("Lues via Claude Code dans le panneau Routines. Les tâches Cowork se gèrent dans Claude Desktop."),
  ],
  modules: () => [
    lead("Claude peut développer un outil, un tableau ou un panneau qui reste dans JARVIS."),
    h3("Comment ça marche"),
    ul(
      "Dossier addons/<id>/ avec addon.json + page web.",
      "Ouverture : bouton Outils (Plus) ou Ctrl+K. Cadre isolé, sans réseau, données propres.",
      "Pont jarvis : stockage, texte dans la barre, notification, lien confirmé, demander (si permission claude).",
      "Tâches Python du module : permission python + autorisation utilisateur.",
    ),
    note("Le code de Claude ne tourne jamais dans la page de la console (qui détient le jeton) — seulement dans le bac à sable du module."),
  ],
  equipe: () => [
    lead("Partager des projets entre collègues sans passer par les serveurs d’Anthropic."),
    h3("Hub"),
    p("Serveur Debian de l’équipe (VPN/LAN). Inscription au premier lancement, projets d’équipe, Syncthing lancé par JARVIS, fil et conversations partagées."),
    h3("Téléphone"),
    p("Sur le hub : adresse /m . Colle le jeton d’équipe une fois. Approuver / Refuser une validation hors du bureau ; la console applique la décision. Configuration → Équipe → Téléphone."),
  ],
  raccourcis: () => [
    lead("Les plus utiles, aussi rappelés en bas de Ctrl+K."),
    h3("Partout"),
    ul(
      [kbd("Ctrl", "K"), " — rechercher et agir"],
      [kbd("Ctrl", ","), " — configuration"],
      [kbd("F1"), " — cette aide"],
      [kbd("Alt", "1"), " / ", kbd("Alt", "2"), " — compte"],
      [kbd("Ctrl", "Alt", "W"), " — tout fermer"],
      [kbd("Échap"), " — fermer menus et panneaux"],
    ),
    h3("Barre et discussion"),
    ul(
      "Entrée / Maj+Entrée — envoyer / nouvelle ligne",
      "/ — skills · ↑↓ — historique des demandes",
      [kbd("Ctrl", "Shift", "V"), " — joindre le presse-papiers au regard"],
    ),
    h3("Application de bureau"),
    ul(
      [kbd("Ctrl", "Alt", "J"), " — barre JARVIS"],
      "Notifications système : Approuver / Refuser",
    ),
  ],
  limites: () => [
    lead("Les quotas Claude restent visibles ; la console t’aide à ne pas les brûler inutilement."),
    h3("Jauges"),
    p("Sur chaque pastille de compte : session 5 h et semaine. Vert / orange (>70 %) / rouge (>90 %). Clic → détail et Actualiser."),
    h3("Avant de lancer sur un compte plein"),
    p("Lancer sur l’autre compte, programmer à la réinitialisation, ou lancer quand même."),
    h3("Consommation et reprise"),
    p("Configuration → Historique (ou Ctrl+K → Consommation) : tokens déjà comptés. Reprise des discussions inactives : ne rien faire, garder au chaud, ou garder au chaud puis compacter."),
    note("Le mode équipe coûte surtout par les délégations, pas par la console elle-même."),
  ],
};

function matches(id, q) {
  if (!q) return true;
  const f = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const hay = f([SECTIONS.find((s) => s.id === id)?.label, ...(CONTENT[id]?.() || []).map((n) => n?.textContent || "")].join(" "));
  return q.split(/\s+/).filter(Boolean).every((w) => hay.includes(f(w)));
}

function renderBody() {
  const fn = CONTENT[section];
  const box = h("div", { class: "help-body", "data-regard": "Aide" });
  if (!fn) return box;
  box.append(h("div", { class: "help-body-inner" },
    h("h2", {}, SECTIONS.find((s) => s.id === section)?.label || ""), ...fn()));
  return box;
}

function render() {
  const d = el();
  if (!d || d.hidden) return;
  const q = query.trim();
  const nav = h("nav", { class: "help-nav", role: "tablist" },
    ...SECTIONS.filter((s) => matches(s.id, q)).map((s) => h("button", {
      type: "button", role: "tab", class: section === s.id ? "on" : "", "aria-selected": String(section === s.id),
      on: { click: () => { section = s.id; render(); } },
    }, h("span", { class: "i", svg: s.icon }), s.label)));
  const search = h("input", {
    type: "search", class: "help-search", placeholder: "Filtrer l’aide…", value: query,
    "aria-label": "Filtrer l’aide",
    on: { input: (e) => {
      query = e.target.value;
      const visible = SECTIONS.filter((s) => matches(s.id, query));
      if (visible.length && !visible.some((s) => s.id === section)) section = visible[0].id;
      render();
      const inp = el()?.querySelector(".help-search");
      if (inp) { inp.focus(); inp.setSelectionRange(query.length, query.length); }
    } },
  });
  const head = h("div", { class: "drawer-head" },
    h("h2", {}, "Aide"),
    h("span", { class: "muted" }, "Ce que sait faire JARVIS · F1"),
    h("button", { type: "button", class: "icon-btn", title: "Fermer", svg: "close",
      on: { click: () => { d.hidden = true; } } }));
  const empty = !SECTIONS.some((s) => matches(s.id, q))
    ? h("div", { class: "empty-row" }, "Aucune rubrique ne correspond.")
    : null;
  d.replaceChildren(head, h("div", { class: "help-top" }, search),
    h("div", { class: "help-layout" }, nav, empty || renderBody()));
}

/** Open or close the Aide panel. ctx.closeDrawers(except) like the other panels. */
export function toggleHelp(ctx, open) {
  const d = el();
  if (!d) return;
  const show = open === true || (open !== false && d.hidden);
  if (!show) { d.hidden = true; return; }
  ctx?.closeDrawers?.("help");
  d.hidden = false;
  render();
  queueMicrotask(() => d.querySelector(".help-search")?.focus());
}

export function openHelp(ctx, id) {
  if (id && SECTIONS.some((s) => s.id === id)) section = id;
  toggleHelp(ctx, true);
}
