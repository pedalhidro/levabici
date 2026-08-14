// levabici — service worker
//
// Mesmo desenho do ecossistema Pedal (ver amora/web/sw.js):
//   STATIC_CACHE  — casca do app (HTML/CSS/JS/ícones/libs): stale-while-
//                   revalidate — serve do cache na hora e atualiza por
//                   trás, então um deploy chega na visita seguinte.
//                   Exceção: data/*.ttl é network-first, porque ali
//                   "velho" significa dados velhos, não só casca velha.
//   RUNTIME_CACHE — tiles do OSM: stale-while-revalidate.
// Nominatim (geocodificação) nunca é cacheado.
//
// DISCIPLINA: qualquer mudança em arquivo servido exige subir a VERSION.
const VERSION = 'levabici-v6';
const STATIC_CACHE = `${VERSION}-static`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

const STATIC_ASSETS = [
  './',
  'index.html',
  'style.css',
  'app.js',
  'manifest.json',
  'favicon.ico',
  'icon.svg',
  'logo.svg',
  'icon-192.png',
  'icon-512.png',
  'icon-512-maskable.png',
  'apple-touch-icon.png',
  'lib/leaflet/leaflet.css',
  'lib/leaflet/leaflet.js',
  'lib/leaflet/images/layers.png',
  'lib/leaflet/images/layers-2x.png',
  'lib/leaflet/images/marker-icon.png',
  'lib/leaflet/images/marker-icon-2x.png',
  'lib/leaflet/images/marker-shadow.png',
  'lib/n3.min.js',
  'lib/tom-select.complete.min.js',
  'lib/tom-select.min.css',
  'data/vocab.ttl',
  'data/reviews.ttl',
  'data/shapes.ttl',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

function staleWhileRevalidate(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((res) => {
          if (res && res.ok) cache.put(event.request, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
}

function networkFirst(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    fetch(event.request)
      .then((res) => {
        if (res && res.ok) cache.put(event.request, res.clone());
        return res;
      })
      .catch(() => cache.match(event.request))
  );
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;

  // geocodificação: sempre rede, nunca cache
  if (url.hostname === 'nominatim.openstreetmap.org') return;

  // API do grafo compartilhado: sempre rede (mutações e health nunca
  // podem vir de cache). `includes` cobre o app servido em sub-caminho.
  if (url.pathname.includes('/api/') || url.pathname.endsWith('/health')) return;

  if (url.origin === location.origin) {
    // dados mutáveis: rede primeiro (o grafo publicado muda com o repo)
    if (url.pathname.endsWith('.ttl')) {
      event.respondWith(networkFirst(event, STATIC_CACHE));
    } else {
      event.respondWith(staleWhileRevalidate(event, STATIC_CACHE));
    }
    return;
  }

  // tiles do OSM
  if (url.hostname === 'tile.openstreetmap.org') {
    event.respondWith(staleWhileRevalidate(event, RUNTIME_CACHE));
  }
});
