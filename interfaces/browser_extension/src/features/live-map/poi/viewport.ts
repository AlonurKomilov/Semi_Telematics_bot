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

export function brandMatch(terms: string[], f: PoiFeature): boolean {
  const [name, brand, op] = brandFields(f);
  return terms.some((t) => wordMatch(name, t) || wordMatch(brand, t) || wordMatch(op, t));
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
