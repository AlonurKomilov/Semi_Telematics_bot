/**
 * A dialog arriving and leaving — and the reason the obvious signal is
 * only half of one.
 *
 * `onOpenChange` looks like the answer. Base UI calls it from
 * `DialogStore.setOpen` only, which runs on INTERACTIONS — a trigger,
 * the close button, Escape, a dismissal. When a parent changes the
 * `open` PROP instead, `DialogRoot` syncs the store through
 * `useControlledProp` and `setOpen` never runs.
 *
 * Every Dialog and Sheet in this product is controlled: 46 of them, and
 * not one `<DialogTrigger>`. A wrapper built on the callback alone would
 * have been silent everywhere — and GREEN, because nothing tests a sound
 * that does not play.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StrictMode } from 'react';
import { renderHook } from '@testing-library/react';

const { playActCue } = vi.hoisted(() => ({ playActCue: vi.fn() }));
vi.mock('./cue', () => ({ playActCue }));

import { useSurfaceCue } from './surface';

beforeEach(() => { playActCue.mockClear(); });

describe('a controlled surface is read from its prop', () => {
  it('opens when the parent opens it', () => {
    const { rerender } = renderHook(({ open }) => useSurfaceCue({ open }),
      { initialProps: { open: false } });
    expect(playActCue, 'a closed dialog announced itself on mount')
      .not.toHaveBeenCalled();
    rerender({ open: true });
    expect(playActCue).toHaveBeenCalledWith('surface_open');
  });

  it('and closes when the parent closes it', () => {
    const { rerender } = renderHook(({ open }) => useSurfaceCue({ open }),
      { initialProps: { open: false } });
    rerender({ open: true });
    rerender({ open: false });
    expect(playActCue).toHaveBeenLastCalledWith('surface_close');
  });

  /** Several call sites render the root only while it is up, so the
   *  first render they ever do is already open. */
  it('a surface that mounts open did open', () => {
    renderHook(() => useSurfaceCue({ open: true }));
    expect(playActCue).toHaveBeenCalledWith('surface_open');
  });

  it('and a re-render that changes nothing says nothing', () => {
    const { rerender } = renderHook(({ open }) => useSurfaceCue({ open }),
      { initialProps: { open: true } });
    playActCue.mockClear();
    rerender({ open: true });
    rerender({ open: true });
    expect(playActCue).not.toHaveBeenCalled();
  });

  /**
   * THE DOUBLE-FIRE. A controlled surface closed by its own X button
   * fires `onOpenChange` AND changes the prop a moment later. Two
   * detectors would make one act two sounds, so a controlled surface's
   * callback is left strictly alone.
   */
  it('and its callback is left alone, so one act stays one sound', () => {
    const onOpenChange = vi.fn();
    const { result, rerender } = renderHook(
      ({ open }) => useSurfaceCue({ open, onOpenChange }),
      { initialProps: { open: true } });
    playActCue.mockClear();

    // Base UI calls this from its own interaction…
    result.current.onOpenChange?.(false);
    expect(playActCue, 'the callback spoke as well as the prop')
      .not.toHaveBeenCalled();
    expect(onOpenChange, 'the caller stopped being told').toHaveBeenCalledWith(false);

    // …and the parent reflects it on the next render. ONE sound.
    rerender({ open: false });
    expect(playActCue).toHaveBeenCalledTimes(1);
    expect(playActCue).toHaveBeenCalledWith('surface_close');
  });
});

describe('an uncontrolled surface is read from its callback', () => {
  /** Not dead code: `DialogTrigger` is exported and typed, and nothing
   *  in the product uses one YET. */
  it('because there is no prop to watch', () => {
    const onOpenChange = vi.fn();
    const { result } = renderHook(() => useSurfaceCue({ onOpenChange }));
    result.current.onOpenChange?.(true);
    expect(playActCue).toHaveBeenCalledWith('surface_open');
    result.current.onOpenChange?.(false);
    expect(playActCue).toHaveBeenLastCalledWith('surface_close');
  });

  it('and the caller still hears about it', () => {
    const onOpenChange = vi.fn();
    const { result } = renderHook(() => useSurfaceCue({ onOpenChange }));
    result.current.onOpenChange?.(true);
    expect(onOpenChange, 'wrapping the callback swallowed it')
      .toHaveBeenCalledWith(true);
  });

  /**
   * The branch that has no prop must not READ one. `open` is
   * `undefined` there, and an effect that compares it to a remembered
   * `false` announces a close on the second render — a sound for a
   * dialog nobody touched, which is the exact failure mode this axis
   * cannot afford. A mutation caught that this was untested.
   */
  it('and re-rendering one never announces anything', () => {
    const onOpenChange = vi.fn();
    const { rerender } = renderHook(() => useSurfaceCue({ onOpenChange }));
    rerender();
    rerender();
    expect(playActCue, 'an uncontrolled surface spoke without being touched')
      .not.toHaveBeenCalled();
  });

  it('and a surface with no callback at all does not throw', () => {
    const bare: { onOpenChange?: (open: boolean) => void } = {};
    const { result } = renderHook(() => useSurfaceCue(bare));
    expect(() => result.current.onOpenChange?.(true)).not.toThrow();
    expect(playActCue).toHaveBeenCalledWith('surface_open');
  });
});


/**
 * StrictMode is ON in `main.tsx`, so every effect mounts, tears down and
 * mounts again in development — with the SAME deps, which is the one
 * case the dependency array cannot filter.
 *
 * Without the equality check inside the effect a dialog that mounts open
 * announces itself twice on every developer's machine and once in
 * production, which is the worst shape of bug to chase: it does not
 * reproduce where it was reported.
 */
describe('under StrictMode, one act is still one sound', () => {
  it('a surface that mounts open announces once', () => {
    renderHook(() => useSurfaceCue({ open: true }), { wrapper: StrictMode });
    expect(playActCue).toHaveBeenCalledTimes(1);
    expect(playActCue).toHaveBeenCalledWith('surface_open');
  });

  /**
   * And an UNCONTROLLED one says nothing at all.
   *
   * This is where the `!controlled` guard earns its place, and only
   * here: `open` is `undefined` there, so an effect that remembers a
   * `false` and compares on the second pass reports a CLOSE — a sound
   * for a dialog nobody has touched, in development only, which is the
   * worst shape of bug to chase because it does not reproduce where it
   * was reported.
   */
  it('and an uncontrolled surface stays silent through both passes', () => {
    renderHook(() => useSurfaceCue({ onOpenChange: vi.fn() }), { wrapper: StrictMode });
    expect(playActCue, 'the branch with no prop read one anyway')
      .not.toHaveBeenCalled();
  });

  it('and one that opens later announces once', () => {
    const { rerender } = renderHook(({ open }) => useSurfaceCue({ open }),
      { wrapper: StrictMode, initialProps: { open: false } });
    playActCue.mockClear();
    rerender({ open: true });
    expect(playActCue).toHaveBeenCalledTimes(1);
  });
});
