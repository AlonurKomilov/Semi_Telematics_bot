/**
 * The store page: it lists the catalogue, and Apply lands in the one
 * home the axis declares.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// `vi.mock` is hoisted above the file, so the spy has to be hoisted too
// or the factory closes over a variable that does not exist yet.
const { setTheme, undoSpy } = vi.hoisted(() => ({ setTheme: vi.fn(), undoSpy: vi.fn() }));

// Spread the real modules: the preferences barrel pulls in AuthContext,
// which pulls in src/i18n.ts, which needs `initReactI18next` to exist.
// A bare factory here replaced the whole module and the suite failed to
// collect rather than failing an assertion.
vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));
vi.mock('./context', () => ({
  useMods: () => ({
    // Every axis the page can write, as the real preference always has
    // them: an undo that restores `undefined` is not an undo.
    theme: {
      mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
      motion: 'default', icons: 'regular', mod: '',
      font: 'geist', iconPack: 'lucide', cursor: 'system', shader: 'flat',
      wallpaper: 'none', wallpaperPage: 'none', wallpaperLive: false,
    },
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: (k: string) => ({
    value: k === 'mods.sound.volume' ? 1 : k === 'dispatch.soundOn' ? true : 'chime',
    setValue: () => {},
  }),
}));

// The picker's scope row asks who this is: it offers only the places
// this person may open. These tests are not about that gate, so they
// wear an owner's view — every place reachable, permissions settled.
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => true, hasAny: () => true, hasWide: () => true,
    vehicleScope: 'all', role: 'owner', ready: true,
  }),
}));



vi.mock('./store/local', async (orig) => {
  const real = await orig<typeof import('./store/local')>();
  return {
    ...real,
    offered: <T,>(axis: string, items: readonly T[], idOf: (i: T) => string) =>
      items.filter((i) => `${axis}/${idOf(i)}` !== 'font/mono'),
  };
});

vi.mock('../components/banners/stagedAction', () => ({ undoableAction: undoSpy }));

import { ModsStorePage } from './store/StorePage';
import { AXIS_UI } from './store/axes';
import { PACK_AXES } from './store/packs';
import { rowsOf } from './store/index';
import { TAXONOMY } from './taxonomy';

describe('every shelf has a home', () => {
  it('the table and the catalogue name the same axes', () => {
    expect(Object.keys(AXIS_UI).sort()).toEqual(PACK_AXES.map((a) => a.axis).sort());
  });

  it('a shelf is called what the rest of the product calls it', () => {
    const titles = TAXONOMY.flatMap((c) => c.items.map((i) => i.title));
    for (const [axis, ui] of Object.entries(AXIS_UI)) {
      // `mods` is the looks row, which is not a settings item anywhere.
      if (axis === 'mods') continue;
      expect(titles, `the store calls ${axis} "${ui.label}", and nothing else does`)
        .toContain(ui.label);
    }
  });
});

describe('the store page', () => {
  it('draws a tile per pack, with the sentence the pack carries', () => {
    render(<ModsStorePage />);
    const wallpapers = rowsOf('wallpaper');
    expect(wallpapers.length).toBeGreaterThan(1);
    for (const row of wallpapers) {
      expect(screen.getByText(row.label)).toBeTruthy();
      expect(screen.getByText(row.description)).toBeTruthy();
    }
  });

  it('asks the same door every picker asks', () => {
    render(<ModsStorePage />);
    expect(screen.queryByText('Mono'), 'a pack the store withheld is on the shelf').toBeNull();
    expect(screen.getByText('Serif')).toBeTruthy();
  });

  it('Apply writes the field the axis declares', () => {
    render(<ModsStorePage />);
    const serif = screen.getByText('Serif').closest('div[class*="p-3"]') as HTMLElement;
    fireEvent.click(within(serif).getByRole('button', { name: /apply/i }));
    expect(setTheme).toHaveBeenCalledWith({ font: 'serif' });
  });

  it('one wallpaper pick lands on both grounds', () => {
    render(<ModsStorePage />);
    const tile = screen.getByText('Mesh').closest('div[class*="p-3"]') as HTMLElement;
    fireEvent.click(within(tile).getByRole('button', { name: /apply/i }));
    expect(setTheme).toHaveBeenCalledWith({ wallpaper: 'mesh', wallpaperPage: 'mesh' });
  });
});

describe('applying is as cheap to undo as it was to try', () => {
  it('says what changed, and puts the old value back', async () => {
    undoSpy.mockClear(); setTheme.mockClear();
    render(<ModsStorePage />);
    const tile = screen.getByText('Sharp').closest('div[class*="p-3"]') as HTMLElement;
    fireEvent.click(within(tile).getByRole('button', { name: /apply/i }));

    expect(undoSpy, 'a quiet axis changed with nothing said').toHaveBeenCalledTimes(1);
    const call = undoSpy.mock.calls[0][0];
    expect(call.label, 'the banner does not name the shelf and the pack')
      .toBe('{{shelf}} set to {{pack}}');
    setTheme.mockClear();
    await call.undo();
    expect(setTheme, 'undo did not restore the cursor it replaced')
      .toHaveBeenCalledWith({ cursor: 'system' });
  });

  it('what IS does not wear what you can DO', () => {
    render(<ModsStorePage />);
    // The harness wears the blue accent, so its tile is the applied one.
    const tile = screen.getByText('Blue').closest('div[class*="p-3"]') as HTMLElement;
    expect(within(tile).queryByRole('button'), 'the applied state is still a button').toBeNull();
    expect(within(tile).getByText('Applied')).toBeTruthy();
  });
});
