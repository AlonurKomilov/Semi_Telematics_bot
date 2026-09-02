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
import { render, screen, fireEvent } from '@testing-library/react';

// `vi.mock` is hoisted above the file, so the spy has to be hoisted too
// or the factory closes over a variable that does not exist yet.
const { setTheme } = vi.hoisted(() => ({ setTheme: vi.fn() }));

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

import { ModControls } from './ModPanel';
import { MODS } from './catalogue';

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
    // The literal, NOT an import of MODS_HREF: a guard that reads its
    // subject from the source it guards passes on any value that source
    // happens to hold. Changing the address should be a deliberate act
    // that turns this red.
    const door = document.querySelector('a[href="/profile#modifications"]');
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
      document.querySelector('a[href="/profile#modifications"]'),
      'the full surface must not link to itself',
    ).toBeNull();
  });
});

/**
 * The mod-only axes, and why a SOURCE grep could never guard them.
 *
 * `icons` and `entrance` are the two things a mod may carry that the
 * panel offers no control for — the asymmetry is the point, because a
 * mod whose every setting is also a chip is a shortcut, not a look.
 *
 * The guard that used to assert this read ModPanel.tsx and matched
 * /setTheme\(\{\s*icons:/ — which requires the axis to be the FIRST key
 * after the brace. The real write site is
 * `setTheme({ mod: m.id, accent: …, …icons })`, so the axis is never
 * first and THE REGEX COULD NEVER MATCH ANYTHING. It passed on an
 * impossibility for its whole life.
 *
 * Broadening the regex does not fix it: `applyMod` writes both axes
 * legitimately — that IS a mod being installed — so a looser pattern
 * goes red on correct code. The distinction is not lexical, it is
 * behavioural: a MOD CHIP may write them, no other control may. So the
 * guard clicks the panel instead of reading it.
 */
const MOD_ONLY = ['icons', 'entrance'];

describe('only a mod may reach a mod-only axis', () => {
  it('no other control writes one', () => {
    setTheme.mockClear();
    render(<ModControls />);

    const modLabels = new Set(MODS.map((m) => m.label));
    const others = screen
      .getAllByRole('button')
      .filter((b) => !modLabels.has((b.textContent ?? '').trim()));

    // Without this the test passes on a panel that rendered nothing —
    // a mistyped prop, an early return, a section that never mounts.
    expect(
      others.length,
      'clicked no controls, so the assertion below proves nothing',
    ).toBeGreaterThan(8);

    for (const b of others) fireEvent.click(b);

    for (const [patch] of setTheme.mock.calls) {
      for (const axis of MOD_ONLY) {
        expect(
          Object.keys(patch ?? {}),
          `a control that is not a mod chip wrote "${axis}"`,
        ).not.toContain(axis);
      }
    }
  });

  it('and a mod chip still may — the asymmetry is the point', () => {
    setTheme.mockClear();
    render(<ModControls />);
    const cab = screen.getByRole('button', { name: MODS[0].label });
    fireEvent.click(cab);
    // Cab declares icons; if applyMod ever stopped carrying the mod-only
    // axes the test above would still pass, and this is what notices.
    const wrote = setTheme.mock.calls.flatMap(([p]) => Object.keys(p ?? {}));
    expect(wrote, 'installing a mod no longer carries its mod-only axes').toContain('icons');
  });
});

/**
 * The section partition.
 *
 * `section` slices the same component four ways, and the failure it
 * invites is a control appearing in two cards at once — or in none,
 * which is worse, because a section that silently renders nothing looks
 * like a section with nothing in it.
 *
 * Every section is asserted BOTH ways: it shows its own labels, and it
 * shows none of the others'. Without the negative half a section that
 * ignored the prop and rendered everything would pass.
 */
const SECTIONS = {
  mods: ['Mods'],
  interface: ['Color', 'Corners', 'Material'],
  effects: ['Motion'],
  sounds: ['Sound'],
} as const;

describe('each section renders its own controls and only its own', () => {
  it.each(Object.keys(SECTIONS) as (keyof typeof SECTIONS)[])(
    '%s',
    (section) => {
      const { container } = render(<ModControls section={section} />);

      for (const label of SECTIONS[section]) {
        expect(screen.getByText(label), `${section} is missing "${label}"`).toBeTruthy();
      }
      for (const [other, labels] of Object.entries(SECTIONS)) {
        if (other === section) continue;
        for (const label of labels) {
          expect(
            screen.queryByText(label),
            `${section} leaked "${label}", which belongs to ${other}`,
          ).toBeNull();
        }
      }

      // A section that rendered no controls at all would satisfy every
      // negative assertion above.
      expect(
        container.querySelectorAll('button, input').length,
        `${section} rendered no controls`,
      ).toBeGreaterThan(0);
    },
  );

  it('renders every section when none is named', () => {
    // The popover and the guards both want the whole set; if the default
    // ever became "one section" the four tests above would still pass.
    render(<ModControls />);
    for (const labels of Object.values(SECTIONS)) {
      for (const label of labels) expect(screen.getByText(label)).toBeTruthy();
    }
  });
});

