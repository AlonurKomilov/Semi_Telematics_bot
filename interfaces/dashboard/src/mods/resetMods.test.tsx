/**
 * What "Reset appearance" is allowed to touch.
 *
 * Two axes are deliberately NOT in it, and both absences are the kind
 * that reads as an oversight to the next person:
 *
 *   `mode` — dark/light is about the room the person is sitting in, not
 *   about the look. It is the one axis a mod may never carry, and a
 *   reset that threw a light-mode user into dark would be that same
 *   mistake arriving from the other direction.
 *
 *   `size` — SizeCard owns size whole and has its own reset beside its
 *   own title. Neither card reaches into the other.
 *
 * The coverage assertion is the point: every field of MOD_DEFAULT is
 * either reset or named in the exclusion list, so ADDING AN AXIS forces
 * the decision instead of quietly skipping it. That is the same shape
 * `PREPAINT_AXES` uses in the registry, and it exists because the axis
 * set has grown four times already.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MOD_DEFAULT, DEFS } from '../preferences/registry';

/** Axes the reset must NOT restore, each with the reason it is exempt. */
const EXCLUDED = {
  mode: 'the room the person is in, not the look',
  // A derived alias re-written by setTheme on every write; it has no
  // independent value to reset.
  color: 'derived from mode+accent by the one writer in context.tsx',
} as const;

const undoSpy = vi.fn();
vi.mock('../components/banners/stagedAction', () => ({
  undoableAction: (o: unknown) => undoSpy(o),
}));

const setTheme = vi.fn();
const setValue = vi.fn();
let theme: Record<string, unknown> = { ...MOD_DEFAULT };
let prefs: Record<string, unknown> = {
  'mods.sound.pack': DEFS['mods.sound.pack'].default,
  'mods.sound.volume': DEFS['mods.sound.volume'].default,
};

vi.mock('./context', () => ({
  useMods: () => ({
    theme,
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences/usePreference', () => ({
  usePreference: (k: string) => ({ value: prefs[k], setValue }),
}));

import { RESET_AXES, ResetMods } from './Modifications';

describe('Reset appearance owns exactly the axes it should', () => {
  it('covers every axis of MOD_DEFAULT, or names why not', () => {
    for (const key of Object.keys(MOD_DEFAULT)) {
      const reset = key in RESET_AXES;
      const excluded = key in EXCLUDED;
      expect(
        reset !== excluded,
        `"${key}" is ${reset && excluded ? 'both reset AND excluded' : 'neither reset nor excluded'} — a new axis must force the decision`,
      ).toBe(true);
    }
  });

  it('never restores mode, and never reaches into size', () => {
    expect('mode' in RESET_AXES, EXCLUDED.mode).toBe(false);
    expect('global' in RESET_AXES).toBe(false);
    expect('regions' in RESET_AXES).toBe(false);
  });

  it('restores identity and custom tokens, not just the chips', () => {
    // An installed mod and an injected token set both survive a reset
    // that only walks MOD_DEFAULT's keys — neither is one of them.
    expect(RESET_AXES).toHaveProperty('mod');
    expect(RESET_AXES).toHaveProperty('tokens');
    expect(RESET_AXES.mod).toBeUndefined();
    expect(RESET_AXES.tokens).toBeUndefined();
  });
});

describe('the control itself', () => {
  it('is absent while there is nothing to undo', () => {
    // Not disabled — ABSENT. That is not the hidden-control failure
    // SizeCard warns about: there a control vanishes because of an
    // external condition the person cannot see, so they hunt for it.
    // Here its absence means "nothing to reset", and the reason is on
    // screen — every chip is visibly on its default.
    theme = { ...MOD_DEFAULT };
    render(<ResetMods />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('writes the defaults and offers the way back', () => {
    undoSpy.mockClear(); setTheme.mockClear(); setValue.mockClear();
    theme = { ...MOD_DEFAULT, accent: 'purple', mod: 'wall' };
    render(<ResetMods />);
    fireEvent.click(screen.getByRole('button'));
    expect(setTheme).toHaveBeenCalledWith(RESET_AXES);
    expect(undoSpy).toHaveBeenCalledTimes(1);
    // The snapshot must be the value BEFORE the write, or "undo" walks
    // the user back to the defaults they just asked to leave.
    const undo = (undoSpy.mock.calls[0][0] as { undo: () => void }).undo;
    setTheme.mockClear();
    undo();
    expect(setTheme).toHaveBeenCalledWith(
      expect.objectContaining({ accent: 'purple', mod: 'wall' }),
    );
  });
});
