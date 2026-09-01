/**
 * The panel and the page render ONE component, and the split between
 * them is a real division rather than two copies of the same chips.
 *
 * Two things this catches that nothing else would:
 *
 *   1. The global size slider is the one control deliberately in both
 *      places — the popover has it, and the /mods page hands size whole
 *      to `SizeCard`. If someone lifts the Size section out of the
 *      `compact` branch back into the shared part, the page grows a
 *      SECOND global slider under a second "Interface size" heading,
 *      two faces of one object, and nothing turns red. That is a
 *      silent bug: both sliders write the same preference, so the page
 *      still "works" while telling the user the setting lives in two
 *      places.
 *
 *   2. The compact panel's only door to the rest is the /mods link. A
 *      refactor that drops it strands five axes behind a page most
 *      people would never learn is a page.
 *
 * Rendered, not grepped: a source regex would pass on JSX that never
 * reaches the DOM.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

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
    theme: {
      mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
      motion: 'normal', icons: 'regular', mod: '',
    },
    setTheme: () => {},
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

import { ModControls } from './ModPanel';

/** Sections every surface carries — the two questions asked most often. */
const SHARED = ['Mods', 'Color'];
/** Sections the page carries and the popover sends people to the page for. */
const PAGE_ONLY = ['Corners', 'Material', 'Motion', 'Sound'];

describe('the panel is a compact view of the page, not a copy of it', () => {
  it('compact: size plus a door to everything else', () => {
    render(<ModControls compact />);
    for (const s of [...SHARED, 'Interface size']) {
      expect(screen.getByText(s), `${s} missing from the popover`).toBeTruthy();
    }
    for (const s of PAGE_ONLY) {
      expect(screen.queryByText(s), `${s} does not fit a w-56 popover`).toBeNull();
    }
    const door = document.querySelector('a[href="/mods"]');
    expect(door, 'the popover has no way to reach the rest').not.toBeNull();
  });

  it('full: everything except size, which SizeCard owns whole', () => {
    render(<ModControls />);
    for (const s of [...SHARED, ...PAGE_ONLY]) {
      expect(screen.getByText(s), `${s} missing from the page`).toBeTruthy();
    }
    // The assertion the comment above is about.
    expect(
      screen.queryByText('Interface size'),
      'the page renders SizeCard for this — a second global slider is one object with two faces',
    ).toBeNull();
    expect(
      document.querySelector('a[href="/mods"]'),
      'the page must not link to itself',
    ).toBeNull();
  });
});
