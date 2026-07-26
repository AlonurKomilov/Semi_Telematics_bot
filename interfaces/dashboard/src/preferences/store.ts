/**
 * The preferences store — a plain observable OUTSIDE React.
 *
 * Why not a Context provider: theme and language must resolve before
 * login and outside React entirely (``ThemeProvider`` sits above
 * ``AuthProvider``; module-level code reads storage at import time).  An
 * external store has no provider-placement problem and can be read from
 * a plain function.  Why not TanStack Query: preferences are write-heavy
 * LOCAL state, not server cache — staleness/invalidation semantics fight
 * the use case.
 *
 * Shape: per-KEY values (never one blob).  A blob would make two devices
 * writing unrelated keys — theme on a laptop, a table layout on a
 * desktop — clobber each other under whole-object last-writer-wins.
 * Per-key writes + a single BULK read is the combination that stays
 * correct in Phase 2.
 *
 * ``SyncBackend`` is the Phase 2 seam: Phase 1 registers nothing, so the
 * store is local-only.  Phase 2 registers a remote adapter and every
 * existing call site keeps working untouched.
 */

import { DEFS, type PrefKey, type PrefValue, type PrefDef } from './registry';
import {
  readPref, writePref, removePref, prefKeyFromStorageEvent, sanitize,
} from './local';

/**
 * What a remote store must provide.  Implemented in Phase 2 by
 * ``remote.ts`` over ``/user/preferences/ui`` (bulk read) +
 * ``/user/preferences/ui/{key}`` (per-key write/delete).
 */
export interface SyncBackend {
  /** One round-trip returning every stored key for the current user. */
  loadAll(): Promise<Array<{ key: string; value: string }>>;
  put(key: string, rawJson: string): void;
  del(key: string): void;
}

type Listener = () => void;

const values = new Map<string, unknown>();
const listeners = new Map<string, Set<Listener>>();
let backend: SyncBackend | null = null;

function notify(key: string): void {
  listeners.get(key)?.forEach((fn) => fn());
}

/** Current value for a key — reads through to localStorage (migrating a
 *  legacy value forward) the first time, then serves from memory so
 *  ``useSyncExternalStore`` gets a stable reference. */
export function get<K extends PrefKey>(key: K): PrefValue<K> {
  if (!values.has(key)) values.set(key, readPref(key));
  return values.get(key) as PrefValue<K>;
}

export function set<K extends PrefKey>(
  key: K,
  next: PrefValue<K> | ((prev: PrefValue<K>) => PrefValue<K>),
): void {
  const prev = get(key);
  const value = typeof next === 'function'
    ? (next as (p: PrefValue<K>) => PrefValue<K>)(prev)
    : next;
  if (Object.is(prev, value)) return;
  values.set(key, value);
  writePref(key, value);
  // Only 'synced' keys are ever pushed remotely; 'device' ones stay put
  // even once a backend is registered.
  if (backend && DEFS[key].scope === 'synced') {
    backend.put(key, JSON.stringify(value));
  }
  notify(key);
}

/** Reset one key to its registry default (and stop it syncing back from
 *  whichever device wrote it last). */
export function reset<K extends PrefKey>(key: K): void {
  values.set(key, DEFS[key].default);
  removePref(key);
  if (backend && DEFS[key].scope === 'synced') backend.del(key);
  notify(key);
}

/** Reset EVERY preference — the "back to defaults" path. */
export function resetAll(): void {
  (Object.keys(DEFS) as PrefKey[]).forEach(reset);
}

export function subscribe(key: string, fn: Listener): () => void {
  let set_ = listeners.get(key);
  if (!set_) { set_ = new Set(); listeners.set(key, set_); }
  set_.add(fn);
  return () => { set_!.delete(fn); };
}

/**
 * Adopt values that came from somewhere other than this tab (another tab
 * via the ``storage`` event, or the server in Phase 2).  Writes to memory
 * only — it must NOT echo back to the source it came from.
 */
export function adoptRaw(key: string, rawJson: string | null): void {
  if (!Object.prototype.hasOwnProperty.call(DEFS, key)) return;
  const k = key as PrefKey;
  const d = DEFS[k] as PrefDef<unknown>;
  let value: unknown;
  if (rawJson == null) {
    value = d.default;                       // cleared elsewhere → default
  } else {
    let parsed: unknown;
    try { parsed = JSON.parse(rawJson); } catch { return; }
    // A value from another tab / another device is untrusted input too.
    const clean = sanitize(d, parsed);
    if (clean === undefined) return;
    value = clean;
  }
  if (Object.is(values.get(k), value)) return;
  values.set(k, value);
  notify(k);
}

/** Phase 2 entry point: register the remote backend and merge what the
 *  server has.  Local values win only for ``device`` keys — a ``synced``
 *  key is owned by the account, so the server copy is adopted. */
export async function attachBackend(next: SyncBackend): Promise<void> {
  backend = next;
  const items = await next.loadAll();
  for (const { key, value } of items) adoptRaw(key, value);
}

export function detachBackend(): void {
  backend = null;
}

/** Cross-tab sync — cheap, and it's the local rehearsal for the
 *  cross-device merge Phase 2 introduces.  Registered once at module
 *  load; ``storage`` only fires in OTHER tabs, never the writer. */
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    const key = prefKeyFromStorageEvent(e.key);
    if (key) adoptRaw(key, e.newValue);
  });
}
