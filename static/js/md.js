// Safe markdown renderer. The whole source is HTML-escaped FIRST; the parser then
// only ever emits its own tags, so model or tool output cannot inject markup.
import { esc } from "./util.js";

const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const QUOTE = /^\s*&gt;\s?/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function safeUrl(url) {
  const raw = url.replace(/&amp;/g, "&");
  return /^(https?:\/\/|mailto:)/i.test(raw);
}

// Local files worth a preview (Windows and macOS absolute paths, ~/…).
const EXT = "png|jpe?g|gif|webp|svg|bmp|pdf|html?|txt|md|csv|tsv|json|log|xml|xlsx?|xlsm|docx?|pptx?";
const PATH_RX = new RegExp(`^(?:[A-Za-z]:[\\\\/]|~[\\\\/]|/(?:Users|home|Volumes|tmp)/)[^<>|*?\\n\\u0000]*?\\.(?:${EXT})$`, "i");
// In running text, folder and file names may contain spaces ("Dev Projects") but never start or
// end with one. \u0000 is excluded so a path or URL never swallows a rendered placeholder.
const SEG = "[^<>|*?:\\n\\u0000,;\\\\/\\s](?:[^<>|*?:\\n\\u0000,;\\\\/]*?[^<>|*?:\\n\\u0000,;\\\\/\\s])?";
const PATH_IN_TEXT = new RegExp(`(^|[\\s(«;*_\\[])((?:[A-Za-z]:[\\\\/]|~[\\\\/]|/(?:Users|home|Volumes)/)(?:${SEG}[\\\\/])*?${SEG}\\.(?:${EXT}))(?=$|[\\s.,;:!?)»*_\\]&])`, "gi");
const LOCAL_LINK = /(!?)\[([^\]\n]*)\]\((?:&lt;)?((?:[A-Za-z]:[\\/]|~[\\/]|\/(?:Users|home|Volumes|tmp)\/)[^)\n\u0000]*?)(?:&gt;)?\)/g;
export const IMG_EXT = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;

const fileRef = (path, label) => `<a href="#" class="fileref${IMG_EXT.test(path) ? " img" : ""}" data-path="${path}" title="Aperçu : ${path}">${label}</a>`;

function extImage(url, alt) {
  const host = (url.match(/^https?:\/\/([^/?#]+)/i) || [])[1] || "web";
  return `<button type="button" class="ext-img" data-src="${url}" title="${url}">Image ${alt ? `« ${alt} » ` : ""}· ${host} — afficher</button>`;
}

function inline(s) {
  const keep = [];
  const hold = (html) => { keep.push(html); return `\u0000${keep.length - 1}\u0000`; };
  s = s.replace(/`([^`\n]+)`/g, (_, c) => hold(PATH_RX.test(c.trim()) ? fileRef(c.trim(), `<code>${c}</code>`) : `<code>${c}</code>`));
  s = s.replace(LOCAL_LINK, (m, bang, text, path) => (PATH_RX.test(path) ? hold(fileRef(path, text || path)) : m));
  s = s.replace(/!\[([^\]\n]*)\]\(([^)\s\u0000]+)\)/g, (m, alt, url) => {
    if (safeUrl(url) && /^https?:/i.test(url)) return hold(extImage(url, alt));
    if (PATH_RX.test(url)) return hold(fileRef(url, alt || url));
    return m;
  });
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s\u0000]+)(?:\s+&quot;[^\n]*?&quot;)?\)/g, (m, text, url) =>
    safeUrl(url) ? hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`)
      : PATH_RX.test(url) ? hold(fileRef(url, text)) : m);
  s = s.replace(PATH_IN_TEXT, (m, pre, path) => `${pre}${hold(fileRef(path, path))}`);
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)\u0000]+)/g, (m, pre, url) => {
    const trail = url.match(/[.,;:!?]+$/)?.[0] || "";
    const u = trail ? url.slice(0, -trail.length) : url;
    return `${pre}${hold(`<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`)}${trail}`;
  });
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__(?=\S)([\s\S]*?\S)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, n) => keep[Number(n)]);
}

function codeBlock(lang, code) {
  return `<div class="codeblock${lang ? " has-lang" : ""}">${lang ? `<span class="lang">${lang}</span>` : ""}` +
    `<button type="button" class="btn small ghost copy-code" title="Copier le code">Copier</button>` +
    `<pre><code>${code}</code></pre></div>`;
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function table(lines, i) {
  const head = splitRow(lines[i]);
  const aligns = splitRow(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "al-c" : c.endsWith(":") ? "al-r" : ""));
  const cell = (tag, c, k) => `<${tag}${aligns[k] ? ` class="${aligns[k]}"` : ""}>${inline(c)}</${tag}>`;
  let html = `<table><thead><tr>${head.map((c, k) => cell("th", c, k)).join("")}</tr></thead><tbody>`;
  i += 2;
  while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
    const row = splitRow(lines[i]);
    html += `<tr>${head.map((_, k) => cell("td", row[k] ?? "", k)).join("")}</tr>`;
    i++;
  }
  return [html + "</tbody></table>", i];
}

function list(lines, i) {
  const items = [];
  while (i < lines.length) {
    const l = lines[i];
    const m = l.match(LIST);
    if (m) {
      items.push({ indent: m[1].replace(/\t/g, "    ").length, ordered: /\d/.test(m[2]), text: m[3] });
      i++;
    } else if (l.trim() && /^\s{2,}/.test(l) && items.length && !FENCE.test(l)) {
      items[items.length - 1].text += "\n" + l.trim();
      i++;
    } else break;
  }
  let html = "";
  const stack = [];
  for (const it of items) {
    while (stack.length && it.indent < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
    if (!stack.length || it.indent > stack[stack.length - 1].indent) {
      const tag = it.ordered ? "ol" : "ul";
      html += `<${tag}>`;
      stack.push({ indent: it.indent, tag });
    } else html += "</li>";
    let text = it.text, cls = "";
    const task = text.match(/^\[([ xX])\]\s+([\s\S]*)$/);
    if (task) { text = (task[1] === " " ? "☐ " : "☑ ") + task[2]; cls = ' class="task"'; }
    html += `<li${cls}>${inline(text).replace(/\n/g, "<br>")}`;
  }
  while (stack.length) html += `</li></${stack.pop().tag}>`;
  return [html, i];
}

function startsBlock(lines, i) {
  const l = lines[i];
  return FENCE.test(l) || HEADING.test(l) || HR.test(l) || LIST.test(l) || QUOTE.test(l) ||
    (l.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]));
}

function blocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if ((m = line.match(FENCE))) {
      const fence = m[1];
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) body.push(lines[i++]);
      i++;
      out.push(codeBlock(m[2], body.join("\n")));
    } else if (!line.trim()) {
      i++;
    } else if ((m = line.match(HEADING))) {
      const lvl = Math.min(m[1].length, 4);
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
      i++;
    } else if (HR.test(line)) {
      out.push("<hr>");
      i++;
    } else if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const [html, next] = table(lines, i);
      out.push(html);
      i = next;
    } else if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && QUOTE.test(lines[i])) body.push(lines[i++].replace(QUOTE, ""));
      out.push(`<blockquote>${blocks(body)}</blockquote>`);
    } else if (LIST.test(line)) {
      const [html, next] = list(lines, i);
      out.push(html);
      i = next;
    } else {
      const para = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) para.push(lines[i++]);
      out.push(`<p>${para.map((p) => inline(p.trim())).join("<br>")}</p>`);
    }
  }
  return out.join("");
}

export function renderMarkdown(src) {
  const text = esc(String(src ?? "").replace(/\r\n?/g, "\n"));
  return blocks(text.split("\n"));
}

/** Element with rendered markdown (the only innerHTML sink for untrusted text). */
export function mdElement(src, cls = "md") {
  const div = document.createElement("div");
  div.className = cls;
  div.innerHTML = renderMarkdown(src);
  return div;
}
