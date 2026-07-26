import { useState, useEffect, useRef, useCallback } from 'react';
import { apiJSON } from '../api/client';
import { preferences } from '../preferences';

/**
 * The master "sync my preferences" switch, read from the preferences
 * service.  When an operator turns it off, THIS hook must stop talking to
 * the server too — otherwise the profile toggle would lie: it would claim
 * "this browser only" while table layouts and saved tabs kept syncing.
 *
 * Read imperatively (not as a hook) so it's evaluated at the moment of
 * each fetch/PUT rather than captured at mount.
 */
const syncEnabled = (): boolean => {
  try { return preferences.get('prefs.syncEnabled'); } catch { return true; }
};

/**
 * Hybrid per-user preference store.
 *
 * Reads / writes go to ``GET/PUT /user/preferences/ui/<key>`` so the
 * value follows the operator across devices.  ``localStorage`` is used
 * as a fast-paint cache: the initial render gets the last known value
 * instantly (no flicker), then a background fetch reconciles against
 * the server's authoritative copy.
 *
 * Writes are debounced (default 400ms) — operators tweak the same
 * preference repeatedly (toggling visibility for a few columns in a
 * row, dragging headers around) and we don't want to PUT once per
 * keystroke / drag event.
 *
 * Generic over ``T`` because every consumer stores a different shape:
 * ``VisibilityState`` (Record<string, boolean>), ``ColumnOrderState``
 * (string[]), etc.  Serialisation is JSON in both directions.
 *
 * API shape mirrors ``useState`` so call sites read like:
 *
 *   const [vis, setVis] = useUserPreference('table.foo.visibility', {});
 *
 * with one extra return field — ``hydrated`` — that tells the caller
 * whether the server response has come back yet.  Most consumers
 * ignore it (the fast-cache is good enough for UX) but it's useful
 * for "first-load" branching.
 */

const LS_PREFIX = '4truck.pref.';

function lsGet<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(LS_PREFIX + key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch { return fallback; }
}

function lsSet(key: string, value: unknown): void {
  try { localStorage.setItem(LS_PREFIX + key, JSON.stringify(value)); }
  catch { /* quota / private mode — skip */ }
}

function lsDel(key: string): void {
  try { localStorage.removeItem(LS_PREFIX + key); } catch { /* ignore */ }
}

interface UserPreferenceOpts {
  /** ms to wait after the last setValue before PUTting to the server.
   *  Bursts of changes (drag-reorder, fast checkbox toggles) collapse
   *  into one network round-trip.  400ms balances perceived latency
   *  vs request count. */
  debounceMs?: number;
  /** When the operator hits "Reset to defaults", we want the next
   *  device to ALSO see defaults — so reset issues a DELETE rather
   *  than PUTting an empty value.  Detection is "set value deep-
   *  equals the fallback".  Off by default to keep semantics simple. */
  deleteOnFallback?: boolean;
}

export function useUserPreference<T>(
  /** Storage key.  Pass an empty string to disable persistence —
   *  the hook behaves like a vanilla ``useState(fallback)`` then,
   *  with no localStorage / no network.  Used by callers that
   *  conditionally opt into persistence (e.g. DataGrid without a
   *  ``tableId``). */
  key: string,
  fallback: T,
  opts: UserPreferenceOpts = {},
): {
  value: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  hydrated: boolean;
} {
  const { debounceMs = 400, deleteOnFallback = false } = opts;
  const disabled = !key;

  // Initial state from localStorage fast-cache.  Renders instantly
  // with whatever this browser saw last; server reconciles below.
  // When disabled, the cache is also skipped — start at fallback.
  const [value, _setValue] = useState<T>(() => disabled ? fallback : lsGet<T>(key, fallback));
  const [hydrated, setHydrated] = useState(disabled);
  const debounceRef = useRef<number | null>(null);
  // Latest value to PUT — captured by the timer so its closure
  // doesn't see stale state from a previous tick.
  const pendingRef = useRef<T>(value);
  pendingRef.current = value;
  // Don't write back on the first hydrate (otherwise the server's
  // value would immediately get overwritten by the local cache).
  const skipNextPutRef = useRef(true);
  // Track the key in a ref so the unmount cleanup writes to the right
  // entry even if ``key`` changed mid-lifecycle (rare but defensive).
  const keyRef = useRef(key);
  keyRef.current = key;
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;

  // Initial server fetch.  Runs once per key; updates state if the
  // server's value differs from the localStorage cache.  Soft-fails
  // on network errors — we keep the cached value, the next mutation
  // will retry.
  useEffect(() => {
    if (disabled) return;
    if (!syncEnabled()) {
      // Local-only mode: the cached value IS the value.  Mark hydrated so
      // consumers that gate on it (DataGrid's default-tab apply-once)
      // still run.
      setHydrated(true);
      skipNextPutRef.current = false;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await apiJSON<{ value: string }>(
          `/user/preferences/ui/${encodeURIComponent(key)}`,
        );
        if (cancelled) return;
        const raw = res?.value ?? '';
        if (!raw) {
          // Server has no entry yet — keep the fallback / local cache.
          // CLEAR the initial skip: there's nothing to echo-suppress, so
          // the operator's FIRST mutation (e.g. creating a saved tab on a
          // grid that's never persisted one) MUST reach the server rather
          // than be swallowed as a phantom echo.
          skipNextPutRef.current = false;
          setHydrated(true);
          return;
        }
        let parsed: T;
        try { parsed = JSON.parse(raw) as T; }
        catch { skipNextPutRef.current = false; setHydrated(true); return; }
        // Only update state when it actually changed — avoids a spurious
        // render + a write-back loop when local + server already agree.
        const sameAsLocal = JSON.stringify(parsed) === JSON.stringify(value);
        if (!sameAsLocal) {
          skipNextPutRef.current = true;   // don't echo the just-fetched value back
          _setValue(parsed);
          lsSet(key, parsed);
        } else {
          // Cache already matched the server — nothing was applied, so the
          // next setValue is a genuine edit that must persist.
          skipNextPutRef.current = false;
        }
        setHydrated(true);
      } catch {
        if (!cancelled) setHydrated(true);
      }
    })();
    return () => { cancelled = true; };
  // Only re-fetch when the key changes — value updates are local.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, disabled]);

  // Public setter — updates state + cache immediately, schedules a
  // debounced PUT to the server.  Accepts either a value or a
  // functional updater (matches ``useState`` ergonomics).
  const setValue = useCallback((next: T | ((prev: T) => T)) => {
    _setValue(prev => {
      const nv = typeof next === 'function'
        ? (next as (p: T) => T)(prev)
        : next;
      if (disabledRef.current) return nv;       // in-memory only — no cache / no PUT
      lsSet(keyRef.current, nv);
      if (skipNextPutRef.current) {
        skipNextPutRef.current = false;
        return nv;
      }
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        debounceRef.current = null;
        // Local-only mode — the localStorage write above already happened;
        // nothing goes to the account.
        if (!syncEnabled()) return;
        const valueToSend = pendingRef.current;
        const isFallback = JSON.stringify(valueToSend) === JSON.stringify(fallback);
        if (deleteOnFallback && isFallback) {
          apiJSON(
            `/user/preferences/ui/${encodeURIComponent(keyRef.current)}`,
            { method: 'DELETE' },
          ).catch(() => { /* soft-fail; next mutation retries */ });
          lsDel(keyRef.current);
        } else {
          apiJSON(
            `/user/preferences/ui/${encodeURIComponent(keyRef.current)}`,
            {
              method: 'PUT',
              body: JSON.stringify({ value: JSON.stringify(valueToSend) }),
              headers: { 'Content-Type': 'application/json' },
            },
          ).catch(() => { /* soft-fail */ });
        }
      }, debounceMs);
      return nv;
    });
  // fallback + debounceMs are intentionally captured by closure; if
  // they actually change between renders the parent should re-key
  // the hook (rare in practice — preferences have stable shape).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Flush any pending PUT on unmount so a fast tab-close doesn't
  // lose the operator's last tweak.  Synchronous PUT is unreliable
  // mid-unload — best-effort fire-and-forget is what we can do here.
  useEffect(() => {
    return () => {
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current);
        if (disabledRef.current) return;
        if (!syncEnabled()) return;          // local-only mode
        const valueToSend = pendingRef.current;
        apiJSON(
          `/user/preferences/ui/${encodeURIComponent(keyRef.current)}`,
          {
            method: 'PUT',
            body: JSON.stringify({ value: JSON.stringify(valueToSend) }),
            headers: { 'Content-Type': 'application/json' },
          },
        ).catch(() => { /* best-effort */ });
      }
    };
  }, []);

  return { value, setValue, hydrated };
}
