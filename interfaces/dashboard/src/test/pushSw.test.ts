/**
 * The push worker's one decision: loud, or quiet.
 *
 * It cannot be reached by any other test in this suite. `public/` is
 * served verbatim and never enters a bundle, the file has no exports and
 * cannot import, and it runs in a scope with no DOM — so the only way to
 * watch it work is to read it off disk and give it that scope, which is
 * what `themeBoot.test.ts` does with the pre-paint script for the same
 * reason.
 *
 * What it guards is a sound nobody can trace. An alert arriving while a
 * dispatcher watches the board used to be announced twice — the in-app
 * banner by severity, then the operating system over the top — and the
 * second one goes around the person's own alert-sound switch, which a
 * service worker cannot read. It is heard on the floor and reproduces
 * nowhere, because it needs a real push, a real subscription and a
 * visible tab at once.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SW = join(dirname(fileURLToPath(import.meta.url)), '../../public/push-sw.js');

type Shown = { title: string; opts: Record<string, unknown> };
type Client = { visibilityState: string };

/** Run the REAL worker in a scope shaped like a service worker's, and
 *  hand back a way to push one message through it. */
function load(wins: Client[]) {
  const shown: Shown[] = [];
  const listeners: Record<string, (e: unknown) => void> = {};
  const self = {
    addEventListener: (name: string, fn: (e: unknown) => void) => { listeners[name] = fn; },
    registration: {
      showNotification: (title: string, opts: Record<string, unknown>) => {
        shown.push({ title, opts });
        return Promise.resolve();
      },
    },
  };
  const clients = {
    matchAll: () => Promise.resolve(wins),
    openWindow: () => Promise.resolve(null),
  };
  new Function('self', 'clients', readFileSync(SW, 'utf8'))(self, clients);

  return {
    shown,
    listeners,
    async push(payload: Record<string, unknown>) {
      let waited: Promise<unknown> = Promise.resolve();
      listeners.push({
        data: { json: () => payload, text: () => JSON.stringify(payload) },
        waitUntil: (p: Promise<unknown>) => { waited = p; },
      });
      await waited;
      return shown[shown.length - 1];
    },
  };
}

const CLOSED: Client[] = [];
const HIDDEN: Client[] = [{ visibilityState: 'hidden' }];
const WATCHED: Client[] = [{ visibilityState: 'visible' }];

describe('the worker is wired at all', () => {
  it('registers a push handler', () => {
    expect(load(CLOSED).listeners.push, 'no push handler — every test below watches nothing')
      .toBeTypeOf('function');
  });
});

describe('nobody is looking — severity decides', () => {
  it('a critical push interrupts', async () => {
    expect((await load(CLOSED).push({ title: 'x', severity: 'critical' })).opts.silent).toBe(false);
  });

  it('so does a warning', async () => {
    expect((await load(CLOSED).push({ title: 'x', severity: 'warning' })).opts.silent).toBe(false);
  });

  it('anything below waits to be looked at', async () => {
    for (const severity of ['info', 'low', undefined]) {
      expect((await load(CLOSED).push({ title: 'x', severity })).opts.silent,
        `${severity} interrupted somebody`).toBe(true);
    }
  });

  /** A tab that exists but is not on screen is not somebody looking —
   *  the banner it renders is behind another window. */
  it('and a backgrounded tab still counts as nobody', async () => {
    expect((await load(HIDDEN).push({ title: 'x', severity: 'critical' })).opts.silent).toBe(false);
  });
});

describe('somebody is looking — the dashboard has it', () => {
  it('a critical push arrives quietly, because the banner already sounded it', async () => {
    expect((await load(WATCHED).push({ title: 'x', severity: 'critical' })).opts.silent,
      'the OS spoke over the in-app banner').toBe(true);
  });

  /** Quiet, not absent. A push handler that ends without a notification
   *  gets the browser's own "this site has been updated in the
   *  background" in its place — so the record has to be shown either
   *  way, and silence is the whole of our say in it. */
  it('but it still arrives — the tray keeps the record', async () => {
    const w = load(WATCHED);
    await w.push({ title: 'Truck 42 idle', body: 'b', severity: 'critical' });
    expect(w.shown).toHaveLength(1);
    expect(w.shown[0].title).toBe('Truck 42 idle');
    expect(w.shown[0].opts.body).toBe('b');
  });
});

describe('everything the payload carries still arrives', () => {
  it('tag, url and body survive the visibility check', async () => {
    const n = await load(CLOSED).push({
      title: 't', body: 'b', tag: 'alert-idle', url: '/alerts/7', severity: 'critical',
    });
    expect(n.opts.tag).toBe('alert-idle');
    expect(n.opts.data).toEqual({ url: '/alerts/7' });
    expect(n.opts.body).toBe('b');
  });

  it('and a payload that is not JSON still shows something', async () => {
    const shown: Shown[] = [];
    const listeners: Record<string, (e: unknown) => void> = {};
    new Function('self', 'clients', readFileSync(SW, 'utf8'))(
      {
        addEventListener: (n: string, f: (e: unknown) => void) => { listeners[n] = f; },
        registration: {
          showNotification: (title: string, opts: Record<string, unknown>) => {
            shown.push({ title, opts }); return Promise.resolve();
          },
        },
      },
      { matchAll: () => Promise.resolve(CLOSED) },
    );
    let waited: Promise<unknown> = Promise.resolve();
    listeners.push({
      data: { json: () => { throw new Error('not json'); }, text: () => 'raw text' },
      waitUntil: (p: Promise<unknown>) => { waited = p; },
    });
    await waited;
    expect(shown[0].opts.body).toBe('raw text');
  });
});
