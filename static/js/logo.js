// The JARVIS emblem: an arc reactor. A white-hot core, a crown of ten coils, a brushed metal ring;
// the home screen adds a HUD around it (graduations, data arcs, an orbiting spark, a radar sweep).
// It lives: idle it turns slowly while a charge runs around the coils; when agents work it spins up
// like a turbine and the sweep scans; when a task awaits validation the core pulses amber.
// static/img/favicon.svg and tests/make_icons.py draw the same reactor, frozen, on a tile.
const NS = "http://www.w3.org/2000/svg";
const logos = new Set();
let uid = 0;
let activity = { run: 0, await: 0 };

// degrees per second of each rotating layer: [idle, running]
const SPEED = { coils: [5, 150], ticks: [-3, -18], arcs: [8, 55], orbit: [22, 90], sweep: [150, 150] };

const pt = (r, deg) => {
  const a = (deg - 90) * Math.PI / 180;
  return `${(r * Math.cos(a)).toFixed(3)} ${(r * Math.sin(a)).toFixed(3)}`;
};

/** A coil: an annular sector between r0 and r1 centred on `deg`, `w` degrees wide at the rim. */
function coil(r0, r1, deg, w) {
  const w0 = w * 0.8;
  return `M${pt(r1, deg - w / 2)}A${r1} ${r1} 0 0 1 ${pt(r1, deg + w / 2)}L${pt(r0, deg + w0 / 2)}A${r0} ${r0} 0 0 0 ${pt(r0, deg - w0 / 2)}Z`;
}

function markup(id, hud) {
  const coils = Array.from({ length: 10 }, (_, i) =>
    `<path class="jv-coil" d="${coil(12.6, 19.4, i * 36, 25)}" fill="url(#jv-cg-${id})"/>`).join("");
  const bolts = [45, 135, 225, 315].map((a) => `<circle class="jv-bolt" cx="${pt(23, a).split(" ")[0]}" cy="${pt(23, a).split(" ")[1]}" r=".95"/>`).join("");
  return `
  <defs>
    <radialGradient id="jv-amb-${id}"><stop offset="0" class="jv-s-a" stop-opacity=".30"/><stop offset=".5" class="jv-s-a" stop-opacity=".07"/><stop offset="1" class="jv-s-a" stop-opacity="0"/></radialGradient>
    <radialGradient id="jv-cg-${id}" cx="0" cy="0" r="19.4" gradientUnits="userSpaceOnUse"><stop offset=".62" class="jv-s-hot"/><stop offset=".8" class="jv-s-a"/><stop offset="1" class="jv-s-deep"/></radialGradient>
    <radialGradient id="jv-co-${id}" cx=".44" cy=".4" r=".64"><stop offset="0" stop-color="#fff"/><stop offset=".45" class="jv-s-hot"/><stop offset=".8" class="jv-s-a"/><stop offset="1" class="jv-s-a2"/></radialGradient>
    <radialGradient id="jv-ha-${id}"><stop offset="0" class="jv-s-hot" stop-opacity=".95"/><stop offset=".42" class="jv-s-a" stop-opacity=".42"/><stop offset="1" class="jv-s-a" stop-opacity="0"/></radialGradient>
    <radialGradient id="jv-hw-${id}"><stop offset="0" stop-color="#ffe2b0" stop-opacity=".95"/><stop offset=".45" class="jv-s-amber" stop-opacity=".5"/><stop offset="1" class="jv-s-amber" stop-opacity="0"/></radialGradient>
    <linearGradient id="jv-me-${id}" x1="-22" y1="-24" x2="20" y2="24" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#d2dde8"/><stop offset=".28" stop-color="#63768b"/><stop offset=".55" stop-color="#1b2531"/><stop offset=".78" stop-color="#52657a"/><stop offset="1" stop-color="#a9b9ca"/></linearGradient>
    <linearGradient id="jv-sw-${id}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" class="jv-s-a" stop-opacity="0"/><stop offset=".7" class="jv-s-a" stop-opacity=".12"/><stop offset="1" class="jv-s-a" stop-opacity=".38"/></linearGradient>
    <filter id="jv-bl-${id}" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="${hud ? 1.3 : 0.9}" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <g class="jv-boot">
    <circle r="${hud ? 48 : 30}" fill="url(#jv-amb-${id})"/>
    ${hud ? `
    <g class="jv-ticks">
      <circle r="45" class="jv-hair"/>
      <circle r="41.5" class="jv-minor"/>
      <circle r="41.5" class="jv-major"/>
    </g>
    <g class="jv-arcs">
      <circle r="36" class="jv-data"/>
      <circle r="36" class="jv-hair"/>
    </g>
    <g class="jv-sweep">
      <path d="M${pt(26, -46)}A26 26 0 0 1 ${pt(26, 0)}L${pt(40.5, 0)}A40.5 40.5 0 0 0 ${pt(40.5, -46)}Z" fill="url(#jv-sw-${id})"/>
      <path d="M${pt(26, 0)}L${pt(40.5, 0)}" class="jv-beam" filter="url(#jv-bl-${id})"/>
    </g>
    <g class="jv-orbit"><circle cx="0" cy="-45" r="1.3" class="jv-spark" filter="url(#jv-bl-${id})"/></g>
    <circle r="29.5" class="jv-hair"/>` : ""}
    <circle r="24.9" class="jv-rim"/>
    <circle r="23" class="jv-metal" stroke="url(#jv-me-${id})"/>
    ${bolts}
    <circle r="21.1" class="jv-inner"/>
    <g class="jv-coils" filter="url(#jv-bl-${id})">${coils}</g>
    <circle r="11" class="jv-gap"/>
    <circle r="${hud ? 16 : 14}" class="jv-halo" fill="url(#jv-ha-${id})"/>
    <circle r="${hud ? 17 : 15}" class="jv-halo jv-warn" fill="url(#jv-hw-${id})"/>
    <circle r="8.6" class="jv-core" fill="url(#jv-co-${id})" filter="url(#jv-bl-${id})"/>
    <circle r="5" class="jv-ring"/>
    <ellipse cx="-2.7" cy="-3.1" rx="1.9" ry="1.3" class="jv-shine" transform="rotate(-35 -2.7 -3.1)"/>
  </g>`;
}

/** A new emblem; `hud` adds the rings around the reactor (home screen). Sizes come from the CSS. */
export function logo(cls = "", { hud = false } = {}) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", hud ? "-50 -50 100 100" : "-26 -26 52 52");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", `jv-logo${hud ? " jv-hud" : ""} ${cls}`.trim());
  svg.innerHTML = markup(++uid, hud);
  // the charge's stagger: set through the CSSOM, the CSP blocks style="" attributes in markup
  svg.querySelectorAll(".jv-coil").forEach((p, i) => p.style.setProperty("--i", i));
  const parts = {};
  for (const k of Object.keys(SPEED)) parts[k] = svg.querySelector(`.jv-${k}`);
  const entry = { svg, parts, angle: { coils: 0, ticks: 0, arcs: 0, orbit: 0, sweep: 0 }, speed: Object.fromEntries(Object.entries(SPEED).map(([k, v]) => [k, v[0]])) };
  logos.add(entry);
  paint(entry);
  start();
  return svg;
}

/** Swap a placeholder for the live emblem, keeping its classes (data-logo="hud" for the full one). */
export function mountLogo(el) {
  const svg = logo(el.getAttribute("class") || "", { hud: el.dataset.logo === "hud" });
  el.replaceWith(svg);
  return svg;
}

/** running / awaiting task counts, from the console state. */
export function setLogoActivity(running, awaiting) {
  activity = { run: running || 0, await: awaiting || 0 };
  for (const e of logos) paint(e);
}

function paint(e) {
  e.svg.classList.toggle("is-running", activity.run > 0);
  e.svg.classList.toggle("is-awaiting", activity.await > 0);
}

const still = matchMedia("(prefers-reduced-motion: reduce)");
let raf = 0, last = 0;
let frames = window;   // whose animation frames turn the emblems

/** The desktop app ("Intégré au bureau"): the bar's window turns them, the page's window being hidden. */
export function animateWith(win) {
  if (!win || win === frames) return;
  if (raf) frames.cancelAnimationFrame(raf);
  raf = 0;
  last = 0;
  frames = win;
  start();
}

function start() {
  if (!raf && !still.matches) raf = frames.requestAnimationFrame(tick);
}

function tick(now) {
  const dt = Math.min(0.1, last ? (now - last) / 1000 : 0);
  last = now;
  const busy = activity.run > 0 ? 1 : 0;
  for (const e of logos) {
    if (!e.svg.isConnected) { logos.delete(e); continue; }
    if (!e.svg.getClientRects().length) continue;  // hidden (home screen under the windows)
    for (const [k, [idle, run]] of Object.entries(SPEED)) {
      if (!e.parts[k]) continue;
      const target = idle + (run - idle) * busy;
      e.speed[k] += (target - e.speed[k]) * Math.min(1, dt * 1.6);  // turbine: spins up and down smoothly
      e.angle[k] = (e.angle[k] + e.speed[k] * dt) % 360;
      e.parts[k].setAttribute("transform", `rotate(${e.angle[k].toFixed(2)})`);
    }
  }
  raf = logos.size ? frames.requestAnimationFrame(tick) : 0;
  if (!raf) last = 0;
}

still.addEventListener?.("change", () => { if (still.matches) { frames.cancelAnimationFrame(raf); raf = 0; last = 0; } else start(); });
