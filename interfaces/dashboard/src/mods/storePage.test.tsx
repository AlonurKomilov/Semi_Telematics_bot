/**
 * The store page: it lists the catalogue, and Apply lands in the one
 * home the axis declares.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// `vi.mock` is hoisted above the file, so the spy has to be hoisted too
// or the factory closes over a variable that does not exist yet.
const { setTheme, undoSpy, setPref, removed } = vi.hoisted(() => ({
  setTheme: vi.fn(), undoSpy: vi.fn(), setPref: vi.fn(),
  // What this person has taken off their shelves, per test.
  removed: { current: {} as Record<string, string[]> },
}));

// Spread the real modules: the preferences barrel pulls in AuthContext,
// which pulls in src/i18n.ts, which needs `initReactI18next` to exist.
// A bare factory here replaced the whole module and the suite failed to
// collect rather than failing an assertion.
vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  // Interpolating, unlike the simpler harness elsewhere: this page puts
  // the pack's name INSIDE a label ("Remove Serif"), and a mock that
  // handed back `Remove {{pack}}` would let a control ship with a name
  // no screen reader could use.
  useTranslation: () => ({
    t: (_k: string, d?: string, o?: Record<string, unknown>) =>
      Object.entries(o ?? {}).reduce(
        (s, [k, v]) => s.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), String(v)),
        d ?? _k),
  }),
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
      motion: 'default', icons: 'regular', mod: 'cab',
      font: 'geist', iconPack: 'lucide', cursor: 'system', shader: 'soft',
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
    value: k === 'mods.sound.volume' ? 1
      : k === 'dispatch.soundOn' ? true
        : k === 'mods.packs.removed' ? removed.current
          : 'chime',
    setValue: k === 'mods.packs.removed' ? setPref : () => {},
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
      .toBe('Cursor set to Sharp');
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

describe('a shelf is what this person kept', () => {
  /** Scoped to its shelf: two axes ship a pack called Soft, which is
   *  fine on a page where each sits under its own heading. */
  const tileOf = (label: string, axis?: string) => {
    const scope = axis ? within(screen.getByTestId(`store-axis-${axis}`)) : screen;
    return scope.getByText(label).closest('div[class*="p-3"]') as HTMLElement;
  };

  beforeEach(() => {
    removed.current = {};
    setPref.mockClear(); setTheme.mockClear();
  });

  it('taking a pack off the shelf records it, by axis', () => {
    render(<ModsStorePage />);
    fireEvent.click(within(tileOf('Serif')).getByRole('button', { name: /remove serif/i }));
    expect(setPref).toHaveBeenCalledWith({ font: ['serif'] });
  });

  it('what a shelf falls back to cannot be taken off it', () => {
    render(<ModsStorePage />);
    // Blue IS the base accent; a Color shelf with nothing on it is one
    // nobody could leave.
    expect(within(tileOf('Blue')).queryByRole('button', { name: /remove/i })).toBeNull();
    expect(within(tileOf('Serif')).getByRole('button', { name: /remove/i })).toBeTruthy();
  });

  it('removing what you are WEARING puts the shelf default back on', () => {
    render(<ModsStorePage />);
    // The harness wears the Soft shader — the one pack in this mock that
    // is not its shelf's default, so it is the only one where removing
    // and wearing are the same gesture.
    const soft = tileOf('Soft', 'shader');
    expect(within(soft).getByText('Applied'), 'the harness is not wearing it').toBeTruthy();
    fireEvent.click(within(soft).getByRole('button', { name: /remove soft/i }));
    expect(setPref).toHaveBeenCalledWith({ shader: ['soft'] });
    expect(setTheme, 'the app is left wearing a pack no shelf offers')
      .toHaveBeenCalledWith({ shader: 'flat' });
  });

  it('removing the look you WEAR stops you wearing it, and leaves the axes it wrote', () => {
    render(<ModsStorePage />);
    const cab = tileOf('Cab', 'mods');
    fireEvent.click(within(cab).getByRole('button', { name: /remove cab/i }));
    expect(setPref).toHaveBeenCalledWith({ mods: ['cab'] });
    // No shelf default here — wearing no look is a fine answer — so the
    // reset is the look itself, not the seven axes it wrote.
    expect(setTheme).toHaveBeenCalledWith({ mod: '' });
  });

  it('a pack taken off stays on the page, offering the way back', () => {
    removed.current = { font: ['serif'] };
    render(<ModsStorePage />);
    const tile = tileOf('Serif');
    expect(within(tile).queryByRole('button', { name: /^apply$/i })).toBeNull();
    expect(within(tile).getByRole('button', { name: /add/i })).toBeTruthy();
  });
});
