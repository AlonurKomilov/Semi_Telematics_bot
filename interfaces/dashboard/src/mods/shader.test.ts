/**
 * The light reaches the surfaces it is supposed to move.
 *
 * There is no readability gate here and there should not be: a lighting
 * model touches no token that carries meaning, so nothing it can do
 * makes anything unreadable. What CAN go wrong is quieter — an axis
 * that stores, stamps and offers three presets while the shadow scale
 * still draws Tailwind's hardcoded numbers. Every chip would highlight,
 * the value would persist, and not one pixel would move.
 *
 * So this reads the two files that have to agree: the pack table, and
 * the config that spends it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { SHADER_PACKS, SHADER_IDS, SHADER_BAND, shaderPackById } from './shader';

const ROOT = join(__dirname, '..', '..');
const CONFIG = readFileSync(join(ROOT, 'tailwind.config.js'), 'utf8');
const CSS = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

const VARS = ['--light-lift', '--light-spread', '--light-strength'] as const;

/** Tailwind's shadow scale, as the config declares it. */
function shadowScale(): Record<string, string> {
  const block = /boxShadow:\s*\{([\s\S]*?)\n      \},/.exec(CONFIG)?.[1] ?? '';
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/^\s*'?([A-Za-z0-9]+)'?:\s*'([^']*)',/gm)) out[m[1]] = m[2];
  return out;
}

function blockOf(id: string): string {
  return new RegExp(`:root\\[data-shader="${id}"\\]\\s*\\{([^}]*)\\}`).exec(CSS)?.[1] ?? '';
}

describe('the axis actually drives the shadow scale', () => {
  const scale = shadowScale();

  it('finds a scale to check', () => {
    // Without this every assertion below is about an empty object, and
    // an empty object satisfies "every step uses the variables".
    expect(Object.keys(scale).length, 'no boxShadow scale parsed out of the config')
      .toBeGreaterThanOrEqual(6);
    expect(scale, 'the scale lost its lg step — 37 popovers use it').toHaveProperty('lg');
  });

  it('every step composes from the light, not from a fixed number', () => {
    for (const [step, value] of Object.entries(scale))
      for (const v of VARS)
        expect(value, `shadow-${step} does not answer to ${v}`).toContain(`var(${v}`);
  });

  /** Every fallback is 1, so a document with no `[data-shader]` — a
   *  print sheet, an email preview, a test — renders the light this app
   *  shipped with rather than no shadow at all. */
  it('and falls back to the shipped light where the axis is absent', () => {
    for (const [step, value] of Object.entries(scale))
      for (const m of value.matchAll(/var\((--light-[a-z]+)(?:,\s*([^)]*))?\)/g))
        expect(m[2]?.trim(), `shadow-${step}: ${m[1]} has no fallback of 1`).toBe('1');
  });
});

describe('every preset says what it does, and only that', () => {
  it('declares all three multipliers, or none at all', () => {
    for (const p of SHADER_PACKS) {
      const block = blockOf(p.id);
      if (p.id === 'flat') {
        // The absence of a rule IS the shipped light — the same reason
        // wallpaper's `none` and the cursor's `system` have no block.
        // A `flat` block would have to out-rank whatever a later preset
        // declares, for no gain.
        expect(block, '"flat" declares a light; it should declare none').toBe('');
        continue;
      }
      for (const v of VARS)
        expect(block, `${p.id} does not set ${v}`).toContain(`${v}:`);
    }
  });

  it('and the stylesheet says what the table says', () => {
    // Two files, one fact. A preset whose CSS drifted from its own
    // numbers would move the light by an amount nothing describes.
    let checked = 0;
    for (const p of SHADER_PACKS.filter((x) => x.id !== 'flat')) {
      const block = blockOf(p.id);
      for (const [v, want] of [
        ['--light-lift', p.lift], ['--light-spread', p.spread], ['--light-strength', p.strength],
      ] as const) {
        const got = new RegExp(`${v}:\\s*([\\d.]+)`).exec(block)?.[1];
        expect(got, `${p.id} declares no ${v}`).toBeDefined();
        expect(+got!, `${p.id} ${v}: css says ${got}, the table says ${want}`).toBe(want);
        checked++;
      }
    }
    expect(checked, 'nothing compared').toBe((SHADER_PACKS.length - 1) * VARS.length);
  });

  it('and no block for a preset the list does not offer', () => {
    const orphans = [...CSS.matchAll(/\[data-shader="([^"]+)"\]/g)]
      .map((m) => m[1]).filter((id) => !SHADER_IDS.includes(id));
    expect([...new Set(orphans)], 'CSS paints a light nobody can choose').toEqual([]);
  });
});

describe('the light stays light', () => {
  /** `flat` is TODAY, exactly. Every multiplier 1, so an unstamped
   *  document and a `flat` one are the same document — which is what
   *  makes the axis safe to ship on by default. */
  it('flat is the shipped light, unmodified', () => {
    const flat = shaderPackById('flat')!;
    expect([flat.lift, flat.spread, flat.strength]).toEqual([1, 1, 1]);
  });

  /**
   * A shadow is a depth cue and past a point it stops being one: at
   * high strength a card reads as a hole rather than a raised surface,
   * and at high lift the shadow detaches from the thing casting it.
   *
   * And nothing may reach ZERO. `shadow-lg` is what separates 37
   * popovers and menus from the content beneath them; a preset that put
   * the light out would not be a flatter look, it would be a menu whose
   * edge cannot be found.
   */
  it('no preset puts the light out, or turns it into a hole', () => {
    for (const p of SHADER_PACKS)
      for (const [name, v] of [['lift', p.lift], ['spread', p.spread], ['strength', p.strength]] as const) {
        expect(v, `${p.id}.${name} is ${v} — the light is out`).toBeGreaterThanOrEqual(SHADER_BAND.min);
        expect(v, `${p.id}.${name} is ${v} — that is a hole, not a shadow`)
          .toBeLessThanOrEqual(SHADER_BAND.max);
      }
  });

  /** The control: the band is a real bound, so a value outside it must
   *  fail the same comparison. */
  it('and the band can reject something', () => {
    expect(0).toBeLessThan(SHADER_BAND.min);
    expect(9).toBeGreaterThan(SHADER_BAND.max);
  });

  /** It is a LIGHTING model. The moment a preset sets a colour it has
   *  stopped being one and become the thing GX shipped — a treatment
   *  that changes what things are. */
  it('and no preset touches a colour', () => {
    for (const p of SHADER_PACKS.filter((x) => x.id !== 'flat')) {
      const block = blockOf(p.id);
      expect(block, `${p.id} sets a colour — this axis moves light, not meaning`)
        .not.toMatch(/oklch|#[0-9a-f]{3}|rgb|hsl|--primary|--danger|--ok|--warn|--info/i);
      for (const decl of block.matchAll(/(--[a-z-]+):/g))
        expect(VARS as readonly string[], `${p.id} sets ${decl[1]}, which is not a light`)
          .toContain(decl[1]);
    }
  });
});
