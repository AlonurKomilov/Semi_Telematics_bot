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

import { RESET_AXES, SECTION_AXES, CONTAINER_AXES, ResetMods } from './Modifications';
import { MOD_SECTIONS } from './ModPanel';

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

/**
 * How the reset scopes divide, and what the division must never do.
 *
 * NOT asserted here, deliberately: that every RESET_AXES key belongs to
 * some group. `RESET_AXES` is BUILT by spreading the groups, so that is
 * true by construction and a test for it would pass whatever anyone
 * wrote. A guard whose subject cannot vary is decoration.
 *
 * What CAN go wrong is asserted below.
 */
const SECTION_KEYS = Object.fromEntries(
  Object.entries(SECTION_AXES).map(([k, v]) => [k, Object.keys(v)]),
) as Record<keyof typeof SECTION_AXES, string[]>;

describe('the reset scopes divide cleanly', () => {
  it('no axis belongs to two sections', () => {
    // A key in both would be silently overwritten by the later spread in
    // RESET_AXES, and BOTH section resets would then write it — so
    // "Reset effects" would quietly reach into Interface.
    const seen = new Map<string, string>();
    for (const [section, keys] of Object.entries(SECTION_KEYS)) {
      for (const k of keys) {
        expect(
          seen.get(k),
          `"${k}" is in ${seen.get(k)} and ${section} — a section reset would reach outside itself`,
        ).toBeUndefined();
        seen.set(k, section);
      }
    }
  });

  it('no section owns a container axis', () => {
    // `mod` and `tokens` are the container's. Filed under a section,
    // "Reset interface" would uninstall the look that supplied all four
    // sections — which is not what the words say.
    for (const [section, keys] of Object.entries(SECTION_KEYS)) {
      for (const c of Object.keys(CONTAINER_AXES)) {
        expect(keys, `${section} claims the container's "${c}"`).not.toContain(c);
      }
    }
  });

  it('a section holds the same default the whole-card reset does', () => {
    // Two spellings of one default drift; the section reset would then
    // land somewhere "Reset mods" does not.
    for (const axes of Object.values(SECTION_AXES)) {
      for (const [k, v] of Object.entries(axes)) {
        expect((RESET_AXES as Record<string, unknown>)[k], `"${k}" disagrees`).toBe(v);
      }
    }
  });

  it('every section either owns axes or is declared axis-free', () => {
    // Adding a section must be a decision, not a silent no-op. `sounds`
    // is the declared exception: its state lives in preference keys
    // (mods.sound.pack / .volume), not in the theme object, so it has no
    // theme axes and its reset writes those keys directly. `mods` is the
    // container's row, and the container's axes are CONTAINER_AXES.
    const AXIS_FREE = ['mods', 'sounds'];
    for (const section of MOD_SECTIONS) {
      const owns = section in SECTION_KEYS;
      const declared = AXIS_FREE.includes(section);
      expect(
        owns !== declared,
        `"${section}" is ${owns && declared ? 'both' : 'neither'} — say which it is`,
      ).toBe(true);
    }
  });
});

