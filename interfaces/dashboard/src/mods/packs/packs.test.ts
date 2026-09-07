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
import { WALLPAPERS } from './wallpaper';
import { CURSOR_PACKS } from './cursor';
import { SHADER_PACKS } from './shader';
import { engineCss } from '../../test/stylesheet';
import { isCueWithin, CUE_LIMITS, CUE_NAMES } from '../sound/engine';
import { KEY_LIMITS, KEY_CLASSES } from '../sound/keys';

const MODS = join(__dirname, '..');
const src = (rel: string) =>
  readFileSync(join(MODS, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/** The files in a pack folder that ARE packs — everything but the
 *  index and the tests. `ext` is what a pack of that axis is made of:
 *  a cue table is TypeScript, a pattern is CSS. */
const packFiles = (folder: string, ext: 'ts' | 'css' = 'ts') =>
  readdirSync(join(__dirname, folder))
    .filter((f) => f.endsWith(`.${ext}`) && !/^index\.ts$|\.test\.ts$/.test(f))
    .map((f) => f.replace(new RegExp(`\\.${ext}$`), ''))
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

/**
 * The CSS-backed axes — wallpaper, cursor, shader — keep the same
 * shape with a different material: a pack is a `.css` file, the index
 * lists the ids, and the DEFAULT of each axis has no file on purpose.
 * `none`, `system` and `flat` are the absence of a rule — flat chrome,
 * the OS pointer, the shipped light — and a file for them would have to
 * out-rank whatever a later pack declares, for no gain.
 */
const CSS_AXES = [
  ['wallpaper', WALLPAPERS, 'none'],
  ['cursor', CURSOR_PACKS, 'system'],
  ['shader', SHADER_PACKS, 'flat'],
] as const;

describe('a CSS pack is a file, and the index is exactly the files', () => {
  for (const [axis, packs, dflt] of CSS_AXES) {
    it(`${axis}: every non-default id has a file, and every file an id`, () => {
      const files = packFiles(axis, 'css');
      const ids = packs.map((p) => p.id).filter((id) => id !== dflt).sort();
      expect(files.length, `no pack files in packs/${axis}`).toBeGreaterThan(0);
      expect(ids, `packs/${axis}: the index and the folder disagree`).toEqual(files);
      expect(packs.some((p) => p.id === dflt), `${axis} lost its default "${dflt}"`).toBe(true);
    });

    it(`${axis}: every pack file is screen-only and addresses only itself`, () => {
      for (const id of packFiles(axis, 'css')) {
        const code = src(`packs/${axis}/${id}.css`);
        // Print puts the light palette back through specificity alone;
        // a pattern or a pointer that survived into paper would be ink
        // saying nothing. The wrapper used to be index.css's; a pack
        // that leaves it carries it.
        expect(code.trim(), `packs/${axis}/${id}.css is not wrapped in @media screen`)
          .toMatch(/^@media screen\s*\{[\s\S]*\}\s*$/);
        // One file, one pack: a rule for a sibling in here is the old
        // shared block reassembling itself.
        for (const m of code.matchAll(new RegExp(`\\[data-${axis}="([^"]+)"\\]`, 'g')))
          expect(m[1], `packs/${axis}/${id}.css addresses "${m[1]}"`).toBe(id);
        expect(code, `packs/${axis}/${id}.css reaches for !important`).not.toMatch(/!important/);
      }
    });
  }

  it('and the engine sheet carries none of them', () => {
    // index.css keeps the MECHANISM — the pane that steps aside, the
    // card rung of the light — and may name the axis for that. It may
    // not name a pack. `[data-wallpaper="none"]` is allowed: it is the
    // mechanism referring to the absence of one.
    const engine = engineCss().replace(/\/\*[\s\S]*?\*\//g, '');
    for (const [axis, packs, dflt] of CSS_AXES)
      for (const p of packs) {
        if (p.id === dflt) continue;
        expect(engine, `index.css still carries [data-${axis}="${p.id}"] — a pack moved back in`)
          .not.toContain(`[data-${axis}="${p.id}"]`);
      }
  });

  it('and the engine sheet imports every pack file', () => {
    const engine = engineCss();
    for (const [axis] of CSS_AXES)
      for (const id of packFiles(axis, 'css'))
        expect(engine, `packs/${axis}/${id}.css exists and is never imported — a pack nobody can wear`)
          .toContain(`@import './mods/packs/${axis}/${id}.css';`);
  });
});
