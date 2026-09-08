/**
 * Mods is a service: one flag, and every door reads it — see `access.ts`.
 *
 * Two halves. The LOCK: a role without the grant has every `mods.*`
 * preference put back to its default, on the device, and nothing else
 * touched. The DOORS: the palette in the bar, the avatar-menu item, the
 * profile card and the /mods routes all read the one flag by name, so a
 * renamed flag is a compile error and a door that stopped checking is a
 * test failure rather than a role quietly keeping a look it lost.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const view = vi.hoisted(() => ({ allow: new Set<string>(), ready: true }));
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: (f: string) => view.allow.has(f),
    hasAny: (...f: string[]) => f.some((x) => view.allow.has(x)),
    hasWide: () => true, vehicleScope: 'all', role: 'fleet', ready: view.ready,
  }),
}));

import { ModsLock, lockedModsKeys } from './ModsLock';
import { MODS_PERMISSION } from './access';
import { DEFS, preferences } from '../preferences';

const SRC = join(__dirname, '..');
const src = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

beforeEach(() => { cleanup(); view.allow = new Set([MODS_PERMISSION]); view.ready = true; });

describe('what the lock resets', () => {
  it('is every mods preference, and nothing that is not one', () => {
    const keys = lockedModsKeys();
    expect(keys.length, 'no mods keys found — the lock would reset nothing').toBeGreaterThanOrEqual(6);
    for (const k of keys) expect(k.startsWith('mods.'), k).toBe(true);
    // The alerts sound is the Alerts service's, with its own row.
    expect(keys).not.toContain('dispatch.soundOn');
    expect(Object.keys(DEFS).filter((k) => k.startsWith('mods.'))).toEqual(keys);
  });
});

describe('the lock', () => {
  it('resets every mods preference for a role without the grant', () => {
    const reset = vi.spyOn(preferences, 'reset');
    view.allow = new Set();
    render(<ModsLock />);
    expect(reset.mock.calls.map((c) => c[0]).sort()).toEqual([...lockedModsKeys()].sort());
    reset.mockRestore();
  });

  it('touches nothing for a role that holds it, and nothing before permissions are known', () => {
    const reset = vi.spyOn(preferences, 'reset');
    render(<ModsLock />);
    expect(reset).not.toHaveBeenCalled();
    cleanup();
    view.allow = new Set(); view.ready = false;
    render(<ModsLock />);
    expect(reset, 'reset before the permissions were loaded — "not loaded" is not "denied"').not.toHaveBeenCalled();
    reset.mockRestore();
  });
});

describe('every door reads the one flag', () => {
  const DOORS = {
    'shells/AppShell.tsx': 'the palette in the bar, and the lock',
    'components/AvatarMenu.tsx': 'the avatar-menu item',
    'pages/Profile.tsx': 'the Modifications card',
    'router.tsx': 'the /mods routes',
  };
  it('by name, never as a literal', () => {
    for (const [file, what] of Object.entries(DOORS)) {
      const code = src(file);
      expect(code, `${what} (${file}) does not read MODS_PERMISSION`).toMatch(/\bMODS_PERMISSION\b/);
      expect(code, `${file} spells the flag instead of naming it`).not.toMatch(/['"]can_view_mods['"]/);
    }
  });
  it('the shell mounts the lock and gates the palette', () => {
    const shell = src('shells/AppShell.tsx');
    expect(shell).toMatch(/<ModsLock \/>/);
    expect(shell).toMatch(/canMods && <ModPanel \/>/);
  });
  it('every /mods route is gated', () => {
    // One route per line; greedy to the line's end, because the element
    // itself carries braces (`perm={MODS_PERMISSION}`).
    const routes = [...src('router.tsx').matchAll(/<Route path="mods[^"]*" element=\{(.*)\} \/>\s*$/gm)].map((m) => m[1]);
    expect(routes.length, 'no /mods routes found').toBeGreaterThanOrEqual(3);
    for (const r of routes) expect(r, `an ungated /mods route: ${r}`).toMatch(/<P perm=\{MODS_PERMISSION\}>/);
  });
  it('and the flag is the one the matrix grants', () => {
    expect(MODS_PERMISSION).toBe('can_view_mods');
    expect(src('features/permissions/permRows.ts')).toMatch(/mods: 'can_view_mods'/);
  });
});
