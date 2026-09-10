/**
 * The offline half of the service worker, and the page it serves.
 *
 * A worker that gets its fetch rules wrong is worse than the error it
 * replaces: it can pin a stale build in front of every customer until
 * they clear site data, and nobody can tell them to do that if the app
 * will not load. So the three rules that make it safe are asserted here
 * rather than trusted to a comment:
 *
 *   · navigations only — an API call or a bundle must reach the network
 *     untouched
 *   · network first, always — the network's answer wins whenever there
 *     is one, so a deploy is live on the next navigation
 *   · the cache holds only the error page — there is nothing else in it
 *     that could go stale
 *
 * Loaded off disk with a service-worker-shaped scope, the way
 * pushSw.test.ts and themeBoot.test.ts reach code that never enters a
 * bundle.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SW = join(HERE, '../../public/push-sw.js');
const PAGE = join(HERE, '../../public/offline.html');

type Listener = (e: unknown) => void;

interface FetchEvent {
  request: { method: string; mode: string; url: string };
  respondWith: (r: Promise<Response> | Response) => void;
}

/** The real worker, in a scope shaped like a service worker's. */
function load(networkFails: boolean) {
  const listeners: Record<string, Listener> = {};
  const cached = new Map<string, string>();
  const deleted: string[] = [];
  let claimed = false;
  let skipped = false;

  const self = {
    location: { origin: 'https://dash.4truck.us' },
    addEventListener: (n: string, fn: Listener) => { listeners[n] = fn; },
    registration: { showNotification: () => Promise.resolve() },
    skipWaiting: () => { skipped = true; return Promise.resolve(); },
    clients: { claim: () => { claimed = true; return Promise.resolve(); } },
  };
  const clients = { matchAll: () => Promise.resolve([]), openWindow: () => Promise.resolve(null) };

  const caches = {
    open: (name: string) => Promise.resolve({
      addAll: (urls: string[]) => { urls.forEach((u) => cached.set(u, name)); return Promise.resolve(); },
    }),
    keys: () => Promise.resolve(['4truck-shell-v0', '4truck-shell-v1', 'someone-elses-cache']),
    delete: (k: string) => { deleted.push(k); return Promise.resolve(true); },
    match: (url: string) => Promise.resolve(
      cached.has(url) ? new Response('OFFLINE PAGE', { status: 200 }) : undefined),
  };

  const netCalls: string[] = [];
  const fetchImpl = (req: { url: string }) => {
    netCalls.push(req.url);
    return networkFails
      ? Promise.reject(new TypeError('Failed to fetch'))
      : Promise.resolve(new Response('LIVE APP', { status: 200 }));
  };

  new Function('self', 'clients', 'caches', 'fetch', readFileSync(SW, 'utf8'))(
    self, clients, caches, fetchImpl);

  return { listeners, cached, deleted, netCalls, isClaimed: () => claimed, didSkip: () => skipped };
}

async function fire(listeners: Record<string, Listener>, name: string, evt: unknown) {
  let waited: Promise<unknown> = Promise.resolve();
  (evt as { waitUntil?: unknown }).waitUntil = (p: Promise<unknown>) => { waited = p; };
  listeners[name](evt);
  await waited;
}

/** Push one request through the fetch handler; null when it declined. */
async function navigate(
  listeners: Record<string, Listener>,
  request: { method: string; mode: string; url: string },
): Promise<Response | null> {
  let answer: Promise<Response> | Response | null = null;
  const evt: FetchEvent = { request, respondWith: (r) => { answer = r; } };
  listeners.fetch(evt);
  return answer === null ? null : await answer;
}

const NAV = { method: 'GET', mode: 'navigate', url: 'https://dash.4truck.us/loads' };

describe('service worker — the rules that keep it safe', () => {
  it('declines everything that is not a navigation', async () => {
    const { listeners } = load(true);
    for (const req of [
      { method: 'GET', mode: 'cors', url: 'https://dash.4truck.us/api/loads' },
      { method: 'GET', mode: 'no-cors', url: 'https://dash.4truck.us/assets/index.js' },
      { method: 'POST', mode: 'navigate', url: 'https://dash.4truck.us/login' },
      { method: 'GET', mode: 'same-origin', url: 'https://dash.4truck.us/favicon-32.png' },
    ]) {
      expect(await navigate(listeners, req), `${req.method} ${req.mode}`).toBeNull();
    }
  });

  it('serves the live network answer when there is one — never the cache', async () => {
    const { listeners, netCalls } = load(false);
    await fire(listeners, 'install', {});
    const res = await navigate(listeners, NAV);
    expect(await res!.text()).toBe('LIVE APP');
    expect(netCalls).toContain(NAV.url);
  });

  it('serves the offline page only when the network throws', async () => {
    const { listeners } = load(true);
    await fire(listeners, 'install', {});
    const res = await navigate(listeners, NAV);
    expect(await res!.text()).toBe('OFFLINE PAGE');
  });

  it('answers 503, not 200, when even the cache is empty', async () => {
    const { listeners } = load(true);   // no install, so nothing cached
    const res = await navigate(listeners, NAV);
    expect(res!.status).toBe(503);
  });

  it('precaches the error page and its icon, and nothing else', async () => {
    const { listeners, cached } = load(false);
    await fire(listeners, 'install', {});
    expect([...cached.keys()].sort()).toEqual(['/favicon-32.png', '/offline.html']);
  });

  it('drops its own old caches on activate and leaves other apps alone', async () => {
    const { listeners, deleted, isClaimed } = load(false);
    await fire(listeners, 'activate', {});
    expect(deleted).toEqual(['4truck-shell-v0']);
    expect(deleted).not.toContain('someone-elses-cache');
    expect(isClaimed()).toBe(true);
  });

  it('still registers the push handlers it shared a file with', () => {
    const { listeners } = load(false);
    expect(typeof listeners.push).toBe('function');
    expect(typeof listeners.notificationclick).toBe('function');
  });
});

describe('offline page — it must render with the network down', () => {
  const html = readFileSync(PAGE, 'utf8');

  it('pulls nothing over the network to render', () => {
    const refs = [...html.matchAll(/(?:src|href)="(https?:\/\/[^"]+)"/g)].map((m) => m[1]);
    expect(refs, 'an external stylesheet, font or image would be a hole in the page').toEqual([]);
    expect(html).not.toMatch(/<link[^>]+rel="stylesheet"/);
  });

  it('says which side the problem is on, in both cases', () => {
    // The whole reason the page exists: the browser refuses to draw this
    // distinction, and it is the one a customer needs.
    expect(html).toContain('No internet connection');       // their network
    expect(html).toContain('Can’t reach 4truck');            // the route to us
    expect(html).toMatch(/navigator\.onLine/);
  });

  it('tells the customer their data is safe', () => {
    expect(html).toContain('Your data is safe');
  });

  it('recovers by itself instead of asking the customer to keep trying', () => {
    expect(html).toMatch(/location\.reload\(\)/);
    expect(html).toMatch(/addEventListener\('online'/);
  });

  it('hides the status link until a status page actually exists', () => {
    // A "Status page" button that opens a vendor's marketing homepage is
    // worse than no button.
    expect(html).toMatch(/var STATUS_PAGE_URL = '[^']*';/);
    const url = html.match(/var STATUS_PAGE_URL = '([^']*)';/)![1];
    if (!url) {
      expect(html).toMatch(/id="status"[^>]*hidden/);
      expect(html).toMatch(/id="check-status"[^>]*hidden/);
    } else {
      expect(url).toMatch(/^https:\/\//);
    }
  });
});
