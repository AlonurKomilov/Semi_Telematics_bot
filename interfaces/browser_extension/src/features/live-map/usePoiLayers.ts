/**
 * The panel's Map Layers: fetch, cache, and draw the overlays the
 * dashboard draws — fuel, DEF, parking, showers, weigh stations, rest
 * areas, the repair-shop directory, and whatever custom layers the
 * account has made.
 *
 * This is the dashboard's `hooks/usePoiLayers.ts` with two deliberate
 * differences, both forced by where it runs:
 *
 *  1. NO CLUSTERING.  The dashboard loads the markercluster plugin;
 *     the panel bundles no plugin and is 320px wide, where a cluster
 *     bubble would cover a city.  It draws the ones nearest the centre
 *     up to `MARKER_BUDGET` and SAYS SO in the layer's row — see
 *     `pois.ts`.  A cap the reader cannot see is a lie about the map.
 *  2. THE LAYERS ARE REMEMBERED.  The side panel is closed and reopened
 *     all day, and switching Truck parking back on every time is how a
 *     feature stops being used.
 *
 * Everything else is shared on purpose: the same ids (they are the
 * API's `type`), the same 1° grid (so the two surfaces fill each
 * other's cache rather than asking twice), the same colours.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type L from 'leaflet';

import { apiFetch, apiJSON } from '../../api/client';
import { POI_LAYERS_KEY, getWords, setWords } from '../../prefs';
import {
  POI_LAYERS, customLayerDef, esc, glyphSvg, osmPopup,
  type CustomLayerDto, type PoiLayerDef,
} from './poiLayers';
import {
  LS_FRESH_MS, MARKER_BUDGET, bboxCovers, bboxKey, bboxParam, brandMatch,
  filterToView, lsFindCovering, lsRead, lsWrite, nearestFirst,
  type PoiFeature, type ViewBox,
} from './pois';

/** An Overpass query over a state-sized box legitimately runs past the
 *  client's ordinary thirty seconds.  The dashboard waits ninety; so
 *  does the panel, or the same query fails here and succeeds there. */
const POI_TIMEOUT_MS = 90_000;

/** Long enough that crossing three grid cells while dragging is one
 *  request, short enough that letting go feels like it did something. */
const MOVE_DEBOUNCE_MS = 400;

const HALO = '#fff', SHADOW = 'rgba(0,0,0,.45)';

export interface LayerCount {
  /** Markers actually on the map. */
  shown: number;
  /** Markers the layer has here.  Differs from `shown` only when the
   *  budget bit, and then the row has to say both numbers. */
  total: number;
  /** What came back BEFORE the brand chips narrowed it.  The difference
   *  between "there is nothing here" and "there is nothing here of the
   *  chain you picked" — two empties a row must not say the same way,
   *  because only one of them is one press from being fixed. */
  fetched: number;
}

export interface PoiLayersState {
  layers: PoiLayerDef[];
  enabled: Record<string, boolean>;
  loading: Record<string, boolean>;
  errors: Record<string, string | undefined>;
  counts: Record<string, LayerCount>;
  /** A layer that is ON and simply cannot draw HERE yet — the view is
   *  wider than the server will answer for.  Deliberately not an error:
   *  nothing is broken, and six red rows on a panel that reopened at
   *  country zoom would say otherwise. */
  notes: Record<string, string | undefined>;
  /** Which of a layer's brand chips have anything to match in this view. */
  present: Record<string, Set<string>>;
  /** Which of them the person has pressed. */
  brands: Record<string, Set<string>>;
  toggle: (id: string) => void;
  toggleBrand: (layerId: string, value: string) => void;
  /** How many layers are on — the collapsed control's whole summary. */
  activeCount: number;
}

function viewOf(map: L.Map): ViewBox {
  const b = map.getBounds();
  return { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() };
}

/** Marker diameter by zoom — the dashboard's ladder.  At country zoom a
 *  26px disc is a blob; at street zoom a 12px one cannot be tapped. */
function markerSize(zoom: number): number {
  return zoom >= 13 ? 26 : zoom >= 10 ? 22 : zoom >= 7 ? 16 : 12;
}

function markerHtml(def: PoiLayerDef, size: number, hasDef: boolean): string {
  const inner = glyphSvg(def.glyph, Math.round(size * 0.55))
    // A custom layer's mark is an emoji the account chose — a character,
    // not one of ours, so it is escaped and drawn as text.
    || `<span style="font-size:${Math.round(size * 0.55)}px;line-height:1">${esc(def.glyph)}</span>`;
  // The DEF badge is on the FUEL layer's markers, because that is where
  // the question is asked: "does this stop I can see also have DEF".
  const badge = hasDef
    ? `<span style="position:absolute;bottom:-3px;right:-5px;background:#0d9488;color:#fff;`
      + `font-size:6px;padding:1px 3px;border-radius:2px;font-weight:700;line-height:1.2;`
      + `border:1px solid ${HALO}">DEF</span>`
    : '';
  return `<div style="position:relative;width:${size}px;height:${size}px;border-radius:50%;`
    + `background:${def.color};border:2px solid ${HALO};box-shadow:0 1px 4px ${SHADOW};`
    + `display:flex;align-items:center;justify-content:center">${inner}${badge}</div>`;
}

export function usePoiLayers(
  mapRef: React.MutableRefObject<L.Map | null>,
  Leaf: typeof L,
  ready: boolean,
): PoiLayersState {
  const [custom, setCustom] = useState<PoiLayerDef[]>([]);
  // Memoised, and not for speed: this array is a dependency of the
  // restore effect, and a fresh one every render would re-run it.
  const layers: PoiLayerDef[] = useMemo(() => [...POI_LAYERS, ...custom], [custom]);

  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors,  setErrors]  = useState<Record<string, string | undefined>>({});
  const [counts,  setCounts]  = useState<Record<string, LayerCount>>({});
  const [notes,   setNotes]   = useState<Record<string, string | undefined>>({});
  const [present, setPresent] = useState<Record<string, Set<string>>>({});
  const [brands,  setBrands]  = useState<Record<string, Set<string>>>({});

  // Refs shadow the state the Leaflet handlers read: those handlers are
  // registered once and would otherwise close over the first render's
  // values forever.
  const layersRef  = useRef(layers);   layersRef.current  = layers;
  const enabledRef = useRef(enabled);  enabledRef.current = enabled;
  const brandsRef  = useRef(brands);   brandsRef.current  = brands;

  const groups   = useRef<Record<string, L.LayerGroup>>({});
  /** layer id → bbox key → the features that box answered with. */
  const mem      = useRef<Record<string, Record<string, PoiFeature[]>>>({});
  const lastKey  = useRef<Record<string, string>>({});
  const inFlight = useRef<Map<string, AbortController>>(new Map());

  // ── draw ───────────────────────────────────────────────────────────────

  const render = useCallback((id: string, features: PoiFeature[]) => {
    const map = mapRef.current;
    if (!map) return;
    const def = layersRef.current.find((l) => l.id === id);
    if (!def) return;

    // Which brand chips this view could offer.  Written even when empty,
    // so panning somewhere Pilot has no presence REMOVES the chip rather
    // than leaving a button that filters to nothing.
    if (def.brands?.length) {
      const here = new Set<string>();
      for (const f of features) {
        for (const b of def.brands) {
          if (here.has(b.value)) continue;
          if (brandMatch(b.matchTerms ?? [b.value], f)) here.add(b.value);
        }
      }
      setPresent((prev) => ({ ...prev, [id]: here }));
    }

    // The chips narrow what is DRAWN; they never decide what was
    // fetched, so clearing them is instant.
    const picked = brandsRef.current[id];
    const matching = picked?.size
      ? features.filter((f) => [...picked].some((v) => {
          const b = def.brands?.find((x) => x.value === v);
          return brandMatch(b?.matchTerms ?? [v], f);
        }))
      : features;

    const c = map.getCenter();
    const drawn = nearestFirst(matching, c.lat, c.lng);
    setCounts((prev) => ({
      ...prev,
      [id]: { shown: drawn.length, total: matching.length, fetched: features.length },
    }));

    if (!groups.current[id]) groups.current[id] = Leaf.layerGroup().addTo(map);
    const group = groups.current[id];
    group.clearLayers();

    const size = markerSize(map.getZoom());
    for (const f of drawn) {
      const [lng, lat] = f.geometry.coordinates;
      const hasDef = (f.properties as Record<string, string> | null)?.['fuel:adblue'] === 'yes';
      group.addLayer(
        Leaf.marker([lat, lng], {
          icon: Leaf.divIcon({
            className: '', html: markerHtml(def, size, hasDef),
            iconSize: [size, size], iconAnchor: [size / 2, size / 2],
          }),
          // Under the trucks, always.  A fuel stop must never be the
          // thing a person clicks when they meant the truck beside it.
          zIndexOffset: -500,
          keyboard: false,
        }).bindPopup(def.popup ? def.popup(f, def) : osmPopup(f, def)),
      );
    }
  }, [mapRef, Leaf]);

  // ── fetch ──────────────────────────────────────────────────────────────
  //
  // Cheapest answer first: an exact box we hold, a WIDER box we hold
  // (that is a zoom-in, and it is free), the same two out of storage,
  // and only then the network.

  const fetchAndRender = useCallback(async (id: string) => {
    const map = mapRef.current;
    if (!map) return;
    const view = viewOf(map);
    const key  = bboxKey(view);

    if (mem.current[id]?.[key]) {
      if (enabledRef.current[id]) render(id, mem.current[id][key]);
      lastKey.current[id] = key;
      return;
    }
    for (const [ckey, features] of Object.entries(mem.current[id] ?? {})) {
      if (!bboxCovers(ckey, view)) continue;
      const visible = filterToView(features, view);
      mem.current[id] = { ...mem.current[id], [key]: visible };
      if (enabledRef.current[id]) render(id, visible);
      lastKey.current[id] = key;
      return;
    }

    let stored = lsRead(id, key);
    let fromWider = false;
    if (!stored) {
      const wider = lsFindCovering(id, view);
      if (wider) { stored = { ts: wider.ts, features: filterToView(wider.features, view) }; fromWider = true; }
    }

    // A stored answer is DRAWN first and only then questioned: the
    // person gets their layer immediately, and a silent revalidation
    // replaces it if it was old.
    let spinner = true;
    if (stored) {
      mem.current[id] = { ...mem.current[id], [key]: stored.features };
      lastKey.current[id] = key;
      if (enabledRef.current[id]) render(id, stored.features);
      if (Date.now() - stored.ts < LS_FRESH_MS) return;
      spinner = false;
      // The wider box will be revalidated at its own zoom; re-asking for
      // this slice would fetch the same square twice.
      if (fromWider) return;
    }

    const fetchKey = `${id}::${key}`;
    if (inFlight.current.has(fetchKey)) return;
    const ctrl = new AbortController();
    inFlight.current.set(fetchKey, ctrl);
    if (spinner) setLoading((prev) => ({ ...prev, [id]: true }));
    try {
      const res = await apiFetch(
        `/map/pois?type=${encodeURIComponent(id)}&bbox=${bboxParam(view)}`,
        { signal: ctrl.signal }, POI_TIMEOUT_MS,
      );
      if (!res.ok) {
        let detail = 'Could not load this layer';
        try {
          const body = await res.json() as { detail?: unknown };
          if (typeof body.detail === 'string') detail = body.detail;
        } catch { /* no body to read — the status is the whole answer */ }
        // 422 here means one thing in practice: the view is wider than
        // the server will answer for.  That is a STATE of a working
        // layer, not a fault, and it clears by zooming — so it is said
        // quietly, in the row, and the layer stays on.
        if (res.status === 422) setNotes((prev) => ({ ...prev, [id]: detail }));
        else setErrors((prev) => ({ ...prev, [id]: detail }));
        return;
      }
      const data = await res.json() as { features?: PoiFeature[] };
      const features = data.features ?? [];
      setErrors((prev) => ({ ...prev, [id]: undefined }));
      setNotes((prev) => ({ ...prev, [id]: undefined }));
      mem.current[id] = { ...mem.current[id], [key]: features };
      lastKey.current[id] = key;
      lsWrite(id, key, features);
      // The layer may have been switched off while this was in the air.
      if (enabledRef.current[id]) render(id, features);
    } catch (err) {
      // Three aborts that look alike and mean different things: the
      // person switched the layer off (say nothing), the request ran out
      // of time, or the network failed (say both).
      if (ctrl.signal.aborted && !enabledRef.current[id]) { /* their doing */ }
      else if ((err as { name?: string })?.name === 'AbortError') {
        setErrors((prev) => ({ ...prev, [id]: 'Took too long — zoom in and try again' }));
      } else {
        setErrors((prev) => ({ ...prev, [id]: (err as Error)?.message || 'Could not load this layer' }));
      }
    } finally {
      inFlight.current.delete(fetchKey);
      if (spinner) setLoading((prev) => ({ ...prev, [id]: false }));
    }
  }, [mapRef, render]);

  // ── switches ───────────────────────────────────────────────────────────

  const toggle = useCallback((id: string) => {
    const on = !enabledRef.current[id];
    const next = { ...enabledRef.current, [id]: on };
    enabledRef.current = next;
    setEnabled(next);
    void setWords(POI_LAYERS_KEY, Object.keys(next).filter((k) => next[k]));
    if (on) {
      setErrors((prev) => ({ ...prev, [id]: undefined }));
      setNotes((prev) => ({ ...prev, [id]: undefined }));
      void fetchAndRender(id);
      return;
    }
    groups.current[id]?.clearLayers();
    setErrors((prev) => ({ ...prev, [id]: undefined }));
    setNotes((prev) => ({ ...prev, [id]: undefined }));
    setLoading((prev) => ({ ...prev, [id]: false }));
    for (const [k, ctrl] of inFlight.current.entries()) {
      if (k.startsWith(`${id}::`)) { ctrl.abort(); inFlight.current.delete(k); }
    }
    // Chips are cleared with the layer: a selection carried over from a
    // viewport three states away silently hides everything on the next
    // switch-on, and looks like a broken layer.
    setBrands((prev) => (prev[id]?.size ? { ...prev, [id]: new Set<string>() } : prev));
    setPresent((prev) => (prev[id]?.size ? { ...prev, [id]: new Set<string>() } : prev));
  }, [fetchAndRender]);

  const toggleBrand = useCallback((layerId: string, value: string) => {
    setBrands((prev) => {
      const next = new Set(prev[layerId]);
      if (next.has(value)) next.delete(value); else next.add(value);
      return { ...prev, [layerId]: next };
    });
  }, []);

  // A chip press redraws from what is already in hand — no request.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const key = bboxKey(viewOf(map));
    for (const l of layersRef.current) {
      if (!enabledRef.current[l.id]) continue;
      const features = mem.current[l.id]?.[key];
      if (features) render(l.id, features);
    }
  }, [brands, render, mapRef]);

  // ── what the account has added ─────────────────────────────────────────

  useEffect(() => {
    let stopped = false;
    void apiJSON<{ layers?: CustomLayerDto[] }>('/map/custom-layers')
      .then((d) => { if (!stopped) setCustom((d.layers ?? []).map(customLayerDef)); })
      // A custom-layer read that fails costs the custom layers, not the
      // built-in ones — the map still has everything it shipped with.
      .catch(() => { /* built-ins are enough */ });
    return () => { stopped = true; };
  }, []);

  // ── the layers that were left on ───────────────────────────────────────

  const restored = useRef(false);
  useEffect(() => {
    if (!ready || restored.current || !layers.length) return;
    restored.current = true;
    void getWords(POI_LAYERS_KEY, layers.map((l) => l.id)).then((on) => {
      if (!on.length) return;
      const next: Record<string, boolean> = {};
      for (const id of on) next[id] = true;
      enabledRef.current = next;
      setEnabled(next);
      // Two at a time: the public Overpass mirrors refuse a client that
      // opens three parallel queries, and a restored session can be six
      // layers deep.
      const queue = [...on];
      let active = 0;
      const pump = () => {
        while (active < 2 && queue.length) {
          active++;
          void fetchAndRender(queue.shift()!).finally(() => { active--; pump(); });
        }
      };
      pump();
    });
  }, [ready, layers, fetchAndRender]);

  // ── the map moved ──────────────────────────────────────────────────────

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onMove = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        const key = bboxKey(viewOf(map));
        for (const l of layersRef.current) {
          if (enabledRef.current[l.id] && lastKey.current[l.id] !== key) void fetchAndRender(l.id);
        }
      }, MOVE_DEBOUNCE_MS);
    };
    // Zoom redraws without asking: the discs change size, and which 250
    // are nearest the centre changes with the centre.
    const onZoom = () => {
      const key = bboxKey(viewOf(map));
      for (const l of layersRef.current) {
        if (!enabledRef.current[l.id]) continue;
        const features = mem.current[l.id]?.[key] ?? mem.current[l.id]?.[lastKey.current[l.id]];
        if (features) render(l.id, features);
      }
    };
    map.on('moveend', onMove);
    map.on('zoomend', onZoom);
    return () => {
      if (timer) clearTimeout(timer);
      map.off('moveend', onMove);
      map.off('zoomend', onZoom);
    };
  }, [ready, mapRef, fetchAndRender, render]);

  // ── going away ─────────────────────────────────────────────────────────

  useEffect(() => {
    const flying = inFlight.current, drawn = groups.current;
    return () => {
      for (const ctrl of flying.values()) ctrl.abort();
      flying.clear();
      for (const g of Object.values(drawn)) g.remove();
      groups.current = {};
      mem.current = {};
    };
  }, []);

  return {
    layers, enabled, loading, errors, notes, counts, present, brands,
    toggle, toggleBrand,
    activeCount: Object.values(enabled).filter(Boolean).length,
  };
}

export { MARKER_BUDGET };
