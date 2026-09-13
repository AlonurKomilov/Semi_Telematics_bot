/**
 * Which host a deep link opens.
 *
 * The owner pressed "Web" on the overlay card and got an nginx 404.
 * The apex serves `/`, the sign-in pages, `/extension/connect` and
 * `/assets/` — nothing else — while the app lives on `dash.` or the
 * person's own persona subdomain.  Two more doors had the same bug and
 * had simply never been pressed.
 */
import { describe, expect, it } from 'vitest';

import { ROLE_SUBDOMAIN, appBaseFor } from './appHost';
import overlaySrc from './content/mapsOverlay.ts?raw';
import panelSrc from './features/inventory/InventoryPanel.tsx?raw';
import bgSrc from './background.ts?raw';

const APEX = 'https://4truck.us';

/** Comments explain the rule; they are not the rule. */
function code(src: string): string {
  return String(src).replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
}

describe('a role names its host', () => {
  it('sends owner and admin to the shared one', () => {
    expect(appBaseFor('owner', APEX)).toBe('https://dash.4truck.us');
    expect(appBaseFor('admin', APEX)).toBe('https://dash.4truck.us');
  });

  it('sends a persona to its own', () => {
    expect(appBaseFor('fleet', APEX)).toBe('https://fleet.4truck.us');
    expect(appBaseFor('safety', APEX)).toBe('https://safety.4truck.us');
    expect(appBaseFor('recruiter', APEX)).toBe('https://recruiter.4truck.us');
  });

  it('knows the dispatcher lives at dispatch', () => {
    // The ROLE is "dispatcher"; the HOST label is "dispatch".  Getting
    // this wrong is a 404 that looks like a typo nobody made.
    expect(appBaseFor('dispatcher', APEX)).toBe('https://dispatch.4truck.us');
    expect(appBaseFor('dispatch', APEX)).toBe('https://dash.4truck.us');
  });

  it('sends a role it has never heard of somewhere that works', () => {
    // The subdomains are a hint to the shell, not a permission: every
    // one of them serves the same bundle, so `dash.` is always right,
    // just not branded.
    expect(appBaseFor('', APEX)).toBe('https://dash.4truck.us');
    expect(appBaseFor('inspector', APEX)).toBe('https://dash.4truck.us');
  });

  it('is case-insensitive about the role', () => {
    expect(appBaseFor('FLEET', APEX)).toBe('https://fleet.4truck.us');
  });

  it('treats www as the apex', () => {
    expect(appBaseFor('fleet', 'https://www.4truck.us')).toBe('https://fleet.4truck.us');
  });

  it('leaves a development base alone', () => {
    // localhost has no persona subdomains, and rewriting it would break
    // the dev loop for no gain.
    expect(appBaseFor('fleet', 'http://localhost:5173')).toBe('http://localhost:5173');
    expect(appBaseFor('fleet', 'https://preview.example.com')).toBe('https://preview.example.com');
  });

  it('hands back the base rather than nothing when it will not parse', () => {
    // The caller is about to open a tab; an empty URL is worse than a
    // wrong one, which at least shows the misconfiguration.
    expect(appBaseFor('fleet', 'not a url')).toBe('not a url');
  });

  it('covers every persona the dashboard has a subdomain for', () => {
    for (const role of ['owner', 'admin', 'fleet', 'dispatcher', 'safety',
                        'hr', 'accounting', 'recruiter', 'driver']) {
      expect(ROLE_SUBDOMAIN[role], role).toBeTruthy();
    }
  });
});

describe('no deep link is built on the apex', () => {
  it('not the overlay card, not either Inventory door', () => {
    // DASHBOARD_BASE is correct for exactly two things — signing in and
    // connecting — and those are not built here.
    for (const [name, src] of [['mapsOverlay', overlaySrc],
                               ['InventoryPanel', panelSrc],
                               ['background', bgSrc]] as const) {
      expect(code(src), `${name} must not deep-link from the apex`)
        .not.toContain('DASHBOARD_BASE');
    }
  });
});
