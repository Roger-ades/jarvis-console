// Service worker: makes the console installable and shows a clear page when the
// local server is not running. It never caches or touches API calls.
const CACHE = "jarvis-shell-v4";
const OFFLINE = "/static/offline.html";
const SHELL = [OFFLINE, "/static/css/app.css", "/static/img/favicon.svg", "/static/img/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }
  const url = new URL(req.url);
  if (url.origin === location.origin && SHELL.includes(url.pathname)) {
    event.respondWith(fetch(req).catch(() => caches.match(url.pathname)));
  }
  // everything else (API, stream, scripts) goes straight to the network
});
