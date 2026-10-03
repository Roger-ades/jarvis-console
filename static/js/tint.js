// Colors of a window, a modal or a reminder: the account's alone, or the account's mixed with the
// project's when it belongs to one. A tint is {account, project, color}: color is the mix (or the account's
// color), project is "" outside a project. Elements painted get --pc (the mix) and, in a project, --pc-a,
// --pc-p and the class "tinted" for the two-color gradients of the CSS.
import { projectFor } from "./projects.js";
export { paint } from "./util.js";

const FALLBACK = "#72c9ff";

const hex = (c) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(c || "").trim());
  return m ? `#${m[1].toLowerCase()}` : "";
};

// sRGB <-> OKLab (Björn Ottosson), to mix in OKLCH: the mix keeps the saturation of both colors
// (orange + sky blue gives a fresh green between them on the wheel, not a grey).
const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const gam = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

function toLch(c) {
  const n = parseInt(c.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => lin(v / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [L, Math.hypot(A, B), Math.atan2(B, A)];
}

function fromLch([L, C, H]) {
  const A = C * Math.cos(H), B = C * Math.sin(H);
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  return `#${rgb.map((v) => Math.round(Math.min(1, Math.max(0, gam(Math.min(1, Math.max(0, v))))) * 255)
    .toString(16).padStart(2, "0")).join("")}`;
}

/** Half of each color, hue taken the short way round. A grey takes the other color's hue. */
export function mix(a, b, w = 0.5) {
  a = hex(a); b = hex(b);
  if (!a || !b) return a || b || FALLBACK;
  if (a === b) return a;
  const [la, ca, ha] = toLch(a), [lb, cb, hb] = toLch(b);
  let h1 = ca < 0.02 ? hb : ha, h2 = cb < 0.02 ? ha : hb;
  let d = h2 - h1;
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d < -Math.PI) d += 2 * Math.PI;
  return fromLch([la + (lb - la) * w, ca + (cb - ca) * w, h1 + d * w]);
}

/** The tint of an account color and, maybe, a project color. */
export function tintOf(account, project = "") {
  const a = hex(account) || FALLBACK, p = hex(project);
  return { account: a, project: p, color: p ? mix(a, p) : a };
}

let accounts = { color: () => "", current: () => "" };
/** Where the accounts' colors come from (the configuration), and the account chosen in the request bar. */
export function setAccounts(source) { accounts = { ...accounts, ...source }; }
export const accountColor = (pid) => accounts.color(pid || accounts.current()) || FALLBACK;

/** The tint of a project folder for an account (by default the one of the request bar, else the project's). */
export function projectTint(folder, pid = "") {
  const proj = typeof folder === "string" ? projectFor(folder) : folder;
  return tintOf(accountColor(pid || accounts.current() || proj?.profile), proj?.color);
}

/** A discussion's tint: its account, mixed with its project's color when its folder is a project. */
export function taskTint(t) {
  if (!t) return null;
  return tintOf(t.color, projectFor(t.workdir)?.color);
}

/** The single color of a tint (or of a plain color string), for what shows one color only. */
export const colorOf = (c) => (typeof c === "string" ? c : c?.color) || "";
