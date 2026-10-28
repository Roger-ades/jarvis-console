(function(){
// The motion of the modules and applications (with addon_kit.css): what appears comes in, key figures count up,
// charts draw themselves, the pointer lights the key figures. It runs in the page itself (in the desktop app the
// page is a window of its own: its observers and frames are its own). Never when the system asks for less motion;
// never for a list redrawn while the user types, nor for content redrawn the same.
if (window.__jvKit) return;
window.__jvKit = 1;
const calme = matchMedia("(prefers-reduced-motion: reduce)");
const ANIM = ".carte,.card,.chiffre,.kpi,.tuile,.panneau,article,li,tbody>tr,.alerte-liste>div,svg";
const COMPTE = ".chiffre b,.kpi b,.kpi-valeur,[data-compteur]";
const RANG_MAX = 14, LIGNES = 24, MAX = 90;
let saisie = -1e9;
addEventListener("input", () => { saisie = performance.now(); }, true);
const texte = (nodes) => { let s = ""; for (const n of nodes) s += n.textContent; return s; };

// a chart: bars grow from their base (from the left when they are horizontal), curves trace themselves
function graphique(svg) {
  const rects = svg.querySelectorAll("rect");
  if (rects.length > 2) {
    const w = new Set(), h = new Set();
    for (const r of rects) { w.add(Math.round(+r.getAttribute("width") || 0)); h.add(Math.round(+r.getAttribute("height") || 0)); }
    svg.classList.toggle("jv-barres-h", h.size < w.size);
  }
  for (const p of svg.querySelectorAll("polyline,path")) {
    const f = p.getAttribute("fill"), s = p.getAttribute("stroke");
    if (p.getAttribute("stroke-dasharray") || !s || s === "none") continue;
    if (f === "none" || (p.tagName === "polyline" && !f)) { p.setAttribute("pathLength", "1"); p.classList.add("jv-trace"); }
  }
}

// a key figure counts up to its value, in its own format ("4 928 027 €", "23,8 %", "32 / 182")
const NOMBRE = /-?\d+(?:[   .]\d{3})*(?:,\d+)?/g;
function compter(el) {
  if (el.childElementCount || el.dataset.compteur === "non") return;
  const brut = el.textContent;
  if (/\d[/:.-]\d/.test(brut.replace(/\d[   .]\d{3}/g, ""))) return;   // a date, an hour, a reference
  const morceaux = [];
  for (const m of brut.matchAll(NOMBRE)) {
    const t = m[0], sep = (t.match(/\d([   .])\d{3}/) || [])[1] || " ";
    const dec = (t.split(",")[1] || "").length;
    const v = parseFloat(t.replace(/[   .]/g, "").replace(",", "."));
    if (!isFinite(v) || /^-?0\d/.test(t)) return;
    morceaux.push({i: m.index, n: t.length, v, sep, fmt: new Intl.NumberFormat("fr-FR", {minimumFractionDigits: dec, maximumFractionDigits: dec})});
  }
  if (!morceaux.length || morceaux.every((m) => Math.abs(m.v) < 2)) return;
  const ecrire = (k) => {
    let s = "", j = 0;
    for (const m of morceaux) {
      s += brut.slice(j, m.i) + m.fmt.format(m.v * k).replace(/[   ]/g, m.sep).replace("−", "-");
      j = m.i + m.n;
    }
    return s + brut.slice(j);
  };
  const t0 = performance.now(), duree = 750;
  let dernier = el.textContent = ecrire(0);
  const pas = (t) => {
    if (el.textContent !== dernier) return;   // the module wrote it again: its value wins
    const k = Math.min(1, (t - t0) / duree);
    el.textContent = dernier = k < 1 ? ecrire(1 - Math.pow(1 - k, 3)) : brut;
    if (k < 1) requestAnimationFrame(pas);
  };
  requestAnimationFrame(pas);
  // a hidden page gets no frames: the true value comes back all the same
  setTimeout(() => { if (el.textContent === dernier) el.textContent = brut; }, duree + 150);
}

function entrer(racines, tout) {
  const els = [], nombres = [];
  for (const n of racines) {
    if (els.length < MAX) {
      if (!tout && n.matches(ANIM)) els.push(n);
      for (const e of n.querySelectorAll(ANIM)) { if (els.length >= MAX) break; els.push(e); }
    }
    if (n.matches(COMPTE)) nombres.push(n);
    nombres.push(...n.querySelectorAll(COMPTE));
  }
  const rang = new Map();
  for (const el of els) {
    if (el.closest(".jv-fixe")) continue;
    const p = el.parentNode, i = rang.get(p) || 0;
    rang.set(p, i + 1);
    if (el.tagName === "TR" && i >= LIGNES) continue;
    el.style.setProperty("--jv-i", Math.min(i, RANG_MAX));
    if (el.tagName === "svg") graphique(el);
    el.classList.add("jv-in");
  }
  for (const el of nombres.slice(0, 40)) if (!el.closest(".jv-fixe")) compter(el);
}

new MutationObserver((recs) => {
  if (calme.matches || performance.now() - saisie < 600) return;
  const racines = [], montres = [];
  for (const r of recs) {
    if (r.type === "attributes") {
      if (r.target.nodeType === 1 && !r.target.hidden && r.oldValue !== null) montres.push(r.target);
      continue;
    }
    if (!r.addedNodes.length) continue;
    if (r.removedNodes.length && texte(r.removedNodes) === texte(r.addedNodes)) continue;
    for (const n of r.addedNodes) if (n.nodeType === 1 && n.isConnected) racines.push(n);
  }
  if (racines.length) entrer(racines, false);
  if (montres.length) entrer(montres, true);
}).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"], attributeOldValue: true});

// the class goes once the motion is over: shown again, the element comes in again
addEventListener("animationend", (ev) => {
  const el = ev.target;
  if (el.classList && el.classList.contains("jv-in") && /^jv-in/.test(ev.animationName)) el.classList.remove("jv-in");
  else if (el.tagName === "svg" && el.classList.contains("jv-in") && ev.animationName === "jv-trace") el.classList.remove("jv-in");
}, true);
setInterval(() => { for (const s of document.querySelectorAll("svg.jv-in")) if (!s.getAnimations({subtree: true}).length) s.classList.remove("jv-in"); }, 3000);

// tables: each figure is marked (aligned, below zero, nothing), the sorted column (its title ends with ▲ or ▼,
// or carries aria-sort) is tinted and shows the size of each value; data-barres on a table shows it on every
// column of figures. The height of the header is given to the group lines that stay under it.
const CHIFFRE = /^[-−+]?\s?\d[\d \u00a0\u202f.]*(?:,\d+)?\s?(?:k€|M€|€|%|h|j)?$/, VIDE = /^[—–-]$/;
const valeur = (t) => parseFloat(t.replace(/[^\d,−-]/g, "").replace("−", "-").replace(",", "."));
function tables() {
  for (const t of [...document.querySelectorAll("table")].slice(0, 30)) {
    const tete = t.tHead && t.tHead.rows[t.tHead.rows.length - 1];
    if (t.tHead && t.tHead.offsetHeight) t.style.setProperty("--jv-th", t.tHead.offsetHeight + "px");
    const ths = tete ? [...tete.cells] : [];
    let trie = -1;
    ths.forEach((th, i) => {
      const s = /[▲▼↑↓]\s*$/.test(th.textContent) || /^(a|de)scending$/.test(th.getAttribute("aria-sort") || "");
      th.classList.toggle("jv-trie", s);
      if (s && trie < 0) trie = i;
    });
    const barres = t.hasAttribute("data-barres"), cols = [];
    const lignes = [];
    for (const b of t.tBodies) for (const tr of b.rows) { if (lignes.length >= 800) break; lignes.push(tr); }
    for (const tr of lignes) {
      const seul = tr.cells.length === 1 && tr.cells[0].colSpan > 1;
      tr.classList.toggle("jv-groupe", seul && !tr.classList.contains("groupe"));
      const aligne = ths.length > 0 && tr.cells.length === ths.length, compte = aligne && !tr.classList.contains("groupe");
      [...tr.cells].forEach((td, i) => {
        const s = td.textContent.trim(), n = CHIFFRE.test(s);
        const v = n ? valeur(s) : NaN;
        td.classList.toggle("jv-num", n);
        td.classList.toggle("jv-neg", n && v < 0);
        td.classList.toggle("jv-nul", (n && v === 0) || VIDE.test(s));
        td.classList.toggle("jv-col", aligne && i === trie);
        if (compte && (barres || i === trie)) {
          const c = cols[i] || (cols[i] = {max: 0, cases: []});
          if (n && isFinite(v)) { c.max = Math.max(c.max, Math.abs(v)); c.cases.push([td, Math.abs(v)]); }
          else if (s) c.texte = (c.texte || 0) + 1;
        }
        if (!compte || !(barres || i === trie)) td.classList.remove("jv-mesure");
      });
    }
    for (const c of cols) {
      if (!c) continue;
      const ok = c.max > 0 && c.cases.length >= 2 && c.cases.length >= 2 * (c.texte || 0);
      for (const [td, v] of c.cases) {
        td.classList.toggle("jv-mesure", ok);
        if (ok) td.style.setProperty("--jv-p", Math.max(2, Math.round(v / c.max * 100)) + "%");
      }
    }
  }
}

// a search lights up what it finds in the rows and lists, without touching them (CSS highlights); capitals and
// accents count for nothing ("ecole" finds "École")
const plier = (s) => { let r = ""; for (const ch of s) r += (ch.normalize("NFD")[0] || ch).toLowerCase(); return r; };
function surligner() {
  if (!window.CSS || !CSS.highlights || typeof Highlight !== "function") return;
  const mots = new Set();
  for (const i of document.querySelectorAll("input[type=search]")) {
    if (!i.value.trim() || i.closest(".menu") || !i.offsetParent) continue;
    for (const m of plier(i.value).split(/\s+/)) if (m.length >= 2) mots.add(m);
  }
  if (!mots.size) { CSS.highlights.delete("jv-trouve"); return; }
  const plages = [];
  let noeuds = 0;
  for (const racine of document.querySelectorAll("tbody,.alerte-liste,[data-jv-cherche]")) {
    const w = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n && noeuds < 4000 && plages.length < 2000; n = w.nextNode()) {
      noeuds++;
      const p = plier(n.data);
      if (p.length !== n.data.length) continue;
      for (const m of mots) {
        for (let k = p.indexOf(m); k >= 0; k = p.indexOf(m, k + m.length)) {
          const r = new Range(); r.setStart(n, k); r.setEnd(n, k + m.length); plages.push(r);
        }
      }
    }
  }
  CSS.highlights.set("jv-trouve", new Highlight(...plages));
}

// a filter that narrows the view is marked: a search with text, a date, a list moved from its first "all" choice
function filtres() {
  for (const el of document.querySelectorAll("input[type=search],select,:is(.filtres,.pfiltres,.puces,.recherche) input:not([type=checkbox],[type=radio],[type=button])")) {
    let actif;
    if (el.tagName === "SELECT") actif = !el.multiple && el.options.length > 1 && el.options[0].value === "" && el.value !== "";
    else actif = el.value !== "";
    el.classList.toggle("jv-actif", actif);
  }
}

let prevu = 0;
const bientot = () => { clearTimeout(prevu); prevu = setTimeout(() => { tables(); surligner(); filtres(); }, 140); };
new MutationObserver(bientot).observe(document.documentElement, {childList: true, subtree: true});
addEventListener("input", bientot, true);
addEventListener("change", bientot, true);
addEventListener("DOMContentLoaded", bientot);

// "/" reaches the search of the page, as in the tools of developers
addEventListener("keydown", (ev) => {
  if (ev.key !== "/" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const a = document.activeElement;
  if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
  const champ = [...document.querySelectorAll("input[type=search]")].find((i) => i.offsetParent && !i.closest(".menu,dialog:not([open])"));
  if (!champ) return;
  ev.preventDefault();
  champ.focus();
  champ.select();
});

// the halo of a key figure follows the pointer
addEventListener("pointermove", (ev) => {
  const c = ev.target.closest && ev.target.closest(".chiffre,.kpi,.jv-halo");
  if (!c) return;
  const r = c.getBoundingClientRect();
  c.style.setProperty("--mx", (ev.clientX - r.left) + "px");
  c.style.setProperty("--my", (ev.clientY - r.top) + "px");
}, {passive: true});
})();
