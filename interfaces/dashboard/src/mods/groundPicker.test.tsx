/**
 * The grounds row: two seeds, one gate, and a clear that can be undone.
 *
 * What it holds is the part a person can get wrong. A colour the
 * semantic tones cannot be read on is never written — the panel says
 * which tone, and the store keeps what it had. A seed that stops being
 * wearable when the mode changes is not shown as worn. And clearing one
 * ground leaves the other alone, because they are two decisions.
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
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => false, hasAny: () => false,
    hasWide: () => true, vehicleScope: 'all', role: 'fleet', ready: true,
  }),
}));

import { ModControls } from './panel/ModControls';
import { GROUNDS } from './theme/grounds';
import { groundTokens } from './theme/grounds';

const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'default', mod: '',
};
const mount = (over: Record<string, unknown> = {}) => {
  theme = { ...BASE, ...over };
  return render(<ModControls />);
};
const input = (label: string) =>
  screen.getAllByLabelText(label).find(
    (el): el is HTMLInputElement => el instanceof HTMLInputElement)!;

const GREYS = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`);
const WORN = GREYS.find((h) => groundTokens('card', h, 'dark').tokens !== null)!;
const REFUSED = GREYS.find((h) => groundTokens('card', h, 'dark').tokens === null)!;

beforeEach(() => { setTheme.mockClear(); undoableAction.mockClear(); cleanup(); });

describe('the row offers every ground', () => {
  it('one picker each, by its own name', () => {
    mount();
    expect(GROUNDS.length).toBeGreaterThan(1);
    for (const g of GROUNDS) expect(input(g.label)).toBeTruthy();
  });
});

describe('what a pick writes', () => {
  it('seeds that ground and leaves the others alone', () => {
    mount({ grounds: { sidebar: WORN } });
    fireEvent.change(input('Cards'), { target: { value: WORN } });
    expect(setTheme).toHaveBeenCalledWith({ grounds: { sidebar: WORN, card: WORN } });
  });

  it('and a colour the tones cannot be read on is refused, by name, unwritten', () => {
    mount();
    fireEvent.change(input('Cards'), { target: { value: REFUSED } });
    expect(setTheme, `${REFUSED} was written though the gate refuses it`).not.toHaveBeenCalled();
    expect(screen.getByText(/would not be readable/i)).toBeTruthy();
  });
});

describe('clearing one', () => {
  it('drops that key, keeps the other, and offers an undo', () => {
    mount({ grounds: { card: WORN, sidebar: WORN } });
    const clears = screen.getAllByRole('button', { name: /^clear$/i });
    fireEvent.click(clears[clears.length - 1]);
    expect(setTheme).toHaveBeenCalledWith({ grounds: { card: WORN } });
    expect(undoableAction).toHaveBeenCalled();
  });

  it('and clearing the last one removes the object rather than leaving it empty', () => {
    mount({ grounds: { card: WORN } });
    fireEvent.click(screen.getAllByRole('button', { name: /^clear$/i })[0]);
    expect(setTheme).toHaveBeenCalledWith({ grounds: undefined });
  });
});

describe('a seed the mode cannot wear', () => {
  it('says so instead of pretending it is on', () => {
    // The engine drops it on the paint path; the panel's job is to stop
    // the person wondering why nothing changed.
    const light = GREYS.find((h) =>
      groundTokens('card', h, 'dark').tokens !== null
      && groundTokens('card', h, 'light').tokens === null);
    if (!light) return;                       // no such colour today: nothing to claim
    mount({ mode: 'light', grounds: { card: light } });
    expect(screen.getByText(/not worn in light mode/i)).toBeTruthy();
  });
});
