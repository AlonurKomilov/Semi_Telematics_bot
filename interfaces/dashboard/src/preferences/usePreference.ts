import { useCallback, useState, useSyncExternalStore } from 'react';

import type { PrefKey, PrefValue } from './registry';
import {
  get, set, reset, subscribe, isSyncLoaded, subscribeSyncLoaded,
} from './store';
import {
  tableKey, TABLE_PARTS, type TablePart, type TablePartValue,
} from './registry';

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

/**
 * One DataGrid preference for one table — the family counterpart of
 * ``usePreference``.  The key is built by ``tableKey`` so the stored
 * string stays byte-identical to what the grid wrote before.
 *
 * ``defaultValue`` overrides the family default for call sites whose
 * default is per-instance (a grid's ``defaultRowGroup`` prop).
 */
export function useTablePreference<P extends TablePart>(
  tableId: string | undefined,
  part: P,
  defaultValue?: TablePartValue<P>,
): {
  value: TablePartValue<P>;
  setValue: (next: TablePartValue<P> | ((prev: TablePartValue<P>) => TablePartValue<P>)) => void;
} {
  // No tableId → nothing is persisted; fall back to in-memory state so a
  // grid without one still works (the pre-service behaviour).
  const key = tableId ? tableKey(tableId, part) : '';
  const fallback = (defaultValue !== undefined
    ? defaultValue
    : TABLE_PARTS[part].default) as TablePartValue<P>;
  const [local, setLocal] = useState<TablePartValue<P>>(fallback);

  const stored = useSyncExternalStore(
    useCallback((fn: () => void) => (key ? subscribe(key, fn) : () => {}), [key]),
    useCallback(() => (key ? get(key) as TablePartValue<P> : undefined), [key]),
    useCallback(() => (key ? get(key) as TablePartValue<P> : undefined), [key]),
  );

  const value = key ? (stored as TablePartValue<P>) : local;
  const setValue = useCallback(
    (next: TablePartValue<P> | ((prev: TablePartValue<P>) => TablePartValue<P>)) => {
      if (!key) { setLocal(next as TablePartValue<P>); return; }
      set(key, typeof next === 'function'
        ? (next as (p: unknown) => unknown)(get(key))
        : next);
    },
    [key],
  );

  return { value, setValue };
}

/**
 * Has the account's copy of the synced preferences arrived?
 *
 * Consumers that must act exactly once on an authoritative value — e.g.
 * DataGrid opening its default tab — gate on this rather than guessing
 * from a provisional local value.  Always true when syncing is off.
 */
export function useSyncLoaded(): boolean {
  return useSyncExternalStore(subscribeSyncLoaded, isSyncLoaded, isSyncLoaded);
}
