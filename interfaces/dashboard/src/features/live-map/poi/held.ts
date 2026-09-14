/**
 * Holding a whole POI layer, so that panning stops asking the server.
 *
 * The built-in layers describe things that do not move and they are
 * small — measured on the real data, the largest is about 5,400 points
 * and 169 KB gzipped, the smallest 18 KB; all six together are 427 KB.
 * One viewport's worth is 1 KB.  So per-viewport fetching is cheaper
 * per request and dearer per session, and it is the reason this module's
 * neighbour carries a 1-degree grid, supersets, TTLs and an eviction
 * ladder: all of that exists to make per-bbox caching correct.  A client
 * holding the layer needs none of it — it filters to the view and draws.
 *
 * VERSION-AND-REPLACE, NOT A DELTA.  The server's weekly import sweeps
 * with a hard DELETE, so a delta would have to carry tombstones or a
 * held copy would keep a truck stop that closed.  Asking "is my version
 * still yours" and replacing the set wholesale gets that right for
 * nothing, and a weekly import makes the download rare.
 *
 * INDEXEDDB AND NOT localStorage, which is what everything else in this
 * feature uses.  The six layers are 3.1 MB of JSON, which localStorage
 * stores as UTF-16 and counts at roughly twice that against an origin
 * budget of about 5 MB — a budget the viewport cache beside it is
 * already spending, and one that every preferences write shares.  A
 * quota failure here would not stay here.
 */

import type { PoiFeature } from './layers';

/** A layer as held: the points, the version they are, and how old the
 *  data itself is.
 *
 *  TWO DATES, AND THEY ANSWER DIFFERENT QUESTIONS.  `version` is when
 *  the server imported — it is compared, never shown.  `sourceAsOf` is
 *  what the OpenStreetMap extract behind it was stamped, which is what
 *  the freshness line renders, and the mirrors run months behind: a
 *  copy imported this morning can hold data from June.  Null when the
 *  server did not report one, and unknown says nothing. */
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
  // them may be drawn empty: "there is nothing here" and "I could not
  // ask" are different sentences, and only one of them is true.
  if (!serverVersion) return 'per-view';
  if (cachedVersion && cachedVersion === serverVersion) return 'hold';
  return 'download';
}

// ── the store ─────────────────────────────────────────────────────────

const DB_NAME = '4truck-poi';
const STORE = 'layers';

let opening: Promise<IDBDatabase | null> | null = null;

/** The database, or null if this browser will not give us one — a
 *  private window, a blocked origin, an upgrade another tab is holding.
 *  Null is a first-class answer everywhere below: the layer is still
 *  drawn, it is just re-downloaded next session. */
function openDb(): Promise<IDBDatabase | null> {
  opening ??= new Promise<IDBDatabase | null>((resolve) => {
    try {
      if (typeof indexedDB === 'undefined') { resolve(null); return; }
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) {
          req.result.createObjectStore(STORE);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch { resolve(null); }
  });
  return opening;
}

export async function readHeld(layer: string): Promise<HeldLayer | null> {
  const db = await openDb();
  if (!db) return null;
  const got = await new Promise<unknown>((resolve) => {
    try {
      const t = db.transaction(STORE, 'readonly');
      const r = t.objectStore(STORE).get(layer);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => resolve(null);
      t.onabort = () => resolve(null);
    } catch { resolve(null); }
  });
  const held = got as HeldLayer | undefined;
  // The store survives a deploy, so a shape written by an older build
  // can still be sitting there.  A half-read is worse than a miss: it
  // would draw with `features` undefined.
  if (!held || typeof held.version !== 'string' || !Array.isArray(held.features)) {
    return null;
  }
  return held;
}

/** Store a layer.  Returns false when it could not be kept — a full
 *  quota, most likely — and the caller then simply does not hold it
 *  across sessions: the layer is in memory either way. */
export async function writeHeld(layer: string, held: HeldLayer): Promise<boolean> {
  const db = await openDb();
  if (!db) return false;
  return new Promise<boolean>((resolve) => {
    try {
      const t = db.transaction(STORE, 'readwrite');
      // The TRANSACTION and not the request: a quota refusal aborts the
      // transaction, and a request that reported success inside an
      // aborted one has written nothing.
      t.oncomplete = () => resolve(true);
      t.onerror = () => resolve(false);
      t.onabort = () => resolve(false);
      t.objectStore(STORE).put(held, layer);
    } catch { resolve(false); }
  });
}

export async function dropHeld(layer: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const t = db.transaction(STORE, 'readwrite');
      t.oncomplete = () => resolve();
      t.onerror = () => resolve();
      t.onabort = () => resolve();
      t.objectStore(STORE).delete(layer);
    } catch { resolve(); }
  });
}

/** Test seam: forget the cached handle so a fresh database is opened.
 *  Production never needs this — the handle lives as long as the tab. */
export function _resetDbForTests(): void {
  opening = null;
}
