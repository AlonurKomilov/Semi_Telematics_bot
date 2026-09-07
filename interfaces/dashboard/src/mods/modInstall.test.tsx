/**
 * Installing a look actually installs it.
 *
 * `catalogue.test.ts` proves every field a Mod declares is filed under
 * a category and shows up in the footprint. Nothing proved the field is
 * APPLIED — and one was not: `font` was declared on `Mod`, filed under
 * Typeface, listed in the footprint, and never written by anything. A
 * look promising a typeface changed the colour and left the lettering
 * alone, and the whole suite was green, because the installer listed
 * the fields it knew by hand and that list had quietly stopped matching
 * the type.
 *
 * So this walks the TYPE, not a list: a mod carrying every field the
 * catalogue says a mod can carry must write every one of them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const { setTheme, setSize, setSoundPack, undoableAction } = vi.hoisted(() => ({
  setTheme: vi.fn(), setSize: vi.fn(), setSoundPack: vi.fn(), undoableAction: vi.fn(),
}));

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('./context', () => ({
  useMods: () => ({
    theme: { mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
      motion: 'default', icons: 'regular', font: 'geist', mod: '' },
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize,
  }),
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: () => ({ value: 'chime', setValue: setSoundPack }),
}));
vi.mock('../components/banners/stagedAction', () => ({ undoableAction }));

/**
 * Two fabricated looks, added to the shipped ones.
 *
 * `MOD_OPTIONS` is computed when `ModsRow` loads, so the list has to be
 * complete before the import rather than swapped per test — which is
 * why every case below picks its look by clicking its chip.
 */
const { EVERYTHING, THIN } = vi.hoisted(() => ({
  /** A value for every field, none of them the mocked theme's current
   *  one, so "was it written" cannot be confused with "was it already
   *  that". */
  EVERYTHING: {
    id: 'everything', label: 'Everything', why: 'carries every field a mod can',
    accent: 'green', radius: 'pill', material: 'glass', motion: 'calm',
    icons: 'bold', iconPack: 'phosphor', font: 'serif', entrance: true,
    wallpaper: 'grid', cursor: 'sharp', shader: 'studio', size: 1.25, sound: 'blip',
  },
  THIN: { id: 'thin', label: 'Thin', accent: 'green', why: 'carries almost nothing' },
}));
vi.mock('./catalogue', async (orig) => {
  const real = await orig<typeof import('./catalogue')>();
  return { ...real, MODS: [...real.MODS, EVERYTHING, THIN] };
});

import { ModsRow } from './panel/ModsRow';
import { MOD_FIELD_APPLIER, MOD_THEME_FIELDS, MODS } from './catalogue';

const SHIPPED = MODS.filter((m) => m.id !== 'everything' && m.id !== 'thin');
const field = (m: unknown, f: string) => (m as Record<string, unknown>)[f];

beforeEach(() => {
  cleanup();
  setTheme.mockClear(); setSize.mockClear();
  setSoundPack.mockClear(); undoableAction.mockClear();
});

const install = (label: string) => {
  render(<ModsRow label="" />);
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}$`, 'i') }));
  return (setTheme.mock.calls[0]?.[0] ?? {}) as Record<string, unknown>;
};

describe('a look that carries everything installs everything', () => {
  it('covers every field the type declares — no field has no applier', () => {
    // The forcing partition. A new Mod field must name where it is
    // installed, and the two that are not `theme` have homes of their
    // own, asserted below.
    const declared = Object.keys(MOD_FIELD_APPLIER).sort();
    expect(Object.keys(EVERYTHING).filter((k) => declared.includes(k)).sort(),
      'the fixture stopped carrying every field — it proves nothing then')
      .toEqual(declared);
    expect(MOD_THEME_FIELDS.length, 'no theme fields — every assertion below is vacuous')
      .toBeGreaterThan(5);
  });

  it('writes every theme field, under its own name', () => {
    const wrote = install('Everything');
    for (const f of MOD_THEME_FIELDS)
      expect(wrote[f], `installing the look did not write "${f}"`)
        .toBe(field(EVERYTHING, f));
  });

  /** The one that was broken, named on its own so a regression reads as
   *  what it is rather than as one line of a loop. */
  it('including the typeface, which used to be declared and never applied', () => {
    expect(install('Everything').font).toBe('serif');
  });

  it('and the pack, which a look could not carry at all before', () => {
    expect(install('Everything').iconPack).toBe('phosphor');
  });

  it('sends size and sound to their own homes, not through the theme', () => {
    const wrote = install('Everything');
    expect(wrote).not.toHaveProperty('size');
    expect(wrote).not.toHaveProperty('sound');
    expect(setSize).toHaveBeenCalledWith({ global: 1.25 });
    expect(setSoundPack).toHaveBeenCalledWith('blip');
  });

  it('and stores the identity, so editing an axis afterwards does not uninstall it', () => {
    expect(install('Everything').mod).toBe('everything');
  });
});

describe('a look that carries little writes little', () => {
  it('leaves the fields it omits alone', () => {
    const wrote = install('Thin');
    expect(Object.keys(wrote).sort()).toEqual(['accent', 'mod']);
    expect(setSize, 'a look with no size resized the app').not.toHaveBeenCalled();
    expect(setSoundPack, 'a look with no sound changed the pack').not.toHaveBeenCalled();
  });
});

describe('the shipped looks are installable', () => {
  it('each writes exactly the fields it declares', () => {
    expect(SHIPPED.length, 'no shipped looks — this test measures nothing')
      .toBeGreaterThan(0);
    for (const m of SHIPPED) {
      cleanup(); setTheme.mockClear();
      const wrote = install(m.label);
      const expected = MOD_THEME_FIELDS.filter((f) => field(m, f) !== undefined);
      expect(Object.keys(wrote).filter((k) => k !== 'mod').sort(),
        `${m.id} installs a different set than it declares`)
        .toEqual([...expected].sort());
    }
  });
});
