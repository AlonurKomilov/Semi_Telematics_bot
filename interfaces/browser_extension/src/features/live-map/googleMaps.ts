/**
 * The free hand-off to Google Maps: their documented URL API, no key.
 * If a Google Maps tab is already open we drive THAT one, so the person
 * who has satellite view zoomed where they want it keeps it; otherwise
 * we open one.  This is deliberately not "draw on Google's map" — the
 * pin is Google's, one truck at a time, and it does not move.
 */
import { getFlag, setFlag } from '../../prefs';

export function searchUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lng.toFixed(6)}`;
}
export function directionsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat.toFixed(6)},${lng.toFixed(6)}`;
}
export function isGoogleMapsUrl(url: string | undefined): boolean {
  return !!url && /^https:\/\/www\.google\.[a-z.]+\/maps/.test(url);
}
export async function openInGoogleMaps(url: string): Promise<void> {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.id != null && isGoogleMapsUrl(active.url)) {
    await chrome.tabs.update(active.id, { url });
  } else {
    await chrome.tabs.create({ url });
  }
}

/**
 * Selecting a vehicle while Google Maps is the tab in front moves
 * Google's pin to it — the person reading Google's map does not have to
 * press a button per truck.  ONLY that tab: with a load board or email
 * in front nothing happens, the explicit buttons stay for that.
 * Returns whether a tab was driven.
 */
export async function followInGoogleMaps(url: string): Promise<boolean> {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.id != null && isGoogleMapsUrl(active.url)) {
    await chrome.tabs.update(active.id, { url });
    return true;
  }
  return false;
}

const FOLLOW_KEY = 'followGoogleMaps';
/** On until the person switches it off; the choice survives the panel closing. */
export async function getFollowPref(): Promise<boolean> {
  return getFlag(FOLLOW_KEY, true);
}
export async function setFollowPref(on: boolean): Promise<void> {
  await setFlag(FOLLOW_KEY, on);
}
