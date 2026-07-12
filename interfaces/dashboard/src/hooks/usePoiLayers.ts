/**
 * usePoiLayers — manages all POI map overlay layers.
 *
 * Caching strategy (fastest → slowest):
 *   1. In-memory exact key  — instant, session-only
 *   2. In-memory superset   — zoom-in is always free: current view ⊆ cached area,
 *                             filter existing features, no network call
 *   3. localStorage exact   — persistent across sessions (stale-while-revalidate)
 *   4. localStorage superset— zoom-in after a page reload: same superset logic
 *                             applied to persisted data
 *   5. Network fetch        — last resort; result stored in both caches
 *
 * TTLs: fresh < 30 min (serve + return), stale < 2 hr (serve + silent revalidate),
 *       expired ≥ 2 hr (treat as miss).
 *
 * The hook is consumed by <PoiLayerPanel> — no other component needs to know
 * this hook exists.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch, apiJSON } from '../api/client';
import { POI_LAYERS } from '../config/poiLayers';
import type { PoiLayerDef } from '../config/poiLayers';
import type L from 'leaflet';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PoiFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: Record<string, unknown>;
}

interface PoisResponse {
  type: 'FeatureCollection';
  features: PoiFeature[];
}

/** Server-side custom POI layer DTO (from GET /map/custom-layers). */
export interface CustomLayerDto {
  id: number;
  layer_key: string;
  label: string;
  color: string;
  icon: string;
  source_type: 'overpass' | 'csv';
  overpass_query: string;
  brand_filters: { value: string; label: string; icon?: string; matchTerms?: string[] }[];
  default_on: boolean;
  created_at: string;
  updated_at: string;
}

export interface UsePoiLayersResult {
  enabled: Record<string, boolean>;
  toggle: (id: string) => void;
  loading: Record<string, boolean>;
  errors: Record<string, string | undefined>;
  counts: Record<string, number>;
  brandFilters: Record<string, Set<string>>;
  toggleBrand: (layerId: string, value: string) => void;
  /** Which of the hardcoded brandFilters values actually appear in the current loaded data. */
  presentBrands: Record<string, Set<string>>;
  /** All loaded features per layer (before brand filter) — used for nearest-POI and CSV export. */
  allFeatures: Record<string, PoiFeature[]>;
  /** Combined built-in + custom layer definitions in display order. */
  effectiveLayers: PoiLayerDef[];
  /** Re-fetch the custom layer list from the server (call after create/edit/delete). */
  refreshCustomLayers: () => Promise<void>;
}

// ── localStorage persistence ──────────────────────────────────────────────────

/** Bump this version string whenever the PoiFeature shape changes to auto-bust stale caches. */
const LS_PREFIX     = 'poi_v2_';
const LS_FRESH_MS   = 30 * 60 * 1000;  // <30 min  → use directly
const LS_STALE_MS   = 2  * 60 * 60 * 1000; // <2 hr   → use + revalidate silently

interface LsEntry { ts: number; features: PoiFeature[] }

function lsRead(layerId: string, key: string): LsEntry | null {
  try {
    const raw = localStorage.getItem(`${LS_PREFIX}${layerId}_${key}`);
    if (!raw) return null;
    const entry = JSON.parse(raw) as LsEntry;
    // Hard expiry — ignore entries older than 2 hours
    if (Date.now() - entry.ts > LS_STALE_MS) {
      localStorage.removeItem(`${LS_PREFIX}${layerId}_${key}`);
      return null;
    }
    return entry;
  } catch { return null; }
}

function lsWrite(layerId: string, key: string, features: PoiFeature[]): void {
  try {
    localStorage.setItem(
      `${LS_PREFIX}${layerId}_${key}`,
      JSON.stringify({ ts: Date.now(), features } satisfies LsEntry),
    );
    lsPrune();
  } catch { /* quota exceeded — skip silently */ }
}

/** Remove all POI cache entries older than 2 hours to keep storage bounded. */
function lsPrune(): void {
  const cutoff = Date.now() - LS_STALE_MS;
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (!k?.startsWith(LS_PREFIX)) continue;
      const raw = localStorage.getItem(k);
      if (!raw) { localStorage.removeItem(k); continue; }
      try {
        if ((JSON.parse(raw) as LsEntry).ts < cutoff) localStorage.removeItem(k);
      } catch { localStorage.removeItem(k); }
    }
  } catch { /* ignore */ }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Word-boundary-aware substring match (case-insensitive).
 *
 *   wordMatch('station',          'TA')    === false  ✓  (no boundary inside word)
 *   wordMatch('TA Travel Center', 'TA')    === true   ✓
 *   wordMatch('TA-Petro #145',    'TA')    === true   ✓  (hyphen is a boundary)
 *   wordMatch('Pilot Flying J',   'Pilot') === true   ✓
 *
 * Uses a real \b regex (cached per needle).  Treats spaces, hyphens, slashes,
 * commas, periods all as boundaries — so brands embedded in compound names
 * like "TA-Petro" or "Pilot/Flying J" still match.  The previous pad-with-
 * spaces trick missed those cases.
 */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const _wordMatchCache = new Map<string, RegExp>();
function needleRegex(needle: string): RegExp {
  let re = _wordMatchCache.get(needle);
  if (!re) {
    re = new RegExp('\\b' + escapeRegExp(needle) + '\\b', 'i');
    _wordMatchCache.set(needle, re);
  }
  return re;
}

function wordMatch(haystack: string, needle: string): boolean {
  if (!haystack || !needle) return false;
  return needleRegex(needle).test(haystack);
}

/** Returns true when any of the given terms word-matches any of the text fields. */
function brandMatch(terms: string[], name: string, brand: string, op: string): boolean {
  return terms.some((t) => wordMatch(name, t) || wordMatch(brand, t) || wordMatch(op, t));
}

/**
 * Snap bbox coordinates to a fixed 1° grid so panning half a degree re-uses
 * the same cache entry rather than creating a new miss.
 * Each grid cell covers ~111km × ~111km which is a reasonable tile size for POI data.
 */
function snapToGrid(v: number, step: number): number {
  return Math.floor(v / step) * step;
}

/** Expand the snapped bbox by one grid cell in each direction so the current
 * view is always fully contained in the fetched area.
 *
 * Antimeridian: clamp longitude to [-180, 180].  Maps that straddle the date
 * line (Alaska/Hawaii fleets) would otherwise produce a degenerate bbox the
 * server rejects.  Clamping splits at ±180 — the cached entry on each side
 * still works for typical US use.
 */
function _expandedBbox(map: L.Map): Bbox4 {
  const b    = map.getBounds();
  const step = 1.0; // 1° ≈ 111km
  let s = snapToGrid(b.getSouth(), step) - step;
  let n = snapToGrid(b.getNorth(), step) + step;
  let w = snapToGrid(b.getWest(),  step) - step;
  let e = snapToGrid(b.getEast(),  step) + step;
  // Clamp into valid coordinate ranges
  s = Math.max(s, -90);
  n = Math.min(n,  90);
  w = Math.max(w, -180);
  e = Math.min(e,  180);
  // Defensive: ensure n>s and e>w (Leaflet should guarantee this, but be safe)
  if (n <= s) n = Math.min(s + step, 90);
  if (e <= w) e = Math.min(w + step, 180);
  return [s, w, n, e];
}

function bboxKey(map: L.Map): string {
  const [s, w, n, e] = _expandedBbox(map);
  return [s.toFixed(0), w.toFixed(0), n.toFixed(0), e.toFixed(0)].join(',');
}

function bboxParam(map: L.Map): string {
  // Use the same snapped+expanded bbox for the API call so cache keys match
  const [s, w, n, e] = _expandedBbox(map);
  return [s, w, n, e].join(',');
}

// ── Superset coverage helpers ─────────────────────────────────────────────────
//
// When the user zooms in, the new view bbox is always a subset of an already-
// fetched bbox.  Instead of a network call we can filter the existing features
// down to the visible area — completely free, runs in microseconds.

type Bbox4 = [south: number, west: number, north: number, east: number];

function parseBboxKey(key: string): Bbox4 | null {
  const parts = key.split(',').map(Number);
  if (parts.length !== 4 || parts.some(isNaN)) return null;
  return parts as Bbox4;
}

/** Returns true when cachedKey's bbox fully contains the view bbox. */
function bboxCovers(cachedKey: string, s: number, w: number, n: number, e: number): boolean {
  const c = parseBboxKey(cachedKey);
  if (!c) return false;
  return c[0] <= s && c[1] <= w && c[2] >= n && c[3] >= e;
}

/** Filter a feature list to those whose coordinates fall inside the view. */
function filterToView(features: PoiFeature[], s: number, w: number, n: number, e: number): PoiFeature[] {
  return features.filter((f) => {
    const [lng, lat] = f.geometry.coordinates;
    return lat >= s && lat <= n && lng >= w && lng <= e;
  });
}

/**
 * Search localStorage for any cached bbox that fully covers the current view.
 * Returns the matching entry (features are the full superset — caller filters).
 */
function lsFindCovering(layerId: string, s: number, w: number, n: number, e: number): LsEntry | null {
  try {
    const prefix = `${LS_PREFIX}${layerId}_`;
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k?.startsWith(prefix)) continue;
      const bboxPart = k.slice(prefix.length);
      if (!bboxCovers(bboxPart, s, w, n, e)) continue;
      const raw = localStorage.getItem(k);
      if (!raw) continue;
      try {
        const entry = JSON.parse(raw) as LsEntry;
        if (Date.now() - entry.ts > LS_STALE_MS) continue; // expired
        return entry;
      } catch { /* malformed — skip */ }
    }
  } catch { /* ignore */ }
  return null;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

/** Convert a server custom-layer DTO to the same `PoiLayerDef` shape as
 *  built-in layers so the render loop can treat them uniformly.  The id is
 *  prefixed with `custom_` so it dispatches to the per-tenant /pois branch
 *  on the backend and lives alongside built-ins in the localStorage cache.
 */
function customDtoToDef(dto: CustomLayerDto): PoiLayerDef {
  return {
    id: `custom_${dto.id}`,
    label: dto.label,
    color: dto.color,
    icon: dto.icon,
    defaultOn: dto.default_on,
    group: 'custom',
    brandFilters: (dto.brand_filters || []).map((bf) => ({
      value: bf.value,
      label: bf.label,
      icon: bf.icon,
      matchTerms: bf.matchTerms,
    })),
  };
}

export function usePoiLayers(
  leafletMap: React.RefObject<L.Map | null>,
  isReady: boolean,
): UsePoiLayersResult {
  // ── effective layer set: built-ins + custom (fetched from /map/custom-layers)
  const [customLayers, setCustomLayers] = useState<PoiLayerDef[]>([]);
  const effectiveLayers: PoiLayerDef[] = [...POI_LAYERS, ...customLayers];
  // Ref version so async callbacks always read the current list without
  // forcing every callback to re-create when custom layers change.
  const layersRef = useRef<PoiLayerDef[]>(effectiveLayers);
  useEffect(() => { layersRef.current = effectiveLayers; }, [effectiveLayers]);

  // Helper: ensure a record has an entry for every layer id (preserves existing
  // values, adds default for new ids when custom layers arrive after mount).
  const fillRecord = <V,>(prev: Record<string, V>, mk: (id: string) => V): Record<string, V> => {
    const next: Record<string, V> = { ...prev };
    let mutated = false;
    layersRef.current.forEach((l) => {
      if (!(l.id in next)) { next[l.id] = mk(l.id); mutated = true; }
    });
    return mutated ? next : prev;
  };

  const [enabled, setEnabled] = useState<Record<string, boolean>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, l.defaultOn])),
  );
  const [loading, setLoading] = useState<Record<string, boolean>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, false])),
  );
  const [errors, setErrors] = useState<Record<string, string | undefined>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, undefined])),
  );
  const [counts, setCounts] = useState<Record<string, number>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, 0])),
  );
  const [brandFilters, setBrandFilters] = useState<Record<string, Set<string>>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, new Set<string>()])),
  );
  const brandFiltersRef = useRef(brandFilters);
  useEffect(() => { brandFiltersRef.current = brandFilters; }, [brandFilters]);
  // Which hardcoded brandFilter values actually appear in the currently-loaded features
  const [presentBrands, setPresentBrands] = useState<Record<string, Set<string>>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, new Set<string>()])),
  );
  // Raw (pre-filter) features per layer — for nearest-POI calculation and CSV export
  const [allFeatures, setAllFeatures] = useState<Record<string, PoiFeature[]>>(
    () => Object.fromEntries(POI_LAYERS.map((l) => [l.id, []])),
  );

  // Whenever the custom layer list changes, extend each per-layer state record
  // with default values for the newly-added ids (preserving existing entries).
  useEffect(() => {
    setEnabled((prev)       => fillRecord(prev, (id) => layersRef.current.find((l) => l.id === id)?.defaultOn ?? false));
    setLoading((prev)       => fillRecord(prev, () => false));
    setErrors((prev)        => fillRecord(prev, () => undefined));
    setCounts((prev)        => fillRecord(prev, () => 0));
    setBrandFilters((prev)  => fillRecord(prev, () => new Set<string>()));
    setPresentBrands((prev) => fillRecord(prev, () => new Set<string>()));
    setAllFeatures((prev)   => fillRecord(prev, () => []));
  }, [customLayers]);

  // One LayerGroup per POI type, stored by id
  const layerGroups = useRef<Record<string, L.LayerGroup>>({});

  // Per-layer response cache: layerId → bboxKey → features[]
  const cache = useRef<Record<string, Record<string, PoiFeature[]>>>({});

  // Last rendered bboxKey per layer (for moveend re-renders)
  const lastBbox = useRef<Record<string, string>>({});

  // Per-layer in-flight fetch tracking.  Stores AbortController per
  // `${id}::${bboxKey}` so we can:
  //   1. dedupe concurrent fetches for the same bbox (existing behaviour)
  //   2. cancel in-flight fetches when the layer is toggled off or unmounted
  const fetchingRef = useRef<Map<string, AbortController>>(new Map());

  // enabledRef mirrors enabled state so async callbacks always see the current
  // value without needing to be re-created on every toggle.
  // Declared here (before any callback) to avoid TDZ references.
  const enabledRef = useRef(enabled);
  useEffect(() => { enabledRef.current = enabled; }, [enabled]);

  // ── custom layer fetch / refresh ──────────────────────────────────────────

  const refreshCustomLayers = useCallback(async () => {
    try {
      const data = await apiJSON<{ layers: CustomLayerDto[] }>('/map/custom-layers');
      setCustomLayers((data.layers || []).map(customDtoToDef));
    } catch {
      // 401 / 403 / network — keep current set, don't break built-ins
    }
  }, []);

  useEffect(() => { refreshCustomLayers(); }, [refreshCustomLayers]);

  // ── render helpers ─────────────────────────────────────────────────────────

  const renderLayer = useCallback((id: string, features: PoiFeature[]) => {
    const map = leafletMap.current;
    if (!map) return;
    const Leaf = window.L as typeof L;
    const def = layersRef.current.find((l) => l.id === id);
    if (!def) return;

    // Apply active brand sub-filters (client-side, no new network request).
    //
    // CROSS-LAYER INDEPENDENCE: each layer fetches its own data independently.
    // A location can appear in multiple layers simultaneously (e.g. a Pilot
    // station tagged fuel:adblue=yes shows in BOTH fuel_station AND def_station).
    // There is intentionally NO deduplication between layers.
    //
    // Matching uses wordMatch() (word-boundary aware) to prevent false positives:
    //   'TA' must NOT match 'station', 'BP' must NOT match 'Sapp Bros', etc.
    // PoiBrandFilter.matchTerms lists all OSM brand aliases for a single chip;
    // if omitted the chip's value is used directly as the sole match term.
    const activeFilters = brandFiltersRef.current[id];
    const visible = activeFilters?.size
      ? features.filter((f) => {
          const name  = ((f.properties?.name     as string) || '').toLowerCase();
          const brand = ((f.properties?.brand    as string) || '').toLowerCase();
          const op    = ((f.properties?.operator as string) || '').toLowerCase();
          return [...activeFilters].some((v) => {
            const filterDef = def.brandFilters?.find((bf) => bf.value === v);
            const terms = (filterDef?.matchTerms ?? [v]).map((t) => t.toLowerCase());
            return brandMatch(terms, name, brand, op);
          });
        })
      : features;

    setCounts((prev) => ({ ...prev, [id]: visible.length }));
    // Store raw (pre-filter) features for nearest-POI and CSV export
    setAllFeatures((prev) => ({ ...prev, [id]: features }));

    // Compute which hardcoded brand filters actually have matching features in this dataset
    // so the panel only shows chips for brands that exist in the current view.
    // ALWAYS write a fresh Set (even when empty) so chips disappear when panning
    // to an area where the brand has no presence.
    if (def.brandFilters?.length) {
      const present = new Set<string>();
      features.forEach((f) => {
        const nm = ((f.properties?.name     as string) || '').toLowerCase();
        const br = ((f.properties?.brand    as string) || '').toLowerCase();
        const op = ((f.properties?.operator as string) || '').toLowerCase();
        def.brandFilters!.forEach((bf) => {
          if (present.has(bf.value)) return;
          const terms = (bf.matchTerms ?? [bf.value]).map((t) => t.toLowerCase());
          if (brandMatch(terms, nm, br, op)) present.add(bf.value);
        });
      });
      setPresentBrands((prev) => ({ ...prev, [id]: present }));
    }

    // Ensure layer group exists and is attached to map.
    //
    // For dense layers (fuel stations, parking) the bbox can return 1000+
    // features.  Adding that many DOM-backed markers to a plain LayerGroup
    // makes pan/zoom unusable: every drag re-positions every marker, and
    // Leaflet creates real DOM nodes even for off-screen ones.  When the
    // markercluster plugin is loaded we use it instead — it bundles distant
    // markers into a cluster bubble at lower zooms, removes off-screen
    // markers from DOM via removeOutsideVisibleBounds, and adds new markers
    // in chunks so the UI stays responsive even with thousands of POIs.
    if (!layerGroups.current[id]) {
      const LExtra = window.L as unknown as {
        markerClusterGroup?: (o?: object) => L.LayerGroup;
      };
      // Per-layer cluster icon: use the layer's own color (def.color) instead
      // of markercluster's default green/yellow/red palette.  When the user
      // has multiple layers turned on at the same time, the screenshot from
      // QA showed every layer's clusters drawing in the same green/red so
      // they were indistinguishable.  By colouring the bubble with the
      // layer's brand colour and matching the icon emoji we let the user
      // see at a glance which layer a cluster belongs to.
      const iconCreateFunction = (cluster: { getChildCount: () => number }) => {
        const n = cluster.getChildCount();
        // Two compact size buckets — small for ≤10, large otherwise — so the
        // bubble stays readable at state-zoom without ever growing into the
        // gigantic 60 px circles markercluster ships by default.
        const size = n < 10 ? 32 : n < 100 ? 38 : 44;
        const fontSize = n < 100 ? 11 : 10;
        return Leaf.divIcon({
          className: 'poi-layer-cluster',
          html: `<div style="
            display:flex;align-items:center;justify-content:center;
            width:${size}px;height:${size}px;border-radius:50%;
            background:${def.color};color:#fff;font-weight:700;
            font-size:${fontSize}px;line-height:1;
            border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45);
            gap:2px;
          "><span style="font-size:${fontSize - 1}px">${def.icon}</span><span>${n}</span></div>`,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        });
      };
      layerGroups.current[id] = (LExtra.markerClusterGroup
        ? LExtra.markerClusterGroup({
            // 60 px is dense enough to merge a row of 5 highway exits at
            // state-zoom while still showing individual pumps street-level.
            maxClusterRadius: 60,
            // Above zoom 13 (city level) every POI renders individually,
            // matching the previous LayerGroup behaviour.
            disableClusteringAtZoom: 13,
            showCoverageOnHover: false,
            spiderfyOnMaxZoom: true,
            // Remove off-viewport DOM nodes — biggest single win for drag
            // performance with 1000+ markers.
            removeOutsideVisibleBounds: true,
            chunkedLoading: true,
            chunkInterval: 50,
            chunkDelay: 25,
            iconCreateFunction,
          })
        : Leaf.layerGroup()
      ).addTo(map);
    }
    const group = layerGroups.current[id];
    group.clearLayers();

    // Build all markers off-DOM, then bulk-insert.  markerClusterGroup
    // exposes addLayers() (note plural) which is dramatically faster than
    // calling addTo() per feature because it triggers a single index rebuild
    // instead of N.  The plain LayerGroup fallback also accepts addLayers().
    const markers: L.Layer[] = [];
    visible.forEach((f) => {
      const [lng, lat] = f.geometry.coordinates;
      const p = f.properties as Record<string, string> | null | undefined;
      const hasDef = p?.['fuel:adblue'] === 'yes';

      // Scale icon with zoom level for less clutter at overview zooms
      const zoom = map.getZoom();
      const sz   = zoom >= 13 ? 26 : zoom >= 10 ? 22 : zoom >= 7 ? 16 : 12;
      const fs   = zoom >= 13 ? 13 : zoom >= 10 ? 11 : zoom >= 7 ? 8  : 6;

      const icon = Leaf.divIcon({
        className: '',
        html: `<div style="
          position:relative;width:${sz}px;height:${sz}px;border-radius:50%;
          background:${def.color};border:2px solid #fff;
          box-shadow:0 1px 4px rgba(0,0,0,.45);
          display:flex;align-items:center;justify-content:center;
          font-size:${fs}px;line-height:1;
        ">${def.icon}${hasDef ? '<span style="position:absolute;bottom:-3px;right:-5px;background:#0d9488;color:#fff;font-size:6px;padding:1px 3px;border-radius:2px;font-weight:700;line-height:1.2;border:1px solid #fff">DEF</span>' : ''}</div>`,
        iconSize: [sz, sz],
        iconAnchor: [sz / 2, sz / 2],
      });

      const name = (p?.name) || def.label;
      const subtitle = (p?.brand && p.brand !== name) ? p.brand
        : (p?.operator && p.operator !== name) ? p.operator
        : def.label;

      // Amenity badges
      const amenities: string[] = [];
      if (p?.['fuel:diesel'] === 'yes') amenities.push('⛽ Diesel');
      if (p?.['fuel:adblue'] === 'yes') amenities.push('🧪 DEF');
      if (p?.shower === 'yes')          amenities.push('🚿 Showers');
      if (p?.toilets === 'yes')         amenities.push('🚻 Restrooms');
      if (p?.capacity)                  amenities.push(`🅿 ${p.capacity} spots`);
      if (p?.fee === 'no')              amenities.push('🆓 Free');
      if (p?.fee === 'yes')             amenities.push('💰 Fee');

      const meta: string[] = [];
      if (p?.opening_hours) meta.push(`🕐 ${p.opening_hours}`);
      if (p?.phone)         meta.push(`📞 ${p.phone}`);

      const amenityHtml = amenities.length
        ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:3px">${
            amenities.map((a) => `<span style="background:#374151;color:#e5e7eb;font-size:10px;padding:2px 6px;border-radius:10px;white-space:nowrap">${a}</span>`).join('')
          }</div>`
        : '';
      const metaHtml = meta.length
        ? `<div style="color:#9ca3af;font-size:10px;margin-top:4px">${meta.join(' &nbsp;·&nbsp; ')}</div>`
        : '';

      // Defer attach: we'll bulk-add via addLayers() after the loop.
      markers.push(Leaf.marker([lat, lng], { icon }).bindPopup(
        `<div style="min-width:160px;max-width:240px">`
        + `<div style="font-weight:600;font-size:13px">${name}</div>`
        + `<div style="color:#9ca3af;font-size:11px">${subtitle}</div>`
        + amenityHtml
        + metaHtml
        + `</div>`,
      ));
    });
    (group as L.LayerGroup & { addLayers?: (l: L.Layer[]) => void }).addLayers
      ? (group as L.LayerGroup & { addLayers: (l: L.Layer[]) => void }).addLayers(markers)
      : markers.forEach((m) => group.addLayer(m));
  }, [leafletMap]);

  const clearLayer = useCallback((id: string) => {
    layerGroups.current[id]?.clearLayers();
  }, []);

  // ── fetch + render for one layer ───────────────────────────────────────────
  //
  // Priority (fastest → slowest):
  //   1. in-memory exact key        → instant render, return
  //   2. in-memory superset (zoom-in) → filter + instant render, return
  //   3. localStorage exact key     → instant render; fresh→return, stale→revalidate silently
  //   4. localStorage superset      → filter + instant render; same freshness logic
  //   5. network fetch              → show spinner only if no cached data was shown

  const fetchAndRender = useCallback(async (id: string) => {
    const map = leafletMap.current;
    if (!map) return;

    const bbox  = bboxParam(map);
    const key   = bboxKey(map);
    const b     = map.getBounds();
    const s = b.getSouth(), w = b.getWest(), n = b.getNorth(), e = b.getEast();

    // 1. Exact in-memory hit
    if (cache.current[id]?.[key]) {
      if (enabledRef.current[id]) renderLayer(id, cache.current[id][key]);
      lastBbox.current[id] = key;
      return;
    }

    // 2. In-memory superset hit (zoom-in: current view ⊆ a previously-fetched area)
    if (cache.current[id]) {
      for (const [ckey, features] of Object.entries(cache.current[id])) {
        if (bboxCovers(ckey, s, w, n, e)) {
          const visible = filterToView(features, s, w, n, e);
          // Store the filtered slice so subsequent exact hits are O(1)
          cache.current[id][key] = visible;
          if (enabledRef.current[id]) renderLayer(id, visible);
          lastBbox.current[id] = key;
          return;
        }
      }
    }

    // 3. localStorage exact hit
    let lsEntry = lsRead(id, key);
    let lsWasSuperset = false;

    // 4. localStorage superset hit (zoom-in from a previously-persisted wide fetch)
    if (!lsEntry) {
      const superEntry = lsFindCovering(id, s, w, n, e);
      if (superEntry) {
        // Filter to current view before using
        lsEntry = { ts: superEntry.ts, features: filterToView(superEntry.features, s, w, n, e) };
        lsWasSuperset = true;
      }
    }

    let showSpinner = true;

    if (lsEntry) {
      if (!cache.current[id]) cache.current[id] = {};
      cache.current[id][key] = lsEntry.features;
      lastBbox.current[id] = key;
      if (enabledRef.current[id]) renderLayer(id, lsEntry.features);

      const age = Date.now() - lsEntry.ts;
      if (age < LS_FRESH_MS) return;  // Fresh — done
      showSpinner = false;            // Stale — revalidate silently (no spinner)
      // Superset was stale — don't re-fetch for the zoomed-in slice;
      // the superset entry will be revalidated at its own zoom level.
      if (lsWasSuperset) return;
    }

    // 5. Network fetch — deduplicated to prevent concurrent calls for the same layer+bbox
    const fetchKey = `${id}::${key}`;
    if (fetchingRef.current.has(fetchKey)) return;
    const controller = new AbortController();
    fetchingRef.current.set(fetchKey, controller);

    if (showSpinner) setLoading((prev) => ({ ...prev, [id]: true }));
    try {
      // 90 s timeout: Overpass queries on CONUS-scale bboxes (fuel +
      // brand-allowlist, weigh-station tag union) can legitimately take
      // 30-45 s before the backend returns.  The default 30 s apiFetch
      // timeout was clipping these requests with AbortError, which the
      // catch block below silently swallows (intended for user-toggled-off
      // aborts) — leaving the layer empty with no error indicator.
      const res = await apiFetch(
        `/map/pois?type=${encodeURIComponent(id)}&bbox=${bbox}`,
        { signal: controller.signal },
        90_000,
      );
      if (!res.ok) {
        // Extract the detail message (FastAPI 422 includes {detail: string})
        let msg = 'Failed to load layer';
        try {
          const body = await res.json();
          if (typeof body?.detail === 'string') msg = body.detail;
        } catch { /* ignore parse error */ }
        setErrors((prev) => ({ ...prev, [id]: msg }));
        return;
      }
      // Clear any previous error on success
      setErrors((prev) => ({ ...prev, [id]: undefined }));
      const data: PoisResponse = await res.json();
      const features = data.features || [];

      if (!cache.current[id]) cache.current[id] = {};
      cache.current[id][key] = features;
      lastBbox.current[id] = key;

      lsWrite(id, key, features);
      // Guard: layer may have been disabled while the request was in flight
      if (enabledRef.current[id]) renderLayer(id, features);
    } catch (err) {
      // Distinguish three abort/error cases:
      //   1. Caller-aborted (user toggled the layer off / unmounted) — our
      //      `controller` was aborted explicitly.  Swallow silently.
      //   2. Timeout-aborted (apiFetch's internal 90s timer fired) — surface
      //      a useful error so the panel doesn't show stale "zoom in to load".
      //   3. Network/other — same: surface so the user sees what's wrong.
      const isAbort = (err as { name?: string })?.name === 'AbortError';
      if (isAbort && controller.signal.aborted) {
        // (1) — silent
      } else if (isAbort) {
        setErrors((prev) => ({ ...prev, [id]: 'Layer request timed out — try zooming in or panning to refresh' }));
      } else {
        setErrors((prev) => ({ ...prev, [id]: (err as Error)?.message || 'Network error' }));
      }
    }
    finally {
      fetchingRef.current.delete(fetchKey);
      if (showSpinner) setLoading((prev) => ({ ...prev, [id]: false }));
    }
  }, [leafletMap, renderLayer]);

  // ── toggle ─────────────────────────────────────────────────────────────────

  const toggle = useCallback((id: string) => {
    // Read current value from ref (always current after render) to know direction
    const willEnable = !enabledRef.current[id];
    setEnabled((prev) => ({ ...prev, [id]: !prev[id] }));
    if (willEnable) {
      // Clear any stale error so the panel shows the spinner, not the old error
      setErrors((prev) => ({ ...prev, [id]: undefined }));
      // Fetch+render on next tick so enabledRef is updated before fetchAndRender reads it
      setTimeout(() => fetchAndRender(id), 0);
    } else {
      clearLayer(id);
      setErrors((prev) => ({ ...prev, [id]: undefined }));
      setLoading((prev) => ({ ...prev, [id]: false }));
      // Cancel any in-flight fetches for this layer so a slow response can't
      // pollute caches or re-render markers after the user disabled the layer.
      for (const [k, ctrl] of fetchingRef.current.entries()) {
        if (k.startsWith(`${id}::`)) {
          ctrl.abort();
          fetchingRef.current.delete(k);
        }
      }
      // Reset brand chip selection so re-enabling the layer doesn't carry over
      // a chip selection from a previous viewport (which would silently hide
      // markers and confuse the user).
      setBrandFilters((prev) => prev[id]?.size ? { ...prev, [id]: new Set<string>() } : prev);
      setPresentBrands((prev) => prev[id]?.size ? { ...prev, [id]: new Set<string>() } : prev);
    }
  }, [fetchAndRender, clearLayer]);

  // ── brand filter toggle ────────────────────────────────────────────────────

  const toggleBrand = useCallback((layerId: string, value: string) => {
    setBrandFilters((prev) => {
      const next = new Set(prev[layerId]);
      if (next.has(value)) next.delete(value); else next.add(value);
      return { ...prev, [layerId]: next };
    });
  }, []);

  // Re-render the layer whenever its brand filters change so markers update instantly
  // without a new network call — the full feature list is already in cache.
  useEffect(() => {
    layersRef.current.forEach((l) => {
      if (!enabledRef.current[l.id]) return;
      const map = leafletMap.current;
      if (!map) return;
      const key = bboxKey(map);
      const features = cache.current[l.id]?.[key];
      if (features) renderLayer(l.id, features);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brandFilters, renderLayer]);

  // ── initial load for defaultOn layers ─────────────────────────────────
  //
  // Stagger initial fetches so we don't hit Overpass with N concurrent
  // requests — the public mirrors block clients that issue ≥3 parallel
  // queries.  Two-deep concurrency keeps the UI snappy without tripping limits.
  //
  // Tracks which layer ids have already been "kicked off" so the effect
  // remains idempotent across customLayers refreshes — fetching the same
  // built-in layer twice on mount would race the AbortController dedup logic
  // and surface as "Failed to load layer" in the UI.
  const initialLoadedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!isReady) return;
    const queue = layersRef.current
      .filter((l) => l.defaultOn && !initialLoadedRef.current.has(l.id))
      .map((l) => l.id);
    queue.forEach((id) => initialLoadedRef.current.add(id));
    if (queue.length === 0) return;
    let active = 0;
    const runNext = () => {
      while (active < 2 && queue.length) {
        const id = queue.shift()!;
        active++;
        fetchAndRender(id).finally(() => { active--; runNext(); });
      }
    };
    runNext();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReady, customLayers]);

  // ── re-fetch on map pan/zoom ───────────────────────────────────────────────
  // Use a ref for enabled so the handler always sees current state without
  // needing to re-register on every toggle.
  // (enabledRef is declared at the top of the hook, before all callbacks.)

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    const map = leafletMap.current;

    // Debounce moveend: the user can cross several 1° grid cells while panning,
    // each firing moveend.  Wait 400ms after the last movement before fetching
    // so we don't hit Overpass with a cascade of requests.
    let moveTimer: ReturnType<typeof setTimeout> | null = null;
    const onMoveEnd = () => {
      if (moveTimer) clearTimeout(moveTimer);
      moveTimer = setTimeout(() => {
        moveTimer = null;
        const key = bboxKey(map);
        layersRef.current.forEach((l) => {
          if (enabledRef.current[l.id] && lastBbox.current[l.id] !== key) {
            fetchAndRender(l.id);
          }
        });
      }, 400);
    };

    map.on('moveend', onMoveEnd);

    // Re-render on zoom to update icon sizes (no network request — uses cached features)
    const onZoomEnd = () => {
      layersRef.current.forEach((l) => {
        if (!enabledRef.current[l.id]) return;
        const lastKey = lastBbox.current[l.id];
        if (!lastKey) return;
        const cached = cache.current[l.id]?.[lastKey];
        if (cached) renderLayer(l.id, cached);
      });
    };
    map.on('zoomend', onZoomEnd);

    return () => {
      if (moveTimer) clearTimeout(moveTimer);
      map.off('moveend', onMoveEnd);
      map.off('zoomend', onZoomEnd);
    };
  // fetchAndRender is stable (useCallback with no deps that change);
  // enabledRef is a ref so we intentionally exclude it from deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReady, leafletMap, fetchAndRender]);

  // ── cleanup on unmount ────────────────────────────────────────────────────

  useEffect(() => {
    const fetching = fetchingRef.current;
    const groups = layerGroups.current;
    return () => {
      // Abort any in-flight POI fetches so they don't write to caches after
      // the component is gone.
      for (const ctrl of fetching.values()) ctrl.abort();
      fetching.clear();
      Object.values(groups).forEach((g) => g.remove());
      layerGroups.current = {};
      cache.current = {};
    };
  }, []);

  return {
    enabled, toggle, loading, errors, counts,
    brandFilters, toggleBrand, presentBrands, allFeatures,
    effectiveLayers, refreshCustomLayers,
  };
}
