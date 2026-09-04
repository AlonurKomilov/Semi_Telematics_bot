/**
 * The places that wear a look of their own — and the line they may not
 * cross.
 *
 * The list is fixed because we own every route it names; the resolver
 * has to agree with the router, or a surface is a colour nobody can
 * reach. And a surface may set the CANVAS and never the accent: a
 * scoped accent would have to out-rank the `[data-accent]` blocks the
 * way the global one does, and the `:not([data-mod-accent])` stand-down
 * that makes that work is written per block — so it would lose under
 * purple, green and azure while appearing to work under blue.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { SURFACES, surfaceFor, surfaceById } from './surfaces';
import { surfaceTokens, SURFACE_TOKENS } from './theme/canvas';
import { DERIVED_TOKENS } from './theme/palette';

const ROUTER = readFileSync(join(__dirname, '..', 'router.tsx'), 'utf8');

describe('every surface names a route the router actually has', () => {
  it('finds surfaces to check', () => {
    expect(SURFACES.length).toBeGreaterThan(2);
  });

  it('and each one resolves', () => {
    // A surface whose route was renamed is a colour nobody can reach:
    // it stays in storage, never matches, and never paints.
    const missing = SURFACES
      .filter((s) => !ROUTER.includes(`<Route path="${s.route.slice(1)}"`))
      .map((s) => `${s.id} → ${s.route}`);
    expect(missing, 'a surface names a route the router does not declare').toEqual([]);
  });

  it('ids are usable as an attribute value and as a storage key', () => {
    for (const s of SURFACES) expect(s.id).toMatch(/^[a-z][a-z0-9-]*$/);
    expect(new Set(SURFACES.map((s) => s.id)).size, 'two surfaces share an id')
      .toBe(SURFACES.length);
  });
});

describe('resolving a path to a surface', () => {
  it('matches the route and its children', () => {
    expect(surfaceFor('/loads')?.id).toBe('loads');
    expect(surfaceFor('/loads/42')?.id).toBe('loads');
  });

  it('does not match a sibling that merely starts the same', () => {
    // `/loadsheets` is not the Loads board, and a naive startsWith says
    // it is.
    expect(surfaceFor('/loadsheets')).toBeNull();
  });

  it('everything else is null — the global look, stated rather than guessed', () => {
    for (const p of ['/', '/vehicles', '/profile', '/settings'])
      expect(surfaceFor(p), p).toBeNull();
  });

  it('surfaceById round-trips', () => {
    for (const s of SURFACES) expect(surfaceById(s.id)).toBe(s);
    expect(surfaceById('nope')).toBeUndefined();
  });
});

describe('a surface sets the conditions, never the identity', () => {
  const ACCENT = ['--primary', '--primary-foreground', '--primary-hover', '--primary-text'];

  it('installs no accent token at all', () => {
    const t = surfaceTokens('#ffffff', '#ff6a00', 'light').tokens!;
    for (const a of ACCENT)
      expect(Object.keys(t), `a surface wrote ${a} — the brand is global`).not.toContain(a);
  });

  it('but still installs the surfaces, inks and boundaries', () => {
    const t = surfaceTokens('#ffffff', '#ff6a00', 'light').tokens!;
    for (const k of ['--background', '--card', '--sidebar', '--border', '--muted-foreground'])
      expect(Object.keys(t), `${k} missing — a surface that changes nothing`).toContain(k);
  });

  it('the two lists differ by exactly the accent family', () => {
    const diff = DERIVED_TOKENS.filter((t) => !SURFACE_TOKENS.includes(t));
    expect([...diff].sort()).toEqual([...ACCENT].sort());
  });

  it('and a canvas the mode cannot wear contributes nothing', () => {
    // Cream breaks --warn in dark. The page falls back to the global
    // look rather than to half a palette.
    expect(surfaceTokens('#f5f0e8', '#ff6a00', 'dark').tokens).toBeNull();
  });
});
