/**
 * The background picker, from the chip to the palette.
 *
 * `canvas.test.ts` proves the engine judges a canvas correctly and
 * derives the right tokens. `context.canvas.test.tsx` proves the
 * provider installs them. This is the part between: a picker that
 * writes the wrong field, swallows a refusal, or claims a background is
 * painting when the mode cannot wear it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const { setTheme, undoableAction } = vi.hoisted(() => ({
  setTheme: vi.fn(), undoableAction: vi.fn(),
}));
vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('../../components/banners/stagedAction', () => ({ undoableAction }));

import { CanvasChip } from './CanvasChip';
import { CANVAS_SEED } from '../theme/canvas';
import type { Mode } from '../context';

const mount = (canvas: string | undefined, mode: Mode = 'light') =>
  render(
    <CanvasChip
      canvas={canvas}
      mode={mode}
      onPick={(hex) => setTheme({ canvas: hex })}
      onClear={() => setTheme({ canvas: undefined })}
    />,
  );

const input = () => document.querySelector('input[type="color"]') as HTMLInputElement;
const chip = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}$`, 'i') });

beforeEach(() => { setTheme.mockClear(); undoableAction.mockClear(); cleanup(); });

describe('what a pick writes', () => {
  it('writes the canvas seed, and nothing else', () => {
    mount(undefined, 'light');
    fireEvent.change(input(), { target: { value: '#f5f0e8' } });
    expect(setTheme).toHaveBeenCalledWith({ canvas: '#f5f0e8' });
    expect(setTheme.mock.calls[0][0]).not.toHaveProperty('brand');
  });

  it('writes NOTHING when the canvas would break a tone, and names it', () => {
    mount(undefined, 'light');
    // Navy under the light tones: --info measures 2.49:1.
    fireEvent.change(input(), { target: { value: '#1a2332' } });
    expect(setTheme, 'a refused background was stored anyway').not.toHaveBeenCalled();
    expect(screen.getByText(/info colour would not be readable/i)).toBeTruthy();
  });

  it('opens on the mode\'s own canvas, not on grey', () => {
    // Through CANVAS_SEED, not a literal: the dark canvas is #030303,
    // not the #0a0a0a a reader would guess, and a literal here would
    // have hard-coded the same wrong guess the drift test caught.
    for (const mode of ['dark', 'light'] as const) {
      cleanup(); mount(undefined, mode);
      expect(input().value).toBe(CANVAS_SEED[mode]);
    }
    // …and the two really differ, or this proves nothing.
    expect(CANVAS_SEED.dark).not.toBe(CANVAS_SEED.light);
  });

  it('opens on the chosen one once there is one', () => {
    mount('#f5f0e8', 'light');
    expect(input().value).toBe('#f5f0e8');
  });
});

describe('what the chip says', () => {
  it('reads as active while the background is painting', () => {
    mount('#f5f0e8', 'light');
    expect(chip('Background').getAttribute('aria-pressed')).toBe('true');
  });

  it('says so when the mode cannot wear the stored one, and yields', () => {
    // Cream is fine in light and breaks --warn in dark (1.56:1).
    mount('#f5f0e8', 'dark');
    expect(chip('Background').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText(/cannot be worn in dark mode/i)).toBeTruthy();
  });

  it('offers Clear whenever one is STORED, even when it cannot be worn', () => {
    // The same trap the accent picker had: gating the way out on "is it
    // painting" strands the person who most needs it.
    mount('#f5f0e8', 'dark');
    expect(screen.queryByRole('button', { name: /^clear$/i })).not.toBeNull();
  });

  it('offers no Clear when there is nothing to clear', () => {
    mount(undefined, 'light');
    expect(screen.queryByRole('button', { name: /^clear$/i })).toBeNull();
  });

  it('clearing is undoable, and the undo restores the same hex', async () => {
    mount('#f5f0e8', 'light');
    fireEvent.click(chip('Clear'));
    expect(setTheme).toHaveBeenCalledWith({ canvas: undefined });
    expect(undoableAction, 'the background was thrown away with no undo').toHaveBeenCalled();
    setTheme.mockClear();
    const { undo } = undoableAction.mock.calls[0][0] as { undo: () => Promise<void> };
    await undo();
    expect(setTheme).toHaveBeenCalledWith({ canvas: '#f5f0e8' });
  });
});
