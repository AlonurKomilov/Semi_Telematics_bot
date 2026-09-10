/**
 * The door and the sign agree.
 *
 * A page is reached two ways: the sidebar / palette entry (drawn when the
 * viewer holds the catalog entry's `permission`) and the route (which
 * bounces the viewer unless they hold the `<P perm>` guard). When the two
 * name different verbs, a role sees an entry it cannot open — the sign
 * says View, the door asks Manage. Six pages disagreed on 2026-09-10;
 * the router is read here the way the catalog is read in
 * tests/test_feature_registry_drift.py, so the next drift fails loud.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FEATURE_CATALOG } from '../config/featureCatalog';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const router = readFileSync(join(SRC, 'router.tsx'), 'utf8');

/** path → the guard's flag set, for every `<Route path="…" element={L(<P perm=…>`
 *  whose perm is a literal (a string, or an array of strings). A perm given
 *  as an identifier (MODS_PERMISSION) is resolved when the identifier is a
 *  known constant; otherwise the route is skipped rather than guessed. */
function routeGuards(): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const consts: Record<string, string[]> = { MODS_PERMISSION: ['can_view_mods'] };
  const re = /<Route path="([^"]+)"[^>]*?element=\{L?\(?<P perm=(?:"([^"]+)"|\{\[([^\]]+)\]\}|\{([A-Za-z_]+)\})/g;
  for (const m of router.matchAll(re)) {
    const [, path, single, arr, ident] = m;
    const flags = single ? [single]
      : arr ? [...arr.matchAll(/['"]([^'"]+)['"]/g)].map((x) => x[1])
      : (consts[ident!] ?? null);
    if (flags) out.set(path, new Set(flags));
  }
  return out;
}

describe('every catalog page whose route is guarded is guarded on the catalog’s own verb', () => {
  const guards = routeGuards();

  it('reads the router at all', () => {
    expect(guards.size).toBeGreaterThan(20);
  });

  it('door === sign', () => {
    const mismatches: string[] = [];
    for (const e of FEATURE_CATALOG) {
      if (!e.permission) continue;
      const path = e.path.replace(/^\//, '');
      const guard = guards.get(path);
      if (!guard) continue;                       // unguarded or identifier-guarded: not this test's claim
      const sign = new Set(Array.isArray(e.permission) ? e.permission : [e.permission]);
      const same = sign.size === guard.size && [...sign].every((f) => guard.has(f));
      if (!same) mismatches.push(`${e.id} (/${path}): sign ${[...sign].join('|')} ≠ door ${[...guard].join('|')}`);
    }
    expect(mismatches).toEqual([]);
  });
});
