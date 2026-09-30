// Context from other discussions: sessions of the same account, picked in the request bar.
// Each becomes a chip; at send time the server turns it into a Markdown transcript that the
// new discussion reads when useful (much cheaper than reloading a whole conversation).
import { api } from "./api.js";
import { dialog, fmtDate, h, toast } from "./util.js";

const MAX = 5;
const ORIGIN = { desktop: "Claude Desktop", cli: "CLI", console: "Console" };

export class ContextPicker {
  /** list: element for the chips; profile(): the account of the next request. */
  constructor(list, profile) {
    this.list = list;
    this.profile = profile;
    this.items = [];
    this.render();
  }

  button(cls = "chip-toggle") {
    this.count = h("span", { class: "cnt", hidden: true });
    this.btn = h("button", { type: "button", class: `${cls} ctx-btn`, title: "Contexte : reprendre d'autres discussions du même compte",
      "aria-label": "Contexte d'autres discussions", on: { click: () => this.pick() } }, h("span", { class: "i", svg: "link" }), this.count);
    return this.btn;
  }

  add({ profile, session, title }) {
    if (!session) return;
    if (profile && profile !== this.profile()) { toast("Contexte limité aux discussions du compte choisi.", "err"); return; }
    if (this.items.some((i) => i.session === session)) return;
    if (this.items.length >= MAX) { toast(`${MAX} discussions au plus comme contexte.`, "err"); return; }
    const item = { profile: this.profile(), session, title: title || "Discussion" };
    item.el = h("div", { class: "att ok ctx", title: `Contexte : ${item.title}` },
      h("span", { class: "att-ic", svg: "link" }), h("span", { class: "att-name" }, item.title),
      h("button", { type: "button", class: "att-x", title: "Retirer", "aria-label": `Retirer ${item.title}`, svg: "x",
        on: { click: () => this.remove(item) } }));
    this.items.push(item);
    this.list.append(item.el);
    this.render();
  }

  remove(item) {
    this.items = this.items.filter((i) => i !== item);
    item.el.remove();
    this.render();
  }

  /** The account changed: contexts of the other one cannot follow. */
  profileChanged() {
    const pid = this.profile();
    const gone = this.items.filter((i) => i.profile !== pid);
    gone.forEach((i) => this.remove(i));
    if (gone.length) toast("Contexte retiré : il appartenait à l'autre compte.");
  }

  sources() { return this.items.map(({ profile, session, title }) => ({ profile, session, title })); }
  clear() { [...this.items].forEach((i) => this.remove(i)); }
  render() {
    this.list.hidden = !this.items.length;
    if (this.count) { this.count.hidden = !this.items.length; this.count.textContent = String(this.items.length); }
    this.btn?.setAttribute("aria-pressed", String(!!this.items.length));
  }

  async pick() {
    const pid = this.profile();
    const chosen = new Map(this.items.map((i) => [i.session, i]));
    const search = h("input", { type: "text", placeholder: "Rechercher une discussion…" });
    const list = h("div", { class: "ctx-list" }, h("div", { class: "muted" }, "Lecture des discussions…"));
    const note = h("p", { class: "muted ctx-note" },
      "Discussions de ce compte (console, Claude Desktop, CLI). Leur transcription est jointe à ta demande ; Claude la lit si elle l'aide.");
    let rows = [];
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      const shown = rows.filter((r) => !q || `${r.title} ${r.first_prompt} ${r.cwd}`.toLowerCase().includes(q)).slice(0, 200);
      list.replaceChildren(...(shown.length ? shown.map((r) => {
        const box = h("input", { type: "checkbox" });
        box.checked = chosen.has(r.id);
        box.addEventListener("change", () => {
          if (box.checked && chosen.size >= MAX) { box.checked = false; toast(`${MAX} discussions au plus.`, "err"); return; }
          if (box.checked) chosen.set(r.id, { profile: pid, session: r.id, title: r.title });
          else chosen.delete(r.id);
        });
        return h("label", { class: "ctx-row" }, box,
          h("span", { class: "ctx-t" }, h("b", {}, r.title), h("small", {},
            [ORIGIN[r.origin] || r.origin, fmtDate(r.updated), `${r.prompts} message${r.prompts > 1 ? "s" : ""}`].filter(Boolean).join(" · "))));
      }) : [h("div", { class: "muted" }, rows.length ? "Aucune discussion ne correspond." : "Aucune discussion pour ce compte.")]));
    };
    search.addEventListener("input", draw);
    api(`/api/sessions?profile=${encodeURIComponent(pid)}`).then((r) => { rows = r.sessions || []; draw(); })
      .catch((e) => list.replaceChildren(h("div", { class: "line err" }, e.message)));
    const ok = await dialog({
      title: "Reprendre le contexte d'autres discussions", body: h("div", { class: "ctx-pick" }, note, search, list),
      buttons: [{ label: "Annuler", value: false }, { label: "Joindre", value: true, cls: "primary" }],
      onOpen: (box) => { box.classList.add("ctx-dialog"); search.focus(); },
    });
    if (!ok) return;
    this.items.filter((i) => !chosen.has(i.session)).forEach((i) => this.remove(i));
    for (const c of chosen.values()) this.add(c);
  }
}
