/**
 * The custom colour, from the chip to what it writes.
 *
 * `accent.test.ts` proves the engine judges a colour correctly.
 * `accentCascade.test.ts` proves an accepted colour reaches the screen.
 * Neither can see the thing between them: a picker that writes the wrong
 * field, keeps a pack highlighted while a custom colour paints, or
 * swallows a refusal and leaves the person looking at an unchanged
 * screen wondering what they did wrong.
 *
 * Rendered rather than grepped, and driven through the real engine — the
 * refusal under test is a real refusal, found by sweeping, not a mock
 * saying no on cue.
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

import { ModControls } from './ModPanel';
import { accentTokens, TONES, ACCENT_BAND } from './theme/accent';
import { oklchToSrgb, toHex } from './theme/contrast';
import { THEME_PACKS } from './catalogue';

const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'normal', icons: 'regular', mod: '',
};

const mount = (over: Record<string, unknown> = {}) => {
  theme = { ...BASE, ...over };
  render(<ModControls />);
};
const colourInput = () => document.querySelector('input[type="color"]') as HTMLInputElement;
const chip = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}$`, 'i') });

/** A light-mode hue the engine really refuses. Swept, not assumed —
 *  hard-coding one would rot the day the floor or the tones move. */
const REFUSED = Array.from({ length: 360 }, (_, H) =>
  toHex(oklchToSrgb(ACCENT_BAND.light, 0.2, H).rgb))
  .find((hex) => accentTokens(hex, 'light').tokens === null);

beforeEach(() => { setTheme.mockClear(); undoableAction.mockClear(); cleanup(); });

describe('the control exists and is the platform picker', () => {
  it('sits beside the packs with a real colour input on it', () => {
    mount();
    expect(chip('Custom')).toBeTruthy();
    expect(colourInput(), 'no colour input — the chip does nothing').toBeTruthy();
  });
});

describe('what a pick writes', () => {
  it('writes brand, and never touches accent', () => {
    mount();
    fireEvent.change(colourInput(), { target: { value: '#ff6a00' } });
    expect(setTheme).toHaveBeenCalledWith({ brand: '#ff6a00' });
    expect(setTheme.mock.calls[0][0]).not.toHaveProperty('accent');
  });

  it('writes NOTHING when the engine refuses, and says which tone', () => {
    expect(REFUSED, 'no light hue is refused — this test is watching nothing').toBeDefined();
    mount({ mode: 'light' });
    fireEvent.change(colourInput(), { target: { value: REFUSED! } });
    expect(setTheme, 'a refused colour was stored anyway').not.toHaveBeenCalled();
    expect(screen.getByText(/reads as the .* colour/i)).toBeTruthy();
  });

  it('clearing writes an absent brand rather than a colour', () => {
    mount({ brand: '#ff6a00' });
    fireEvent.click(chip('Clear'));
    expect(setTheme).toHaveBeenCalledWith({ brand: undefined });
  });
});

describe('what the chips show while a custom colour paints', () => {
  it('no pack reads as selected', () => {
    mount({ brand: '#ff6a00', accent: 'blue' });
    expect(chip('Custom').getAttribute('aria-pressed')).toBe('true');
    for (const pack of ['Blue', 'Purple', 'Green', 'Azure'])
      expect(chip(pack).getAttribute('aria-pressed'), `${pack} is highlighted under a custom colour`)
        .toBe('false');
  });

  it('and the pack comes back the moment there is no custom colour', () => {
    mount({ accent: 'blue' });
    expect(chip('Blue').getAttribute('aria-pressed')).toBe('true');
    expect(chip('Custom').getAttribute('aria-pressed')).toBe('false');
    expect(screen.queryByRole('button', { name: /^clear$/i }), 'Clear offered with nothing to clear')
      .toBeNull();
  });

  /**
   * A hex can clear the tones on near-black and collide on white, and
   * then the stored colour is simply not what paints in this mode — the
   * pack underneath is. Saying nothing would leave a chip highlighted
   * for a colour that is not on the screen.
   */
  it('a colour that cannot be worn in this mode says so, and yields to the pack', () => {
    const stuck = Object.keys(TONES.light)
      .map((n) => {
        const [L, C, H] = TONES.light[n];
        return toHex(oklchToSrgb(L, C, H).rgb);
      })
      .find((hex) => accentTokens(hex, 'dark').tokens !== null
        && accentTokens(hex, 'light').tokens === null);
    if (!stuck) return; // nothing in the tone table has this shape today
    mount({ mode: 'light', brand: stuck, accent: 'blue' });
    expect(chip('Custom').getAttribute('aria-pressed')).toBe('false');
    expect(chip('Blue').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText(/cannot be worn in light mode/i)).toBeTruthy();
  });

  it('says when the engine had to move the colour', () => {
    // A hue that is accepted only after a nudge — swept, for the same
    // reason as REFUSED.
    const nudged = Array.from({ length: 360 }, (_, H) =>
      toHex(oklchToSrgb(ACCENT_BAND.light, 0.2, H).rgb))
      .find((hex) => accentTokens(hex, 'light').movedFrom);
    expect(nudged, 'nothing is ever nudged — this test is watching nothing').toBeDefined();
    mount({ mode: 'light', brand: nudged });
    expect(screen.getByText(/lightened away from the .* colour/i)).toBeTruthy();
  });
});

describe('the picker opens on something worth starting from', () => {
  /**
   * A colour picker that opens on grey asks a blank question. The one
   * honest starting point is the colour already on the screen, so the
   * person adjusts rather than invents.
   */
  it('starts at the pack currently painting, in the mode being worn', () => {
    for (const mode of ['dark', 'light'] as const)
      for (const pack of THEME_PACKS) {
        cleanup();
        mount({ mode, accent: pack.id });
        expect(colourInput().value, `${pack.id}/${mode}`).toBe(pack.seed[mode]);
      }
  });

  it('and at the picked colour once there is one', () => {
    mount({ brand: '#ff6a00' });
    expect(colourInput().value).toBe('#ff6a00');
  });
});

describe('clearing a colour is undoable, like everything else that destroys work', () => {
  it('offers the way back, and the way back restores the same hex', () => {
    mount({ brand: '#ff6a00' });
    fireEvent.click(chip('Clear'));
    expect(undoableAction, 'Clear threw the colour away with no undo').toHaveBeenCalled();

    setTheme.mockClear();
    const { undo } = undoableAction.mock.calls[0][0] as { undo: () => Promise<void> };
    return undo().then(() => {
      expect(setTheme, 'the undo restored nothing').toHaveBeenCalledWith({ brand: '#ff6a00' });
    });
  });

  it('never offers an undo for a colour that was refused and so never stored', () => {
    expect(REFUSED).toBeDefined();
    mount({ mode: 'light' });
    fireEvent.change(colourInput(), { target: { value: REFUSED! } });
    expect(undoableAction).not.toHaveBeenCalled();
  });
});
