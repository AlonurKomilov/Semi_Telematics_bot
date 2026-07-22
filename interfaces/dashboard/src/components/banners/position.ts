/**
 * Notification-lane position preference — where banners/toasts appear.
 *
 * One lane for the whole app (sonner's Toaster renders everything), so
 * this preference moves ALL notifications together — a single consistent
 * home, chosen visually on the preferences page.  Stored in localStorage
 * (the established per-user UI-pref store — same as DataGrid's column
 * state): position is a per-device layout choice, so device-local is the
 * right scope, and it needs no backend.
 */
import { useSyncExternalStore } from 'react';

export type NotifPosition = 'top-right' | 'bottom-right' | 'bottom-center';

const KEY = 'notif.position';
const DEFAULT_POSITION: NotifPosition = 'top-right';
const VALID: NotifPosition[] = ['top-right', 'bottom-right', 'bottom-center'];
const EVENT = 'notif-position-changed';

// Session fallback for storage-restricted contexts (strict private
// browsing): the choice still applies for THIS session even when it
// can't persist — so the picker + toast never lie about what happened.
let _memory: NotifPosition | null = null;

export function getNotifPosition(): NotifPosition {
  try {
    const v = localStorage.getItem(KEY) as NotifPosition | null;
    if (v && VALID.includes(v)) return v;
  } catch {
    /* storage unavailable — fall through to the session value */
  }
  return _memory ?? DEFAULT_POSITION;
}

export function setNotifPosition(pos: NotifPosition): void {
  _memory = pos;
  try {
    localStorage.setItem(KEY, pos);
  } catch {
    /* storage unavailable — _memory carries this session */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(EVENT, cb);
  // Cross-tab sync comes free via the storage event.
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener('storage', cb);
  };
}

/** Live position for the Toaster mount — re-renders on change. */
export function useNotifPosition(): NotifPosition {
  return useSyncExternalStore(subscribe, getNotifPosition, () => DEFAULT_POSITION);
}

export function resetNotifPositionForTests(): void {
  _memory = null;
}
