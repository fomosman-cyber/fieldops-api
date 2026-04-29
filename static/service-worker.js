/**
 * FieldOps PWA Service Worker
 * Strategie:
 *  - App shell (portaal HTML, icons, manifest): cache-first
 *  - API calls (/api/*): network-first met fallback naar cache
 *  - Externe libs (fonts, leaflet, chart.js): stale-while-revalidate
 */
const VERSION = 'v1.0.1';
const SHELL_CACHE = `fieldops-shell-${VERSION}`;
const API_CACHE = `fieldops-api-${VERSION}`;
const RUNTIME_CACHE = `fieldops-runtime-${VERSION}`;

const SHELL_URLS = [
  '/portaal',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
];

// ───── Install: pre-cache app shell ─────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      cache.addAll(SHELL_URLS).catch((err) => {
        console.warn('[SW] Pre-cache deels gefaald:', err);
      })
    )
  );
  self.skipWaiting();
});

// ───── Activate: oude caches opruimen ─────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => ![SHELL_CACHE, API_CACHE, RUNTIME_CACHE].includes(k))
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ───── Fetch handler ─────
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // 1. Auth/login endpoints: altijd network (nooit cachen — security)
  if (url.pathname.startsWith('/auth/') || url.pathname.includes('/login')) {
    return; // browser default
  }

  // 2. API calls: network-first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(req, API_CACHE));
    return;
  }

  // 3. App shell HTML: network-first met offline fallback
  if (req.mode === 'navigate' || url.pathname === '/portaal') {
    event.respondWith(
      networkFirst(req, SHELL_CACHE).catch(() => caches.match('/portaal'))
    );
    return;
  }

  // 4. Static assets (icons, manifest): cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }

  // 5. Externe libs (fonts, leaflet, chart.js): stale-while-revalidate
  if (url.origin !== location.origin) {
    event.respondWith(staleWhileRevalidate(req, RUNTIME_CACHE));
    return;
  }
});

// ───── Strategieën ─────
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const fresh = await fetch(req);
    if (fresh.ok) cache.put(req, fresh.clone());
    return fresh;
  } catch (err) {
    return cached || new Response('Offline', { status: 503 });
  }
}

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(req);
    if (fresh.ok) cache.put(req, fresh.clone());
    return fresh;
  } catch (err) {
    const cached = await cache.match(req);
    if (cached) return cached;
    throw err;
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => cached);
  return cached || fetchPromise;
}

// ───── Push notifications hook (later uitbreiden) ─────
self.addEventListener('push', (event) => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'FieldOps', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-96.png',
      data: data.url || '/portaal',
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data || '/portaal'));
});
