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


/**
 * No runtime import cycle between the mods service and the preferences
 * store — measured, not assumed.
 *
 * The two lean on each other by design: `mods/context` reads the store,
 * and `preferences/registry` imports five mods LEAVES (the catalogue,
 * the injector, the two sound modules and the contrast maths) because
 * validating a stored value belongs with the thing that defines what a
 * valid value is. That arrangement is sound only while those five stay
 * leaves. The moment one of them imports the store back, the ring
 * closes — and a JS cycle does not throw. Vite reports nothing; a
 * binding is simply `undefined` at module-init, which surfaces later as
 * a blank theme or a sanitiser that silently rejects everything.
 *
 * `import type` is EXCLUDED, and that is the whole reason this can be
 * checked at all: the catalogue already imports `ModRadius` from the
 * registry, and taxonomy imports `Mod` from the catalogue. Both are
 * erased at build time and cannot participate in a runtime cycle —
 * counting them would make this test red on a structure that works.
 */
describe('the service and the store do not close a ring', () => {
  const ROOTS = ['mods', 'preferences'];

  const walkAll = (dir: string, out: string[] = []): string[] => {
    for (const name of readdirSync(join(SRC, dir))) {
      const rel = `${dir}/${name}`;
      if (statSync(join(SRC, rel)).isDirectory()) walkAll(rel, out);
      else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(rel);
    }
    return out;
  };

  /** Value imports only — a clause that is entirely `type` is erased. */
  const valueImports = (rel: string): string[] => {
    const src = readFileSync(join(SRC, rel), 'utf8');
    const out: string[] = [];
    for (const m of src.matchAll(/^import\s+(?!type\b)([^;]*?)\s+from\s+'(\.[^']+)'/gm)) {
      const clause = m[1].trim();
      const inner = clause.startsWith('{') ? clause.slice(1, -1) : '';
      if (inner && inner.split(',').every((t) => !t.trim() || t.trim().startsWith('type '))) continue;
      const base = join(rel, '..', m[2]);
      for (const ext of ['.ts', '.tsx', '/index.ts']) {
        const cand = base + ext;
        try { statSync(join(SRC, cand)); out.push(cand.replace(/\\/g, '/')); break; } catch { /* next */ }
      }
    }
    return out;
  };

  const files = ROOTS.flatMap((r) => walkAll(r));

  it('finds the files it is checking', () => {
    // A walker that returned nothing would report "no cycles" forever.
    expect(files.length, 'no source files found in mods/ or preferences/').toBeGreaterThan(25);
    expect(files.some((f) => f === 'preferences/registry.ts')).toBe(true);
  });

  it('sees the edges that exist, so a clean result means something', () => {
    // The registry really does import mods leaves; if this reader stopped
    // seeing those, the cycle check below would pass vacuously.
    const fromRegistry = valueImports('preferences/registry.ts');
    expect(fromRegistry.filter((f) => f.startsWith('mods/')).length,
      'the reader no longer sees preferences → mods edges').toBeGreaterThan(3);
  });

  it('has no cycle', () => {
    const graph = new Map(files.map((f) => [f, valueImports(f)]));
    const found: string[] = [];
    const seen = new Set<string>();
    const stack: string[] = [];
    const walk = (n: string) => {
      const at = stack.indexOf(n);
      if (at >= 0) { found.push([...stack.slice(at), n].join(' → ')); return; }
      if (seen.has(n)) return;
      seen.add(n); stack.push(n);
      for (const m of graph.get(n) ?? []) walk(m);
      stack.pop();
    };
    for (const f of files) walk(f);
    expect(
      found,
      'a runtime import cycle. JS does not throw on one — the binding is '
        + 'undefined at module-init and surfaces later as a blank theme or a '
        + 'sanitiser that rejects everything. Break it by importing the leaf '
        + 'directly, or by making the back-edge `import type`.',
    ).toEqual([]);
  });
});
