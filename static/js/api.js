// Access token, REST calls and the server-sent event stream (read through fetch
// so the token travels in a header, never in a URL).
import { dialog, h, store } from "./util.js";

const TOKEN_KEY = "jarvis.token";
let token = store.get(TOKEN_KEY, "");
let authPrompt = null;

export class ApiError extends Error {
  constructor(status, data) {
    super((data && data.detail) || `Erreur ${status}`);
    this.status = status;
    this.data = data || {};
  }
}

/** Trade the one-time #code from the launcher for the access token. */
export async function bootstrapAuth() {
  const m = location.hash.match(/code=([\w-]+)/);
  if (m) {
    history.replaceState(null, "", location.pathname);
    try {
      const r = await fetch("/api/auth/exchange", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: m[1] }),
      });
      if (r.ok) { setToken((await r.json()).token); return true; }
    } catch { /* fall through to the stored token */ }
  }
  return !!token;
}

export function setToken(t) { token = t; store.set(TOKEN_KEY, t); }

// A fresh access link opened on an already loaded page only changes the #fragment.
window.addEventListener("hashchange", async () => {
  if (/code=[\w-]+/.test(location.hash) && (await bootstrapAuth())) location.reload();
});

async function askToken() {
  if (!authPrompt) {
    authPrompt = dialog({
      title: "Accès à la console",
      body: h("div", {},
        h("p", {}, "Le lien d'accès a expiré ou ce navigateur ne connaît pas encore la console."),
        h("p", {}, "Relance start.bat (un nouvel onglet s'ouvre), ou colle ici le jeton du fichier data\\token.")),
      input: { type: "password", placeholder: "Jeton d'accès" },
      buttons: [{ label: "Valider", value: true, cls: "primary" }],
    }).then((v) => { authPrompt = null; if (v) setToken(v.trim()); return !!v; });
  }
  return authPrompt;
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch(path, {
      method,
      headers: { "X-Console-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401 && attempt === 0) { await askToken(); continue; }
    if (raw) { if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null)); return r; }
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(r.status, data);
    return data;
  }
  throw new ApiError(401, { detail: "Accès refusé." });
}

/** Resilient SSE reader. handlers: {event: fn(data)}, onOpen(), onDown() */
export function openStream(handlers, onOpen, onDown) {
  let stopped = false, delay = 1000;
  async function loop() {
    while (!stopped) {
      try {
        const r = await fetch("/api/stream", { headers: { "X-Console-Token": token }, cache: "no-store" });
        if (r.status === 401) { await askToken(); continue; }
        if (!r.ok || !r.body) throw new Error(String(r.status));
        delay = 1000;
        onOpen?.();
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            let ev = "message", data = "";
            for (const line of chunk.split("\n")) {
              if (line.startsWith("event:")) ev = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim();
            }
            if (!data) continue;
            try { handlers[ev]?.(JSON.parse(data)); } catch (e) { console.error("événement", ev, e); }
          }
        }
      } catch { /* network down: retry below */ }
      if (stopped) break;
      onDown?.();
      await new Promise((res) => setTimeout(res, delay));
      delay = Math.min(delay * 2, 10000);
    }
  }
  loop();
  return () => { stopped = true; };
}

export async function download(path, fallbackName) {
  const r = await api(path, { raw: true });
  const cd = r.headers.get("Content-Disposition") || "";
  const name = cd.match(/filename="([^"]+)"/)?.[1] || fallbackName;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
