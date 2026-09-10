/**
 * The dashboard's service worker. Two jobs that share one registration
 * because a scope may only have one worker:
 *
 *   1. WEB PUSH — receives pushes for a CLOSED dashboard and shows the OS
 *      notification (below). Registered on push opt-in since it shipped.
 *   2. OFFLINE FALLBACK — serves our own page when a navigation cannot
 *      reach the network, instead of the browser's ERR_CONNECTION_RESET.
 *      That error reads as "this product is broken" to every customer
 *      who sees it; nobody suspects their own browser when other sites
 *      work. Registered for EVERYONE at app boot (main.tsx), which is
 *      safe: registering a worker never prompts for anything — only
 *      Notification.requestPermission() does, and that stays behind the
 *      explicit opt-in click.
 *
 * "Closed" is now something the push handler CHECKS rather than assumes:
 * a visible tab is already announcing the alert itself, so the push
 * arrives quietly.
 *
 * Lives in public/ so Vite serves it verbatim from the origin root (a
 * service worker's scope can't exceed its own path).  The payload is the
 * JSON the backend WebPushChannel renders: {title, body, url, severity,
 * tag}.  `tag` makes a newer notification of the same alert type REPLACE
 * the older one instead of stacking forever.
 */
/* global self, clients -- service-worker globals */
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: 'Notification', body: event.data && event.data.text() };
  }
  const title = data.title || 'Notification';
  // PRIORITY, as the only thing we can control out here. A service
  // worker has no page and no AudioContext, so the cue the dashboard
  // plays for an in-app banner is unavailable — the operating system
  // makes the noise, and `silent` is the whole of our say in it. So the
  // severity that the backend already puts in the payload, and that
  // this worker used to drop on the floor, decides whether a push is
  // worth interrupting somebody for. Anything below a warning arrives
  // quietly and waits to be looked at.
  var loud = data.severity === 'critical' || data.severity === 'warning';
  // AND WHETHER ANYBODY IS ALREADY LOOKING. The first line of this file
  // says it shows the OS notification for a CLOSED dashboard, and until
  // now nothing checked. An alert arriving while a dispatcher watches
  // the board was announced twice: the in-app banner sounds it by
  // severity through `playBannerCue`, and then the operating system
  // sounded it again over the top — one event, two noises, and the
  // second one going around the person's own alert-sound switch, which
  // a service worker cannot read.
  //
  // Showing NOTHING is not the alternative. A push handler that ends
  // without a notification gets the browser's own "this site has been
  // updated in the background" instead, so the choice is loud or quiet,
  // and quiet is right: the banner is the announcement, and this stays
  // as the record of it in the notification tray.
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (wins) {
      var watched = wins.some(function (w) { return w.visibilityState === 'visible'; });
      return self.registration.showNotification(title, {
        body: data.body || '',
        tag: data.tag || 'notif',
        icon: '/favicon-64.png',
        badge: '/favicon-32.png',
        silent: !loud || watched,
        data: { url: data.url || '/alerts' },
      });
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = sameOriginPath(event.notification.data && event.notification.data.url);
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      // Focus an existing dashboard tab if one is open; else open a new one.
      for (const w of wins) {
        if ('focus' in w) {
          w.navigate(url);
          return w.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});


// Same-origin boundary: a notification may only send the dashboard to
// one of its own paths — enforced in the backend render too; this is
// the belt in case a future payload source slips a foreign URL through.
// Resolved the way the browser will resolve it, because a string test
// misses what the parser accepts: '/\\evil.com' is not '//evil.com' to
// startsWith, and is exactly that to the navigation.
function sameOriginPath(raw) {
  if (typeof raw !== 'string' || !raw.startsWith('/')) return '/alerts';
  try {
    const u = new URL(raw, self.location.origin);
    if (u.origin !== self.location.origin) return '/alerts';
    return u.pathname + u.search + u.hash;
  } catch (_e) {
    return '/alerts';
  }
}


// ── Offline fallback ──────────────────────────────────────────────
//
// The rules this half must never break, because a service worker that
// gets them wrong serves a stale app to every customer until they clear
// site data — a failure far worse than the one it prevents:
//
//   · NAVIGATIONS ONLY. Never an API call, never a script, never a
//     style. Anything else keeps its normal path to the network.
//   · NETWORK FIRST, ALWAYS. The network's answer wins whenever there
//     is one, so a deploy is live on the next navigation and this
//     worker can never pin an old build.
//   · THE CACHE HOLDS ONLY THIS PAGE. No app HTML, no bundle, no API
//     response. There is nothing here that can go stale except the
//     error page itself.
//
// To retire it: ship a worker whose `install` calls
// self.registration.unregister(), or bump SHELL and drop the fetch
// handler. Clients pick either up on their next navigation.
var SHELL = '4truck-shell-v1';
var OFFLINE_URL = '/offline.html';

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(SHELL).then(function (cache) {
      // The favicon rides along: the page shows our mark, and fetching
      // it at display time is exactly what will not work.
      return cache.addAll([OFFLINE_URL, '/favicon-32.png']);
    }).then(function () {
      return self.skipWaiting();
    }).catch(function () {
      // A failed precache must not block activation — the push half of
      // this worker still has to install.
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k !== SHELL && k.indexOf('4truck-') === 0 ? caches.delete(k) : null;
      }));
    }).then(function () {
      return self.clients.claim();
    }).catch(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET' || req.mode !== 'navigate') return;
  event.respondWith(
    fetch(req).catch(function () {
      return caches.match(OFFLINE_URL, { ignoreSearch: true }).then(function (hit) {
        // 503, not 200: this is not the page that was asked for, and a
        // crawler or a monitor must not read it as a healthy answer.
        return hit || new Response(
          'Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
      });
    })
  );
});
