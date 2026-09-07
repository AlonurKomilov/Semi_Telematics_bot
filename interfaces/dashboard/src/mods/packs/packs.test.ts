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
import { THEME_PACKS } from './theme';
import { FONT_PACKS } from './font';
import { MATERIAL_PACKS } from './material';
import { MODS as MOD_PACKS } from './mods';
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

const ENGINE_FILES = ['sound/engine.ts', 'sound/keys.ts', 'sound/cue.ts', 'sound/useCue.ts', 'catalogue.ts'];

describe('an engine file holds no pack content', () => {
  it('finds engine files to check', () => {
    for (const f of ENGINE_FILES) expect(src(f).length, `${f} is empty`).toBeGreaterThan(100);
  });

  it('no cue table, no pack literal, no catalogue of what exists', () => {
    for (const f of ENGINE_FILES) {
      // Comments stripped: a docstring that says "MODS used to live
      // here" is not a list, and a guard that trips on prose gets its
      // pattern loosened until it trips on nothing.
      const code = src(f).replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, '');
      expect(code, `${f} carries a cue table — a pack is growing back inside the engine`)
        .not.toMatch(/\bcues:\s*\{/);
      expect(code, `${f} lists packs — the engine knows what exists again`)
        .not.toMatch(/\b(SOUND_PACKS|KEY_PACKS|THEME_PACKS|FONT_PACKS|MATERIAL_PACKS|MODS)\s*[:=]/);
      // A seed VALUE, not the `seed:` slot in the ThemePack type — the
      // contract says a pack has one; the engine must not say which.
      expect(code, `${f} carries a seed — an accent pack is growing back inside the engine`)
        .not.toMatch(/\bseed:\s*\{\s*light:\s*['"]#/);
    }
  });

  it('and the detector can fail', () => {
    // The positive control: the same regex on a fabricated engine line.
    expect("export const KEY_PACKS: readonly KeyPack[] = [").toMatch(/\b(SOUND_PACKS|KEY_PACKS)\s*[:=]/);
    expect("  cues: {").toMatch(/\bcues:\s*\{/);
  });
});

describe('a pack is a file, and the index is exactly the files', () => {
  for (const [folder, packs] of [['sound', SOUND_PACKS], ['keys', KEY_PACKS], ['mods', MOD_PACKS]] as const) {
    it(`${folder}: every file is listed and every entry has a file`, () => {
      const files = packFiles(folder);
      const ids = [...packs].map((p) => p.id).sort();
      expect(files.length, `no pack files in packs/${folder}`).toBeGreaterThan(0);
      expect(ids, `packs/${folder}: the index and the folder disagree`).toEqual(files);
    });
  }
});

describe('a pack file imports only types', () => {
  const onlyTypes = (rel: string) => {
    const code = src(`packs/${rel}`);
    const imports = [...code.matchAll(/^import\s+(?!type\s)[^;]*;/gm)].map((m) => m[0]);
    expect(imports, `packs/${rel} has a runtime import: ${imports[0] ?? ''}`).toEqual([]);
  };
  for (const folder of ['sound', 'keys', 'mods'] as const) {
    it(`${folder}: no runtime import — the registry would be one hop from itself`, () => {
      for (const f of packFiles(folder)) onlyTypes(`${folder}/${f}.ts`);
    });
  }
  // Theme and font packs are CSS; the list beside them is the one TS
  // file, and it is held to the same rule.
  for (const folder of ['theme', 'font'] as const) {
    it(`${folder}: the index imports only the contract`, () => onlyTypes(`${folder}/index.ts`));
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
  // Blue and Geist are the BASE — their values are `:root` / `.dark`
  // themselves, so a file for either would restate the engine's own
  // defaults under a stamp nothing needs.
  ['accent', THEME_PACKS, 'blue', 'theme'],
  ['font', FONT_PACKS, 'geist'],
  ['material', MATERIAL_PACKS, 'solid'],
] as const;

describe('a CSS pack is a file, and the index is exactly the files', () => {
  for (const [axis, packs, dflt, folderName] of CSS_AXES) {
    const folder = folderName ?? axis;
    it(`${axis}: every non-default id has a file, and every file an id`, () => {
      const files = packFiles(folder, 'css');
      const ids = packs.map((p) => p.id).filter((id) => id !== dflt).sort();
      expect(files.length, `no pack files in packs/${folder}`).toBeGreaterThan(0);
      expect(ids, `packs/${folder}: the index and the folder disagree`).toEqual(files);
      expect(packs.some((p) => p.id === dflt), `${axis} lost its default "${dflt}"`).toBe(true);
    });

    it(`${axis}: every pack file addresses only itself, and is screen-only where it must be`, () => {
      for (const id of packFiles(folder, 'css')) {
        const code = src(`packs/${folder}/${id}.css`);
        // Wallpaper, cursor and shader are screen-only: print puts the
        // light palette back through specificity alone, and a pattern
        // or a pointer that survived into paper would be ink saying
        // nothing. An accent or a face is NOT — a printed page keeps
        // its accent and its typeface — and those blocks came out of
        // `@layer base`, not the screen block.
        if (['wallpaper', 'cursor', 'shader', 'material'].includes(axis))
          expect(code.trim(), `packs/${folder}/${id}.css is not wrapped in @media screen`)
            .toMatch(/^@media screen\s*\{[\s\S]*\}\s*$/);
        // One file, one pack: a rule for a sibling in here is the old
        // shared block reassembling itself.
        for (const m of code.matchAll(new RegExp(`\\[data-${axis}="([^"]+)"\\]`, 'g')))
          expect(m[1], `packs/${folder}/${id}.css addresses "${m[1]}"`).toBe(id);
        expect(code, `packs/${folder}/${id}.css reaches for !important`).not.toMatch(/!important/);
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
    for (const [axis, , , folderName] of CSS_AXES) {
      const folder = folderName ?? axis;
      for (const id of packFiles(folder, 'css'))
        expect(engine, `packs/${folder}/${id}.css exists and is never imported — a pack nobody can wear`)
          .toContain(`@import './mods/packs/${folder}/${id}.css';`);
    }
  });
});

/**
 * The one piece of pack data still held by the engine sheet, by name.
 *
 * `--swatch-accent-*` — the colour the picker paints a chip dot from —
 * is declared in `:root`, `.dark` and the print block, because print
 * restates every light token and a swatch moved into a pack file would
 * either be restated in three places or vanish from paper. It stays,
 * and `catalogue.test.ts` holds each swatch to its pack's hue. Named
 * here so it is a known seam and not a forgotten one; the honest end
 * state is a dot painted from the seed itself, with no token at all.
 */
describe('the known seam', () => {
  it('the engine sheet still carries the accent swatches, and nothing else of a pack', () => {
    const engine = engineCss().replace(/\/\*[\s\S]*?\*\//g, '');
    // Three declarations, by name: `:root`, `.dark`, and the print
    // reset — one of them gone and paper or dark loses the dot.
    for (const p of THEME_PACKS)
      expect(engine.match(new RegExp(`--swatch-accent-${p.id}:`, 'g'))?.length,
        `--swatch-accent-${p.id} is not in all three engine root blocks — update this seam note`)
        .toBe(3);
  });
});
