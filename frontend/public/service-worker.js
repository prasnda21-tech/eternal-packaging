/* Eternal Packaging PWA — static app-shell only.
   Network-first for navigations. Never cache /api or authenticated business data.
   Hashed /static/* assets are cache-first (content-hashed filenames).
   No offline mutation queue. Installability only — not offline ERP. */

const SHELL_CACHE = "eternal-pwa-shell-v2";
const SHELL_ASSETS = ["/manifest.json", "/sales-manifest.json", "/eternal-favicon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key)),
    )).then(() => self.clients.claim()),
  );
});

function isApiRequest(url) {
  return url.pathname.startsWith("/api/") || url.pathname.includes("/auth/");
}

function isStaticAsset(url) {
  return /\.(?:js|css|png|jpg|jpeg|gif|webp|svg|woff2?|ico)$/i.test(url.pathname);
}

function isHashedStaticAsset(url) {
  return url.pathname.startsWith("/static/") && isStaticAsset(url);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (isApiRequest(url)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/")),
    );
    return;
  }

  if (!isStaticAsset(url)) return;

  if (isHashedStaticAsset(url)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        });
      }),
    );
    return;
  }

  event.respondWith(
    fetch(request).then((response) => {
      if (response && response.ok) {
        const copy = response.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }).catch(() => caches.match(request)),
  );
});
