/**
 * Staged-acknowledgement ids — module-level, because the bell dropdown
 * UNMOUNTS when it closes.
 *
 * A staged "Acknowledge all" hides its rows for the countdown window; if
 * that hide-state lived in the panel it would vanish on close/reopen —
 * the rows would reappear mid-window and a second overlapping stage of
 * the same ids becomes possible.  This tiny external store (same pattern
 * as the position store) survives the remount: a fresh panel re-derives
 * "these ids are already staged" instead of starting blank.
 */
import { useSyncExternalStore } from 'react';

const _staged = new Set<string>();
const _listeners = new Set<() => void>();
// useSyncExternalStore needs a referentially-stable snapshot per version.
let _snapshot: ReadonlySet<string> = new Set();

function _emit() {
  _snapshot = new Set(_staged);
  _listeners.forEach((l) => l());
}

export function addStagedAcks(ids: string[]): void {
  ids.forEach((id) => _staged.add(id));
  _emit();
}

export function removeStagedAcks(ids: string[]): void {
  ids.forEach((id) => _staged.delete(id));
  _emit();
}

function _subscribe(cb: () => void): () => void {
  _listeners.add(cb);
  return () => _listeners.delete(cb);
}

/** The ids currently inside a pending acknowledge window. */
export function useStagedAckIds(): ReadonlySet<string> {
  return useSyncExternalStore(_subscribe, () => _snapshot, () => _snapshot);
}
