/**
 * The funnel every appearance write passes through, with a picked colour
 * in it.
 *
 * Two claims live here and nowhere else.
 *
 * A pack and a picked colour are one question. `setTheme` is where that
 * is enforced for the DATA, exactly as `:not([data-mod-accent])`
 * enforces it for the CSS — and the picker cannot test it, because the
 * picker mocks this module.
 *
 * A picked colour is stored as a seed and derived per mode. That is only
 * true if the mode is in the effect's dependency list, which is the kind
 * of thing that is correct on the day it is written and quietly wrong
 * after the next refactor. So this mounts the real provider and reads
 * the real stylesheet back.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';

const store: Record<string, unknown> = {};
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  // A store, not a hook: the provider only needs the value it was given
  // and a way to write. Re-rendering is the harness's job, so a write and
  // the render that follows it stay two visible steps.
  usePreference: (k: string) => ({
    value: store[k],
    setValue: (next: unknown) => {
      store[k] = typeof next === 'function'
        ? (next as (p: unknown) => unknown)(store[k])
        : next;
    },
  }),
}));
vi.mock('../preferences/appearance', () => ({ publishAppearanceDefault: () => {} }));

import { useState } from 'react';
import { ModProvider, useMods } from './context';
import { modStyleText } from './inject';
import { accentTokens } from './theme/accent';
import { paletteTokens, surfaceTokens } from './theme/canvas';
import { packById } from './catalogue';

const THEME_KEY = 'mods.theme';
const SIZE_KEY = 'mods.size';
const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'default', icons: 'regular', entrance: false, color: 'dark-blue',
};
const SIZE = { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} };

/** Re-renders on demand, so a `setTheme` written into the fake store is
 *  visible to the provider on the next pass. */
let api: ReturnType<typeof useMods>;
let bump: () => void;
function Probe() {
  api = useMods();
  return null;
}
/**
 * No `key` on the provider, deliberately.
 *
 * Keying it would remount on every bump, and a remount runs every effect
 * fresh regardless of its dependency list — which is exactly the thing
 * under test. The first draft did that, and the mutation that strips
 * `theme.mode` from the deps passed. It re-renders instead, so an effect
 * that does not list what it reads simply never fires.
 */
function Harness() {
  const [, setN] = useState(0);
  bump = () => setN((x) => x + 1);
  return <ModProvider><Probe /></ModProvider>;
}

const mount = (over: Record<string, unknown> = {}) => {
  store[THEME_KEY] = { ...BASE, ...over };
  store[SIZE_KEY] = SIZE;
  render(<Harness />);
};
const stored = () => store[THEME_KEY] as Record<string, unknown>;
const write = (p: Record<string, unknown>) => act(() => { api.setTheme(p as never); });

beforeEach(() => { cleanup(); document.head.querySelectorAll('style').forEach((e) => e.remove()); });

describe('a pack and a picked colour are never both live', () => {
  it('choosing a pack drops the picked colour', () => {
    mount({ brand: '#ff6a00' });
    write({ accent: 'purple' });
    expect(stored().accent).toBe('purple');
    expect(stored(), 'the pack was chosen and the custom colour survived').not.toHaveProperty('brand');
  });

  it('picking a colour leaves the pack stored, so Clear has something to return to', () => {
    mount({ accent: 'green' });
    write({ brand: '#ff6a00' });
    expect(stored().brand).toBe('#ff6a00');
    expect(stored().accent, 'the pack was forgotten — Clear would return to nothing').toBe('green');
  });

  it('a write that names BOTH means both — it is not the pack path', () => {
    mount({ brand: '#ff6a00' });
    write({ accent: 'azure', brand: '#00ff88' });
    expect(stored().accent).toBe('azure');
    expect(stored().brand).toBe('#00ff88');
  });

  it('still re-derives the deprecated alias on every write', () => {
    mount({});
    write({ mode: 'light', accent: 'purple' });
    expect(stored().color).toBe('light');
  });
});

describe('a picked colour is a seed, not a set of values', () => {
  it('reaches the stylesheet as the accent the engine derived', () => {
    mount({ brand: '#ff6a00' });
    const want = accentTokens('#ff6a00', 'dark').tokens!;
    expect(modStyleText()).toContain(`--primary: ${want['--primary']};`);
  });

  it('re-derives when the mode changes, instead of wearing the other mode is value', () => {
    mount({ brand: '#ff6a00' });
    const dark = accentTokens('#ff6a00', 'dark').tokens!['--primary'];
    expect(modStyleText()).toContain(`--primary: ${dark};`);

    write({ mode: 'light' });
    act(() => bump());
    const light = accentTokens('#ff6a00', 'light').tokens!['--primary'];
    // If these were equal the test would be watching nothing — the whole
    // reason the seed is stored instead of the tokens is that they differ.
    expect(light, 'the two modes derive the same accent').not.toBe(dark);
    expect(modStyleText(), 'the accent did not re-derive on a mode change')
      .toContain(`--primary: ${light};`);
  });

  it('installs nothing at all when there is no picked colour', () => {
    mount({});
    expect(modStyleText()).toBeNull();
    expect(document.documentElement.hasAttribute('data-mod-accent')).toBe(false);
  });
});


describe('a canvas is a seed too, and it claims the whole palette', () => {
  it('installs every derived token, not the accent\'s four', () => {
    mount({ canvas: '#ffffff', mode: 'light' });
    const sheet = modStyleText() ?? '';
    // Surfaces the accent path never writes — the proof it is the full
    // palette rather than the four-token one.
    for (const token of ['--background', '--card', '--sidebar', '--border', '--muted-foreground'])
      expect(sheet, `${token} missing — this is not the full palette`).toContain(token);
  });

  it('derives from the pack in force when no custom accent is picked', () => {
    mount({ canvas: '#ffffff', mode: 'light', accent: 'green' });
    const seed = packById('green')!.seed.light;
    const want = paletteTokens('#ffffff', seed, 'light').tokens!;
    expect(modStyleText()).toContain(`--primary: ${want['--primary']};`);
  });

  it('derives from the person\'s own accent when they picked one', () => {
    mount({ canvas: '#ffffff', mode: 'light', brand: '#ff6a00' });
    const want = paletteTokens('#ffffff', '#ff6a00', 'light').tokens!;
    expect(modStyleText()).toContain(`--primary: ${want['--primary']};`);
  });

  it('re-derives on a mode change instead of wearing the other mode\'s palette', () => {
    mount({ canvas: '#0a0a0a', mode: 'dark', brand: '#ff6a00' });
    const darkCard = paletteTokens('#0a0a0a', '#ff6a00', 'dark').tokens!['--card'];
    expect(modStyleText()).toContain(`--card: ${darkCard};`);

    // A canvas the other mode CAN wear, so the switch is about the
    // derivation rather than about the gate refusing.
    write({ mode: 'light', canvas: '#ffffff' });
    act(() => bump());
    const lightCard = paletteTokens('#ffffff', '#ff6a00', 'light').tokens!['--card'];
    expect(lightCard, 'the two modes derive the same card — nothing is proved')
      .not.toBe(darkCard);
    expect(modStyleText(), 'the palette did not re-derive on a mode change')
      .toContain(`--card: ${lightCard};`);
  });

  it('falls back to the accent path when the canvas cannot be worn', () => {
    // Cream in dark mode breaks --warn. The person is not stranded on a
    // half-applied palette; their accent still paints.
    mount({ canvas: '#f5f0e8', mode: 'dark', brand: '#ff6a00' });
    const sheet = modStyleText() ?? '';
    const accentOnly = accentTokens('#ff6a00', 'dark').tokens!;
    expect(sheet).toContain(`--primary: ${accentOnly['--primary']};`);
    expect(sheet, 'a refused canvas repainted the page anyway').not.toContain('--background');
  });

  it('a canvas alone still paints — no custom accent required', () => {
    mount({ canvas: '#ffffff', mode: 'light' });
    expect(modStyleText()).toContain('--background');
  });
});


describe('a place can wear its own conditions', () => {
  it('emits a scoped block that the global one does not reach', () => {
    mount({ surfaces: { loads: '#f5f0e8' }, mode: 'light' });
    const sheet = modStyleText() ?? '';
    expect(sheet, 'no scoped block was emitted').toContain(':root[data-surface="loads"]');
    const want = surfaceTokens('#f5f0e8', packById('blue')!.seed.light, 'light').tokens!;
    expect(sheet).toContain(`--background: ${want['--background']};`);
  });

  it('scopes ONLY that place — the others are untouched', () => {
    mount({ surfaces: { loads: '#f5f0e8' }, mode: 'light' });
    const sheet = modStyleText() ?? '';
    for (const s of ['live-map', 'work-orders'])
      expect(sheet, `${s} got a block nobody asked for`).not.toContain(`data-surface="${s}"`);
  });

  it('never writes the accent into a scoped block', () => {
    // The brand is global. A scoped accent would lose under purple,
    // green and azure while appearing to work under blue.
    mount({ surfaces: { loads: '#f5f0e8' }, mode: 'light', brand: '#ff6a00' });
    const sheet = modStyleText() ?? '';
    const scoped = sheet.slice(sheet.indexOf(':root[data-surface="loads"]'));
    expect(scoped, 'a scoped block set the accent').not.toContain('--primary:');
  });

  it('and the global accent still paints', () => {
    mount({ surfaces: { loads: '#f5f0e8' }, mode: 'light', brand: '#ff6a00' });
    const sheet = modStyleText() ?? '';
    const global = sheet.slice(0, sheet.indexOf(':root[data-surface'));
    expect(global, 'the surface swallowed the accent').toContain('--primary:');
  });

  it('re-derives a scoped canvas on a mode change like the global one', () => {
    mount({ surfaces: { loads: '#ffffff' }, mode: 'light' });
    const lightBg = surfaceTokens('#ffffff', packById('blue')!.seed.light, 'light').tokens!['--background'];
    expect(modStyleText()).toContain(`--background: ${lightBg};`);

    write({ mode: 'dark', surfaces: { loads: '#030303' } });
    act(() => bump());
    const darkBg = surfaceTokens('#030303', packById('blue')!.seed.dark, 'dark').tokens!['--background'];
    expect(darkBg, 'the two modes derive the same ground — nothing is proved').not.toBe(lightBg);
    expect(modStyleText(), 'the scoped canvas did not re-derive').toContain(`--background: ${darkBg};`);
  });

  it('a place whose canvas this mode cannot wear contributes no block', () => {
    mount({ surfaces: { loads: '#f5f0e8' }, mode: 'dark' });
    expect(modStyleText() ?? '', 'a refused canvas emitted a block anyway')
      .not.toContain('data-surface="loads"');
  });
});
