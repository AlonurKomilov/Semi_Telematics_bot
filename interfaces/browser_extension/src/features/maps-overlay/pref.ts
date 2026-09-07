/**
 * Whether the overlay draws on google.com/maps.
 *
 * Its own file, and read through ``chrome.storage.local`` on both
 * sides, because the two readers cannot import each other: the panel
 * is a module bundle and the content script is a classic script in a
 * page Google controls.  The storage key IS the contract between them,
 * and ``storage.onChanged`` is how a switch in the panel reaches a tab
 * that is already open without a reload.
 *
 * Default ON.  Somebody who installed a vehicle-tracking extension and
 * opened Google Maps is the person this was built for; the switch is
 * for the one who does not want it, not a gate the rest must find.
 */
import { getFlag, setFlag } from '../../prefs';

export const OVERLAY_PREF_KEY = 'overlayOnGoogleMaps';

export function getOverlayPref(): Promise<boolean> {
  return getFlag(OVERLAY_PREF_KEY, true);
}
export function setOverlayPref(on: boolean): Promise<void> {
  return setFlag(OVERLAY_PREF_KEY, on);
}
