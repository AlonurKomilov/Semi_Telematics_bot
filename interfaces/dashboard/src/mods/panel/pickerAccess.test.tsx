/**
 * Every colour picker on this panel is ONE control.
 *
 * They were two: a swatch `<button>` with no handler, and behind it an
 * `opacity-0` `<input type="color">` carrying the same accessible name.
 * A keyboard user tabbed to the button, pressed Enter, got nothing,
 * tabbed again into a control with no visible focus at all — four dead
 * stops on this surface. The button opens the picker now and the input
 * has left the tab order, so what is announced and what works are the
 * same thing.
 *
 * And the line under a picker is announced: a refusal that only exists
 * as grey text is a refusal a screen-reader user never hears.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const { setTheme } = vi.hoisted(() => ({ setTheme: vi.fn() }));
let theme: Record<string, unknown> = {};

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('../context', () => ({
  useMods: () => ({
    theme, setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: () => ({ value: 'chime', setValue: () => {} }),
}));
vi.mock('../../components/banners/stagedAction', () => ({ undoableAction: vi.fn() }));
vi.mock('../../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => false, hasAny: () => false,
    hasWide: () => true, vehicleScope: 'all', role: 'fleet', ready: true,
  }),
}));

import { ModControls } from './ModControls';
import { groundTokens } from '../theme/grounds';

/** Every picker the panel offers, by the name it announces. */
const PICKERS = ['Background', 'Custom', 'Cards', 'Sidebar'];

const mount = (over: Record<string, unknown> = {}) => {
  theme = {
    mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
    motion: 'default', mod: '', ...over,
  };
  return render(<ModControls />);
};
const inputFor = (name: string) =>
  screen.getAllByLabelText(name).find(
    (el): el is HTMLInputElement => el instanceof HTMLInputElement)!;

beforeEach(() => { setTheme.mockClear(); cleanup(); });

describe('one control per picker', () => {
  it('announces the button, not the input', () => {
    mount();
    for (const name of PICKERS) {
      const input = inputFor(name);
      expect(input.getAttribute('aria-hidden'), `${name}: the input is announced as well as the button`).toBe('true');
      expect(input.tabIndex, `${name}: the input is still a tab stop`).toBe(-1);
      // Exactly one thing with this name is reachable: the button.
      expect(screen.getAllByRole('button', { name }).length, `${name}: not exactly one announced control`).toBe(1);
    }
  });

  it('and the button opens the picker instead of doing nothing', () => {
    mount();
    for (const name of PICKERS) {
      const input = inputFor(name);
      const opened = vi.spyOn(input, 'click');
      fireEvent.click(screen.getByRole('button', { name }));
      expect(opened, `${name}: the button is a dead stop`).toHaveBeenCalled();
      opened.mockRestore();
    }
  });

  it('and the button carries a focus ring, since it is the one that gets focus', () => {
    mount();
    for (const name of PICKERS)
      expect(screen.getByRole('button', { name }).className,
        `${name}: focusing the only reachable control shows nothing`).toMatch(/focus-visible:ring/);
  });
});

describe('what a refusal says, and to whom', () => {
  it('is announced rather than only printed', () => {
    const REFUSED = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`)
      .find((h) => groundTokens('card', h, 'dark').tokens === null)!;
    mount();
    fireEvent.change(inputFor('Cards'), { target: { value: REFUSED } });
    const note = screen.getByText(/would not be readable/i);
    expect(note.getAttribute('role'), 'the refusal is silent to a screen reader').toBe('status');
    expect(note.getAttribute('aria-live')).toBe('polite');
  });

  it('and a ground says whether it is set', () => {
    const WORN = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`)
      .find((h) => groundTokens('card', h, 'dark').tokens !== null)!;
    mount();
    expect(screen.getByRole('button', { name: 'Cards' }).getAttribute('aria-pressed')).toBe('false');
    cleanup();
    mount({ grounds: { card: WORN } });
    expect(screen.getByRole('button', { name: 'Cards' }).getAttribute('aria-pressed')).toBe('true');
  });
});

describe('a refusal belongs to the colour it was said about', () => {
  it('and does not outlive a mode change', () => {
    const REFUSED = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`)
      .find((h) => groundTokens('card', h, 'dark').tokens === null)!;
    mount();
    fireEvent.change(inputFor('Cards'), { target: { value: REFUSED } });
    expect(screen.queryByText(/would not be readable/i)).toBeTruthy();
    cleanup();
    // The panel is re-rendered in the other mode: the picker is keyed on
    // it, so the refusal — which is mode-dependent — starts again.
    mount({ mode: 'light' });
    expect(screen.queryByText(/would not be readable/i),
      'a refusal about the other mode survived the switch').toBeNull();
  });
});

describe('the popover cannot outgrow the window', () => {
  it('caps its height and scrolls', async () => {
    const src = (await import('node:fs')).readFileSync(
      (await import('node:path')).join(__dirname, 'ModPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    // It grows by one group every time an axis is added, and at 706px it
    // already clipped a 1366x768 laptop — taking the Size slider and the
    // door to the rest of the axes with it.
    expect(src, 'the panel cannot scroll what it clips').toMatch(/overflow-y-auto/);
    // MEASURED, not guessed: `calc(100vh - 4rem)` would encode today's
    // header and gutter, and both move with the Size axis — which is why
    // `chrome.test.ts` refuses a viewport calc that subtracts the frame.
    expect(src, 'the cap is a guess at the frame').not.toMatch(/calc\(100d?vh\s*-/);
    expect(src, 'the panel never measures the room it has')
      .toMatch(/window\.innerHeight - el\.getBoundingClientRect\(\)\.top/);
    expect(src, 'the cap is never applied').toMatch(/maxHeight: maxH/);
  });
});
