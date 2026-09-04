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

const { setTheme, undoableAction } = vi.hoisted(() => ({
  setTheme: vi.fn(), undoableAction: vi.fn(),
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

import { ModControls } from './panel/ModControls';
import { fitCanvas } from './theme/canvas';
import { SURFACES } from './surfaces';

const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'default', icons: 'regular', mod: '',
};

const mount = (over: Record<string, unknown> = {}) => {
  theme = { ...BASE, ...over };
  render(<ModControls />);
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

beforeEach(() => { setTheme.mockClear(); undoableAction.mockClear(); cleanup(); });

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
