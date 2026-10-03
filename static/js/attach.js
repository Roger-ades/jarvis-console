// Attachments of a message being written: file picker, drag and drop, paste. Each file is
// uploaded at once (progress on its chip); the message then carries the upload ids.
import { api, uploadFile } from "./api.js";
import { h, toast } from "./util.js";

const MAX = 20;
const IMG = /^image\//;

export function fmtSize(n) {
  if (n < 1024) return `${n} o`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} Ko`;
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} Mo`;
}

/** Screenshots arrive as "image.png": give them a name worth keeping. */
function fileName(file) {
  if (file.name && !/^image\.(png|jpe?g|gif|webp)$/i.test(file.name)) return file.name;
  const d = new Date(), p = (x) => String(x).padStart(2, "0");
  const ext = ((file.type || "").split("/")[1] || "png").replace("jpeg", "jpg");
  return `capture-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`;
}

export const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

export class Attacher {
  /** list: element receiving the chips; onChange(): after any change (layout). */
  constructor(list, onChange = () => {}) {
    this.list = list;
    this.onChange = onChange;
    this.items = [];
    this.picker = h("input", { type: "file", multiple: true, hidden: true,
      on: { change: () => { this.add([...this.picker.files]); this.picker.value = ""; } } });
    document.body.append(this.picker);
    this.render();
  }

  button(cls = "icon-btn") {
    return h("button", { type: "button", class: `${cls} att-btn`, title: "Joindre des fichiers (ou glisse-les, ou colle une capture)",
      "aria-label": "Joindre des fichiers", svg: "clip", on: { click: () => this.pick() } });
  }

  /** The file picker, opened from the document of this list (a native window's, in the desktop app). */
  pick() {
    const doc = this.list.ownerDocument;
    if (this.picker.ownerDocument !== doc) doc.body.append(this.picker);
    // the floating bar of the desktop app does not put itself away while its file picker is open
    if (doc.documentElement.classList.contains("bar-doc")) {
      window.jarvis?.win("bar-hold", null, true);
      const release = () => window.jarvis?.win("bar-hold", null, false);
      this.picker.addEventListener("change", release, { once: true });
      this.picker.addEventListener("cancel", release, { once: true });
    }
    this.picker.click();
  }

  add(files) {
    for (const file of files) {
      if (this.items.length >= MAX) { toast(`${MAX} pièces jointes au plus par message.`, "err"); break; }
      const name = fileName(file);
      const item = { name, size: file.size, id: null, state: "up", preview: IMG.test(file.type) ? URL.createObjectURL(file) : "" };
      const bar = h("span", { class: "att-bar" });
      const size = h("span", { class: "att-size" }, "0 %");
      item.el = h("div", { class: "att up", title: name },
        item.preview ? h("img", { class: "att-thumb", src: item.preview, alt: "" }) : h("span", { class: "att-ic", svg: "file" }),
        h("span", { class: "att-name" }, name), size,
        h("button", { type: "button", class: "att-x", title: "Retirer", "aria-label": `Retirer ${name}`, svg: "x",
          on: { click: () => this.remove(item) } }),
        bar);
      item.upload = uploadFile(file, name, (p) => { bar.style.width = `${Math.round(p * 100)}%`; size.textContent = `${Math.round(p * 100)} %`; });
      item.upload.promise.then((r) => {
        item.id = r.id;
        item.state = "ok";
        item.el.classList.replace("up", "ok");
        size.textContent = fmtSize(r.size);
      }, (e) => {
        if (!this.items.includes(item)) return; // removed while uploading
        // 404/405: a console started before this feature (the page is newer than the server)
        const why = e.status === 404 || e.status === 405
          ? "la console doit être redémarrée pour recevoir des fichiers (bandeau en haut)" : e.message;
        item.state = "err";
        item.el.classList.replace("up", "err");
        size.textContent = "échec";
        item.el.title = why;
        toast(`${name} : ${why}`, "err");
        if (e.status === 404 || e.status === 405) window.dispatchEvent(new Event("jarvis-check-version"));
      });
      this.items.push(item);
      this.list.append(item.el);
    }
    this.render();
  }

  remove(item) {
    this.items = this.items.filter((i) => i !== item);
    if (item.state === "up") item.upload.abort();
    if (item.id) api(`/api/uploads/${item.id}`, { method: "DELETE" }).catch(() => {});
    this.drop(item);
    this.render();
  }

  drop(item) {
    item.el.remove();
    if (item.preview) URL.revokeObjectURL(item.preview);
  }

  /** The files just sent: they now belong to the task. */
  sent(ids) {
    const gone = this.items.filter((i) => ids.includes(i.id));
    this.items = this.items.filter((i) => !ids.includes(i.id));
    gone.forEach((i) => this.drop(i));
    this.render();
  }

  busy() { return this.items.some((i) => i.state === "up"); }
  ids() { return this.items.filter((i) => i.id).map((i) => i.id); }

  /** Ready to send? Explains why not. */
  ready() {
    if (this.busy()) { toast("Envoi des pièces jointes en cours, un instant…"); return false; }
    if (this.items.some((i) => i.state === "err")) { toast("Une pièce jointe n'a pas pu être envoyée : retire-la ou joins-la à nouveau.", "err"); return false; }
    return true;
  }

  render() {
    this.list.hidden = !this.items.length;
    this.onChange();
  }

  /** Drag and drop onto zone (the drop is kept from reaching the page underneath). */
  bindDrop(zone) {
    let depth = 0;
    zone.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; zone.classList.add("drop-on"); });
    zone.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
    zone.addEventListener("dragleave", (e) => { if (hasFiles(e) && --depth <= 0) { depth = 0; zone.classList.remove("drop-on"); } });
    zone.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth = 0;
      zone.classList.remove("drop-on");
      this.add([...e.dataTransfer.files]);
    });
  }

  /** Paste a screenshot or files; a text paste stays a text paste. */
  bindPaste(input) {
    input.addEventListener("paste", (e) => {
      const files = [...(e.clipboardData?.files || [])];
      if (!files.length || e.clipboardData.getData("text/plain")) return;
      e.preventDefault();
      this.add(files);
    });
  }
}
