/**
 * localStorage adapter for the preferences store.
 *
 * Canonical namespace is ``4truck.pref.<key>`` holding a JSON value —
 * deliberately the SAME prefix ``useUserPreference`` already writes, so
 * when Phase 2 moves the server-synced keys onto this store their cached
 * values are already in the right place (no storage migration, no
 * re-download).
 *
 * Legacy reads: pre-service call sites were inconsistent — some wrote
 * ``JSON.stringify(value)``, others a bare ``'1'`` or a bare enum string.
 * ``readLegacy`` tries JSON first and falls back to the raw string, and a
 * def can override with ``fromLegacy``.  A migrated value is copied
 * FORWARD under the canonical key; the legacy entry is left in place so
 * rolling the release back doesn't lose it.
 */

import { DEFS, type PrefKey, type PrefDef, defFor } from './registry';

/** Canonical prefix — shared with the legacy useUserPreference cache. */
export const LS_PREFIX = '4truck.pref.';

const lsKey = (key: string) => LS_PREFIX + key;

/** localStorage can throw (Safari private mode, quota, disabled).  Every
 *  access is guarded — a preference failing to persist must never break
 *  the surface that reads it. */
function safeGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k: string, v: string): void {
  try { localStorage.setItem(k, v); } catch { /* quota / private mode */ }
}
function safeRemove(k: string): void {
  try { localStorage.removeItem(k); } catch { /* ignore */ }
}

/** Parse a stored canonical value.  Returns ``undefined`` when absent or
 *  unparseable (caller falls back to the registry default). */
function parseCanonical<T>(raw: string | null): T | undefined {
  if (raw == null) return undefined;
  try { return JSON.parse(raw) as T; } catch { return undefined; }
}

/** Pull a value from the pre-service ``localStorage`` key, if any. */
function readLegacy<T>(d: PrefDef<T>): T | undefined {
  for (const legacy of d.legacyKeys ?? []) {
    const raw = safeGet(legacy);
    if (raw == null || raw === '') continue;
    if (d.fromLegacy) return d.fromLegacy(raw);
    // No explicit converter: the old site most likely wrote JSON; if it
    // didn't, the bare string IS the value.
    try { return JSON.parse(raw) as T; } catch { return raw as unknown as T; }
  }
  return undefined;
}

/** Run a candidate value through the def's guard.  A stored value can be
 *  stale (an enum member that no longer exists), corrupt, or out of
 *  range — the guard is what keeps a bad entry from reaching the UI. */
export function sanitize<T>(d: PrefDef<T>, candidate: T | undefined): T | undefined {
  if (candidate === undefined) return undefined;
  return d.sanitize ? d.sanitize(candidate) : candidate;
}

/**
 * Read a preference: canonical → legacy (migrating forward) → default,
 * with each candidate sanitized before it's accepted.
 */
export function readPref<K extends PrefKey>(key: K): unknown;
export function readPref(key: string): unknown;
export function readPref(key: string): unknown {
  // ``defFor`` — not DEFS[key] — because per-table FAMILY keys
  // (table.<id>.views …) have no fixed registry entry.
  const d = defFor(key);
  if (!d) return undefined;

  const canonical = sanitize(d, parseCanonical<unknown>(safeGet(lsKey(key))));
  if (canonical !== undefined) return canonical;

  const migrated = sanitize(d, readLegacy(d));
  if (migrated !== undefined) {
    // Copy forward so the next read is a straight canonical hit.
    safeSet(lsKey(key), JSON.stringify(migrated));
    return migrated;
  }
  return d.default;
}

/**
 * Has this browser ever stored a value for the key — as opposed to
 * merely having a default for it?
 *
 * ``readPref`` deliberately cannot answer this: it returns the registry
 * default when nothing is stored, so its result is identical whether the
 * user chose that value or never touched the setting. The distinction
 * matters wherever "this screen has an opinion" must beat "here is what
 * you use elsewhere" — see appearance.ts, where getting it wrong would
 * let every sign-in overwrite a screen the user had deliberately set.
 *
 * Legacy keys count: a value mid-migration is still a stored opinion.
 */
export function hasStored(key: string): boolean {
  if (safeGet(lsKey(key)) != null) return true;
  const d = defFor(key);
  return (d?.legacyKeys ?? []).some((k) => {
    const raw = safeGet(k);
    return raw != null && raw !== '';
  });
}

export function writePref(key: string, value: unknown): void {
  safeSet(lsKey(key), JSON.stringify(value));
}

export function removePref(key: string): void {
  safeRemove(lsKey(key));
}

/** Map a raw ``storage`` event key back to a preference key — used for
 *  cross-TAB sync.  Returns null for unrelated keys. */
export function prefKeyFromStorageEvent(rawKey: string | null): string | null {
  if (!rawKey || !rawKey.startsWith(LS_PREFIX)) return null;
  return rawKey.slice(LS_PREFIX.length);
}
