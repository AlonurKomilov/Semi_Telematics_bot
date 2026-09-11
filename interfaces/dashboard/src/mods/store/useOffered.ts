/**
 * What this person KEPT — the picker's door, and the store's shelves.
 *
 * Two questions stack here, and they are not the same one:
 *
 *   `local.ts`      does this install CARRY the pack?  (a fact about the app)
 *   this file       did this person KEEP it?           (a choice they made)
 *
 * A picker offers the intersection. The store page draws everything
 * carried, marking what was kept — otherwise a pack you removed could
 * never be found again, which is not removal, it is loss.
 *
 * It lives in React and reads a preference, which is exactly why
 * `local.ts` may not: `preferences/registry.ts` asks the store for its
 * valid ids, so a `local` that read preferences would close a ring and
 * an ESM cycle would boot one side `undefined`. The pure half stays a
 * leaf; the choice half is a hook.
 */
import { useCallback } from 'react';
import { usePreference } from '../../preferences';
import { isInstalled } from './local';
import { defaultOf } from './axes';

export interface Shelves {
  /** Does this person keep it — and does this install even have it? */
  isKept: (axis: string, id: string) => boolean;
  /** What a picker may draw: carried, and kept. */
  offered: <T>(axis: string, items: readonly T[], idOf: (item: T) => string) => readonly T[];
  /** Put it back on the shelf. */
  keep: (axis: string, id: string) => void;
  /** Take it off. A shelf's default is refused — an axis with nothing
   *  on it is an axis nobody can leave. */
  drop: (axis: string, id: string) => void;
  /** Whether taking it off is allowed at all, so a surface can say so
   *  instead of offering a control that quietly does nothing. */
  canDrop: (axis: string, id: string) => boolean;
}

export function useShelves(): Shelves {
  const { value, setValue } = usePreference('mods.packs.removed');
  const removed = value ?? {};

  const canDrop = useCallback(
    (axis: string, id: string) => id !== defaultOf(axis), []);

  const isKept = useCallback((axis: string, id: string) =>
    isInstalled(axis, id)
    && (!canDrop(axis, id) || !(removed[axis] ?? []).includes(id)),
  [removed, canDrop]);

  const offered = useCallback(<T,>(
    axis: string, items: readonly T[], idOf: (item: T) => string,
  ) => items.filter((i) => isKept(axis, idOf(i))), [isKept]);

  const keep = useCallback((axis: string, id: string) => {
    const next = (removed[axis] ?? []).filter((x) => x !== id);
    setValue({ ...removed, [axis]: next });
  }, [removed, setValue]);

  const drop = useCallback((axis: string, id: string) => {
    if (!canDrop(axis, id)) return;
    const was = removed[axis] ?? [];
    if (was.includes(id)) return;
    setValue({ ...removed, [axis]: [...was, id] });
  }, [removed, setValue, canDrop]);

  return { isKept, offered, keep, drop, canDrop };
}

/** The picker's half, on its own, so a chip row does not import a
 *  store's worth of machinery to draw four chips. */
export const useOffered = (): Shelves['offered'] => useShelves().offered;
