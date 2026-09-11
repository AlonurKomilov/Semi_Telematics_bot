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

  it('speaks as ABC Checker, and still names whose product this is', () => {
    // The product's own voice is the weakest one available when the
    // product is what failed — but a stranger's name appearing at the
    // moment a connection was interfered with reads as a redirect, so
    // the product's mark and name sit beside the service's, and the
    // footer says who operates what.
    // The company wordmark is set as TYPE, not shipped as an image: the
    // real logo is black, and a black PNG is invisible on the dark theme
    // these pages default to.
    expect(html).toMatch(/class="wordmark">ABC&nbsp;Legacy&nbsp;LLC</);
    expect(html).toMatch(/class="svc-word">Checker</);
    expect(html).not.toMatch(/<img[^>]+logo/i);
    expect(html).toMatch(/class="prod"/);
    // Service first, product second — never the other way round. Measured
    // INSIDE the header: the file's opening comment names ABC Checker
    // too, and comparing whole-file offsets made this assertion vacuous
    // (it passed with the two swapped).
    const bar = html.match(/<header class="topbar">([\s\S]*?)<\/header>/)![1];
    expect(bar.indexOf('class="svc"')).toBeGreaterThanOrEqual(0);
    expect(bar.indexOf('class="svc"')).toBeLessThan(bar.indexOf('class="prod"'));
  });

  it('changes product with one variable, and the HTML default agrees with it', () => {
    // Reuse for 2bot must not become a search-and-replace across the
    // file: a half-done rename is how a page ends up telling a 2bot
    // customer to unblock 4truck.
    const name = html.match(/var PRODUCT = \{\s*\n?\s*name: '([^']+)'/)![1];
    expect(name).toBe('4truck');
    // Every product mention is a slot, and the pre-JS default matches —
    // so the first paint is right even if the script never runs.
    const slots = [...html.matchAll(/<span data-product>([^<]*)<\/span>/g)].map((m) => m[1]);
    expect(slots.length).toBeGreaterThan(5);
    expect(new Set(slots)).toEqual(new Set([name]));
    // and no bare product name survives outside a slot in the body copy
    const body = html.slice(html.indexOf('<body'));
    const bare = body.replace(/<span data-product>[^<]*<\/span>/g, '')
                     .replace(/<script[\s\S]*?<\/script>/g, '')
                     .match(/4truck/g);
    expect(bare, 'a product mention outside a slot will not follow a rename').toBeNull();
  });

  it('says which side the problem is on, in both cases', () => {
    // The whole reason the page exists: the browser refuses to draw this
    // distinction, and it is the one a customer needs.
    expect(html).toContain('No internet connection');              // their network
    expect(html).toContain('This network can’t reach 4truck');     // the route to us
    expect(html).toMatch(/navigator\.onLine/);
  });

  it('never makes 4truck the thing that failed', () => {
    // The owner read the first draft as a customer and came away
    // thinking it was our fault. The headline is where that happens:
    // "Can't reach 4truck" puts 4truck in the failing position, while
    // "This network can't reach 4truck" names the actual subject.
    const headings = [...html.matchAll(/<h1[^>]*>([\s\S]*?)<\/h1>/g)]
      .map((m) => m[1].replace(/<[^>]+>/g, '').trim());
    const spoken = [...html.matchAll(/title\.textContent = '((?:[^']|\\')+)'/g)].map((m) => m[1]);
    for (const line of [...headings, ...spoken]) {
      expect(line, `"${line}" reads as 4truck being down`)
        .not.toMatch(/^(4truck|Can’t reach|Cannot reach|Service unavailable)/i);
    }
  });

  it('gives a way back in, not only a diagnosis', () => {
    // A dispatcher with drivers on the road wants access, not a verdict.
    // A VPN and a phone's data both get them working while the block
    // stands, and the owner confirmed a VPN restores access.
    expect(html).toMatch(/Get back in now/);
    expect(html).toMatch(/Connect through a VPN/);
    expect(html).toMatch(/phone’s data/);
  });

  it('routes an office block to the office, with a message to forward', () => {
    // Commercially the point of the whole section: a blocked corporate
    // network is fixed by their own IT in a minute, and without
    // something to forward the complaint lands on our support instead.
    expect(html).toMatch(/Get it fixed for good/);
    expect(html).toMatch(/hosts: '\*\.4truck\.us'/);   // the ask an IT admin can act on
    expect(html).toMatch(/id="copy-msg"/);
  });

  it('withholds VPN and office advice from a device with no network at all', () => {
    // Telling someone with no connection to try a VPN is not merely
    // useless, it is wrong — the CSS hides those by state.
    expect(html).toMatch(/body\[data-state="offline"\] \[data-when="unreachable"\] \{ display: none; \}/);
    expect(html).toMatch(/<li data-when="unreachable">/);
    expect(html).toMatch(/<section data-when="unreachable">/);
  });

  it('tells the customer their data is safe', () => {
    expect(html).toContain('Your data is safe');
  });

  it('recovers by itself instead of asking the customer to keep trying', () => {
    expect(html).toMatch(/location\.reload\(\)/);
    expect(html).toMatch(/addEventListener\('online'/);
  });

  it('names itself the same way in every sentence', () => {
    // The lockup may abbreviate; prose may not. "Checker is operated
    // by…" beside seven "ABC Checker"s is one object under two names.
    expect(html).toMatch(/ABC&nbsp;Checker is operated by/);
    expect(html).not.toMatch(/>Checker is operated by/);
  });

  it('spends its one filled button on the action that ends the problem', () => {
    // Weight follows value, not position: "Try again" repeats what the
    // page already does every 10-60s, while copying the message to an
    // office admin is the move that stops the waiting.
    expect(html).toMatch(/<button type="button" class="ghost" id="retry">/);
    expect(html).toMatch(/<button type="button" id="copy-msg">/);
  });

  it('reserves the outline for things that can be acted on', () => {
    // An outlined circle beside text reads as a button nobody can press.
    const ornament = html.match(/\.steps li::before \{[\s\S]*?\}/)![0];
    expect(ornament).not.toMatch(/border: 1px solid var\(--border\)/);
    expect(ornament).toMatch(/background: var\(--sunk\)/);
  });

  it('keeps machine detail closed, and the human sentence in the open', () => {
    // A timestamp and a hostname shown in the open read as wreckage to
    // someone who only wants their loads back — the same reason
    // Cloudflare's "Error 521 Web server is down" makes a working
    // platform look abandoned.
    expect(html).toMatch(/<details class="tech">/);
    expect(html).not.toMatch(/<details class="tech" open>/);
    const summary = html.match(/<summary>([^<]+)<\/summary>/)![1];
    expect(summary).toBe('Technical details');
    // ...and it must not promise a destination the page cannot name.
    expect(html).not.toContain('Send us this line');
  });

  it('hides the status link until a status page actually exists', () => {
    // A "Status page" button that opens a vendor's marketing homepage is
    // worse than no button.
    expect(html).toMatch(/var STATUS_PAGE_URL = '[^']*';/);
    const url = html.match(/var STATUS_PAGE_URL = '([^']*)';/)![1];
    if (!url) {
      expect(html).toMatch(/id="sec-status"[^>]*hidden/);
    } else {
      expect(url).toMatch(/^https:\/\//);
    }
  });
});
