/**
 * The scope row — choosing WHICH place a background paints.
 *
 * `surfaces.test.ts` proves the list and the resolver. `context.brand`
 * proves a stored surface reaches the sheet as its own block. Neither
 * can see the control between them: a picker that writes the global
 * canvas while a place is selected, a Clear that empties every place
 * instead of one, a chip that says nothing about whether the place it
 * names is carrying a colour, or an aiming state that quietly becomes
 * a stored preference.
 *
 * Rendered and driven through the real engine, like the brand picker —
 * the refusal under test is a real refusal, found by sweeping.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const { setTheme, undoableAction, view } = vi.hoisted(() => ({
  setTheme: vi.fn(), undoableAction: vi.fn(),
  /** Who the picker thinks it is talking to. `allow` is the set of
   *  permissions this view holds; `ready` is whether they have settled. */
  view: { allow: new Set<string>(), ready: true },
}));

let theme: Record<string, unknown> = {};

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('./context', () => ({
  useMods: () => ({
    theme,
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: () => ({ value: 'chime', setValue: () => {} }),
}));
vi.mock('../components/banners/stagedAction', () => ({ undoableAction }));
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: (f: string) => view.allow.has(f),
    hasAny: (...f: string[]) => f.some((x) => view.allow.has(x)),
    hasWide: () => true, vehicleScope: 'all', role: 'fleet', ready: view.ready,
  }),
}));

import { ModControls } from './panel/ModControls';
import { fitCanvas } from './theme/canvas';
import { SURFACES, selectableSurfaces, permissionFor } from './surfaces';

const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'default', icons: 'regular', mod: '',
};

const mount = (over: Record<string, unknown> = {}) => {
  theme = { ...BASE, ...over };
  return render(<ModControls />);
};
const chip = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}$`, 'i') });
const dotOf = (name: string) =>
  (chip(name).querySelector('span[aria-hidden]') as HTMLElement | null)?.style.background;
const canvasInput = () =>
  screen.getAllByLabelText('Background').find(
    (el): el is HTMLInputElement => el instanceof HTMLInputElement)!;

/** A grey the dark mode really wears, and one it really refuses. Swept
 *  rather than written down: the gate's floor and its tones both move. */
const GREYS = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`);
const WORN = GREYS.find((h) => fitCanvas(h, 'dark').rgb !== null);
const REFUSED = GREYS.find((h) => fitCanvas(h, 'dark').rgb === null);

/** Every permission the three surfaces need — an owner's view. */
const ALL = new Set(SURFACES.flatMap((s) => permissionFor(s) ?? []));

beforeEach(() => {
  setTheme.mockClear(); undoableAction.mockClear(); cleanup();
  view.allow = new Set(ALL); view.ready = true;
});

describe('the offer is what this person can open', () => {
  const none = () => ({ ...(view.allow = new Set()), });

  it('every surface names a permission the route registry really carries', () => {
    // A surface the registry does not know gets `null` and is never
    // offered — fail closed. That is only safe while it cannot happen
    // silently, which is what this says.
    for (const s of SURFACES)
      expect(permissionFor(s), `${s.id} names a route the registry has no entry for`)
        .not.toBeNull();
    expect(ALL.size, 'no permissions collected — every test below is vacuous')
      .toBeGreaterThan(0);
  });

  it('offers nothing to somebody who can open none of them', () => {
    none();
    expect(selectableSurfaces((...f) => f.some((x) => view.allow.has(x)))).toEqual([]);
  });

  it('so the row is absent — not present with one chip', () => {
    none();
    mount();
    expect(screen.queryByText(/background applies to/i),
      'a question with one answer was asked').toBeNull();
    expect(screen.queryByRole('button', { name: /^everywhere$/i })).toBeNull();
  });

  it('and every pick still goes to the global canvas', () => {
    none();
    mount({ surfaces: { loads: '#101010' } });
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme).toHaveBeenCalledWith({ canvas: WORN });
    expect(setTheme.mock.calls[0][0]).not.toHaveProperty('surfaces');
  });

  /** The branch no shipped surface can reach. A surface naming a route
   *  the registry has no entry for gets no permission, and the rule is
   *  fail CLOSED — offering it would hand somebody a setting for a
   *  screen nobody can describe the access to. */
  it('refuses a surface whose route the registry does not carry', () => {
    const ghost = { id: 'ghost', title: 'Ghost', route: '/nowhere', why: 'x' };
    expect(permissionFor(ghost)).toBeNull();
    expect(selectableSurfaces(() => true, true, [ghost]),
      'a surface with no known permission was offered anyway').toEqual([]);
    // And the control: the same call with a real surface does offer it.
    expect(selectableSurfaces(() => true, true, [SURFACES[0]])).toHaveLength(1);
  });

  /** The other end of the same branch, and also unreachable from the
   *  shipped list: a route the registry gates on NOTHING. `/` is one —
   *  Overview adapts to your role and needs no verb — and a surface
   *  pointing at such a route is offered to everybody, because there is
   *  no permission to lack. Refusing it would be fail-closed applied to
   *  a door that has no lock. */
  it('offers a surface whose route needs no permission at all', () => {
    const open = { id: 'overview', title: 'Overview', route: '/', why: 'x' };
    expect(permissionFor(open), 'the registry started gating Overview')
      .toEqual([]);
    expect(selectableSurfaces(() => false, true, [open]),
      'a route with no permission was refused anyway').toHaveLength(1);
  });

  /**
   * A tripwire, not an assertion about behaviour.
   *
   * `selectableSurfaces` asks for ANY of a route's permissions. Today
   * no route in the whole registry names more than one, so any and all
   * are the same rule on every input that exists — a test claiming to
   * tell them apart would be measuring nothing, and writing one meant
   * fabricating a permission list the registry can never produce.
   *
   * So this says the reason out loud instead. The day a route becomes
   * reachable through either of two verbs, this fails and the real test
   * is worth writing.
   */
  it('cannot yet tell any-of from all-of, and says so', () => {
    const withAtLeast = (n: number) =>
      SURFACES.filter((s) => (permissionFor(s) ?? []).length > n).map((x) => x.id);
    // The same expression, one threshold down, must find everything —
    // otherwise the empty answer below is an empty pipeline, not a fact.
    expect(withAtLeast(0), 'permissionFor stopped returning anything')
      .toEqual(SURFACES.map((x) => x.id));
    expect(withAtLeast(1),
      'a surface now needs more than one permission — write the any-of test')
      .toEqual([]);
  });

  it('offers exactly the ones this view can reach, and no more', () => {
    const loads = SURFACES.find((s) => s.id === 'loads')!;
    view.allow = new Set(permissionFor(loads)!);
    mount();
    expect(chip('Loads')).toBeTruthy();
    for (const other of SURFACES.filter((s) => s.id !== 'loads'))
      expect(screen.queryByRole('button', { name: new RegExp(`^${other.title}$`, 'i') }),
        `${other.title} was offered to somebody who cannot open it`).toBeNull();
  });

  /** VIEW is the gate, not manage — the decision. A background is not
   *  access: it is per-user and per-device and changes nothing for
   *  anyone else. And Live Map has no `can_manage_*` verb at all, so a
   *  manage gate would remove the one screen the feature was built for. */
  it('and a view-only permission is enough — a background is not access', () => {
    const map = SURFACES.find((s) => s.id === 'live-map')!;
    const perms = permissionFor(map)!;
    expect(perms.every((p) => p.startsWith('can_view_')),
      'Live Map gained a manage verb — revisit the gate').toBe(true);
    view.allow = new Set(perms);
    mount();
    expect(chip('Live Map')).toBeTruthy();
  });

  /** An unknown is not a denial. Until the view's permissions settle,
   *  `hasAny` answers false for everything — so the row waits rather
   *  than appearing empty and growing a second later. */
  it('says nothing at all until the permissions have settled', () => {
    view.ready = false;
    mount();
    expect(screen.queryByText(/background applies to/i)).toBeNull();
  });

  /** Aimed at Loads, and then the view narrows under it — a preview of
   *  a narrower role, a permission revoked. Without the effect the
   *  target survives: the chip disappears while the picker still writes
   *  to `surfaces.loads`, so every pick lands on a screen this person
   *  cannot open and cannot find again. Re-RENDERED rather than
   *  remounted — a fresh mount starts at Everywhere anyway and would
   *  prove nothing. */
  it('and drops an aim it can no longer reach', () => {
    const { rerender } = mount({ canvas: '#111111' });
    fireEvent.click(chip('Loads'));
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme).toHaveBeenCalledWith({ surfaces: { loads: WORN } });

    setTheme.mockClear();
    view.allow = new Set();
    rerender(<ModControls />);
    expect(screen.queryByRole('button', { name: /^loads$/i }),
      'the chip survived the permission it needs').toBeNull();
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme, 'a pick landed on a place this person cannot open')
      .toHaveBeenCalledWith({ canvas: WORN });
  });
});

describe('the row offers everywhere plus the named places, and nothing else', () => {
  it('names each surface once, with Everywhere first', () => {
    mount();
    expect(chip('Everywhere')).toBeTruthy();
    for (const s of SURFACES) expect(chip(s.title), `${s.title} is not offered`).toBeTruthy();
  });

  it('says why the selected place earns its own look', () => {
    mount();
    fireEvent.click(chip('Loads'));
    const why = SURFACES.find((s) => s.id === 'loads')!.why;
    expect(screen.getByText(new RegExp(why.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'))).toBeTruthy();
  });
});

describe('what a pick writes depends on where it is aimed', () => {
  it('Everywhere writes the global canvas, and no surfaces', () => {
    expect(WORN, 'no grey is wearable in dark — this test is watching nothing').toBeDefined();
    mount();
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme).toHaveBeenCalledWith({ canvas: WORN });
    expect(setTheme.mock.calls[0][0]).not.toHaveProperty('surfaces');
  });

  it('a named place writes only that key, and never the global canvas', () => {
    mount({ canvas: '#111111' });
    fireEvent.click(chip('Work Orders'));
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme).toHaveBeenCalledWith({ surfaces: { 'work-orders': WORN } });
    expect(setTheme.mock.calls[0][0]).not.toHaveProperty('canvas');
  });

  it('and leaves the other places exactly as they were', () => {
    mount({ surfaces: { loads: '#101010' } });
    fireEvent.click(chip('Live Map'));
    fireEvent.change(canvasInput(), { target: { value: WORN! } });
    expect(setTheme).toHaveBeenCalledWith({ surfaces: { loads: '#101010', 'live-map': WORN } });
  });

  it('clearing one place empties that key alone', () => {
    mount({ surfaces: { loads: '#101010', 'live-map': '#121212' } });
    fireEvent.click(chip('Loads'));
    fireEvent.click(chip('Clear'));
    expect(setTheme).toHaveBeenCalledWith({ surfaces: { 'live-map': '#121212' } });
  });

  it('and clearing the last one leaves no empty map behind', () => {
    mount({ surfaces: { loads: '#101010' } });
    fireEvent.click(chip('Loads'));
    fireEvent.click(chip('Clear'));
    expect(setTheme).toHaveBeenCalledWith({ surfaces: undefined });
  });
});

describe('aiming is a question about this moment, not a preference', () => {
  it('choosing a place stores nothing on its own', () => {
    mount();
    fireEvent.click(chip('Loads'));
    fireEvent.click(chip('Live Map'));
    expect(setTheme, 'the aim was written to the theme').not.toHaveBeenCalled();
  });

  it('and the picker reads the selected place, not the global canvas', () => {
    mount({ canvas: '#111111', surfaces: { loads: '#191919' } });
    expect(canvasInput().value).toBe('#111111');
    fireEvent.click(chip('Loads'));
    expect(canvasInput().value).toBe('#191919');
  });
});

describe('a chip shows what its place is painting', () => {
  it('wears the colour when the place carries one', () => {
    mount({ canvas: '#111111', surfaces: { loads: '#191919' } });
    expect(dotOf('Loads')).toBe('rgb(25, 25, 25)');
    expect(dotOf('Everywhere')).toBe('rgb(17, 17, 17)');
  });

  it('and shows nothing when it does not', () => {
    mount({ surfaces: { loads: '#191919' } });
    expect(dotOf('Work Orders'), 'an unthemed place is wearing a dot').toBeUndefined();
    expect(dotOf('Everywhere'), 'the global dot appeared with no global canvas').toBeUndefined();
  });

  /** Stored is not worn. A canvas the current mode refuses falls back to
   *  the built-in look, so its dot would point at a colour nobody sees. */
  it('stays bare for a colour this mode cannot wear', () => {
    expect(REFUSED, 'no grey is refused in dark — this test is watching nothing').toBeDefined();
    mount({ surfaces: { loads: REFUSED! } });
    expect(dotOf('Loads')).toBeUndefined();
  });

  /** A bare chip must not say two things at once. Without this line,
   *  "no colour set" and "one set that this mode refuses" look alike —
   *  and the second is the one you need to know before picking over it. */
  it('and says WHY it is bare, naming the place and the mode', () => {
    mount({ surfaces: { loads: REFUSED! } });
    expect(screen.getByText(/not worn in dark mode: loads\./i)).toBeTruthy();
  });

  it('which it does not say when every stored place is wearing its colour', () => {
    mount({ surfaces: { loads: WORN! } });
    expect(screen.queryByText(/not worn in/i), 'warned about a place that is fine').toBeNull();
    expect(screen.getByText(/one background for the whole app/i)).toBeTruthy();
  });
});
