import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet.markercluster';
import { Modal, Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { apiJSON } from '../api/client';
import type { VehicleFeature, GeofenceFeature } from '../types';

const REFRESH_MS = 30_000;

interface SheetVehicle {
  name: string;
  company: string;
  status: string;
  speed_mph: number | null;
  fuel_percent: number | null;
  address: string;
  engine_state: string;
}

interface Props {
  /** True when this tab is currently visible — triggers map resize. */
  active: boolean;
}

function createIcon(status: string) {
  return L.divIcon({
    className: '',
    html: `<div class="truck-marker ${status}">🚛</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -20],
  });
}

function fmt(v: number | null, unit: string) {
  return v != null ? `${Math.round(v)} ${unit}` : '—';
}

function truncate(s: string, max: number) {
  return s.length > max ? s.slice(0, max) + '…' : s;
}

export function MapPage({ active }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const geofenceRef = useRef<L.LayerGroup | null>(null);
  const markersRef = useRef<Record<string, L.Marker>>({});
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [sheet, setSheet] = useState<SheetVehicle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // ── Map initialization (once on mount) ───────────────────────────

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: true,
    }).setView([39.8283, -98.5795], 5);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map);

    const cluster = (L as unknown as { markerClusterGroup: (opts?: unknown) => L.MarkerClusterGroup }).markerClusterGroup({
      maxClusterRadius: 50,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      disableClusteringAtZoom: 14,
    });
    map.addLayer(cluster);

    const geofences = L.layerGroup().addTo(map);

    mapRef.current = map;
    clusterRef.current = cluster;
    geofenceRef.current = geofences;

    // Initial load
    Promise.all([loadVehicles(map, cluster), loadGeofences(geofences)])
      .finally(() => setLoading(false));

    // 30-second refresh
    timerRef.current = setInterval(() => loadVehicles(map, cluster), REFRESH_MS);

    setTimeout(() => map.invalidateSize(), 200);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
      geofenceRef.current = null;
      markersRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Resize when tab becomes visible ──────────────────────────────

  useEffect(() => {
    if (active && mapRef.current) {
      setTimeout(() => mapRef.current?.invalidateSize(), 100);
    }
  }, [active]);

  // ── Data loaders ─────────────────────────────────────────────────

  async function loadVehicles(map: L.Map, cluster: L.MarkerClusterGroup) {
    try {
      const data = await apiJSON<{ features: VehicleFeature[] }>('/api/map/vehicles');
      const features = data.features ?? [];
      const activeIds = new Set<string>();

      features.forEach(f => {
        const { id, ...props } = f.properties;
        const [lng, lat] = f.geometry.coordinates;
        activeIds.add(id);

        if (markersRef.current[id]) {
          markersRef.current[id].setLatLng([lat, lng]);
          markersRef.current[id].setIcon(createIcon(props.status));
        } else {
          const marker = L.marker([lat, lng], { icon: createIcon(props.status) });
          marker.on('click', () => setSheet({ ...props, name: props.name }));
          markersRef.current[id] = marker;
          cluster.addLayer(marker);
        }
      });

      // Remove stale markers
      Object.keys(markersRef.current).forEach(id => {
        if (!activeIds.has(id)) {
          cluster.removeLayer(markersRef.current[id]);
          delete markersRef.current[id];
        }
      });

      // Fit bounds on first load
      if (features.length > 0) {
        const bounds = cluster.getBounds();
        if (bounds.isValid()) {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
        }
      }
      setError(false);
    } catch (e) {
      console.error('Failed to load vehicles:', e);
      setError(true);
    }
  }

  async function loadGeofences(layer: L.LayerGroup) {
    try {
      const data = await apiJSON<{ features: GeofenceFeature[] }>('/api/map/geofences');
      layer.clearLayers();

      const style = { color: '#5eaaf0', weight: 2, opacity: 0.6, fillOpacity: 0.1 };

      (data.features ?? []).forEach(f => {
        if (f.geometry.type === 'Polygon') {
          const coords = f.geometry.coordinates[0].map(c => [c[1], c[0]] as [number, number]);
          L.polygon(coords, style).bindTooltip(f.properties.name).addTo(layer);
        } else if (f.geometry.type === 'Point' && f.properties.type === 'circle') {
          L.circle(
            [f.geometry.coordinates[1], f.geometry.coordinates[0]],
            { ...style, radius: f.properties.radius_meters ?? 500 },
          ).bindTooltip(f.properties.name).addTo(layer);
        }
      });
    } catch (e) {
      console.error('Failed to load geofences:', e);
    }
  }

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div className="map-page">
      {/* Leaflet map container — always in DOM */}
      <div id="map-container" ref={containerRef} />

      {/* Loading overlay */}
      {loading && (
        <div className="centered" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <Spinner size="l" />
        </div>
      )}

      {/* Error overlay */}
      {!loading && error && (
        <div className="centered" style={{ position: 'absolute', inset: 0 }}>
          <Placeholder header="No Connection" description="Could not load fleet data. Check your network and try again.">
            📡
          </Placeholder>
        </div>
      )}

      {/* Truck detail bottom sheet */}
      {sheet && (
        <Modal
          open
          onOpenChange={open => { if (!open) setSheet(null); }}
          header={<Modal.Header />}
        >
          <div style={{ padding: '0 16px 24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ fontSize: 17, fontWeight: 600 }}>🚛 {sheet.name}</span>
              <span className={`status-badge ${sheet.status}`}>{sheet.status}</span>
            </div>
            {[
              ['🏢 Company',   sheet.company || '—'],
              ['⚡ Speed',     fmt(sheet.speed_mph, 'mph')],
              ['⛽ Fuel',      sheet.fuel_percent != null ? `${sheet.fuel_percent}%` : '—'],
              ['🔑 Engine',    sheet.engine_state],
              ['📍 Location',  truncate(sheet.address || 'Unknown', 60)],
            ].map(([label, value]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid rgba(128,128,128,0.15)' }}>
                <span style={{ color: 'var(--tgui--hint_color, #8e8e93)', fontSize: 14 }}>{label}</span>
                <span style={{ fontSize: 14, textAlign: 'right', maxWidth: '60%' }}>{value}</span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
