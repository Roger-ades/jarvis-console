// Desktop app, "Intégré au bureau" (docs/electron.md): no JARVIS window. The command bar (with the task
// pills) floats in a window of its own, and the emblem at its left opens the JARVIS menu: the counters
// and the accounts' limits, the actions of the top bar (search, project, notes, history, routines,
// sessions, claude.ai, arrange, configuration, emergency stop), the pinned projects and the discussions
// to pick up again. Above the bar: what the console has to say (connection lost, emergency stop, update)
// and the reminders of the notes. Ctrl+Alt+J or the notification area bring it where the mouse is;
// Échap, a request sent or a click elsewhere put it away, unless it is pinned. The window grows upward
// with what opens above the bar.
import { animateWith, logo } from "./logo.js";
import { h, iconBtn, setNoticeHost, toast } from "./util.js";
import * as wm from "./wm.js";

let menuEl = null, menuBtn = null;

/** dock: the element moved into the bar; popups: what opens above it; menu: {status, actions, home},
 * the elements of the top bar and of the home screen for the menu; notices: elements shown above the
 * bar. Returns the bar's document, or null (browser, or one window). */
export function setupBar({ dock, popups, menu, notices }) {
  if (!wm.isNative() || !window.jarvis) return null;
  const doc = wm.detachBar(dock);
  if (!doc) return null;
  const toasts = doc.getElementById("toasts");
  const notes = h("div", { class: "bar-notices" }, ...notices);
  doc.body.insertBefore(notes, dock);
  setNoticeHost(() => notes);
  // the JARVIS menu
  menuEl = h("div", { class: "bar-menu", hidden: true, role: "dialog", "aria-label": "Menu JARVIS" },
    h("div", { class: "bm-status" }, ...menu.status),
    h("div", { class: "bm-actions" }, menu.actions),
    h("div", { class: "bm-home" }, menu.home));
  dock.prepend(menuEl);
  menuBtn = h("button", { type: "button", class: "bar-jarvis", "aria-expanded": "false", "aria-label": "Menu JARVIS",
    title: "Menu JARVIS : projets, reprendre, historique, notes, routines, configuration…", on: { click: () => toggleMenu() } },
  logo("bar-logo"));
  dock.querySelector(".composer-bar")?.prepend(menuBtn);
  animateWith(doc.defaultView);   // (the page's window is hidden)
  // a choice in the menu closes it (not the account pills, which show their limits, nor the sub-menus)
  menuEl.addEventListener("click", (e) => {
    const b = e.target.closest("button, a");
    if (b && !b.closest(".bm-status") && !b.matches("#btn-claudeai, #btn-arrange")) toggleMenu(false);
  });
  let timer = 0;
  const fit = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const above = Math.max(0, ...popups.filter((p) => !p.hidden).map((p) => p.offsetHeight + 12));
      window.jarvis.win("bar-fit", null, { height: Math.ceil(notes.offsetHeight + toasts.offsetHeight + dock.offsetHeight + above) });
    }, 16);
  };
  const sizes = new ResizeObserver(fit);
  for (const x of [dock, toasts, notes]) sizes.observe(x);
  for (const p of popups) new MutationObserver(fit).observe(p, { attributes: true, attributeFilter: ["hidden"], childList: true, subtree: true });
  // Échap: what is open above the bar first (its own handlers), then the menu, then the bar goes away
  doc.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented || !popups.every((p) => p.hidden) || doc.querySelector(".limits-pop")) return;
    if (!menuEl.hidden) toggleMenu(false);
    else window.jarvis.win("bar-hide");
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
  dock.querySelector(".composer-tools")?.prepend(pin);
  sync();
  fit();
  window.jarvis.win("bar-ready");
  return doc;
}

/** Opens (true), closes (false) or toggles the JARVIS menu of the bar. */
export function toggleMenu(open = menuEl?.hidden) {
  if (!menuEl) return;
  menuEl.hidden = !open;
  menuBtn.classList.toggle("on", !!open);
  menuBtn.setAttribute("aria-expanded", String(!!open));
}

/** After a request is sent from the bar: it goes away (unless pinned). */
export function barSent() {
  if (wm.isNative() && window.jarvis) window.jarvis.win("bar-sent");
}
