/**
 * The barrel is only a single source of truth while nothing walks past
 * it. This is what makes that true rather than aspirational.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');

/** Every .ts/.tsx under src, src-relative, tests included. */
function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(name)) out.push(relative(SRC, full).replace(/\\/g, '/'));
  }
  return out;
}

/**
 * Allowed to reach past the barrel, each for a stated reason.
 *
 * `preferences/registry.ts` sits UNDERNEATH this service: `mods/context`
 * imports the preferences store, so a registry that came through the
 * barrel would close a cycle. It imports the catalogue, the injector and
 * the sound engine directly and deliberately.
 *
 * `test/themeBoot.test.ts` compares the pre-paint script against
 * `applyTheme` itself — the point is the specific implementation, not
 * whatever the barrel currently re-exports.
 *
 * The two undo wrappers reach `mods/sound/cue` directly because the
 * barrel would close a ring: `mods/index` exports `ModPanel`, and
 * `ModPanel` imports `undoableAction` from `components/banners/
 * stagedAction`. Vite reports nothing for that — the binding is simply
 * `undefined` at module-init — so the rule is worth more than the
 * convenience. `mods/sound/cue` is a leaf: it imports the preferences
 * store and the sound engine, and nothing in either reaches back.
 */
const ALLOWED_DEEP = [
  'preferences/registry.ts',
  'test/themeBoot.test.ts',
  'components/banners/stagedAction.tsx',
  'lib/undoable.ts',
];

describe('everything outside mods/ comes through the barrel', () => {
  const files = walk(SRC).filter((f) => !f.startsWith('mods/'));

  it('finds files to check', () => {
    // A walker that returns nothing would pass every assertion below.
    expect(files.length).toBeGreaterThan(100);
  });

  it('has no deep import into the service', () => {
    const offenders: string[] = [];
    for (const f of files) {
      if (ALLOWED_DEEP.includes(f)) continue;
      const src = readFileSync(join(SRC, f), 'utf8');
      // `…/mods/<something>` — the barrel itself ends at `mods` or
      // `mods/index`, so anything with a further segment walked past it.
      for (const m of src.matchAll(/from\s+['"]([^'"]*\/mods\/[^'"]+)['"]/g)) {
        const spec = m[1];
        if (/\/mods\/index$/.test(spec)) continue;
        offenders.push(`${f} → ${spec}`);
      }
    }
    expect(offenders,
      'import from the barrel, or add the file to ALLOWED_DEEP with a reason')
      .toEqual([]);
  });

  it('keeps every allowed exception real', () => {
    // An exception for a file that no longer reaches past the barrel is
    // a hole nobody is watching.
    for (const f of ALLOWED_DEEP) {
      const src = readFileSync(join(SRC, f), 'utf8');
      expect(/from\s+['"][^'"]*\/mods\/[^'"]+['"]/.test(src)
        || /from\s+['"]\.\.\/(catalogue|inject|context)['"]/.test(src),
        `${f} no longer needs its exception — remove it`).toBe(true);
    }
  });
});
