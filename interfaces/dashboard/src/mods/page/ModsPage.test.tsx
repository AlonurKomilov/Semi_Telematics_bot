/**
 * The page renders from the taxonomy, level by level.
 *
 * `state.test.ts` proves the readers. This proves the page USES them
 * and the taxonomy — that a category added to `taxonomy.ts` appears as
 * a tile with no edit here, that an item's level renders the card's
 * own control rather than a copy, and that a wrong address says so
 * instead of rendering an empty hub.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

// The picker's scope row asks who this is: it offers only the places
// this person may open. These tests are not about that gate, so they
// wear an owner's view — every place reachable, permissions settled.
vi.mock('../../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => true, hasAny: () => true, hasWide: () => true,
    vehicleScope: 'all', role: 'owner', ready: true,
  }),
}));

import ModsPage from './ModsPage';
import { ModProvider } from '../context';
import { TAXONOMY, browsableItemsOf, resetAxesOf, MOD_FIELD_CATEGORY } from '../taxonomy';
import { MODS } from '../packs/mods';
import { preferences, MOD_DEFAULT, SIZE_DEFAULT, DEFS } from '../../preferences';

const at = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <ModProvider>
      <Routes>
        <Route path="/mods" element={<ModsPage />} />
        <Route path="/mods/:category" element={<ModsPage />} />
        <Route path="/mods/:category/:item" element={<ModsPage />} />
      </Routes>
    </ModProvider>
  </MemoryRouter>,
);

beforeEach(() => {
  cleanup();
  localStorage.clear();
  // The store keeps values in memory too — clearing storage alone left
  // one test's mod installed for the next one, and the failure read as
  // "multiple elements with the text: Cab" three tests later.
  preferences.set('mods.theme', MOD_DEFAULT);
  preferences.set('mods.size', SIZE_DEFAULT);
  for (const k of ['mods.sound.ui', 'mods.sound.keyboard', 'dispatch.soundOn', 'mods.ambient'] as const)
    preferences.set(k, false);
  preferences.set('mods.sound.volume', DEFS['mods.sound.volume'].default);
});

describe('the hub', () => {
  it('shows one tile per category the taxonomy declares', () => {
    at('/mods');
    const hub = screen.getByTestId('mods-hub');
    const links = hub.querySelectorAll('a');
    expect(links.length, 'the hub does not have one tile per category').toBe(TAXONOMY.length);
    for (const cat of TAXONOMY)
      expect(hub.textContent, `${cat.title} is missing from the hub`).toContain(cat.title);
  });

  it('links each tile to that category', () => {
    at('/mods');
    for (const cat of TAXONOMY)
      expect(screen.getByTestId('mods-hub').querySelector(`a[href="/mods/${cat.id}"]`), cat.id).not.toBeNull();
  });

  it('shows an intensity where a category has one, and a count where it does not', () => {
    preferences.set('mods.sound.volume', 0.4);
    at('/mods');
    const hub = screen.getByTestId('mods-hub').textContent ?? '';
    expect(hub).toContain('40%');                 // sounds — the volume
    expect(hub).toMatch(/\d+ of \d+ changed/);   // interface — no single number
  });

  it('does not print an installed mod\'s reason twice', () => {
    const cab = MODS.find((m) => m.id === 'cab')!;
    preferences.set('mods.theme', { ...preferences.get('mods.theme'), mod: cab.id });
    at('/mods');
    // `ModControls` already prints `why` under the chip in force; the
    // hub header repeating it put the same sentence on screen twice.
    const hits = screen.queryAllByText(cab.why);
    expect(hits.length, 'the mod\'s reason appears more than once').toBe(1);
  });

  it('renders the mod row at the centre', () => {
    at('/mods');
    expect(screen.getByText('Cab')).toBeTruthy();
    expect(screen.getByText('Wall')).toBeTruthy();
  });
});

describe('a category', () => {
  it('shows one tile per item, each with a state', () => {
    at('/mods/interface');
    const grid = screen.getByTestId('mods-category');
    const cat = TAXONOMY.find((c) => c.id === 'interface')!;
    expect(grid.querySelectorAll('a').length).toBe(cat.items.length);
    for (const item of cat.items) expect(grid.textContent).toContain(item.title);
    expect(grid.textContent).toMatch(/Default|Changed|Off/);
  });

  it('shows the current value on the tile, not only that it changed', () => {
    at('/mods/interface');
    const corners = screen.getByText('Corners').closest('a')!;
    // Default is a value too — the tile answers before it is clicked.
    expect(corners.textContent, 'the tile announces a change without naming it').toContain('Rounded');
  });

  it('marks a touched item as changed', () => {
    preferences.set('mods.theme', { ...preferences.get('mods.theme'), radius: 'pill' });
    at('/mods/interface');
    const corners = screen.getByText('Corners').closest('a')!;
    expect(corners.textContent).toContain('Changed');
  });

  it('says so for an address that is not a category', () => {
    at('/mods/wallpaper');
    expect(screen.getByText('Not a category')).toBeTruthy();
    expect(screen.queryByTestId('mods-hub')).toBeNull();
  });
});

describe('an item', () => {
  /**
   * The depth the URL promises, kept.
   *
   * This block used to assert the OPPOSITE — that `/mods/interface/corners`
   * showed Color, Material, Typeface and Icons, "the same controls, not a
   * copy". It did, and that was the defect: the last path segment changed
   * the heading and nothing under it, so `/mods/sounds/keyboard` and
   * `/mods/sounds/interface` were one view with two names. The owner
   * noticed before any test did, with GX's own page as the reference —
   * every item there is its own view.
   */
  it('renders ONE item — its own control, and none of its siblings', () => {
    at('/mods/interface/corners');
    const box = screen.getByTestId('mods-item').textContent ?? '';
    expect(box).toContain('Corners');
    for (const sibling of ['Color', 'Material', 'Typeface', 'Icons', 'Wallpaper', 'Cursor'])
      expect(box, `${sibling} is on the Corners page — the item level is the whole category again`)
        .not.toContain(sibling);
  });

  it('renders SizeCard for the size category — once, and not inside another card', () => {
    at('/mods/size/global');
    const box = screen.getByTestId('mods-item');
    expect(box.textContent).toContain('Interface size');
    // SizeCard is a Card of its own and owns the #interface-size anchor.
    // Wrapping it in a second Card enclosed a box in a box and mounted
    // that id twice — the layout audit's first finding.
    expect(document.querySelectorAll('#interface-size').length, 'the size anchor is mounted twice').toBe(1);
    expect(box.querySelectorAll('section section').length, 'a card inside a card').toBe(0);
  });

  it('renders an item standalone — no stray rule above it', () => {
    at('/mods/interface/corners');
    const box = screen.getByTestId('mods-item');
    // On the profile card a section stacks under the mod row and carries
    // a top rule; alone in its own card that rule has nothing above it.
    expect(box.querySelector('.border-t'), 'the item brought a stacking rule with it').toBeNull();
  });

  it('renders a sound item without the category\'s volume — that moved up a level', () => {
    at('/mods/sounds/keyboard');
    const box = screen.getByTestId('mods-item');
    expect(box.textContent).toContain('Keyboard');
    expect(box.textContent, 'a sibling switch is on the Keyboard page').not.toContain('Live alerts');
    expect(box.textContent, 'a sibling switch is on the Keyboard page').not.toContain('Interface sounds');
    expect(box.querySelector('input,[role="slider"]'), 'the volume slider is on an item page')
      .toBeNull();
  });

  it('and an address naming no item says so, rather than showing the category', () => {
    at('/mods/sounds/nonsense');
    // The title, not the description: `PageHeader description` renders
    // as a learn-once ⓘ, so the sentence is in a tooltip rather than in
    // the text — same reason the category-level test reads the title.
    expect(screen.getByText('Not a category')).toBeTruthy();
    expect(screen.queryByTestId('mods-item')).toBeNull();
  });
});

describe('a category', () => {
  /**
   * What the category OWNS renders on its page, above its tiles — the
   * sound level and cue set, shared by every lane that makes a noise.
   * It is why the item pages could stop being the whole category:
   * what was shared moved up rather than being repeated on each item.
   */
  it('shows its own control above its tiles — sounds has one', () => {
    at('/mods/sounds');
    const own = screen.getByTestId('mods-category-own');
    // The slider's own selector (slider.tsx:71): base-ui's thumb is an
    // <input>, and jsdom does not always surface the role.
    expect(own.querySelector('input,[role="slider"]'), 'the volume is not on the category page').toBeTruthy();
    expect(own.textContent).toContain('Chime');
    // The tiles are still there, under it.
    expect(screen.getByTestId('mods-category').querySelectorAll('a').length).toBe(3);
  });

  it('and a category with nothing of its own shows only its tiles', () => {
    at('/mods/interface');
    expect(screen.queryByTestId('mods-category-own'),
      'Interface grew a category-level control nobody declared').toBeNull();
  });
});

describe('an item can be put back on its own', () => {
  it('offers a reset only once something moved, and the reset restores the default', () => {
    at('/mods/interface/corners');
    expect(screen.queryByRole('button', { name: /reset corners/i }),
      'a reset offered at default — there is nothing to put back').toBeNull();

    // Move the axis the way a person would.
    fireEvent.click(screen.getByRole('button', { name: /^pill$/i }));
    expect(preferences.get('mods.theme').radius).toBe('pill');
    const reset = screen.getByRole('button', { name: /reset corners/i });

    fireEvent.click(reset);
    expect(preferences.get('mods.theme').radius, 'the reset did not restore the default')
      .toBe(MOD_DEFAULT.radius);
    expect(screen.queryByRole('button', { name: /reset corners/i })).toBeNull();
  });

  it('resets a preference-backed item too, and nothing beside it', () => {
    at('/mods/sounds/keyboard');
    fireEvent.click(screen.getByRole('switch', { name: /keyboard/i }));
    expect(preferences.get('mods.sound.keyboard')).toBe(true);
    // A neighbour, left on deliberately, must survive this item's reset.
    preferences.set('dispatch.soundOn', true);

    fireEvent.click(screen.getByRole('button', { name: /reset keyboard/i }));
    expect(preferences.get('mods.sound.keyboard')).toBe(false);
    expect(preferences.get('dispatch.soundOn'), 'resetting Keyboard reset Live alerts')
      .toBe(true);
  });
});

describe('a tile promises a control, and the page keeps the promise', () => {
  /**
   * The one thing the level-2 route cannot fake.
   *
   * `ItemControl` renders the whole CATEGORY's section, so every item
   * page has controls on it — its siblings'. Counting controls would
   * pass for an item that has none of its own, which is exactly the
   * case this describe block exists for. What cannot be borrowed from a
   * sibling is the item's own NAME: if a page headed "Entrance" never
   * says "entrance" anywhere in its controls, the tile sent somebody
   * looking for something that is not there.
   *
   * The header is excluded on purpose — it prints `item.title` itself,
   * so including it would make every case pass by construction.
   */
  for (const cat of TAXONOMY)
    for (const item of browsableItemsOf(cat.id))
      it(`${cat.id}/${item.id} — the page says "${item.title}" and names no sibling`, () => {
        at(`/mods/${cat.id}/${item.id}`);
        const control = screen.getByTestId('mods-item');
        // The subject, asserted rather than assumed. Widen `control` to
        // anything containing the header and every case below passes by
        // construction, because the header prints the title.
        expect(control.contains(screen.getByRole('heading', { name: item.title })),
          'this is measuring the header, not the controls').toBe(false);
        const text = control.textContent?.toLowerCase() ?? '';
        expect(text, `the tile opens a page with no ${item.title} on it`)
          .toContain(item.title.toLowerCase());
        // And ONLY that. Size is the declared exception: `panel: false`
        // in the taxonomy because it is one card, and its two items are
        // that card's two halves — either address renders it whole.
        if (cat.id === 'size') return;
        for (const sibling of browsableItemsOf(cat.id).filter((i) => i.id !== item.id))
          expect(text, `${sibling.title} is on the ${item.title} page — the item level is the whole category again`)
            .not.toContain(sibling.title.toLowerCase());
      });

  /** The positive control. If the check above ever reads the header —
   *  which prints the title — every case would pass by construction.
   *  This is the assertion that says the item text is a real
   *  measurement: the Motion page says motion and does NOT say
   *  entrance, which has no page and no tile. */
  it('and that check is reading the controls, not the header', () => {
    at('/mods/effects/motion');
    const control = screen.getByTestId('mods-item').textContent?.toLowerCase();
    expect(control).toContain('motion');
    expect(control, 'the Motion page grew an Entrance — give it a tile back')
      .not.toContain('entrance');
  });
});

describe('an item a look supplies but nobody sets', () => {
  const modOnly = TAXONOMY.flatMap(
    (c) => c.items.filter((i) => i.modOnly).map((i) => [c.id, i] as const));

  it('exists to be tested at all', () => {
    // Every assertion below is about `entrance`. If the flag is ever
    // dropped they would pass by having nothing to check.
    expect(modOnly.map(([c, i]) => `${c}/${i.id}`)).toEqual(['effects/entrance']);
  });

  it('gets no tile — the grid offers only what can be opened', () => {
    at('/mods/effects');
    const grid = screen.getByTestId('mods-category');
    expect(grid.textContent, 'a tile for a control that does not exist')
      .not.toContain('Entrance');
    // Not simply an empty grid: the siblings are still there, one each.
    expect(grid.querySelectorAll('a').length).toBe(browsableItemsOf('effects').length);
    expect(grid.textContent).toContain('Motion');
  });

  it('and its address stops claiming a control it does not have', () => {
    at('/mods/effects/entrance');
    // It used to be headed "Entrance" and show Motion and Ambient.
    expect(screen.queryByRole('heading', { name: /^Entrance$/ }),
      'the page still promises Entrance').toBeNull();
  });

  it('but it is still part of its category in every other way', () => {
    // The flag is about having somewhere to be clicked, nothing else: a
    // mod still carries it and the category reset still clears it.
    expect(resetAxesOf('effects'), 'the reset stopped clearing it')
      .toContain('entrance');
    expect(MOD_FIELD_CATEGORY.entrance, 'a look can no longer carry it')
      .toBe('effects');
  });
});
