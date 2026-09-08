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
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { SOUND_PACKS } from './sound';
import { KEY_PACKS } from './keys';
import { WALLPAPERS } from './wallpaper';
import { CURSOR_PACKS } from './cursor';
import { SHADER_PACKS } from './shader';
import { THEME_PACKS } from './theme';
import { FONT_PACKS } from './font';
import { MATERIAL_PACKS } from './material';
import { ICON_PACK_IDS } from './icons';
import { MODS as MOD_PACKS } from './mods';
import { PACK_AXES } from './index';
import type { PackMeta } from './meta';
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
        // Either attribute the axis stamps — the wallpaper axis has two,
        // one per ground — must name this pack and no other.
        for (const m of code.matchAll(new RegExp(`\\[data-${axis}(?:-page)?="([^"]+)"\\]`, 'g')))
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
 * Icons: a pack is THREE files — `<id>.tsx` (the provider), `<id>.icons.ts`
 * (the glyphs under our names), `<id>.weights.ts` (how it takes a
 * weight) — flat rather than a folder each so the fetched pack's chunk
 * keeps the pack's name. The index is exactly the packs, and a pack
 * file imports only its library, React, its own siblings, and types.
 * `iconLane.test.ts` holds the rest: every pack carries every name, no
 * library import outside the packs and the door, the door names none.
 */
describe('icons: a pack is three files, and the index is exactly the packs', () => {
  const files = readdirSync(join(__dirname, 'icons')).filter((f) => !/^index\.ts$/.test(f)).sort();
  const PARTS = ['.tsx', '.icons.ts', '.weights.ts'];

  it('every pack has its three files, and every file belongs to a listed pack', () => {
    expect(ICON_PACK_IDS.length, 'no icon packs').toBeGreaterThan(0);
    const expected = ICON_PACK_IDS.flatMap((id) => PARTS.map((p) => `${id}${p}`)).sort();
    expect(files, 'packs/icons: the index and the folder disagree').toEqual(expected);
  });

  it('a pack file imports only its library, React, its siblings, and types', () => {
    for (const f of files) {
      const imports = [...src(`packs/icons/${f}`).matchAll(/^(?:import|export)\s+(?!type\s)[^;]*?from\s+'([^']+)';/gm)]
        .map((m) => m[1]);
      for (const from of imports)
        expect(from, `packs/icons/${f} imports ${from}`)
          .toMatch(/^(react|lucide-react|@phosphor-icons\/react|\.\/)/);
    }
  });

  it('the index imports only the contract by type and its own packs', () => {
    const imports = [...src('packs/icons/index.ts').matchAll(/^import\s+(?!type\s)[^;]*?from\s+'([^']+)';/gm)].map((m) => m[1]);
    for (const from of imports) expect(from, `packs/icons/index.ts imports ${from}`).toMatch(/^\.\//);
  });
});

/**
 * Every pack on every axis carries the same three things a person reads
 * before choosing it — `PackMeta` — and the registry that gathers the
 * axes is exactly the folders beside it, so a new axis cannot ship
 * outside the list a store would read.
 */
const metaFaults = (axis: string, packs: readonly PackMeta[]): string[] => {
  const faults: string[] = [];
  const seen = new Set<string>();
  for (const p of packs) {
    const at = `${axis}/${p.id}`;
    if (!/^[a-z][a-z0-9-]*$/.test(p.id)) faults.push(`${at}: id is not a safe stored value`);
    if (seen.has(p.id)) faults.push(`${at}: duplicate id`);
    seen.add(p.id);
    if (!p.label?.trim()) faults.push(`${at}: no label`);
    else if (p.label.trim() === p.id) faults.push(`${at}: label just repeats the id — name it for a person`);
    const d = (p.description ?? '').trim();
    if (!d) faults.push(`${at}: no description`);
    else if (d.length < 10) faults.push(`${at}: description "${d}" says nothing`);
    else if (d.length > 120) faults.push(`${at}: description is ${d.length} chars — a line, not a paragraph`);
  }
  return faults;
};

describe('every pack on every axis carries its meta', () => {
  it('the registry is exactly the axis folders', () => {
    const folders = readdirSync(__dirname)
      .filter((f) => statSync(join(__dirname, f)).isDirectory()).sort();
    expect(folders.length, 'no axis folders').toBeGreaterThan(5);
    expect(PACK_AXES.map((a) => a.axis).sort(), 'packs/index.ts and the folders disagree').toEqual(folders);
  });

  it('id, label, description — present, safe, one line', () => {
    let checked = 0;
    for (const { axis, packs } of PACK_AXES) {
      expect(packs.length, `${axis} lists no packs`).toBeGreaterThan(0);
      checked += packs.length;
      expect(metaFaults(axis, packs)).toEqual([]);
    }
    expect(checked, 'fewer packs than the folders hold — the sweep skipped some').toBeGreaterThan(25);
  });

  it('and the sweep can fail', () => {
    expect(metaFaults('x', [{ id: 'ok', label: 'Ok', description: 'A description long enough to pass' }])).toEqual([]);
    expect(metaFaults('x', [
      { id: 'Bad Id', label: '', description: '' },
      { id: 'dup', label: 'dup', description: 'short' },
      { id: 'dup', label: 'Dup', description: 'x'.repeat(121) },
    ])).toEqual([
      'x/Bad Id: id is not a safe stored value',
      'x/Bad Id: no label',
      'x/Bad Id: no description',
      'x/dup: label just repeats the id — name it for a person',
      'x/dup: description "short" says nothing',
      'x/dup: duplicate id',
      'x/dup: description is 121 chars — a line, not a paragraph',
    ]);
  });
});
