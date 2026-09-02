/**
 * The one guard that reads the CSS the browser reads.
 *
 * Every other colour test in this tree checks a VALUE — that a palette
 * derives, that a pair clears AA. None of them could see the defect this
 * file exists for, because the defect was not in any value: `derivePalette`
 * produced a correct accent, `applyModTokens` installed it correctly, and
 * the browser then threw it away for a preset that outranked it. Two
 * correct halves, one wrong screen.
 *
 * So this test does not assert about source text. It loads the real
 * `index.css` accent section off disk, runs the real injector, and asks
 * the DOM what `--primary` actually resolved to — which jsdom answers by
 * doing real cascade and real specificity.
 *
 * `data-mod-accent` is written as a LITERAL here on purpose. It is the
 * name a stylesheet and a TypeScript module have agreed on, and a guard
 * that imported it from one of them would still pass if the other half
 * renamed itself into silence.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { applyModTokens, seedTokens } from './inject';
import { derivePalette } from './theme/palette';
import { THEME_PACKS } from './catalogue';

/** The accent presets, exactly as they ship. */
const ACCENT_CSS = (() => {
  const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8');
  const start = css.indexOf('/* ── Accent presets');
  const end = css.indexOf('/* ── Size axes');
  if (start < 0 || end < 0) throw new Error('accent section markers moved — fix this reader');
  return css.slice(start, end);
})();

/** A base layer, so `--primary` has somewhere to fall back to and the
 *  test can tell "the preset won" from "nothing matched". */
const BASE_CSS = `:root { --primary: BASE; --background: BASE-BG; }`;

const root = () => document.documentElement;
const primary = () => getComputedStyle(root()).getPropertyValue('--primary').trim();
const background = () => getComputedStyle(root()).getPropertyValue('--background').trim();

const sheet = (text: string) => {
  const el = document.createElement('style');
  el.textContent = text;
  document.head.appendChild(el);
};

beforeEach(() => {
  document.head.querySelectorAll('style').forEach((el) => el.remove());
  root().className = '';
  root().removeAttribute('data-accent');
  root().removeAttribute('data-mod');
  root().removeAttribute('data-mod-accent');
  applyModTokens(null);
  // Source order as shipped: the app's stylesheet, then whatever the
  // injector appends.
  sheet(BASE_CSS);
  sheet(ACCENT_CSS);
});

const wear = (mode: 'dark' | 'light', accent: string) => {
  root().className = mode === 'dark' ? 'dark' : '';
  root().dataset.accent = accent;
};

/** The presets that actually have a block. Blue has none — it IS the
 *  base — which is exactly why the defect hid: blue was the one accent
 *  where an injected primary appeared to work. */
const BLOCKED = THEME_PACKS.map((p) => p.id).filter((id) => id !== 'blue');

describe('the fixture is real', () => {
  it('reads the shipped accent blocks, not an empty string', () => {
    expect(ACCENT_CSS).toContain('data-accent');
    expect(ACCENT_CSS.length).toBeGreaterThan(2000);
    expect(BLOCKED.length).toBeGreaterThanOrEqual(3);
  });

  it('a preset accent paints, so a later failure means the mod won and not that nothing matched', () => {
    for (const accent of BLOCKED)
      for (const mode of ['dark', 'light'] as const) {
        wear(mode, accent);
        expect(primary(), `${mode}/${accent}`).not.toBe('BASE');
        expect(primary(), `${mode}/${accent}`).toContain('oklch');
      }
  });
});

describe('an authored accent beats the preset it replaces', () => {
  it('in both modes, under every preset that has a block', () => {
    for (const accent of BLOCKED)
      for (const mode of ['dark', 'light'] as const) {
        wear(mode, accent);
        const tokens = seedTokens({ mode, canvas: mode === 'dark' ? '#0a0a0a' : '#ffffff', brand: '#ff6a00' }, derivePalette);
        expect(tokens, `${mode}/${accent} seeded nothing`).not.toBeNull();
        const res = applyModTokens(tokens);
        expect(res.applied, `${mode}/${accent}`).toBeGreaterThan(0);
        expect(root().getAttribute('data-mod-accent'), `${mode}/${accent}`).toBe('');
        expect(primary(), `${mode}/${accent}`).toBe(tokens!['--primary']);
      }
  });

  it('and the preset comes back the moment the mod is removed', () => {
    wear('dark', 'purple');
    const preset = primary();
    applyModTokens(seedTokens({ mode: 'dark', canvas: '#0a0a0a', brand: '#ff6a00' }, derivePalette));
    expect(primary()).not.toBe(preset);
    applyModTokens(null);
    expect(root().hasAttribute('data-mod-accent')).toBe(false);
    expect(primary()).toBe(preset);
  });
});

describe('the preset stands down only for the token that replaces it', () => {
  it('a mod that tints surfaces keeps the accent it was chosen with', () => {
    wear('dark', 'green');
    const preset = primary();
    applyModTokens({ '--background': '#101010', '--card': '#161616' });
    expect(root().hasAttribute('data-mod-accent')).toBe(false);
    expect(primary()).toBe(preset);
    // …and the surface it DID ask for still lands, which is the
    // behaviour that made the accent's failure so easy to miss.
    expect(background()).toBe('#101010');
  });

  it('a REJECTED --primary stands nothing down — otherwise a typo blanks the accent', () => {
    wear('dark', 'purple');
    const preset = primary();
    const res = applyModTokens({ '--primary': 'red; } body { display: none' });
    expect(res.rejectedValues).toContain('--primary');
    expect(root().hasAttribute('data-mod-accent')).toBe(false);
    expect(primary()).toBe(preset);
  });

  /**
   * The known corner, pinned so it stays known.
   *
   * The block is all-or-nothing, so standing it down for a set that
   * carries only `--primary-hover` would drop `--primary` itself to base
   * blue — a colour nobody chose. `derivePalette` emits all three
   * together; reaching this needs a hand-authored set that says half a
   * sentence, and then the half is ignored rather than half-applied.
   */
  it('half an accent is refused rather than half-applied', () => {
    wear('dark', 'purple');
    const preset = primary();
    applyModTokens({ '--primary-hover': '#ff6a00' });
    expect(root().hasAttribute('data-mod-accent')).toBe(false);
    expect(primary()).toBe(preset);
    expect(getComputedStyle(root()).getPropertyValue('--primary-hover').trim())
      .toBe('oklch(0.68 0.195 300)');
  });
});
