/**
 * What this person has INSTALLED — the picker's door, and the store's
 * install state.
 *
 * Two questions stack here, and they are not the same one:
 *
 *   `local.ts`      does this build CARRY the item?   (a fact about the app)
 *   this file       is its PACK installed?            (a choice they made)
 *
 * A picker offers the intersection. The store draws every pack whether
 * installed or not — otherwise one you removed could never be found
 * again, which is not removal, it is loss.
 *
 * The unit is the PACK. An item cannot be installed or removed on its
 * own: it arrives with the pack that ships it and leaves with it. What
 * a person still chooses item by item is which of the installed ones to
 * WEAR, and that is the pickers' job, not the store's.
 *
 * It lives in React and reads a preference, which is exactly why
 * `local.ts` may not: `preferences/registry.ts` asks the store what a
 * valid id is, so a `local` that read preferences would close a ring
 * and an ESM cycle would boot one side `undefined`. The pure half stays
 * a leaf; the choice half is a hook.
 */
import { useCallback } from 'react';
import { usePreference } from '../../preferences';
import { isInstalled } from './local';
import { packOf, packById, removable } from './packs';

export interface Shelves {
  /** Is this pack on this device? */
  hasPack: (packId: string) => boolean;
  /** Does this person have the item — is its pack installed? */
  isKept: (axis: string, id: string) => boolean;
  /** What a picker may draw: carried by the build, shipped by a pack
   *  that is installed. */
  offered: <T>(axis: string, items: readonly T[], idOf: (item: T) => string) => readonly T[];
  /** Put a pack back. */
  install: (packId: string) => void;
  /** Take a pack off. The base pack is refused — it carries every
   *  axis's fallback, so removing it would empty every shelf. */
  uninstall: (packId: string) => void;
}

export function useShelves(): Shelves {
  const { value, setValue } = usePreference('mods.packs.removed');
  const removed = value ?? [];

  const hasPack = useCallback(
    (packId: string) => packById(packId) !== undefined && !removed.includes(packId),
    [removed]);

  const isKept = useCallback((axis: string, id: string) => {
    if (!isInstalled(axis, id)) return false;
    const pack = packOf(axis, id);
    return pack ? hasPack(pack.id) : false;
  }, [hasPack]);

  const offered = useCallback(<T,>(
    axis: string, items: readonly T[], idOf: (item: T) => string,
  ) => items.filter((i) => isKept(axis, idOf(i))), [isKept]);

  const install = useCallback((packId: string) => {
    if (!removed.includes(packId)) return;
    setValue(removed.filter((x) => x !== packId));
  }, [removed, setValue]);

  const uninstall = useCallback((packId: string) => {
    if (!removable(packId) || removed.includes(packId)) return;
    setValue([...removed, packId]);
  }, [removed, setValue]);

  return { hasPack, isKept, offered, install, uninstall };
}

/** The picker's half, on its own, so a chip row does not import a
 *  store's worth of machinery to draw four chips. */
export const useOffered = (): Shelves['offered'] => useShelves().offered;
