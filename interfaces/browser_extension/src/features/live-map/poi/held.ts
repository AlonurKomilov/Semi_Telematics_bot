/**
 * Holding a whole layer, so that panning stops asking the server.
 *
 * The built-in layers describe things that do not move and they are
 * small — measured on the real data, the largest is about 5,400 points
 * and 169 KB gzipped, the smallest 18 KB.  A viewport's worth is 1 KB.
 * So per-viewport fetching is cheaper per request and dearer per
 * session, and it is the reason this package carries a 1-degree grid,
 * an expanded box, a cache-key match and a covering-entry search: all
 * of that exists to make per-bbox caching correct.  A client holding
 * the layer needs none of it — it filters to the view and draws.
 *
 * VERSION-AND-REPLACE, NOT A DELTA.  The server's import sweeps with a
 * hard DELETE, so a delta would have to carry tombstones or a held copy
 * would keep a truck stop that closed.  Asking "is my version still
 * yours" and replacing the set wholesale gets that right for nothing,
 * and a weekly import makes the download rare.
 *
 * chrome.storage.local and not localStorage: MV3 gives it 10 MB where
 * localStorage gives about 5, and all six layers together are 3.1 MB.
 * It is async, which the panel's prefs already are.
 */

import type { PoiFeature } from './viewport';

/** A layer as held: the points, the version they are, and how old the
 *  data itself is.
 *
 *  TWO DATES, AND THEY ANSWER DIFFERENT QUESTIONS.  `version` is when
 *  the server imported — it is compared, never shown.  `sourceAsOf` is
 *  what the OpenStreetMap extract behind it was stamped, which is the
 *  one the freshness line renders, and the mirrors run months behind:
 *  a copy imported this morning can hold data from June.  Undefined
 *  when the server did not report one, and unknown says nothing. */
export interface HeldLayer {
  version: string;
  features: PoiFeature[];
  sourceAsOf?: string | null;
}

/** What to do about one layer, decided before any bytes move. */
export type HeldPlan =
  /** We hold this version — draw from it, ask for nothing. */
  | 'hold'
  /** Ours is missing or stale — fetch the layer whole. */
  | 'download'
  /** The server has no import for it — use the viewport endpoint, which
   *  is what this layer has always done.  NEVER an empty draw. */
  | 'per-view';

export function planFor(
  cachedVersion: string | null | undefined,
  serverVersion: string | null | undefined,
): HeldPlan {
  // No version on the server means the layer has never imported cleanly
  // — a custom layer, the repair-shop directory, or a built-in whose
  // import has not finished.  None of those can be held, and none of
  // them may be drawn empty.
  if (!serverVersion) return 'per-view';
  if (cachedVersion && cachedVersion === serverVersion) return 'hold';
  return 'download';
}

const key = (layer: string) => `poi_held_${layer}`;

export async function readHeld(layer: string): Promise<HeldLayer | null> {
  try {
    const got = await chrome.storage.local.get(key(layer));
    const held = got?.[key(layer)] as HeldLayer | undefined;
    if (!held || typeof held.version !== 'string' || !Array.isArray(held.features)) {
      return null;
    }
    return held;
  } catch {
    // Storage unavailable is not a reason to show nothing — the caller
    // falls through to fetching.
    return null;
  }
}

/** Store a layer.  Returns false when it could not be kept — a full
 *  quota, most likely — and the caller then simply does not hold it:
 *  the layer still draws, it is just re-downloaded next session. */
export async function writeHeld(layer: string, held: HeldLayer): Promise<boolean> {
  try {
    await chrome.storage.local.set({ [key(layer)]: held });
    return true;
  } catch {
    return false;
  }
}

export async function dropHeld(layer: string): Promise<void> {
  try {
    await chrome.storage.local.remove(key(layer));
  } catch { /* nothing held is the state we wanted anyway */ }
}
