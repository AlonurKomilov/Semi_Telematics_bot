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
const LEAFLET_CSS_ID   = 'leaflet-css';
const CLUSTER_CSS_ID   = 'leaflet-cluster-css';
const CLUSTER_CSS2_ID  = 'leaflet-cluster-default-css';

/** Available base map tile types. */
export type MapType = 'standard' | 'satellite' | 'terrain';

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
}

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

  const invalidateSize = useCallback(() => {
    leafletMap.current?.invalidateSize();
  }, []);

  /** Replace the base tile layer; re-apply labels if currently enabled. */
  const setMapType = useCallback((type: MapType) => {
    mapTypeRef.current = type;
    const map = leafletMap.current;
    if (map) {
      const Leaf = window.L as typeof L;
      tileLayerRef.current?.remove();
      labelsLayerRef.current?.remove();
      labelsLayerRef.current = null;
      const cfg = TILES[type];
      tileLayerRef.current = Leaf.tileLayer(cfg.url, {
        attribution: cfg.attr,
        maxZoom: cfg.maxZoom,
      }).addTo(map);
      // Re-apply labels if they were on and the new tile type isn't standard
      // (standard OSM tiles already include road labels)
      if (showLabelsRef.current && type !== 'standard') {
        labelsLayerRef.current = Leaf.tileLayer(LABELS_CFG.url, {
          attribution: LABELS_CFG.attr,
          maxZoom: LABELS_CFG.maxZoom,
          // pane ensures labels render above base tile but below markers
          pane: 'shadowPane',
        }).addTo(map);
      }
    }
    setMapTypeState(type);
  }, []);

  /** Show/hide the ESRI road-labels overlay on top of satellite/terrain tiles. */
  const setShowLabels = useCallback((show: boolean) => {
    showLabelsRef.current = show;
    const map = leafletMap.current;
    if (map) {
      const Leaf = window.L as typeof L;
      labelsLayerRef.current?.remove();
      labelsLayerRef.current = null;
      if (show && mapTypeRef.current !== 'standard') {
        labelsLayerRef.current = Leaf.tileLayer(LABELS_CFG.url, {
          attribution: LABELS_CFG.attr,
          maxZoom: LABELS_CFG.maxZoom,
          pane: 'shadowPane',
        }).addTo(map);
      }
    }
    setShowLabelsState(show);
  }, []);

  useEffect(() => {
    let cancelled = false;

    ensureLeafletCSS();
    ensureClusterCSS();
    // Load Leaflet first, then the cluster plugin (which depends on window.L)
    loadLeafletJS()
      .then(() => loadClusterJS())
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
      });

    return () => {
      cancelled = true;
      if (leafletMap.current) {
        leafletMap.current.remove(); // removes all layers including tile + labels
        leafletMap.current = null;
      }
      tileLayerRef.current  = null;
      labelsLayerRef.current = null;
      setIsReady(false);
    };
    // center/zoom are intentionally excluded — only applied on first mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { mapRef, leafletMap, isReady, invalidateSize, mapType, showLabels, setMapType, setShowLabels };
}
