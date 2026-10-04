// Highlights a passage, or words, in the text of a preview (Word, Excel, text, Markdown, mail…) and scrolls
// to it. Matching ignores accents and case, as the document index does, and every space: a passage cut by a
// line break, a <br> or the cells of a table is still found.

const fold = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const squeeze = (s) => s.replace(/\s+/g, "");

/** The text of root folded into one string without spaces, with the place of each character. */
function flatten(root) {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const map = [];
  let flat = "";
  for (let n; (n = walker.nextNode());) {
    const s = n.nodeValue;
    for (let i = 0; i < s.length; i++) {
      if (/\s/.test(s[i])) continue;
      for (const ch of fold(s[i])) { flat += ch; map.push([n, i]); }
    }
  }
  return { flat, map };
}

const clean = (s) => fold(String(s || "").replace(/[\x02\x03«»]/g, " ")).replace(/\s+/g, " ").replace(/^[\s.…]+|[\s.…]+$/g, "");

/** Pieces of the passage to look for, longest first: the whole, its sentences, then its two ends. */
function candidates(text) {
  const all = clean(text);
  if (!all) return [];
  const parts = all.split(/\s*(?:…|[.;:!?](?:\s|$))\s*/).filter((x) => squeeze(x).length >= 10);
  const cut = (s, n) => (s.length <= n ? s : s.slice(0, s.lastIndexOf(" ", n) > n / 2 ? s.lastIndexOf(" ", n) : n));
  const ends = all.length > 80 ? [cut(all, 80), all.slice(all.indexOf(" ", all.length - 80) + 1)] : [];
  return [...new Set([all, ...parts.sort((a, b) => b.length - a.length), ...ends].map(squeeze))].filter((x) => x.length >= 3);
}

/** Wraps [start, end) ranges of the flattened text in <mark class="pv-hit">; returns the marks of each range
 * (a passage over several cells or lines takes several), in document order. */
function wrap(map, ranges) {
  const segs = new Map(); // text node -> [[a, b, range index]]
  ranges.forEach(([start, end], k) => {
    let node = null, a = 0, b = 0;
    const flush = () => { if (node) (segs.get(node) || segs.set(node, []).get(node)).push([a, b, k]); };
    for (let i = start; i < end; i++) {
      const [n, at] = map[i];
      if (n !== node) { flush(); node = n; a = at; }
      b = at + 1;
    }
    flush();
  });
  const groups = ranges.map(() => []);
  for (const [node, list] of segs) {
    list.sort((x, y) => y[0] - x[0]); // from the end: the offsets before stay valid on the same node
    for (const [a, b, k] of list) {
      const mid = node.splitText(a);
      mid.splitText(b - a);
      const m = node.ownerDocument.createElement("mark");
      m.className = "pv-hit";
      mid.replaceWith(m);
      m.append(mid);
      groups[k].push(m);
    }
  }
  const order = (a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  return groups.filter((g) => g.length).map((g) => g.sort(order));
}

/** Every place of the words in flat[from, to), in order, without overlaps. */
function occurrences(flat, words, from = 0, to = flat.length) {
  const ranges = [];
  for (const w of words) {
    for (let at = flat.indexOf(w, from); at >= 0 && at + w.length <= to && ranges.length < 200; at = flat.indexOf(w, at + w.length)) {
      ranges.push([at, at + w.length]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  let end = -1;
  return ranges.filter(([a, b]) => (a >= end ? ((end = b), true) : false));
}

/** focus: {text, words}. Without words, marks the passage. With words (a search), the passage only says
 * where: the words are marked there, or everywhere if the passage is not found. Returns, for each place
 * marked, its marks. */
export function highlight(root, focus) {
  root.querySelectorAll("mark.pv-hit").forEach((m) => m.replaceWith(...m.childNodes));
  root.normalize();
  const { flat, map } = flatten(root);
  if (!flat) return [];
  const words = [...new Set((focus?.words || []).map((w) => squeeze(clean(w))).filter((w) => w.length >= 2))];
  for (const c of candidates(focus?.text)) {
    const at = flat.indexOf(c);
    if (at < 0) continue;
    const inside = words.length ? occurrences(flat, words, at, at + c.length) : [];
    return wrap(map, inside.length ? inside : [[at, at + c.length]]);
  }
  return wrap(map, occurrences(flat, words));
}
