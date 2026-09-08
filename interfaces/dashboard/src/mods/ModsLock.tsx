/**
 * Puts every mods preference back to its default for a role that does not
 * hold the service — see `access.ts`.
 *
 * A component rather than a call inside `ModProvider` because the provider
 * sits above the auth tree in `main.tsx` and cannot ask who is signed in;
 * this renders inside `AppShell`, where permissions are known. It resets
 * the DEVICE store, not only the screen: the boot script paints the stored
 * theme before any permission is known, and a stored look would otherwise
 * flash on every load for as long as it stayed stored.
 *
 * Only `mods.*` keys — `dispatch.soundOn` is the alerts service's, and it
 * has its own row.
 */
import { useEffect } from 'react';
import { useViewPermissions } from '../hooks/useViewPermissions';
import { DEFS, preferences } from '../preferences';
import { MODS_PERMISSION } from './access';

export const MODS_PREF_PREFIX = 'mods.';

export function lockedModsKeys(): string[] {
  return Object.keys(DEFS).filter((k) => k.startsWith(MODS_PREF_PREFIX));
}

/**
 * Whether the doors to Mods are open. TRUE until the permissions have
 * loaded — "not loaded" is not "denied": a door hidden while the answer
 * is still on its way flashes out of the header on every load for every
 * role, and the lock below already puts a denied role's look back to the
 * defaults the moment the answer arrives.
 */
export function useCanMods(): boolean {
  const { has, ready } = useViewPermissions();
  return !ready || has(MODS_PERMISSION);
}

export function ModsLock() {
  const { has, ready } = useViewPermissions();
  const locked = ready && !has(MODS_PERMISSION);
  useEffect(() => {
    if (!locked) return;
    for (const k of lockedModsKeys()) preferences.reset(k);
  }, [locked]);
  return null;
}
