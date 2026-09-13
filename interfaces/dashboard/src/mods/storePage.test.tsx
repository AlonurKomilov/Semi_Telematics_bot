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
const { setTheme, undoSpy, setPref, removed, at } = vi.hoisted(() => ({
  at: { current: {} as Record<string, string> },
  setTheme: vi.fn(), undoSpy: vi.fn(), setPref: vi.fn(),
  // Which PACKS this device has taken off, per test.
  removed: { current: [] as string[] },
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
  // Which level of the store this render is at: the shelf of packs, or
  // one pack's own page.
  useParams: () => at.current,
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
import { ITEM_AXES } from './store/items';
import { PACKS, packById } from './store/packs';
import { TAXONOMY, headingsOf } from './taxonomy';

const tileOf = (label: string, axis?: string) => {
  const scope = axis ? within(screen.getByTestId(`store-axis-${axis}`)) : screen;
  return scope.getByText(label).closest('div[class*="p-3"]') as HTMLElement;
};

beforeEach(() => {
  at.current = {};
  removed.current = [];
  setPref.mockClear(); setTheme.mockClear(); undoSpy.mockClear();
});

describe('every shelf has a home', () => {
  it('the table and the catalogue name the same axes', () => {
    expect(Object.keys(AXIS_UI).sort()).toEqual(ITEM_AXES.map((a) => a.axis).sort());
  });

  it('the presets shelf does not name the service it sits inside', () => {
    // "Mods" is the service. A shelf of that name inside it made the
    // page point at itself, and produced the question it was meant to
    // answer. A preset is an item like any other; it just carries
    // several shelves' settings at once.
    expect(AXIS_UI.mods.label).not.toBe('Mods');
    const row = readFileSync(join(__dirname, 'panel', 'ModsRow.tsx'), 'utf8');
    const said = /t\('mods\.group_mods',\s*'([^']+)'\)/.exec(row);
    expect(said, 'the panel row no longer names itself in one place').not.toBeNull();
    expect(said![1], 'the panel and the store call the presets shelf different things')
      .toBe(AXIS_UI.mods.label);
    expect(headingsOf('mods'), 'the taxonomy calls it something else again')
      .toEqual([AXIS_UI.mods.label]);
  });

  it('a shelf is called what the rest of the product calls it', () => {
    const titles = TAXONOMY.flatMap((c) => c.items.map((i) => i.title));
    for (const [axis, ui] of Object.entries(AXIS_UI)) {
      if (axis === 'mods') continue;
      expect(titles, `the store calls ${axis} "${ui.label}", and nothing else does`)
        .toContain(ui.label);
    }
  });
});

describe('the store sells packs, not items', () => {
  it('every pack is on the shelf, with what it brings', () => {
    render(<ModsStorePage />);
    for (const p of PACKS) expect(screen.getByText(p.label)).toBeTruthy();
    expect(within(screen.getByTestId('store-packs')).getAllByText(/items across/).length)
      .toBe(PACKS.length);
  });

  it('an item cannot be installed on its own — only its pack can', () => {
    render(<ModsStorePage />);
    // No item names appear on the shelf at all: this level is packs.
    expect(screen.queryByText('Mesh'), 'an item is being sold on the pack shelf').toBeNull();
    expect(screen.queryByRole('button', { name: /^apply$/i })).toBeNull();
  });

  it('what a pack brings is one level in', () => {
    at.current = { pack: 'classic' };
    render(<ModsStorePage />);
    expect(tileOf('Mesh', 'wallpaper')).toBeTruthy();
    expect(within(tileOf('Mesh', 'wallpaper')).queryByRole('button'),
      'an item inside a pack is offering a verb of its own').toBeNull();
  });

  it('a pack that is not there says so instead of drawing an empty page', () => {
    at.current = { pack: 'not-a-pack' };
    render(<ModsStorePage />);
    expect(screen.getByText(/Not a pack/)).toBeTruthy();
  });
});

describe('installing and removing a pack', () => {
  it('the base pack cannot be removed — it carries every fallback', () => {
    render(<ModsStorePage />);
    const classic = tileOf(packById('classic')!.label);
    expect(within(classic).getByText('Installed')).toBeTruthy();
    expect(within(classic).queryByRole('button', { name: /remove/i })).toBeNull();
  });

  it('removing a pack records it, and says so with a way back', () => {
    render(<ModsStorePage />);
    fireEvent.click(within(tileOf('Cab')).getByRole('button', { name: /remove cab/i }));
    expect(setPref).toHaveBeenCalledWith(['cab']);
    expect(undoSpy, 'a pack left with nothing said').toHaveBeenCalledTimes(1);
    expect(undoSpy.mock.calls[0][0].label).toBe('Cab removed');
  });

  it('removing the pack you are WEARING from takes it off you too', () => {
    // The harness wears the Cab preset.
    render(<ModsStorePage />);
    fireEvent.click(within(tileOf('Cab')).getByRole('button', { name: /remove cab/i }));
    expect(setTheme, 'the app is left wearing an item no picker offers')
      .toHaveBeenCalledWith({ mod: '' });
  });

  it('a removed pack stays on the shelf, offering the way back', () => {
    removed.current = ['cab'];
    render(<ModsStorePage />);
    const cab = tileOf('Cab');
    expect(within(cab).queryByText('Installed')).toBeNull();
    expect(within(cab).getByRole('button', { name: /install/i })).toBeTruthy();
  });
});

describe('the shelf whose items are not one setting says so', () => {
  it('explains itself under its own heading', () => {
    at.current = { pack: 'cab' };
    render(<ModsStorePage />);
    expect(AXIS_UI.mods.note, 'the shelf that needs a line has none').toBeTruthy();
    expect(within(screen.getByTestId('store-axis-mods')).getByText(AXIS_UI.mods.note!)).toBeTruthy();
  });

  it('and the shelves that are one setting do not', () => {
    for (const axis of ['theme', 'font', 'cursor']) {
      expect(AXIS_UI[axis].note, `${axis} explains what needs no explaining`).toBeUndefined();
    }
  });
});
