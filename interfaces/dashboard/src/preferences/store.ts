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

import { DEFS, defFor, type PrefKey, type PrefValue } from './registry';
import {
  readPref, writePref, removePref, prefKeyFromStorageEvent, sanitize, LS_PREFIX,
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
export function get<K extends PrefKey>(key: K): PrefValue<K>;
export function get(key: string): unknown;
export function get(key: string): unknown {
  if (!values.has(key)) values.set(key, readPref(key as PrefKey));
  return values.get(key);
}

export function set<K extends PrefKey>(
  key: K,
  next: PrefValue<K> | ((prev: PrefValue<K>) => PrefValue<K>),
): void;
export function set(key: string, next: unknown): void;
export function set(key: string, next: unknown): void {
  const prev = get(key);
  const value = typeof next === 'function'
    ? (next as (p: unknown) => unknown)(prev)
    : next;
  if (Object.is(prev, value)) return;
  values.set(key, value);
  writePref(key, value);
  // Only 'synced' keys are ever pushed remotely; 'device' ones stay put
  // even once a backend is registered.
  if (backend && defFor(key)?.scope === 'synced') {
    backend.put(key, JSON.stringify(value));
  }
  notify(key);
}

/** Reset one key to its registry default (and stop it syncing back from
 *  whichever device wrote it last). */
export function reset<K extends PrefKey>(key: K): void;
export function reset(key: string): void;
export function reset(key: string): void {
  const d = defFor(key);
  if (!d) return;
  values.set(key, d.default);
  removePref(key);
  if (backend && d.scope === 'synced') backend.del(key);
  notify(key);
}

/**
 * Reset EVERY preference — the "back to defaults" path.
 *
 * Must sweep more than ``DEFS``: per-table FAMILY keys
 * (``table.<id>.views`` …) have no registry entry, so iterating DEFS
 * alone would leave an operator's column layouts and saved tabs behind
 * after they clicked "Reset all".
 */
export function resetAll(): void {
  const keys = new Set<string>([...Object.keys(DEFS), ...values.keys()]);
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const raw = localStorage.key(i);
      if (raw?.startsWith(LS_PREFIX)) keys.add(raw.slice(LS_PREFIX.length));
    }
  } catch { /* storage unavailable — reset what's in memory */ }
  keys.forEach((k) => { if (defFor(k)) reset(k); });
}

// ── "Has the account's copy arrived?" ────────────────────────────────
// Local values are readable synchronously, but SYNCED ones only become
// authoritative after the bulk read lands.  Consumers that must not act
// on a provisional value — DataGrid applies its default tab exactly once
// — gate on this instead of a per-key `hydrated` flag.
const SYNC_LOADED = '__syncLoaded__';
let syncLoaded = false;

/** True once a backend's bulk read has been adopted (or when syncing is
 *  off, since then the local value IS the authority). */
export function isSyncLoaded(): boolean {
  return backend === null || syncLoaded;
}

export function subscribeSyncLoaded(fn: Listener): () => void {
  return subscribe(SYNC_LOADED, fn);
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
  const d = defFor(key);
  if (!d) return;
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
  if (Object.is(values.get(key), value)) return;
  values.set(key, value);
  notify(key);
}

/**
 * Server rows keyed by a name this registry no longer uses.
 *
 * `legacyKeys` was a localStorage-ONLY mechanism: `readPref` falls back
 * through it in `local.ts`, but `remote.ts` PUTs the registry key
 * VERBATIM as the row key and adoption looked rows up by that same
 * string with no alias table. So renaming a ``synced`` key orphaned its
 * server row, and the localStorage fallback rescued only the one browser
 * that still happened to hold the old entry — sign in anywhere else and
 * the value was silently gone. That is exactly what `sound.pack` ->
 * `mods.sound.pack` did.
 *
 * A legacy entry that starts with `LS_PREFIX` IS a former registry key,
 * because `lsKey(k) = LS_PREFIX + k` is the only place a canonical
 * storage string is built. Stripping the prefix therefore recovers the
 * row key the server would have written under that name. An entry
 * WITHOUT the prefix (`dashboard-theme`, `4truck.table.density`)
 * predates the preference service, so no server row ever existed for it
 * and it is correctly absent from this map.
 */
function serverLegacyMap(): Map<string, string> {
  const out = new Map<string, string>();
  for (const [canonical, def] of Object.entries(DEFS)) {
    for (const legacy of (def as { legacyKeys?: string[] }).legacyKeys ?? []) {
      if (!legacy.startsWith(LS_PREFIX)) continue;
      const rowKey = legacy.slice(LS_PREFIX.length);
      if (rowKey === canonical) continue;
      out.set(rowKey, canonical);
    }
  }
  return out;
}

/** The canonical key a server row belongs to, or null if it belongs to
 *  nothing this registry knows. Exported for the guard. */
export function canonicalKeyForRow(rowKey: string): string | null {
  if (defFor(rowKey)) return rowKey;
  return serverLegacyMap().get(rowKey) ?? null;
}

/** Phase 2 entry point: register the remote backend and merge what the
 *  server has.  Local values win only for ``device`` keys — a ``synced``
 *  key is owned by the account, so the server copy is adopted. */
export async function attachBackend(next: SyncBackend): Promise<void> {
  backend = next;
  syncLoaded = false;
  notify(SYNC_LOADED);
  try {
    const items = await next.loadAll();
    // Legacy rows FIRST, so that an account carrying both spellings ends
    // up on the canonical one whatever order the bulk read returns them
    // in. Adoption stays read-only: the next time the user changes the
    // preference it PUTs under the canonical key by itself, and a boot
    // that silently rewrites server rows is a surprise nobody asked for.
    const legacy: { key: string; value: string | null }[] = [];
    const canonical: { key: string; value: string | null }[] = [];
    for (const item of items) {
      (defFor(item.key) ? canonical : legacy).push(item);
    }
    for (const { key, value } of legacy) {
      const target = canonicalKeyForRow(key);
      if (target) adoptRaw(target, value);
    }
    for (const { key, value } of canonical) adoptRaw(key, value);
  } finally {
    // Even a FAILED bulk read resolves the gate: consumers would
    // otherwise wait forever on an offline device.  The local value is
    // then the best available answer.
    syncLoaded = true;
    notify(SYNC_LOADED);
  }
}

export function detachBackend(): void {
  backend = null;
  syncLoaded = false;
  // With no backend the gate is open again (isSyncLoaded() returns true
  // for backend === null) — notify so anything waiting re-reads.
  notify(SYNC_LOADED);
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
