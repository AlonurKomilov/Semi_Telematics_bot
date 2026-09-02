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

const THEME_KEY = 'mods.theme';
const SIZE_KEY = 'mods.size';
const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'normal', icons: 'regular', entrance: false, color: 'dark-blue',
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
