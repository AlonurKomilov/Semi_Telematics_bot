/**
 * The free hand-off to Google Maps: their documented URL API, no key.
 * If a Google Maps tab is already open we drive THAT one, so the person
 * who has satellite view zoomed where they want it keeps it; otherwise
 * we open one.  This is deliberately not "draw on Google's map" — the
 * pin is Google's, one truck at a time, and it does not move.
 */
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
