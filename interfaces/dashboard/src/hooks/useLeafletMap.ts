/**
 * useLeafletMap — single source of truth for Leaflet initialisation.
 *
 * Handles:
 *  - CSS + JS injection (once per page load, idempotent)
 *  - L.map() creation and cleanup on unmount
 *  - invalidateSize() when container dimensions change
 *
 * Usage:
 *   const { mapRef, leafletMap, isReady } = useLeafletMap({ center, zoom });
 *   // add layers to leafletMap.current once isReady === true
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type L from 'leaflet';

const LEAFLET_VERSION  = '1.9.4';
const CLUSTER_VERSION  = '1.5.3';
const HEAT_VERSION     = '0.2.0';
const LEAFLET_CSS_ID   = 'leaflet-css';
const CLUSTER_CSS_ID   = 'leaflet-cluster-css';
const CLUSTER_CSS2_ID  = 'leaflet-cluster-default-css';

/** Available base map tile types. */
export type MapType = 'standard' | 'satellite' | 'terrain';

/**
 * Whose tiles sit under the overlays.  'osm' is the free set below
 * (OpenStreetMap, Esri, OpenTopoMap); 'google' is Google's basemap
 * through the Map Tiles API — the product Google sells for exactly
 * this, tiles inside a third-party renderer.  Everything drawn ON the
 * map is untouched either way; only the base layer differs.  Whether an
 * account is on Google is the server's decision (features/live-map/
 * engine/useMapEngine); this hook only knows how to draw it.
 */
export type MapProvider = 'osm' | 'google';

/** One Google session, as the server hands it out.  Declared here rather
 *  than imported so the hook stays free of the feature that owns the
 *  engine — the feature depends on the hook, not the other way. */
export interface GoogleTileSession {
  tile_url: string;
  viewport_url: string;
  tile_size: number;
  max_zoom: number;
}
export type GoogleSessionFetcher = (type: 'roadmap' | 'satellite' | 'terrain') => Promise<GoogleTileSession>;
const GOOGLE_TYPE: Record<MapType, 'roadmap' | 'satellite' | 'terrain'> = {
  standard: 'roadmap', satellite: 'satellite', terrain: 'terrain',
};

interface TileCfg { url: string; attr: string; maxZoom: number; }

/** Base tile layer configurations — all free, no API key required. */
const TILES: Record<MapType, TileCfg> = {
  standard: {
    url:     'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attr:    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  },
  satellite: {
    // ESRI World Imagery — free, no API key
    url:     'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attr:    'Tiles &copy; Esri &mdash; Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, GIS Users',
    maxZoom: 19,
  },
  terrain: {
    // OpenTopoMap — free, no API key; shows elevation contours useful for grade/mountain-pass awareness
    url:     'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attr:    'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | &copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
    maxZoom: 17,
  },
};

/**
 * ESRI Reference/Boundaries overlay — road names + city labels on top of satellite/terrain.
 * Free, no API key.
 */
const LABELS_CFG: TileCfg = {
  url:     'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  attr:    'Labels &copy; Esri',
  maxZoom: 19,
};

export interface UseLeafletMapOptions {
  /** Initial map centre. Defaults to geographic centre of the contiguous US. */
  center?: [number, number];
  /** Initial zoom level. Defaults to 5 (shows whole CONUS). */
  zoom?: number;
}

export interface UseLeafletMapResult {
  /** Attach this ref to the map container div. */
  mapRef: React.RefObject<HTMLDivElement>;
  /** The Leaflet Map instance — null until the map is ready. */
  leafletMap: React.RefObject<L.Map | null>;
  /** True once Leaflet + markercluster have loaded and the map is mounted. */
  isReady: boolean;
  /** Force a tile-size recalculation (call after layout shifts). */
  invalidateSize: () => void;
  /** Currently active base map tile type. */
  mapType: MapType;
  /** Whether the road-labels overlay is shown (meaningful for satellite/terrain). */
  showLabels: boolean;
  /** Switch the base tile layer. Safe to call before the map is ready — queued. */
  setMapType: (type: MapType) => void;
  /** Toggle road-name labels overlay. No-op for standard (labels already in tiles). */
  setShowLabels: (show: boolean) => void;
  /** Whose tiles are under the overlays right now. */
  provider: MapProvider;
  /**
   * Switch the base tiles to Google (with a way to get sessions and a
   * way to report that Google is refusing) or back to the free set.
   * Re-applies the current map type.  Safe before the map is ready.
   */
  setProvider: (p: MapProvider, opts?: { session?: GoogleSessionFetcher; onFail?: (reason: string) => void }) => void;
}

/** Google's policy asks for its name beside its copyright line, kept
 *  apart from the renderer's own attribution.  A Leaflet control in the
 *  opposite corner from Leaflet's is that separation. */
const GOOGLE_LABEL = 'Google Maps';
const GOOGLE_FALLBACK_AFTER_ERRORS = 4;

function ensureLeafletCSS(): void {
  if (document.getElementById(LEAFLET_CSS_ID)) return;
  const link = document.createElement('link');
  link.id   = LEAFLET_CSS_ID;
  link.rel  = 'stylesheet';
  link.href = `https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist/leaflet.css`;
  document.head.appendChild(link);
}

function ensureClusterCSS(): void {
  if (!document.getElementById(CLUSTER_CSS_ID)) {
    const l1 = document.createElement('link');
    l1.id   = CLUSTER_CSS_ID;
    l1.rel  = 'stylesheet';
    l1.href = `https://unpkg.com/leaflet.markercluster@${CLUSTER_VERSION}/dist/MarkerCluster.css`;
    document.head.appendChild(l1);
  }
  if (!document.getElementById(CLUSTER_CSS2_ID)) {
    const l2 = document.createElement('link');
    l2.id   = CLUSTER_CSS2_ID;
    l2.rel  = 'stylesheet';
    l2.href = `https://unpkg.com/leaflet.markercluster@${CLUSTER_VERSION}/dist/MarkerCluster.Default.css`;
    document.head.appendChild(l2);
  }
}

function loadLeafletJS(): Promise<typeof L> {
  return new Promise((resolve) => {
    if (window.L) return resolve(window.L);
    const existing = document.getElementById('leaflet-js');
    if (existing) {
      existing.addEventListener('load', () => resolve(window.L));
      return;
    }
    const s = document.createElement('script');
    s.id  = 'leaflet-js';
    s.src = `https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist/leaflet.js`;
    s.onload = () => resolve(window.L);
    document.head.appendChild(s);
  });
}

function loadClusterJS(): Promise<void> {
  return new Promise((resolve) => {
    // Already loaded if L.markerClusterGroup is present
    if ((window.L as unknown as { markerClusterGroup?: unknown })?.markerClusterGroup) {
      return resolve();
    }
    const existing = document.getElementById('leaflet-cluster-js');
    if (existing) {
      existing.addEventListener('load', () => resolve());
      return;
    }
    const s = document.createElement('script');
    s.id  = 'leaflet-cluster-js';
    s.src = `https://unpkg.com/leaflet.markercluster@${CLUSTER_VERSION}/dist/leaflet.markercluster.js`;
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
}

/**
 * Load leaflet.heat from CDN after window.L is set.
 * Bundling it via `import` causes its UMD wrapper to emit a bare `L`
 * global reference before window.L exists, throwing ReferenceError.
 */
function loadHeatJS(): Promise<void> {
  return new Promise((resolve) => {
    if ((window.L as unknown as { heatLayer?: unknown })?.heatLayer) {
      return resolve();
    }
    const existing = document.getElementById('leaflet-heat-js');
    if (existing) {
      existing.addEventListener('load', () => resolve());
      return;
    }
    const s = document.createElement('script');
    s.id  = 'leaflet-heat-js';
    s.src = `https://unpkg.com/leaflet.heat@${HEAT_VERSION}/dist/leaflet-heat.js`;
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
}

export function useLeafletMap(options: UseLeafletMapOptions = {}): UseLeafletMapResult {
  const { center = [39.8, -98.5], zoom = 5 } = options;

  const mapRef     = useRef<HTMLDivElement>(null);
  const leafletMap = useRef<L.Map | null>(null);
  const [isReady, setIsReady] = useState(false);

  // ── Map-type state ─────────────────────────────────────────────────────────
  const [mapType, setMapTypeState]     = useState<MapType>('standard');
  const [showLabels, setShowLabelsState] = useState(false);
  // Refs mirror state so callbacks never capture stale values.
  const mapTypeRef    = useRef<MapType>('standard');
  const showLabelsRef = useRef(false);
  const tileLayerRef  = useRef<L.TileLayer | null>(null);
  const labelsLayerRef = useRef<L.TileLayer | null>(null);
  // ── Provider state ────────────────────────────────────────────────────────
  const [provider, setProviderState] = useState<MapProvider>('osm');
  const providerRef = useRef<MapProvider>('osm');
  const sessionRef  = useRef<GoogleSessionFetcher | null>(null);
  const onFailRef   = useRef<((reason: string) => void) | null>(null);
  const googleCtrlRef = useRef<L.Control | null>(null);
  const viewportUrlRef = useRef<string>('');
  // A base-layer swap is async under Google (a session is fetched); a
  // newer swap must win over an older one that resolves late.
  const swapSeq = useRef(0);

  const invalidateSize = useCallback(() => {
    leafletMap.current?.invalidateSize();
  }, []);

  /** Labels overlay for satellite/terrain, on whichever base is under it. */
  const applyLabels = (map: L.Map, type: MapType) => {
    const Leaf = window.L as typeof L;
    labelsLayerRef.current?.remove();
    labelsLayerRef.current = null;
    // Standard tiles carry their own labels — OSM's, and Google's roadmap.
    if (showLabelsRef.current && type !== 'standard') {
      labelsLayerRef.current = Leaf.tileLayer(LABELS_CFG.url, {
        attribution: LABELS_CFG.attr,
        maxZoom: LABELS_CFG.maxZoom,
        // pane ensures labels render above base tile but below markers
        pane: 'shadowPane',
      }).addTo(map);
    }
  };

  /** Google's name and copyright, in the corner opposite Leaflet's own
   *  attribution so the two never overlap — what the policy asks. */
  const ensureGoogleControl = (map: L.Map) => {
    if (googleCtrlRef.current) return;
    const Leaf = window.L as typeof L;
    const Ctl = Leaf.Control.extend({
      onAdd() {
        const el = Leaf.DomUtil.create('div', 'leaflet-control-attribution leaflet-google-attribution');
        el.setAttribute('aria-label', 'Google Maps');
        el.innerHTML = `<strong>${GOOGLE_LABEL}</strong> <span data-copyright></span>`;
        return el;
      },
    });
    const ctl = new Ctl({ position: 'bottomleft' });
    ctl.addTo(map);
    googleCtrlRef.current = ctl;
  };
  const removeGoogleControl = () => {
    googleCtrlRef.current?.remove();
    googleCtrlRef.current = null;
    viewportUrlRef.current = '';
  };
  /** Google's copyright line for THIS view, from its viewport service.
   *  Free of quota, debounced by moveend.  Never blanks the line: a
   *  failed lookup keeps the last words rather than showing none. */
  const refreshCopyright = async (map: L.Map) => {
    const url = viewportUrlRef.current;
    const el = googleCtrlRef.current?.getContainer()?.querySelector('[data-copyright]');
    if (!url || !el) return;
    const b = map.getBounds();
    const clamp = (v: number) => Math.max(-85, Math.min(85, v));
    const q = new URLSearchParams({
      zoom: String(Math.max(0, Math.min(22, Math.round(map.getZoom())))),
      north: String(clamp(b.getNorth())), south: String(clamp(b.getSouth())),
      east: String(b.getEast()), west: String(b.getWest()),
    });
    try {
      const r = await fetch(`${url}&${q.toString()}`);
      if (!r.ok) return;
      const j = (await r.json()) as { copyright?: string };
      if (j.copyright && viewportUrlRef.current === url) el.textContent = j.copyright;
    } catch {
      /* keep the previous line */
    }
  };

  /** Replace the base tile layer for the current provider; re-apply labels. */
  const applyBase = async (type: MapType) => {
    const map = leafletMap.current;
    if (!map) return;
    const Leaf = window.L as typeof L;
    const seq = ++swapSeq.current;
    const useGoogle = providerRef.current === 'google' && !!sessionRef.current;

    let layer: L.TileLayer | null = null;
    if (useGoogle) {
      try {
        const sess = await sessionRef.current!(GOOGLE_TYPE[type]);
        if (seq !== swapSeq.current || !leafletMap.current) return;     // superseded
        viewportUrlRef.current = sess.viewport_url;
        layer = Leaf.tileLayer(sess.tile_url, {
          maxZoom: sess.max_zoom || 22,
          tileSize: sess.tile_size || 256,
          attribution: '',           // Google's line is its own control
        });
        // A spent daily quota answers every tile with an error.  Count
        // them per view; several failures that also outnumber loads
        // means Google is refusing, and the map drops to the free set
        // rather than staying blank.
        let errors = 0, loads = 0;
        layer.on('load', () => { loads++; });
        layer.on('tileload', () => { loads++; });
        layer.on('tileerror', () => {
          errors++;
          if (errors >= GOOGLE_FALLBACK_AFTER_ERRORS && errors >= loads) {
            onFailRef.current?.('Google tiles are not loading — daily quota spent, or the key refused.');
          }
        });
        map.on('moveend', () => { void refreshCopyright(map); });
        ensureGoogleControl(map);
      } catch (e) {
        if (seq !== swapSeq.current) return;
        onFailRef.current?.(e instanceof Error ? e.message : 'Google tiles unavailable');
        return;                        // the caller switches us back to osm
      }
    } else {
      const cfg = TILES[type];
      layer = Leaf.tileLayer(cfg.url, { attribution: cfg.attr, maxZoom: cfg.maxZoom });
      removeGoogleControl();
    }
    tileLayerRef.current?.remove();
    tileLayerRef.current = layer.addTo(map);
    applyLabels(map, type);
    if (useGoogle) void refreshCopyright(map);
  };

  /** Replace the base tile layer; re-apply labels if currently enabled. */
  const setMapType = useCallback((type: MapType) => {
    mapTypeRef.current = type;
    void applyBase(type);
    setMapTypeState(type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setProvider = useCallback<UseLeafletMapResult['setProvider']>((p, opts) => {
    if (opts?.session) sessionRef.current = opts.session;
    if (opts?.onFail) onFailRef.current = opts.onFail;
    if (providerRef.current === p && (p === 'osm' || tileLayerRef.current)) {
      setProviderState(p);
      return;
    }
    providerRef.current = p;
    setProviderState(p);
    void applyBase(mapTypeRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Show/hide the ESRI road-labels overlay on top of satellite/terrain tiles. */
  const setShowLabels = useCallback((show: boolean) => {
    showLabelsRef.current = show;
    const map = leafletMap.current;
    if (map) applyLabels(map, mapTypeRef.current);
    setShowLabelsState(show);
  }, []);

  useEffect(() => {
    let cancelled = false;

    ensureLeafletCSS();
    ensureClusterCSS();
    // Load Leaflet first, then plugins that depend on window.L being set
    loadLeafletJS()
      .then(() => loadClusterJS())
      .then(() => loadHeatJS())
      .then(() => {
        if (cancelled || !mapRef.current || leafletMap.current) return;
        const Leaf = window.L as typeof L;
        const map = Leaf.map(mapRef.current).setView(center, zoom);
        const initCfg = TILES['standard'];
        tileLayerRef.current = Leaf.tileLayer(initCfg.url, {
          attribution: initCfg.attr,
          maxZoom: initCfg.maxZoom,
        }).addTo(map);
        leafletMap.current = map;
        setIsReady(true);
        // setProvider('google') may have arrived before the map existed
        // (the engine answer races the Leaflet script); apply it now.
        if (providerRef.current === 'google') void applyBase(mapTypeRef.current);
      });

    return () => {
      cancelled = true;
      if (leafletMap.current) {
        leafletMap.current.remove(); // removes all layers including tile + labels
        leafletMap.current = null;
      }
      tileLayerRef.current  = null;
      labelsLayerRef.current = null;
      googleCtrlRef.current = null;
      setIsReady(false);
    };
    // center/zoom are intentionally excluded — only applied on first mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { mapRef, leafletMap, isReady, invalidateSize, mapType, showLabels, setMapType, setShowLabels, provider, setProvider };
}
