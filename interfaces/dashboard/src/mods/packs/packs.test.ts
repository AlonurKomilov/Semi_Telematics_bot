/**
 * Engine and resource stay two things.
 *
 * GX's shape: the engine is Opera's, a mod is a package the engine
 * consumes by contract, and the person writing a pack never opens the
 * engine. Ours was not that. `KEY_PACKS` sat in `keys.ts` beside
 * `isSensitiveTarget` — so somebody tuning the Soft click edited the
 * file that keeps a password field silent — and `SOUND_PACKS` sat in
 * `engine.ts` beside the code that unlocks audio.
 *
 * Three rules, each with the mutation that motivated it:
 *
 *   1. An ENGINE file holds no pack content. A cue table in `keys.ts`
 *      is the old shape coming back one convenient edit at a time.
 *   2. A PACK is a file, and the index lists exactly the files. A file
 *      not listed is a pack nobody can choose; a listed name with no
 *      file is a string that resolves to nothing.
 *   3. A pack file imports only TYPES. `preferences/registry.ts`
 *      imports the pack indexes to sanitise stored ids, so a runtime
 *      import in a pack file is a ring back to the registry — the same
 *      ring `keys.ts` used to guard by being a leaf. The leaf moved;
 *      the rule moved with it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { SOUND_PACKS } from './sound';
import { KEY_PACKS } from './keys';
import { isCueWithin, CUE_LIMITS, CUE_NAMES } from '../sound/engine';
import { KEY_LIMITS, KEY_CLASSES } from '../sound/keys';

const MODS = join(__dirname, '..');
const src = (rel: string) =>
  readFileSync(join(MODS, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/** The files in a pack folder that ARE packs — everything but the
 *  index and the tests. */
const packFiles = (folder: string) =>
  readdirSync(join(__dirname, folder))
    .filter((f) => /\.ts$/.test(f) && !/^index\.ts$|\.test\.ts$/.test(f))
    .map((f) => f.replace(/\.ts$/, ''))
    .sort();

const ENGINE_FILES = ['sound/engine.ts', 'sound/keys.ts', 'sound/cue.ts', 'sound/useCue.ts'];

describe('an engine file holds no pack content', () => {
  it('finds engine files to check', () => {
    for (const f of ENGINE_FILES) expect(src(f).length, `${f} is empty`).toBeGreaterThan(100);
  });

  it('no cue table, no pack literal, no catalogue of what exists', () => {
    for (const f of ENGINE_FILES) {
      const code = src(f);
      expect(code, `${f} carries a cue table — a pack is growing back inside the engine`)
        .not.toMatch(/\bcues:\s*\{/);
      expect(code, `${f} lists packs — the engine knows what exists again`)
        .not.toMatch(/\b(SOUND_PACKS|KEY_PACKS)\s*[:=]/);
    }
  });

  it('and the detector can fail', () => {
    // The positive control: the same regex on a fabricated engine line.
    expect("export const KEY_PACKS: readonly KeyPack[] = [").toMatch(/\b(SOUND_PACKS|KEY_PACKS)\s*[:=]/);
    expect("  cues: {").toMatch(/\bcues:\s*\{/);
  });
});

describe('a pack is a file, and the index is exactly the files', () => {
  for (const [folder, packs] of [['sound', SOUND_PACKS], ['keys', KEY_PACKS]] as const) {
    it(`${folder}: every file is listed and every entry has a file`, () => {
      const files = packFiles(folder);
      const ids = [...packs].map((p) => p.id).sort();
      expect(files.length, `no pack files in packs/${folder}`).toBeGreaterThan(0);
      expect(ids, `packs/${folder}: the index and the folder disagree`).toEqual(files);
    });
  }
});

describe('a pack file imports only types', () => {
  for (const folder of ['sound', 'keys'] as const) {
    it(`${folder}: no runtime import — the registry would be one hop from itself`, () => {
      for (const f of packFiles(folder)) {
        const code = src(`packs/${folder}/${f}.ts`);
        const imports = [...code.matchAll(/^import\s+(?!type\s)[^;]*;/gm)].map((m) => m[0]);
        expect(imports, `packs/${folder}/${f}.ts has a runtime import: ${imports[0] ?? ''}`)
          .toEqual([]);
      }
    });
  }
});

describe('every pack keeps the engine\'s contract', () => {
  // The rule that lived in engine.test.ts / keys.test.ts while the packs
  // did; it moves with them. A missing cue falls back to silence, which
  // reads as the feature being broken rather than as a typo.
  it('every sound pack answers every cue, inside the bounds', () => {
    for (const p of SOUND_PACKS)
      for (const name of CUE_NAMES)
        expect(isCueWithin(p.cues[name], CUE_LIMITS), `${p.id}.${name}`).toBe(true);
  });

  it('every key pack answers every class, inside the keyboard band', () => {
    for (const p of KEY_PACKS)
      for (const cls of KEY_CLASSES)
        expect(isCueWithin(p.cues[cls], KEY_LIMITS), `${p.id}.${cls}`).toBe(true);
  });

  it('ids are unique and usable as a stored value', () => {
    for (const packs of [SOUND_PACKS, KEY_PACKS]) {
      const ids = packs.map((p) => p.id);
      expect(new Set(ids).size).toBe(ids.length);
      for (const id of ids) expect(id).toMatch(/^[a-z][a-z0-9-]*$/);
    }
  });
});
