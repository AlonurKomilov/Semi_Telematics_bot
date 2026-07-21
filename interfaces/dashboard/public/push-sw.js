/**
 * Web-push service worker — receives pushes for a CLOSED dashboard and
 * shows the OS notification (notifications phase 6).
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
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || '',
      tag: data.tag || 'notif',
      icon: '/favicon-64.png',
      badge: '/favicon-32.png',
      data: { url: data.url || '/alerts' },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  let url = (event.notification.data && event.notification.data.url) || '/alerts';
  // Same-origin boundary: only relative paths may be navigated to —
  // enforced in the backend render too; this is the belt-and-suspenders
  // in case a future payload source slips an absolute URL through.
  if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//')) {
    url = '/alerts';
  }
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
