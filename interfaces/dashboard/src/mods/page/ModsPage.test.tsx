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
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import ModsPage from './ModsPage';
import { ModProvider } from '../context';
import { TAXONOMY } from '../taxonomy';
import { preferences } from '../../preferences';

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

beforeEach(() => { cleanup(); localStorage.clear(); });

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
  it('renders the card\'s own section — the same controls, not a copy', () => {
    at('/mods/interface/corners');
    const box = screen.getByTestId('mods-item');
    // The Interface section's headings, exactly as the profile card
    // renders them. A page-side reimplementation would have to
    // reproduce all of these to pass.
    for (const label of ['Color', 'Corners', 'Material', 'Typeface', 'Icons'])
      expect(box.textContent, `${label} missing — the item level is not the card's Section`).toContain(label);
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

  it('renders a section standalone — no stray rule above its heading', () => {
    at('/mods/interface/corners');
    const box = screen.getByTestId('mods-item');
    // On the profile card a Section stacks under the mod row and carries
    // a top rule; alone in its own card that rule has nothing above it.
    expect(box.querySelector('.border-t'), 'the section brought its stacking rule with it').toBeNull();
  });

  it('renders the sounds section for a sound item', () => {
    at('/mods/sounds/keyboard');
    expect(screen.getByTestId('mods-item').textContent).toContain('Keyboard');
  });
});
