// Libellés français pour le menu « / » de la barre (skills et commandes Claude Code).
// Les descriptions d’origine sont souvent en anglais : on ne les affiche jamais telles quelles.

/** Commandes internes / hors sujet pour JARVIS (session CLI, cloud, renommées). */
const HIDDEN = new Set([
  "__remote-workflow", "workflow-launch-exec", "agents", "extra-usage",
  "heapdump", "color", "focus", "autocompact", "auto-mode-setup",
  "design-consent", "design-revoke", "reload-plugins", "reload-skills",
]);

/** Courtes descriptions FR pour les commandes / skills connus. */
const DESC = {
  // Documents
  docs: "Documents collaboratifs (doc, rapport, lettre…)",
  docx: "Créer ou modifier des fichiers Word (.docx)",
  xlsx: "Créer ou modifier des tableurs Excel (.xlsx, .csv…)",
  pptx: "Créer ou modifier des présentations PowerPoint",
  pdf: "Lire, fusionner, annoter ou créer des PDF",
  "google-workspace": "Google Drive, Docs, Sheets ou Slides",
  // Recherche et navigation
  "deep-research": "Recherche approfondie multi-sources, rapport cité",
  "claude-in-chrome": "Piloter Chrome (clics, formulaires, captures)",
  "chrome-browser": "Utiliser l’extension Claude dans Chrome",
  "built-in-browser": "Navigateur intégré de l’application Claude",
  "computer-use": "Contrôler des applications sur cet ordinateur",
  dataviz: "Graphiques, tableaux de bord et visualisations",
  // Code et revue
  "code-review": "Relire un diff ou une PR pour les bugs",
  "security-review": "Revue de sécurité des changements en cours",
  ultrareview: "Agent cloud qui cherche et vérifie des bugs",
  simplify: "Simplifier et nettoyer le code modifié",
  verify: "Vérifier un changement de bout en bout",
  batch: "Gros changement en parallèle (plusieurs agents / PR)",
  run: "Lancer l’appli du projet pour vérifier un changement",
  "run-skill-generator": "Écrire le skill run-<unité> de ce projet",
  debug: "Activer le journal de débogage de la session",
  // Claude Code / config
  "update-config": "Configurer Claude Code (settings.json, automatismes)",
  "fewer-permission-prompts": "Réduire les demandes d’autorisation fréquentes",
  doctor: "Diagnostic de l’installation Claude Code",
  init: "Initialiser un CLAUDE.md pour le dossier",
  mcp: "Gérer les serveurs MCP",
  import: "Importer une config d’un autre agent",
  model: "Choisir le modèle Claude",
  effort: "Régler l’effort (profondeur) du modèle",
  fast: "Basculer le mode rapide",
  config: "Modifier un réglage Claude Code",
  "output-style": "Styles de réponse",
  context: "Afficher l’usage du contexte",
  compact: "Résumer la conversation pour libérer du contexte",
  clear: "Nouvelle session (l’ancienne reste sur le disque)",
  rename: "Renommer la conversation",
  usage: "Coût et quotas de la session",
  "usage-credits": "Crédits d’usage / demande à l’admin",
  insights: "Rapport sur tes sessions Claude Code",
  recap: "Résumé en une ligne de la session",
  "skill-doctor": "Skills chargés inutilisés (coût contexte)",
  loop: "Relancer une commande à intervalle (/loop 5m /foo)",
  schedule: "Agents cloud planifiés (cron)",
  goal: "Fixer un objectif jusqu’à condition remplie",
  advisor: "Consulter un modèle plus fort à des moments clés",
  "list-agents": "Lister sous-agents et sessions joignables",
  "team-onboarding": "Guide d’adoption Claude Code pour l’équipe",
  // Création de skills / plugins
  "skill-creator": "Créer ou améliorer un skill",
  "plugin-authoring": "Modifier l’interface ou le comportement de Claude",
  "workflow-authoring": "Écrire un script de workflow",
  "claude-api": "Référence API Claude / SDK Anthropic",
  design: "Accès agent aux projets Design",
  "design-sync": "Pousser un design system React vers claude.ai/design",
  // Divers skills
  morning: "Brief du matin (affichage ou tâche récurrente)",
  "import-memory": "Importer une mémoire depuis un autre assistant",
};

const FR_MARK = /[àâäéèêëïîôöùûüçœæ]/i;
const FR_WORD = /\b(le|la|les|un|une|des|du|de|et|ou|pour|avec|dans|sur|qui|que|dont|quand|cette|ces|sont|être|fait|fichier|dossier|projet|créer|modifier|lire)\b/i;
const EN_LEAD = /^(use this skill|read this|grant |enable |show |set |toggle |start |create |review |verify |run |launch |configure |manage |list |import |dump |free up |let claude |teach |complete |generate |research |push |automates |author |make a |help teammates|\(removed\))/i;

function looksFrench(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  if (FR_MARK.test(t) || FR_WORD.test(t)) return true;
  if (EN_LEAD.test(t)) return false;
  // Beaucoup d’anglais ASCII sans indices FR → on ne l’affiche pas
  const letters = t.replace(/[^a-zA-ZÀ-ÿ]/g, "");
  if (!letters) return false;
  const ascii = letters.replace(/[^a-zA-Z]/g, "").length;
  return ascii / letters.length < 0.92;
}

function clip(s, n = 110) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + "…";
}

/** Description courte en français, ou chaîne vide. */
export function slashDescription(name, description) {
  const key = String(name || "").toLowerCase();
  if (DESC[key]) return DESC[key];
  const raw = String(description || "").trim();
  if (raw && looksFrench(raw)) return clip(raw);
  return "";
}

/** true = ne pas proposer cette entrée dans le menu JARVIS. */
export function slashHidden(name) {
  const key = String(name || "").toLowerCase();
  return !key || key.startsWith("__") || HIDDEN.has(key);
}

/** Entrée prête pour le menu « / ». */
export function slashItem(c) {
  const name = String(c?.name || "").trim();
  if (slashHidden(name)) return null;
  const desc = slashDescription(name, c?.description);
  return {
    value: `/${name} `,
    label: `/${name}`,
    desc: desc || (c?.hint && looksFrench(c.hint) ? clip(c.hint, 80) : "Skill ou commande"),
  };
}
