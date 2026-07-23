/* Sổ Thu Chi service worker — same conservative policy as the CRM's.
 *
 * Caches only shell assets (CSS/JS/fonts/icons). It NEVER caches pages, so the
 * sổ always shows fresh numbers — money read from a stale cache would be worse
 * than no app at all. The only offline behaviour is a "mất mạng" fallback card.
 *
 * The /sw.js route substitutes the placeholder below with a hash of app.css and
 * app.js, so the cache name AND the precached URLs both change whenever those
 * files change. No manual version bumping, no stale asset surviving a deploy.
 */
const ASSET_V = '__ASSET_V__';
const CACHE = 'htp-thuchi-' + ASSET_V;
const SHELL = [
  '/static/app.css?v=' + ASSET_V,
  '/static/app.js?v=' + ASSET_V,
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

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
  }
});
