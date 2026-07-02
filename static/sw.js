const CACHE = 'garmin-v3';
const STATIC = ['/garmin-dashboard/static/chart.min.js', '/garmin-dashboard/static/icon-180.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // HTML: sempre busca na rede primeiro; usa cache só se offline
  if (e.request.destination === 'document' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' })
        .then(r => { const c = r.clone(); caches.open(CACHE).then(cache => cache.put(e.request, c)); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  // Outros assets: cache primeiro
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
