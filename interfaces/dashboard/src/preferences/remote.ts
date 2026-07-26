/**
 * PHASE 2 — the cloud backend.
 *
 * Implements ``SyncBackend`` over endpoints that already exist
 * (``capabilities/preferences/router.py``), so this file is the ONLY
 * addition needed to make preferences follow a user to another browser.
 * No call site changes: everything still goes through
 * ``usePreference`` / ``preferences.get``.
 *
 * Reads are BULK — one ``GET /user/preferences/ui`` returns every key for
 * the user.  That's what makes syncing ~20 preferences cost one request
 * instead of twenty (the flaw in the per-key hook this replaces).
 *
 * Writes are per-key and DEBOUNCED: operators nudge the same preference
 * repeatedly (dragging the assistant panel, flipping through themes) and
 * we don't want a PUT per frame.  Per-key rows also mean two devices
 * writing DIFFERENT preferences never clobber each other — the failure a
 * single-blob design would have.
 */

import { apiJSON } from '../api/client';
import type { SyncBackend } from './store';

/** ms after the last change to a key before it's pushed. */
const DEBOUNCE_MS = 400;

const path = (key: string) => `/user/preferences/ui/${encodeURIComponent(key)}`;

export function createRemoteBackend(): SyncBackend {
  const timers = new Map<string, number>();
  /** Latest pending value per key, so the timer always sends the newest
   *  one rather than whatever was current when it was scheduled. */
  const pending = new Map<string, string | null>();   // null => delete

  const flush = (key: string) => {
    timers.delete(key);
    const raw = pending.get(key);
    pending.delete(key);
    if (raw === undefined) return;
    const req = raw === null
      ? apiJSON(path(key), { method: 'DELETE' })
      : apiJSON(path(key), {
          method: 'PUT',
          body: JSON.stringify({ value: raw }),
          headers: { 'Content-Type': 'application/json' },
        });
    // Soft-fail: a preference that didn't reach the server is still
    // correct locally, and the next change retries.  Never surface an
    // error toast for this — it's not an action the user took.
    req.catch(() => { /* ignore */ });
  };

  const schedule = (key: string, raw: string | null) => {
    pending.set(key, raw);
    const existing = timers.get(key);
    if (existing != null) window.clearTimeout(existing);
    timers.set(key, window.setTimeout(() => flush(key), DEBOUNCE_MS));
  };

  return {
    async loadAll() {
      try {
        const res = await apiJSON<{ items?: Array<{ key: string; value: string }> }>(
          '/user/preferences/ui',
        );
        return res?.items ?? [];
      } catch {
        // Offline / 401 during boot — keep local values; the next
        // attach retries.
        return [];
      }
    },
    put(key, rawJson) { schedule(key, rawJson); },
    del(key) { schedule(key, null); },
  };
}
