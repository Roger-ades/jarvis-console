// « On va travailler dans le projet Network »: the project a request names, found in the page before sending
// (no model, no token). The name or the folder's name of a known project, after « projet », « dossier » or
// « dans »; without accents or capitals; the longest name wins, a name two projects share gives nothing.

/** Lower case, without accents, words separated by one space. */
export function fold(text) {
  return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ").trim();
}

const base = (folder) => String(folder || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const LEAD = "(?:^| )(?:projet|project|dossier|dans)(?: (?:le|la|les|l|du|de|des))?(?: (?:projet|dossier))? ";

/** The project the text names, or null. projects: [{name, folder, …}]. */
export function projectMention(text, projects) {
  const f = fold(text);
  if (!f) return null;
  let best = null, size = 0, tie = false;
  for (const p of projects || []) {
    for (const n of new Set([fold(p.name), fold(base(p.folder))])) {
      if (n.length < 2 || !new RegExp(`${LEAD}${esc(n)}(?= |$)`).test(f)) continue;
      if (n.length > size) { best = p; size = n.length; tie = false; }
      else if (n.length === size && best !== p) tie = true;
    }
  }
  return tie ? null : best;
}
