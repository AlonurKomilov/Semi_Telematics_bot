/**
 * A pack's CSS has to be what its seed produces.
 *
 * This is the link that was missing. `themeBoot.test.ts` already keeps
 * the boot script's hand-written accent list in step with the registry,
 * and chrome.test.ts asserts the array and demands a CSS block per
 * accent per mode. Nothing checked that the VALUES in those blocks bear
 * any relation to anything — they were six hand-tuned cells, and a
 * seventh could have been any colour at all.
 *
 * With the catalogue, a pack is a name and a seed, and everything else
 * is `derivePalette`. So the CSS becomes generated output that happens
 * to be committed, and this guard is what makes "committed" safe: paste
 * a value the derivation would not produce and the suite says so.
 *
 * Why committed at all, rather than applied at runtime: because it keeps
 * every other colour guard alive. `colour.test.ts` and the twelve chrome
 * guards read index.css off disk. A pack that exists only at runtime is
 * invisible to all of them.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { PANEL_SECTIONS as MOD_SECTIONS } from './taxonomy';
import { TAXONOMY } from './taxonomy';
import { join } from 'node:path';
import {
  THEME_PACKS, MODS, MOD_ICONS, ICON_STROKE,
  MOD_FIELD_SECTION, modFootprint,
  PACK_TOKENS, packById, modById, activeModId, modMatchesAxes,
} from './catalogue';
import { SIZE_MAX, MOD_RADII } from '../preferences/registry';
import { derivePalette } from './theme/palette';
import { oklchToSrgb, parseHex, toHex, type RGB } from './theme/contrast';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/** `:root` is several blocks in this file; merge in source order. */
function body(selector: string): string {
  let out = '';
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    if (m[1].trim().replace(/\s+/g, ' ') === selector) out += m[2];
  return out;
}

function token(src: string, name: string): RGB | null {
  const m = new RegExp(`${name}:\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s*\\)`).exec(src);
  return m ? oklchToSrgb(+m[1], +m[2], +m[3]).rgb : null;
}

const dE = (a: RGB, b: RGB) => {
  const lab = (rgb: RGB): [number, number, number] => {
    const inv = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const [r, g, bl] = rgb.map(inv);
    const X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * bl;
    const Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * bl;
    const Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * bl;
    const f = (t: number) => (t > (6 / 29) ** 3 ? Math.cbrt(t) : t / (3 * (6 / 29) ** 2) + 4 / 29);
    const [fx, fy, fz] = [f(X / 0.95047), f(Y), f(Z / 1.08883)];
    return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
  };
  const [L1, a1, b1] = lab(a), [L2, a2, b2] = lab(b);
  return Math.hypot(L1 - L2, a1 - a2, b1 - b2);
};

/**
 * Where a pack's tokens live. The FIRST pack is the base — its values
 * are in `:root` / `.dark` themselves, with no attribute block, because
 * it is what an unstamped document paints.
 *
 * The stand-down guard is part of the ADDRESS, not noise tolerated by a
 * looser match. A pack block written without it cannot be overridden by
 * an authored colour — the custom accent would install and lose, which
 * is the exact defect `accentCascade.test.ts` exists for — and the way
 * that fails here is the block simply not being found.
 */
const STAND_DOWN = ':not([data-mod-accent])';
const cellFor = (packIndex: number, id: string, mode: 'light' | 'dark') =>
  packIndex === 0
    ? (mode === 'light' ? ':root' : '.dark')
    : (mode === 'light'
        ? `:root:not(.dark)[data-accent="${id}"]${STAND_DOWN}`
        : `.dark[data-accent="${id}"]${STAND_DOWN}`);

const CANVAS = {
  light: token(body(':root'), '--background')!,
  dark: token(body('.dark'), '--background')!,
};

describe('every pack is its own seed', () => {
  it('finds the canvases it measures against', () => {
    // If this fails everything below is comparing against undefined and
    // would otherwise pass by measuring nothing.
    expect(CANVAS.light, ':root has no --background').not.toBeNull();
    expect(CANVAS.dark, '.dark has no --background').not.toBeNull();
    expect(THEME_PACKS.length).toBeGreaterThan(0);
  });

  for (const [i, pack] of THEME_PACKS.entries()) {
    for (const mode of ['light', 'dark'] as const) {
      it(`${pack.id} / ${mode} matches derivePalette(${pack.seed[mode]})`, () => {
        const cell = cellFor(i, pack.id, mode);
        const src = body(cell);
        expect(src, `no CSS block for ${cell} — a pack without a block paints the base`)
          .not.toBe('');

        const pal = derivePalette({ mode, canvas: toHex(CANVAS[mode]), brand: pack.seed[mode] })!;
        expect(pal, `${pack.id}: seed ${pack.seed[mode]} did not parse`).not.toBeNull();

        for (const name of PACK_TOKENS) {
          const shipped = token(src, name);
          expect(shipped, `${cell} does not declare ${name}`).not.toBeNull();
          const d = dE(shipped!, parseHex(pal[name])!);
          // 3 is above the worst measured (2.0, light green's
          // --primary-text) and well below anything a person reads as a
          // different colour. It is a drift bound, not a licence: a
          // value that needs more than this is not the seed's value.
          expect(
            d,
            `${cell} ${name}: ships ${toHex(shipped!)}, seed gives ${pal[name]} (ΔE ${d.toFixed(1)})`,
          ).toBeLessThan(3);
        }
      });
    }
  }
});

describe('the catalogue and the stylesheet agree', () => {
  it('has no accent block that no pack claims', () => {
    // The other direction: a pack deleted from the list but left in the
    // CSS keeps painting for anyone whose stored value still names it.
    const orphans: string[] = [];
    for (const m of CSS.matchAll(/\[data-accent="([^"]+)"\]/g))
      if (!packById(m[1])) orphans.push(m[1]);
    expect([...new Set(orphans)], 'CSS blocks for packs that no longer exist').toEqual([]);
  });

  it('gives every pack a swatch in both modes', () => {
    // The picker paints its dots from these, and a pack with no swatch
    // is a blank chip — the kind of miss that used to come with adding
    // an accent by hand.
    for (const pack of THEME_PACKS) {
      const re = new RegExp(`--swatch-accent-${pack.id}\\s*:`, 'g');
      const count = (CSS.match(re) || []).length;
      expect(count, `--swatch-accent-${pack.id} appears ${count} times, expected one per theme`)
        .toBeGreaterThanOrEqual(2);
    }
  });

  it('keeps ids usable as an attribute value and a CSS name', () => {
    for (const pack of THEME_PACKS) {
      expect(pack.id, `${pack.id} is not a safe custom-property suffix`).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(pack.label.trim(), `${pack.id} has no label`).not.toBe('');
    }
    expect(new Set(THEME_PACKS.map((p) => p.id)).size, 'duplicate pack id').toBe(THEME_PACKS.length);
  });
});

describe('mods are combinations, not new colours', () => {
  it('wears a colour pack that exists', () => {
    // A mod naming an accent with no CSS block does not fail loudly — the
    // attribute is stamped, no rule matches, and the app silently paints
    // the base blue. That is the whole failure: it looks like the mod
    // simply has no colour of its own.
    for (const m of MODS)
      expect(packById(m.accent), `mod "${m.id}" wears "${m.accent}", which is not a pack`)
        .toBeDefined();
  });

  it('declares at least one axis a colour chip does not', () => {
    // Otherwise it is a second way to press the same button, in a section
    // that promises something more.
    for (const m of MODS)
      expect(m.radius !== undefined || m.size !== undefined,
        `mod "${m.id}" sets only an accent — that is a colour, not a look`).toBe(true);
  });

  it('stays inside what the panel controls can express', () => {
    for (const m of MODS) {
      if (m.radius !== undefined)
        expect(MOD_RADII, `mod "${m.id}" radius`).toContain(m.radius);
      if (m.size === undefined) continue;
      // Floor is 1, not SIZE_MIN. The slider deliberately starts at 100%
      // — everything below waits on the 24px hit-target floor — so a mod
      // that set 0.9 would put the app somewhere the control cannot
      // bring it back from, and the person would have to know to reset.
      expect(m.size, `mod "${m.id}" is below what the Size slider offers`).toBeGreaterThanOrEqual(1);
      expect(m.size, `mod "${m.id}" exceeds SIZE_MAX`).toBeLessThanOrEqual(SIZE_MAX);
    }
  });

  it('never sets the mode', () => {
    // Dark or light is about the room, not the look. Asserted structurally
    // so it cannot be added back by someone who reads the field list and
    // not the reason beside it.
    for (const m of MODS)
      expect(Object.keys(m), `mod "${m.id}" carries a mode`).not.toContain('mode');
  });

  it('has ids that are unique and do not shadow a pack', () => {
    const ids = MODS.map((m) => m.id);
    expect(new Set(ids).size, 'duplicate mod id').toBe(ids.length);
    for (const m of MODS) {
      expect(packById(m.id), `"${m.id}" is both a mod and a pack`).toBeUndefined();
      expect(m.id).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(m.label.trim(), `mod "${m.id}" has no label`).not.toBe('');
      // The one line the panel shows under the applied mod. Without it
      // the chip is a word with no explanation of who it is for.
      expect(m.why.trim(), `mod "${m.id}" has no "why"`).not.toBe('');
      expect(modById(m.id)).toBe(m);
    }
  });
});

describe('a look is on only while it adds up', () => {
  const cab = modById('cab')!;

  it('recognises its own axes', () => {
    expect(activeModId({ accent: cab.accent, radius: cab.radius!, size: cab.size!, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('cab');
  });

  it('goes quiet the moment any axis is tweaked', () => {
    // The behaviour that means there is no "modified" state to store:
    // change a corner and the chip un-highlights by itself.
    expect(activeModId({ accent: cab.accent, radius: 'sharp', size: cab.size!, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('');
    expect(activeModId({ accent: cab.accent, radius: cab.radius!, size: 1, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('');
    expect(activeModId({ accent: 'blue', radius: cab.radius!, size: cab.size!, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).not.toBe('cab');
  });

  it('survives a float round-trip', () => {
    // The size comes back from a slider and from stored JSON; `=== 1.25`
    // is a coin toss on a value that has been through both.
    expect(activeModId({ accent: cab.accent, radius: cab.radius!, size: cab.size! + 1e-9, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('cab');
    expect(activeModId({ accent: cab.accent, radius: cab.radius!, size: cab.size! + 0.01, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('');
  });

  it('answers empty when the axes match nothing', () => {
    expect(activeModId({ accent: 'blue', radius: 'rounded', size: 1, material: 'solid', motion: 'default', icons: cab.icons!, sound: cab.sound! })).toBe('');
  });
});

describe('the properties a mod carries and the panel does not', () => {
  it('names a stroke width for every icon setting', () => {
    // A missing entry falls back to `regular`, silently — the mod would
    // apply and the icons would not change, which reads as the feature
    // not working rather than as a typo.
    for (const w of MOD_ICONS) {
      expect(ICON_STROKE[w], `no stroke width for "${w}"`).toBeGreaterThan(0);
      expect(ICON_STROKE[w]).toBeLessThan(4);
    }
    expect(ICON_STROKE.regular, "regular must be lucide's own default").toBe(2);
  });

  it('reads the installed mod rather than recomputing it', () => {
    // The regression this whole change exists to prevent: going back to
    // deriving identity from the axes would work perfectly until a mod
    // carried a sound pack, and then editing a corner would silence it.
    const panel = readFileSync(join(__dirname, 'panel', 'ModsRow.tsx'), 'utf8');
    // `.not.toContain` succeeds on an empty string, so a file that moved
    // or emptied would satisfy the negative assertion below while proving
    // nothing. Read something before asserting the absence of something.
    expect(panel.length, 'read an empty ModPanel.tsx').toBeGreaterThan(1000);
    expect(panel, 'the panel is deriving mod identity again').not.toContain('activeModId(');
    // A WORD BOUNDARY, not a substring. `theme.mod` is a prefix of
    // `theme.mode`, and the panel is full of the latter — so
    // `toContain('theme.mod')` was satisfied by the colour mode and
    // could never have failed. It reads the real axis only because the
    // split happened to leave `theme.mode` in another file; this makes
    // that independent of where the code lives.
    expect(panel, 'the panel does not read the stored mod')
      .toMatch(/theme\.mod\b(?!e)/);
  });

  // "keeps the mod-only axes out of the panel" LIVED HERE and was deleted
  // deliberately, which is what its own comment asked for. Its regex
  // required the axis to be the first key after the brace; the real write
  // site is `setTheme({ mod: m.id, accent: …, …icons })`, so it could
  // never match anything and passed on an impossibility for its whole
  // life. Broadening it does not work either — applyMod writes both axes
  // legitimately. The rule is behavioural, so the guard is now behavioural:
  // mods/modControls.test.tsx, "only a mod may reach a mod-only axis".

  it('counts them when deciding whether a mod is on', () => {
    const cab = modById('cab')!;
    const base = {
      accent: cab.accent, radius: cab.radius!, size: cab.size!,
      material: 'solid', motion: 'default', icons: cab.icons!,
      sound: cab.sound!,
    };
    expect(activeModId(base)).toBe('cab');
    // Change the one axis the panel cannot reach, and the mod is off.
    expect(activeModId({ ...base, icons: 'hairline', sound: cab.sound! })).toBe('');
  });
});

describe('installed is not the same question as matching', () => {
  const cab = modById('cab')!;
  const axesOf = (m: typeof cab) => ({
    accent: m.accent, radius: m.radius!, size: m.size!,
    material: 'solid', motion: 'default', icons: m.icons!,
    sound: m.sound ?? 'chime',
  });

  it('matches when every axis it declares still agrees', () => {
    expect(modMatchesAxes(cab, axesOf(cab))).toBe(true);
  });

  it('stops matching the moment a DECLARED axis is edited', () => {
    // Derived from the mod rather than listed, because listing them got
    // it wrong on the first run: Cab says nothing about material, so
    // editing material is not editing Cab — it is answering a question
    // Cab left open. Only what a mod declares can be departed from.
    const OTHER: Record<string, unknown> = {
      accent: 'blue', radius: 'sharp', size: 1,
      material: 'glass', motion: 'snappy', icons: 'hairline', sound: 'chime',
    };
    for (const m of MODS) {
      const base = {
        accent: m.accent, radius: m.radius ?? 'rounded', size: m.size ?? 1,
        material: m.material ?? 'solid', motion: m.motion ?? 'default',
        icons: m.icons ?? 'regular', sound: m.sound ?? 'chime',
      };
      expect(modMatchesAxes(m, base), `${m.id} does not match its own axes`).toBe(true);
      for (const k of ['accent', 'radius', 'size', 'material', 'motion', 'icons', 'sound'] as const) {
        if (m[k as keyof typeof m] === undefined) continue;
        const other = k === 'accent' && m.accent === 'blue' ? 'green' : OTHER[k];
        expect(modMatchesAxes(m, { ...base, [k]: other }),
          `${m.id}: editing ${k} should stop the match`).toBe(false);
      }
    }
  });

  it('ignores an axis the mod does not declare', () => {
    // Wall says nothing about material, so a person choosing glass has
    // not edited Wall — they have made a choice Wall left to them.
    const wall = modById('wall')!;
    expect(wall.material).toBeUndefined();
    expect(modMatchesAxes(wall, { ...axesOf(wall), material: 'glass' })).toBe(true);
  });

  it('does not confuse building a look by hand with installing it', () => {
    // The distinction the split exists for. Someone can set every axis
    // Cab sets without ever tapping Cab — matching says yes, and that
    // must not mean Cab is installed, because a mod that carries sounds
    // would then be playing them uninvited.
    expect(modMatchesAxes(cab, axesOf(cab))).toBe(true);
    // Identity lives in the stored value, which this function cannot see
    // and deliberately does not take.
    expect(modMatchesAxes.length, 'modMatchesAxes must not be given the stored id').toBe(2);
  });
});

/**
 * What a mod carries, said in the words the card uses.
 *
 * A mod can set up to eight things and, before this, said none of them —
 * "what will Wall change?" had no answer short of reading the source.
 * GX answers it with a checklist on the installed mod; this is the same
 * answer in our vocabulary.
 *
 * The totality of MOD_FIELD_SECTION is enforced by the COMPILER, not
 * here: it is typed as a Record over `keyof Omit<Mod, 'id'|'label'|'why'>`,
 * so adding a field to Mod fails the build until someone places it. A
 * runtime test for that would be weaker and later.
 */
describe('a mod says what it carries', () => {
  it('files every field under exactly one item of the taxonomy', () => {
    // MOD_FIELD_SECTION is DERIVED from the taxonomy now, so "every
    // value is a real category" is true by construction and not worth
    // asserting. What the `as Record<ModField, …>` cast in taxonomy.ts
    // hides is the opposite drift: an item naming a `modField` that Mod
    // does not have, or two items claiming the same one — the second
    // silently overwrites the first in Object.fromEntries.
    const claimed = TAXONOMY.flatMap((c) => c.items.flatMap((i) => (i.modField ? [i.modField] : [])));
    const dupes = claimed.filter((f, i) => claimed.indexOf(f) !== i);
    expect(dupes, 'a Mod field is claimed by two items').toEqual([]);

    const fields = Object.keys(MOD_FIELD_SECTION).sort();
    expect([...claimed].sort(), 'the taxonomy claims a field Mod does not have, or misses one')
      .toEqual(fields);
    // …and the map really is total over Mod: the sample mod below
    // carries every field the type allows, so a field missing from the
    // map would be a footprint that silently drops it.
    expect(fields.length).toBeGreaterThan(6);
  });

  it('never files a field under the container row', () => {
    // `mods` is the container's own chip row, not somewhere a mod writes.
    expect(Object.values(MOD_FIELD_SECTION)).not.toContain('mods');
  });

  it.each(MODS.map((m) => [m.id, m] as const))('%s carries something', (_id, m) => {
    // A mod that touched nothing would render an empty footprint and
    // read as "this changes nothing", which no shipped mod is.
    expect(modFootprint(m).length).toBeGreaterThan(0);
  });

  it('reports the two shipped mods exactly', () => {
    // Concrete, because a purely structural assertion would pass on a
    // footprint that returned every section for every mod.
    expect(modFootprint(modById('cab')!)).toEqual(['interface', 'sounds', 'size']);
    expect(modFootprint(modById('wall')!)).toEqual(['interface', 'effects', 'size']);
  });

  it('leaves out what a mod does not set', () => {
    // Wall carries no sound and Cab no entrance; a footprint that listed
    // every section regardless would be worse than none.
    expect(modFootprint(modById('wall')!)).not.toContain('sounds');
    expect(modFootprint(modById('cab')!)).not.toContain('effects');
  });
});

