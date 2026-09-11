/**
 * A ground is a seed, and a seeded plane is a whole plane.
 *
 * Three properties carry the feature. It CHANGES NOTHING it was not
 * asked to change: seeding a plane with the colour it already has
 * reproduces that plane. It is COMPLETE: everything inside a seeded
 * plane is re-derived from the seed, so a dark sidebar cannot keep the
 * page's pale hover fill or the page's ink. And it is GATED where the
 * page is gated — the four semantic tones appear on a card and in the
 * sidebar exactly as they appear on the page.
 */
import { describe, it, expect } from 'vitest';
import { GROUNDS, GROUND_IDS, GROUND_TOKENS, groundById, groundTokens } from './grounds';
import { deriveGround, derivePalette, patternGrounds, GROUND_PLANES, DERIVED_TOKENS } from './palette';
import { CANVAS_SEED, worstTone, paletteTokens } from './canvas';
import { parseHex, distance, contrastRatio, AA_TEXT, AA_LARGE, toHex } from './contrast';
import { THEME_PACKS } from '../store/packs/theme';
import { isModToken, isSafeValue } from '../inject';

const MODES = ['light', 'dark'] as const;
const palette = (mode: 'light' | 'dark', brand: string) =>
  derivePalette({ mode, canvas: CANVAS_SEED[mode], brand })!;

describe('what a ground owns', () => {
  it('is a family of real tokens, and no two grounds share one', () => {
    const seen = new Set<string>();
    for (const g of GROUNDS) {
      const family = GROUND_PLANES[g.id];
      expect(family.length, `${g.id} owns nothing`).toBeGreaterThan(1);
      for (const t of family) {
        expect(DERIVED_TOKENS, `${g.id} claims ${t}, which the palette does not derive`).toContain(t);
        expect(seen.has(t), `${t} is owned by two grounds`).toBe(false);
        seen.add(t);
      }
    }
    expect([...seen].sort()).toEqual([...GROUND_TOKENS].sort());
  });

  it('emits exactly the family it declares, and nothing else', () => {
    for (const mode of MODES)
      for (const id of GROUND_IDS) {
        const out = deriveGround(id, '#808080', mode)!;
        expect(Object.keys(out).sort(), `${id}/${mode}`).toEqual([...GROUND_PLANES[id]].sort());
      }
  });

  it('and every value it emits is one the injector will install', () => {
    for (const mode of MODES)
      for (const id of GROUND_IDS)
        for (const [k, v] of Object.entries(deriveGround(id, '#3a4750', mode)!)) {
          expect(isModToken(k), `${k} is not a token a mod may set`).toBe(true);
          expect(isSafeValue(v), `${k}: ${v} would be refused by the injector`).toBe(true);
        }
  });

  it('every ground has a label and a line of its own', () => {
    for (const g of GROUNDS) {
      expect(g.label.trim(), `${g.id} has no label`).not.toBe('');
      expect(g.description.trim().length, `${g.id} has no description`).toBeGreaterThan(10);
      expect(groundById(g.id)).toBe(g);
    }
    expect(groundById('nope')).toBeUndefined();
  });
});

describe('seeding a plane with the colour it already has', () => {
  /**
   * Reproduces it. The two exceptions are named rather than smoothed
   * over, and both are properties of the seed being a seed:
   *
   * `--sidebar-accent-foreground` — the shipped palette softens ONE ink
   * against `--secondary` and reuses it on all three recessed planes,
   * which is what the design does while every plane is a step off the
   * same page. A seeded sidebar is no longer a step off anything, so its
   * hover ink is measured against its OWN hover fill. ΔE 3 apart, and
   * the seeded one is the one measured where it lands.
   *
   * `--sidebar-border` — the same walk from a hex that has been through
   * `#rrggbb`, so it is rounding, not a decision.
   */
  const EXPECTED_DRIFT: Record<string, number> = {
    '--sidebar-accent-foreground': 3.5,
    '--sidebar-border': 1,
  };

  it('changes nothing else, in either mode, under every pack', () => {
    let checked = 0;
    for (const mode of MODES)
      for (const pack of THEME_PACKS) {
        const pal = palette(mode, pack.seed[mode]);
        for (const id of GROUND_IDS) {
          const own = pal[id === 'card' ? '--card' : '--sidebar'];
          const out = deriveGround(id, own, mode)!;
          for (const t of GROUND_PLANES[id]) {
            const d = distance(parseHex(pal[t])!, parseHex(out[t])!);
            checked++;
            expect(d, `${mode}/${pack.id} ${id}: ${t} moved ${d.toFixed(2)} (${pal[t]} → ${out[t]})`)
              .toBeLessThanOrEqual(EXPECTED_DRIFT[t] ?? 0.001);
          }
        }
      }
    expect(checked, 'nothing was compared').toBeGreaterThan(30);
  });

  it('and the drift table is honest — every entry names a real difference', () => {
    // An entry for a token that no longer drifts is an allowance nobody
    // is watching.
    const pal = palette('light', THEME_PACKS[0].seed.light);
    for (const t of Object.keys(EXPECTED_DRIFT)) {
      const out = deriveGround('sidebar', pal['--sidebar'], 'light')!;
      expect(distance(parseHex(pal[t])!, parseHex(out[t])!),
        `${t} no longer drifts — drop it from the table`).toBeGreaterThan(0.001);
    }
  });
});

describe('a seeded plane carries its own ink', () => {
  it('flips it when the plane is dark and the page is light, and clears AA either way', () => {
    for (const mode of MODES)
      for (const seed of ['#0b0f14', '#f7f7f7', '#3a4750', '#c8d3e0']) {
        for (const id of GROUND_IDS) {
          const out = deriveGround(id, seed, mode)!;
          const ground = id === 'card' ? out['--card'] : out['--sidebar'];
          const ink = id === 'card' ? out['--card-foreground'] : out['--sidebar-foreground'];
          expect(contrastRatio(parseHex(ink)!, parseHex(ground)!),
            `${id}/${mode} on ${seed}: ink ${ink} on ${ground}`).toBeGreaterThanOrEqual(AA_TEXT);
        }
      }
    // The headline case: a near-black card on the white page takes light
    // text, where the page's own ink would be invisible.
    const dark = deriveGround('card', '#101418', 'light')!;
    expect(parseHex(dark['--card-foreground'])![0], 'a dark card kept the page ink').toBeGreaterThan(0.5);
  });

  it('and the hover fill inside a seeded plane is a shade of THAT plane', () => {
    // Not of the page: a dark sidebar with the page's pale hover fill is
    // a white strip on a black rail.
    const out = deriveGround('sidebar', '#101418', 'light')!;
    const d = distance(parseHex(out['--sidebar'])!, parseHex(out['--sidebar-accent'])!);
    expect(d, 'the hover fill is nowhere near its own rail').toBeLessThan(12);
    expect(contrastRatio(parseHex(out['--sidebar-accent-foreground'])!,
      parseHex(out['--sidebar-accent'])!), 'the hover ink does not read on the hover fill')
      .toBeGreaterThan(4);
  });
});

describe('the gate', () => {
  it('refuses a ground the semantic tones cannot be read on, by name', () => {
    // The same refusal the page gets, for the same reason: a badge, a
    // chip and an alert dot appear on a card and in the sidebar too.
    const GREYS = Array.from({ length: 64 }, (_, i) => `#${(i * 4).toString(16).padStart(2, '0').repeat(3)}`);
    let refused = 0, kept = 0;
    for (const mode of MODES)
      for (const hex of GREYS) {
        const r = groundTokens('card', hex, mode);
        const tone = worstTone(parseHex(hex)!, mode);
        if (tone.ratio < AA_LARGE) {
          refused++;
          expect(r.tokens, `${hex}/${mode} was accepted though ${tone.name} reads at ${tone.ratio.toFixed(2)}`).toBeNull();
          expect(r.breaks, `${hex}/${mode} refused without naming what broke`).toBe(tone.name);
        } else {
          kept++;
          expect(r.tokens, `${hex}/${mode} was refused though every tone clears`).not.toBeNull();
        }
      }
    expect(refused, 'no ground was refused — the gate holds nothing').toBeGreaterThan(0);
    expect(kept, 'every ground was refused — the gate is a wall').toBeGreaterThan(10);
  });

  it('needs no wallpaper check — a seeded plane carries its own ink over the strongest stop', () => {
    // The page's sidebar wears the PAGE's ink and 21 of 96 wearable dark
    // canvases fail this, which is why `paletteTokens` has a wallpaper
    // refusal. A seeded plane wears its own, and `pickInk` leaves enough
    // headroom that a fifth of the accent cannot spend it. This is the
    // measurement `grounds.ts` cites instead of a check that never fires.
    let worst = Infinity, at = '', tested = 0;
    for (const mode of MODES)
      for (const pack of THEME_PACKS)
        for (let i = 0; i < 256; i += 4) {
          const hex = `#${i.toString(16).padStart(2, '0').repeat(3)}`;
          const r = groundTokens('sidebar', hex, mode);
          if (!r.tokens) continue;                       // the tone gate refused it first
          const ink = parseHex(r.tokens['--sidebar-foreground'])!;
          const ratio = Math.min(
            ...patternGrounds(parseHex(hex)!, parseHex(pack.seed[mode])!).map((g) => contrastRatio(ink, g)),
          );
          tested++;
          if (ratio < worst) { worst = ratio; at = `${mode}/${pack.id}/${hex}`; }
        }
    expect(tested, 'no wearable sidebar seed was measured').toBeGreaterThan(100);
    expect(worst, `worst is ${at} at ${worst.toFixed(2)}`).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it('and the page\'s wallpaper gate measures the rail that will actually paint', () => {
    // `paletteTokens` refuses a canvas whose SIDEBAR cannot carry the
    // frame pattern. With a seeded sidebar the derived one never reaches
    // the screen, so measuring it refuses a canvas over a rail nobody
    // will see. 21 dark canvases sat in exactly that gap.
    const brand = THEME_PACKS[0].seed.dark;
    const freed = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`)
      .filter((hex) => paletteTokens(hex, brand, 'dark', false).tokens
        && !paletteTokens(hex, brand, 'dark', true).tokens
        && paletteTokens(hex, brand, 'dark', true, '#0b0f14').tokens);
    expect(freed.length, 'no canvas is freed by seeding the rail — the override is not read')
      .toBeGreaterThan(10);

    // And it cannot WIDEN the gate: a seeded rail the tones refuse is
    // not a rail, so the derived one is measured as before.
    const refusedSeed = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`)
      .find((h) => groundTokens('sidebar', h, 'dark').tokens === null)!;
    expect(paletteTokens(freed[0], brand, 'dark', true, refusedSeed).tokens,
      'an unwearable seed was allowed to speak for the rail').toBeNull();
  });

  it('refuses what is not a colour at all', () => {
    for (const junk of ['', 'red', '#12', 'var(--danger)'])
      expect(groundTokens('card', junk, 'light').tokens, junk).toBeNull();
  });
});
