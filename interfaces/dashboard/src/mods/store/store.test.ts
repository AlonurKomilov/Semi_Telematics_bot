/**
 * The store's two promises: the catalogue cannot drift from the packs,
 * and the house says who owns what.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { PACK_AXES } from './packs';
import { STORE, STORE_AXES, PUBLISHER, rowsOf, idsOf, rowById } from './index';
import { installed, installedIds, isInstalled } from './local';

const STORE_DIR = __dirname;
const PACKS = join(__dirname, 'packs');
const strip = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const src = (path: string) => strip(readFileSync(path, 'utf8'));

/** Every pack source under the store, recursively. */
const packSources = (dir = PACKS): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return packSources(p);
    return /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) ? [p] : [];
  });

describe('the catalogue is the packs, plus a stamp', () => {
  it('every pack on every axis has exactly one row', () => {
    const expected = PACK_AXES.flatMap((a) => a.packs.map((p) => `${a.axis}/${p.id}`)).sort();
    expect(STORE.map((r) => `${r.axis}/${r.id}`).sort()).toEqual(expected);
    expect(expected.length, 'the store is empty — nothing to keep').toBeGreaterThan(10);
  });

  it('the axes are the catalogue order', () => {
    expect([...STORE_AXES]).toEqual(PACK_AXES.map((a) => a.axis));
  });

  it('a row carries its axis, and finds its way back', () => {
    for (const axis of STORE_AXES) {
      const ids = idsOf(axis);
      expect(ids.length, `${axis}: no rows`).toBeGreaterThan(0);
      for (const id of ids) expect(rowById(axis, id)?.axis).toBe(axis);
    }
    expect(rowsOf('not-an-axis')).toEqual([]);
    expect(rowById('theme', 'not-a-pack')).toBeUndefined();
  });
});

describe('the house says who owns what', () => {
  it('every row is stamped by the store', () => {
    expect(STORE.every((r) => r.publisher === PUBLISHER)).toBe(true);
    expect(PUBLISHER.length, 'an unstamped row belongs to nobody').toBeGreaterThan(0);
  });

  it('a pack file declares no owner of its own', () => {
    const files = packSources();
    expect(files.length, 'no pack sources found — the scan is reading nothing').toBeGreaterThan(10);
    for (const f of files) {
      expect(src(f), `${f} names its own publisher — the stamp is the store's word, not the pack's`)
        .not.toMatch(/\b(publisher|author)\s*:/);
    }
  });

  it('and that scan can fail', () => {
    expect(strip("  publisher: 'someone-else',")).toMatch(/\b(publisher|author)\s*:/);
    expect(strip("// publisher: 'someone-else',")).not.toMatch(/\b(publisher|author)\s*:/);
  });

  it('the pack contract has no room for it either', () => {
    expect(src(join(PACKS, 'meta.ts'))).not.toMatch(/\b(publisher|author)\s*[?]?:/);
  });
});

describe('local is the door, and it is a leaf', () => {
  it('everything the store lists is here today', () => {
    expect(installed().map((r) => r.id)).toEqual(STORE.map((r) => r.id));
    for (const axis of STORE_AXES) expect([...installedIds(axis)]).toEqual([...idsOf(axis)]);
  });

  it('isInstalled answers for what is offered, and refuses the rest', () => {
    const first = STORE[0];
    expect(isInstalled(first.axis, first.id)).toBe(true);
    expect(isInstalled(first.axis, 'not-a-pack')).toBe(false);
    expect(isInstalled('not-an-axis', first.id)).toBe(false);
  });

  it('reads the catalogue and nothing else — a ring through preferences boots undefined', () => {
    const code = src(join(__dirname, 'local.ts'));
    const runtime = [...code.matchAll(/^import\s+(?!type\s)[^;]*from\s+'([^']+)'/gm)].map((m) => m[1]);
    expect(runtime, `local.ts reaches past the catalogue: ${runtime.join(', ')}`).toEqual(['./index']);
  });
});

describe('the engine keeps no sources', () => {
  /** Every file under `mods/`, with the store's own subtree cut out. */
  const engineFiles = (dir: string): string[] =>
    readdirSync(dir).flatMap((f) => {
      const full = join(dir, f);
      if (full === STORE_DIR) return [];
      return statSync(full).isDirectory() ? engineFiles(full) : [full];
    });

  it('no pack lives outside the store', () => {
    const files = engineFiles(join(__dirname, '..'));
    expect(files.length, 'the scan found no engine files at all').toBeGreaterThan(20);
    const stray = files.filter((f) => f.endsWith('.css'));
    expect(stray, `a pack source sits in the engine: ${stray.join(', ')} — mods/ paints, the store keeps`)
      .toEqual([]);
  });

  it('and that scan can fail', () => {
    expect(['x/mesh.css'].filter((f) => f.endsWith('.css'))).not.toEqual([]);
  });
});
