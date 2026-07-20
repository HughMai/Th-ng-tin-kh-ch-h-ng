/* HTP CRM service worker.
 *
 * Deliberately conservative for a LIVE, single-source-of-truth CRM: it caches
 * only immutable-ish shell assets (CSS/JS/fonts/icons). It NEVER caches CRM
 * pages or API responses, so the family always sees fresh data — the only
 * offline behaviour is a friendly "mất mạng" fallback on navigations.
 *
 * Bump CACHE when any precached asset changes so old copies get evicted.
 */
const CACHE = 'htp-crm-v1';
const SHELL = [
  '/static/app.css',
  '/static/app.js',
  '/static/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/fonts/be-vietnam-pro-v12-latin_vietnamese-regular.woff2',
  '/static/fonts/be-vietnam-pro-v12-latin_vietnamese-600.woff2',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;                 // never touch POST/logins/writes
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // let cross-origin pass through

  // Shell assets: cache-first, fill cache on first miss.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      }))
    );
    return;
  }

  // Page navigations: always go to network; only fall back to the offline card
  // if the network is truly unreachable. CRM data is never served from cache.
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
  }
});
