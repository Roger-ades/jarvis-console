// Desktop app, "Intégré au bureau" (docs/electron.md): the command bar (with the task pills) leaves the
// JARVIS window for a floating window of its own. Ctrl+Alt+J or the notification area bring it where
// the mouse is; Échap, a request sent or a click elsewhere put it away, unless it is pinned. The window
// grows upward when a list (skills, profiles), the settings or a message open above the bar.
import { iconBtn, toast } from "./util.js";
import * as wm from "./wm.js";

/** dock: the element moved into the bar; popups: what opens above it. Returns the bar's document, or null
 * (browser, or one window). */
export function setupBar({ dock, popups, callButton }) {
  if (!wm.isNative() || !window.jarvis) return null;
  const doc = wm.detachBar(dock);
  if (!doc) return null;
  const toasts = doc.getElementById("toasts");
  let frame = 0;
  const fit = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const above = Math.max(0, ...popups.filter((p) => !p.hidden).map((p) => p.offsetHeight + 12));
      window.jarvis.win("bar-fit", null, { height: Math.ceil(dock.offsetHeight + toasts.offsetHeight + above) });
    });
  };
  const sizes = new ResizeObserver(fit);
  sizes.observe(dock);
  sizes.observe(toasts);
  for (const p of popups) new MutationObserver(fit).observe(p, { attributes: true, attributeFilter: ["hidden"], childList: true, subtree: true });
  // Échap with nothing open above the bar: put away
  doc.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !e.defaultPrevented && popups.every((p) => p.hidden)) window.jarvis.win("bar-hide");
  });
  // pinned: stays when the user clicks elsewhere
  let pinned = !!wm.prefs().barPinned;
  const pin = iconBtn("pin", "", () => {
    pinned = !pinned;
    wm.savePrefs({ barPinned: pinned });
    sync();
    toast(pinned ? "La barre reste affichée." : "La barre se range quand tu cliques ailleurs.");
  }, "bar-tool bar-pin");
  const sync = () => {
    pin.classList.toggle("on", pinned);
    const label = pinned ? "Barre épinglée : cliquer pour qu'elle se range quand tu cliques ailleurs"
      : "Garder la barre affichée (sinon elle se range quand tu cliques ailleurs ; Ctrl+Alt+J la rappelle)";
    pin.title = label;
    pin.setAttribute("aria-label", label);
    window.jarvis.win("bar-pin", null, pinned);
  };
  // the JARVIS window (history, notes, projects, configuration): hidden until asked for
  const open = iconBtn("panel", "Ouvrir la fenêtre JARVIS (historique, projets, notes, configuration)",
    () => window.jarvis.win("hub"), "bar-tool bar-open");
  dock.querySelector(".composer-tools")?.prepend(open, pin);
  sync();
  // the JARVIS window keeps a way to call it
  if (callButton) {
    callButton.hidden = false;
    callButton.addEventListener("click", () => window.jarvis.win("bar-show"));
  }
  fit();
  window.jarvis.win("bar-ready");
  return doc;
}

/** After a request is sent from the bar: it goes away (unless pinned). */
export function barSent() {
  if (wm.isNative() && window.jarvis) window.jarvis.win("bar-sent");
}

