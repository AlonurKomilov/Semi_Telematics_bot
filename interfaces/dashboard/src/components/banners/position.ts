/**
 * Notification-lane position preference — where banners/toasts appear.
 *
 * One lane for the whole app (sonner's Toaster renders everything), so
 * this preference moves ALL notifications together — a single consistent
 * home, chosen visually on the preferences page.  Position is a per-device
 * LAYOUT choice, so it's registered ``device`` scope and never syncs.
 *
 * Storage, the default, the valid-value guard, cross-tab sync and the
 * session fallback for storage-restricted contexts all live in the
 * preferences service now (``src/preferences``) — this module is a thin
 * typed facade so the existing consumers keep their imports.
 */
import { usePreference, preferences } from '../../preferences';
import type { NotifPosition } from '../../preferences';

export type { NotifPosition };

export function getNotifPosition(): NotifPosition {
  return preferences.get('notif.position');
}

export function setNotifPosition(pos: NotifPosition): void {
  // The store holds the value in memory even when localStorage throws
  // (strict private browsing) — that's what the old ``_memory`` fallback
  // existed for, so the picker never lies about what happened.
  preferences.set('notif.position', pos);
}

/** Live position for the Toaster mount — re-renders on change. */
export function useNotifPosition(): NotifPosition {
  return usePreference('notif.position').value;
}

export function resetNotifPositionForTests(): void {
  preferences.reset('notif.position');
}
