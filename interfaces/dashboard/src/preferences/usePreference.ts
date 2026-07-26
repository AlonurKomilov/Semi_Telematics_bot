import { useCallback, useSyncExternalStore } from 'react';

import type { PrefKey, PrefValue } from './registry';
import { get, set, reset, subscribe } from './store';

/**
 * Read + write one preference.  The type comes from the registry, so no
 * generics at the call site:
 *
 *     const { value, setValue } = usePreference('notif.position');
 *
 * ``useSyncExternalStore`` (not useState + effect) because the store
 * lives outside React: this keeps every subscriber — including ones that
 * mount later, and other tabs via the ``storage`` event — on one value
 * with no tearing.
 *
 * The API is deliberately identical in Phase 1 (local) and Phase 2
 * (cloud-synced), so migrating a call site is a one-time edit.
 */
export function usePreference<K extends PrefKey>(key: K): {
  value: PrefValue<K>;
  setValue: (next: PrefValue<K> | ((prev: PrefValue<K>) => PrefValue<K>)) => void;
  /** Back to the registry default (and stop syncing the old value). */
  resetValue: () => void;
} {
  const value = useSyncExternalStore(
    useCallback((fn: () => void) => subscribe(key, fn), [key]),
    useCallback(() => get(key), [key]),
    // Server snapshot (SSR / prerender): same read — localStorage is
    // guarded, so this resolves to the registry default there.
    useCallback(() => get(key), [key]),
  );

  const setValue = useCallback(
    (next: PrefValue<K> | ((prev: PrefValue<K>) => PrefValue<K>)) => set(key, next),
    [key],
  );
  const resetValue = useCallback(() => reset(key), [key]);

  return { value, setValue, resetValue };
}
