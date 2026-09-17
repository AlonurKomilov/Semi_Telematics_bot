/**
 * The Map Layers' arithmetic — the bbox grid, what is remembered of an
 * answer (both caches), the marker budget, and brand matching.  No
 * Leaflet, no React, no `chrome.*`: everything here is a function of its
 * arguments, which is why it is the part with tests.  Named for the same
 * role the server's `features/live_map/poi/viewport.py` plays: where a
 * request may look, and what is kept of what came back.
 *
 * The dashboard's `features/live-map/poi/usePoiLayers.ts` is the original.  What is
 * shared with it is shared on purpose and MUST stay identical, because
 * the two surfaces hit the same endpoint with the same `bbox` string:
 * the 1° grid, the one-cell expansion, and the cache key spelled from
 * it.  A panel that snapped its bbox differently would miss every
 * cache the dashboard filled and re-ask Overpass for the same square.
 */

/** One GeoJSON point as served by GET /map/pois. */
export interface PoiFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: Record<string, unknown> | null;
}

/** south, west, north, east — the order the API takes them in. */
export type Bbox4 = [south: number, west: number, north: number, east: number];

/** What Leaflet's `getBounds()` tells us, without Leaflet in the types. */
export interface ViewBox { south: number; west: number; north: number; east: number }

// ── the grid ─────────────────────────────────────────────────────────────

/** 1° ≈ 111 km.  Big enough that a person panning across a city never
 *  leaves the cell, small enough that one cell is a sane Overpass query. */
const GRID_STEP = 1.0;

export function snapToGrid(v: number, step: number = GRID_STEP): number {
  return Math.floor(v / step) * step;
}

/**
 * The snapped box, grown by one cell on every side so the visible view
 * is always strictly inside what was fetched — that is what makes a
 * zoom-in free (see `bboxCovers`).
 *
 * Longitude is clamped to ±180: a fleet straddling the date line would
 * otherwise produce a box the server refuses.  Clamping splits the
 * fetch at the meridian, and each side still caches.
 */
export function expandedBbox(b: ViewBox): Bbox4 {
  const step = GRID_STEP;
  let s = snapToGrid(b.south, step) - step;
  let n = snapToGrid(b.north, step) + step;
  let w = snapToGrid(b.west,  step) - step;
  let e = snapToGrid(b.east,  step) + step;
  s = Math.max(s, -90);
  n = Math.min(n,  90);
  w = Math.max(w, -180);
  e = Math.min(e,  180);
  if (n <= s) n = Math.min(s + step, 90);
  if (e <= w) e = Math.min(w + step, 180);
  return [s, w, n, e];
}

/** The cache key: the same box, rounded to whole degrees. */
export function bboxKey(b: ViewBox): string {
  return expandedBbox(b).map((v) => v.toFixed(0)).join(',');
}

/** The `bbox` query parameter — the same box, unrounded, so a key and
 *  the request that filled it always describe one area. */
export function bboxParam(b: ViewBox): string {
  return expandedBbox(b).join(',');
}

export function parseBboxKey(key: string): Bbox4 | null {
  const parts = key.split(',').map(Number);
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return null;
  return parts as Bbox4;
}

/** True when a cached key's box fully contains the view — i.e. the
 *  answer is already in hand and only needs filtering. */
export function bboxCovers(cachedKey: string, view: ViewBox): boolean {
  const c = parseBboxKey(cachedKey);
  if (!c) return false;
  return c[0] <= view.south && c[1] <= view.west
      && c[2] >= view.north && c[3] >= view.east;
}

export function filterToView(features: PoiFeature[], view: ViewBox): PoiFeature[] {
  return features.filter((f) => {
    const [lng, lat] = f.geometry.coordinates;
    return lat >= view.south && lat <= view.north
        && lng >= view.west  && lng <= view.east;
  });
}

// ── the marker budget ────────────────────────────────────────────────────

/**
 * How many markers the panel will draw for one layer.
 *
 * The dashboard answers this with the markercluster plugin: a thousand
 * fuel stops become forty bubbles and pan stays smooth.  The panel has
 * no plugin and a 320px column, where a cluster bubble would cover a
 * city — so it draws the ones NEAREST THE CENTRE of what the person is
 * looking at and says, in the layer's own row, that it did.
 *
 * A cap that lies is worse than no cap: the row shows both numbers, so
 * "12 truck stops near me" is never silently "12 of 900".
 *
 * MEASURED 2026-09-14, once the layers were in our own table and could
 * be counted instead of guessed at — the number was chosen before that
 * was possible, and this is the check it was owed.  Points per layer in
 * a real viewport:
 *
 *     Chicago metro  (0.5 deg)   28 at the most  (weigh stations)
 *     Chicago wide   (1.5 deg)   96
 *     I-80 corridor  (6 deg)    205   <- widest tested, still under
 *     Dallas-Houston (4 deg)    140
 *
 * Six degrees is already absurd on a 320px column, and the densest
 * layer there still fits.  So the cap does not bite in practice: nobody
 * is being shown a partial layer, and nothing is drawing hundreds of
 * markers to stutter over.  Left exactly as it was — but now because it
 * was counted, not because nobody had looked.
 */
export const MARKER_BUDGET = 250;


// ── clustering, without a plugin ──────────────────────────────────────
//
// THE MEASUREMENT ABOVE WENT STALE THE DAY THE PANEL STARTED HOLDING A
// LAYER WHOLE.  It was taken when a pan fetched one viewport and the
// server refused a box bigger than a few degrees — so a wide zoom drew
// nothing at all, and 250 was never reached.  Holding the layer means a
// country-wide view now filters 2,340 weigh stations into the draw, and
// the cap bites every time: "Nearest 250 shown — zoom in for the rest",
// on a map the dashboard shows whole.
//
// The dashboard's answer is the markercluster plugin.  The panel still
// will not carry one — it bundles no Leaflet plugins and the column is
// 320px — so it buckets by hand.  A GRID IN PIXEL SPACE, not in
// degrees: a degree of longitude is a different number of pixels at
// Montana than at Texas, and a degree grid draws visibly coarser
// clusters as you go north.
//
// The cap stays as a floor under the CLUSTER count, where it cannot
// bite: a 320x400 column holds about 60 cells of 44px.

/** One drawn thing: a point, or a bubble standing for several. */
export interface PoiCluster {
  lat: number;
  lng: number;
  count: number;
  /** The feature itself, when the bubble stands for exactly one — so a
   *  single point keeps its popup and its DEF badge. */
  one: PoiFeature | null;
}

/** Degrees of LONGITUDE that `px` screen pixels cover at this zoom.
 *
 *  Web Mercator puts 360 degrees across 256·2^zoom pixels, and that
 *  relation is exact and constant for longitude — which is why the
 *  latitude side is derived from it below rather than measured. */
export function degreesPerPixel(zoom: number, px: number): number {
  return (px * 360) / (256 * Math.pow(2, zoom));
}

/**
 * Bucket features into a screen-square grid.
 *
 * Latitude cells are scaled by cos(lat) because Mercator stretches
 * north-south as you leave the equator: without it a cell that is 44px
 * wide is 44px tall in Texas and about 30px tall at the Canadian
 * border, and the clusters visibly change shape across one screen.
 *
 * A bucket's position is the MEAN of its members, not the cell centre —
 * a bubble that sits where its points are reads as those points; one
 * pinned to a grid corner reads as a grid.
 */
export function clusterByGrid(
  features: PoiFeature[], cellLng: number,
): PoiCluster[] {
  if (cellLng <= 0) {
    return features.map((f) => ({
      lat: f.geometry.coordinates[1], lng: f.geometry.coordinates[0],
      count: 1, one: f,
    }));
  }
  const buckets = new Map<string, { lat: number; lng: number; n: number; one: PoiFeature }>();
  for (const f of features) {
    const [lng, lat] = f.geometry.coordinates;
    // cos(lat) never reaches 0 in any inhabited latitude, but a bad
    // coordinate should not divide by one.
    const cellLat = cellLng * Math.max(0.15, Math.cos((lat * Math.PI) / 180));
    const key = `${Math.floor(lng / cellLng)}:${Math.floor(lat / cellLat)}`;
    const b = buckets.get(key);
    if (b) { b.lat += lat; b.lng += lng; b.n += 1; }
    else buckets.set(key, { lat, lng, n: 1, one: f });
  }
  return [...buckets.values()].map((b) => ({
    lat: b.lat / b.n, lng: b.lng / b.n, count: b.n,
    one: b.n === 1 ? b.one : null,
  }));
}

/** Squared degrees — no need for a real distance to rank by nearness. */
function d2(f: PoiFeature, lat: number, lng: number): number {
  const [flng, flat] = f.geometry.coordinates;
  const dy = flat - lat;
  // Longitude degrees shrink with latitude; without this a north-south
  // neighbour and an east-west one at the same screen distance rank
  // differently, and the cap would keep the wrong ones.
  const dx = (flng - lng) * Math.cos((lat * Math.PI) / 180);
  return dy * dy + dx * dx;
}

/** The `cap` CLUSTERS closest to (lat, lng).
 *
 *  Its own function and not a cast of the one below: a cluster carries
 *  `lat`/`lng` and a feature carries `geometry.coordinates`, so handing
 *  one to the other type-checks only through `as unknown as` and then
 *  reads `undefined.coordinates` the first time the cap actually bites.
 *  Which is to say: at country zoom on a dense layer, in front of
 *  somebody. */
export function nearestClustersFirst(
  clusters: PoiCluster[], lat: number, lng: number, cap: number = MARKER_BUDGET,
): PoiCluster[] {
  if (clusters.length <= cap) return clusters;
  const d = (c: PoiCluster) => {
    const dy = c.lat - lat;
    const dx = (c.lng - lng) * Math.cos((lat * Math.PI) / 180);
    return dy * dy + dx * dx;
  };
  return [...clusters].sort((a, b) => d(a) - d(b)).slice(0, cap);
}


/** The `cap` features closest to (lat, lng).  Returns the input
 *  untouched when it already fits — the common case, and the one that
 *  must not pay for a sort. */
export function nearestFirst(
  features: PoiFeature[], lat: number, lng: number, cap: number = MARKER_BUDGET,
): PoiFeature[] {
  if (features.length <= cap) return features;
  return [...features]
    .sort((a, b) => d2(a, lat, lng) - d2(b, lat, lng))
    .slice(0, cap);
}

// ── brands ───────────────────────────────────────────────────────────────

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const _needles = new Map<string, RegExp>();
function needleRegex(needle: string): RegExp {
  let re = _needles.get(needle);
  if (!re) {
    re = new RegExp('\\b' + escapeRegExp(needle) + '\\b', 'i');
    _needles.set(needle, re);
  }
  return re;
}

/**
 * Word-boundary match, case-insensitive.  The boundary is the whole
 * point: 'TA' must find "TA-Petro #145" and must NOT find "station",
 * and 'BP' must not find "Sapp Bros".
 */
export function wordMatch(haystack: string, needle: string): boolean {
  if (!haystack || !needle) return false;
  return needleRegex(needle).test(haystack);
}

/** The three OSM fields a chain's name can hide in. */
export function brandFields(f: PoiFeature): [string, string, string] {
  const p = f.properties ?? {};
  return [
    String(p.name ?? ''),
    String(p.brand ?? ''),
    String(p.operator ?? ''),
  ];
}

/**
 * A BRAND TAG IS A STATEMENT OF IDENTITY; A NAME IS ONLY A HINT.
 *
 * Word matching every field alike is what put Canada under an American
 * chip.  `wordMatch` treats a hyphen as a boundary on purpose — it is
 * how 'TA' finds "TA-Petro #145" — so the `TA / Petro` chip's bare
 * `Petro` term also matched `Petro-Canada`, `Petro Seven`, `Petro Bras`
 * and `PetroUS`: four different companies, filed under one.
 *
 * Enumerating those would have been a list of everyone we had already
 * met.  The rule instead: when OSM states a `brand`, that IS the chain
 * and the term must equal it; a chip may only go hunting through the
 * free-text name when no brand was stated at all.  Under-claiming a
 * point is a missing pin; mis-claiming one is a lie about where the
 * driver is going.
 */
export function brandMatch(terms: string[], f: PoiFeature): boolean {
  const [name, brand, op] = brandFields(f);
  if (brand) {
    const stated = brand.trim().toLowerCase();
    return terms.some((t) => t.trim().toLowerCase() === stated);
  }
  return terms.some((t) => wordMatch(name, t) || wordMatch(op, t));
}

// ── localStorage, shared with nothing ────────────────────────────────────
//
// The side panel is opened and closed all day — every close throws the
// in-memory cache away.  That makes this cache worth MORE here than on
// the dashboard, where the tab stays open: without it, every reopen
// re-asks Overpass for the square the person was just looking at.

const LS_PREFIX   = 'poi_v1_';
/** Under half an hour old: use it and ask nothing. */
export const LS_FRESH_MS = 30 * 60 * 1000;
/** Over two hours old: too old to show at all. */
export const LS_STALE_MS = 2 * 60 * 60 * 1000;
/** Age alone does not bound growth: a panning session mints one entry
 *  per viewport, and a full quota fails every preference write too. */
const LS_MAX_ENTRIES = 30;

export interface LsEntry { ts: number; features: PoiFeature[] }

export function lsRead(layerId: string, key: string, now = Date.now()): LsEntry | null {
  try {
    const k = `${LS_PREFIX}${layerId}_${key}`;
    const raw = localStorage.getItem(k);
    if (!raw) return null;
    const entry = JSON.parse(raw) as LsEntry;
    if (now - entry.ts > LS_STALE_MS) { localStorage.removeItem(k); return null; }
    return entry;
  } catch { return null; }
}

export function lsWrite(layerId: string, key: string, features: PoiFeature[]): void {
  try {
    localStorage.setItem(
      `${LS_PREFIX}${layerId}_${key}`,
      JSON.stringify({ ts: Date.now(), features } satisfies LsEntry),
    );
    lsPrune();
  } catch { /* quota — the layer still works, it just re-fetches */ }
}

export function lsPrune(now = Date.now()): void {
  const cutoff = now - LS_STALE_MS;
  try {
    const live: { key: string; ts: number }[] = [];
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (!k?.startsWith(LS_PREFIX)) continue;
      const raw = localStorage.getItem(k);
      if (!raw) { localStorage.removeItem(k); continue; }
      try {
        const ts = (JSON.parse(raw) as LsEntry).ts;
        if (ts < cutoff) localStorage.removeItem(k);
        else live.push({ key: k, ts });
      } catch { localStorage.removeItem(k); }
    }
    if (live.length > LS_MAX_ENTRIES) {
      live.sort((a, b) => a.ts - b.ts);
      for (const e of live.slice(0, live.length - LS_MAX_ENTRIES)) {
        localStorage.removeItem(e.key);
      }
    }
  } catch { /* ignore */ }
}

/** Any cached box that covers this view — how a zoom-in costs nothing
 *  even after the panel was closed and reopened. */
export function lsFindCovering(layerId: string, view: ViewBox, now = Date.now()): LsEntry | null {
  try {
    const prefix = `${LS_PREFIX}${layerId}_`;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k?.startsWith(prefix)) continue;
      if (!bboxCovers(k.slice(prefix.length), view)) continue;
      const raw = localStorage.getItem(k);
      if (!raw) continue;
      try {
        const entry = JSON.parse(raw) as LsEntry;
        if (now - entry.ts > LS_STALE_MS) continue;
        return entry;
      } catch { /* malformed — skip */ }
    }
  } catch { /* ignore */ }
  return null;
}
