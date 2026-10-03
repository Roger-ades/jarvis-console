// Folder picker: browse the disk from the console (a web page cannot open the system's own
// dialog and read the real path). Protected folders never appear.
import { ApiError, api } from "./api.js";
import { h, modalHost, toast } from "./util.js";

const ICON = { account: "user", folder: "folder", cloud: "globe", home: "user", drive: "file", network: "globe", recent: "book" };
const baseName = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

/** Resolves with the chosen folder's full path, or null. recent: project folders shown first. */
export function pickFolder({ title = "Choisir un dossier", start = "", recent = [] } = {}) {
  return new Promise((resolve) => {
    let current = "", parent = "";
    const pathIn = h("input", { type: "text", class: "fp-path", spellcheck: "false", placeholder: "Colle ou tape un chemin, puis Entrée" });
    const up = h("button", { type: "button", class: "icon-btn", title: "Dossier parent", svg: "send", on: { click: () => parent && go(parent) } });
    const places = h("nav", { class: "fp-places", "aria-label": "Emplacements" }, h("div", { class: "muted" }, "…"));
    const list = h("div", { class: "fp-list", role: "listbox", "aria-label": "Sous-dossiers" });
    const choose = h("button", { type: "button", class: "btn primary", disabled: true, on: { click: () => done(current) } }, "Choisir ce dossier");
    const newName = h("input", { type: "text", placeholder: "Nom du nouveau dossier", hidden: true });
    const newBtn = h("button", { type: "button", class: "btn small ghost", on: { click: () => mkdir() } }, "+ Nouveau dossier");
    const box = h("div", { class: "dialog fp", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "fp-head" }, h("h3", {}, title),
        h("button", { type: "button", class: "icon-btn", title: "Fermer (Échap)", svg: "close", on: { click: () => done(null) } })),
      h("div", { class: "fp-bar" }, up, pathIn),
      h("div", { class: "fp-main" }, places, list),
      h("div", { class: "fp-foot" }, newBtn, newName, h("span", { class: "grow" }),
        h("button", { type: "button", class: "btn", on: { click: () => done(null) } }, "Annuler"), choose));
    const overlay = h("div", { class: "overlay", on: { mousedown: (e) => { if (e.target === overlay) done(null); } } }, box);
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
    const { root, doc } = modalHost("dialog");
    doc.addEventListener("keydown", onKey, true);
    root.append(overlay);

    function done(v) {
      overlay.remove();
      doc.removeEventListener("keydown", onKey, true);
      resolve(v);
    }

    async function go(path) {
      list.replaceChildren(h("div", { class: "muted fp-empty" }, "Lecture…"));
      try {
        const r = await api(`/api/fs/dirs?path=${encodeURIComponent(path)}`);
        current = r.path;
        parent = r.parent;
        pathIn.value = r.path;
        up.disabled = !r.parent;
        choose.disabled = false;
        choose.textContent = `Choisir « ${baseName(r.path)} »`;
        places.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.path === r.path));
        list.replaceChildren(...(r.dirs.length ? r.dirs.map((name) => h("button", {
          type: "button", class: "fp-dir", role: "option", title: `Ouvrir ${name}`,
          on: { click: () => go(`${r.path.replace(/[\\/]+$/, "")}${r.path.includes("\\") ? "\\" : "/"}${name}`) },
        }, h("span", { class: "i", svg: "folder" }), h("span", {}, name)))
          : [h("div", { class: "muted fp-empty" }, "Aucun sous-dossier. Tu peux choisir celui-ci.")]),
        ...(r.truncated ? [h("div", { class: "muted fp-empty" }, "2 000 premiers dossiers affichés : tape la suite du chemin en haut.")] : []));
      } catch (e) {
        list.replaceChildren(h("div", { class: "line err fp-empty" }, e instanceof ApiError ? e.message : String(e)));
      }
    }

    async function mkdir() {
      if (newName.hidden) { newName.hidden = false; newName.focus(); newBtn.textContent = "Créer"; return; }
      const name = newName.value.trim();
      if (!name) { newName.focus(); return; }
      try {
        const r = await api("/api/fs/mkdir", { method: "POST", body: { path: current, name } });
        newName.value = "";
        newName.hidden = true;
        newBtn.textContent = "+ Nouveau dossier";
        go(r.path);
      } catch (e) { toast(e.message, "err"); }
    }

    newName.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); mkdir(); } });
    pathIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(pathIn.value.trim()); } });
    api("/api/fs/places").then(({ places: ps }) => {
      const place = (p) => h("button", { type: "button", "data-path": p.path, title: p.path,
        class: p.kind === "account" ? "acc" : "", on: { click: () => go(p.path) } },
        h("span", { class: "i", svg: ICON[p.kind] || "folder" }), h("span", {}, p.label));
      const recents = [...new Set(recent.filter(Boolean))].slice(0, 8);
      places.replaceChildren(
        ...(recents.length ? [h("div", { class: "fp-group" }, "Projets récents"),
          ...recents.map((p) => place({ path: p, label: baseName(p), kind: "recent" })), h("div", { class: "fp-group" }, "Emplacements")] : []),
        ...ps.map(place));
      go(start || ps[0]?.path || "");
    }).catch((e) => { places.replaceChildren(); toast(e.message, "err"); go(start); });
    pathIn.focus();
  });
}
