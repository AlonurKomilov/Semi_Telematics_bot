/**
 * MapPage — fleet/driver live map.
 *
 * Layout: Leaflet map full-bleed, translucent header (truck name + last
 * update), floating "fly to my truck" FAB, BottomSheet truck detail when
 * a marker is tapped, optional status card pinned above the tabbar for
 * the single-truck driver flow.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet.markercluster';
import { Placeholder } from '@telegram-apps/telegram-ui';
import {
  Icon24TargetOutline,
  Icon24ReplayOutline,
  Icon24Replay,
  Icon24SpeedometerMiddleOutline,
  Icon24DropsOutline,
  Icon24WaterDropOutline,
  Icon24WarningTriangleOutline,
  Icon24KeyOutline,
  Icon24LocationOutline,
  Icon24RecentOutline,
  Icon24CheckCircleOutline,
  Icon24TruckOutline,
  Icon24LockOutline,
  Icon24GlobeOutline,
} from '@vkontakte/icons';
import type { ComponentType } from 'react';

// Loose icon type — VK Icon components accept a width/height number.
// Don't include `aria-hidden` here because VK uses Booleanish ("true" |
// "false" | boolean), which narrows incompatibly with React.SVGProps;
// JSX spread accepts it fine without the type intersection.
type IconCmp = ComponentType<{ width?: number; height?: number }>;
import { apiJSON, classifyError, type ClassifiedError } from '../api/client';
import type { VehicleFeature, GeofenceFeature } from '../types';
import { BottomSheet } from '../components/BottomSheet';
import { RelativeTime } from '../components/RelativeTime';
import { PTIChip } from '../components/PTIChip';
import { haptics } from '../hooks/useTelegram';
import { useUnitSystem } from '../hooks/useUnitSystem';
import { fmtSpeed } from '../utils/units';

const REFRESH_MS = 30_000;

interface SheetVehicle {
  name: string;
  company: string;
  status: string;
  speed_mph: number | null;
  fuel_percent: number | null;
  def_percent: number | null;
  fault_count: number;
  address: string;
  engine_state: string;
  latitude: number | null;
  longitude: number | null;
  updated_at: string | null;
}

interface Props {
  active: boolean;
  /** Permissions map from /api/user/me — used to gate the PTI chip. */
  userPerms?: Record<string, boolean>;
  /** Route to a different tab.  Passed through to the PTI chip so a
      tap on the chip lands the driver on the PTI page. */
  onNavigate?: (page: 'pti') => void;
}

/**
 * createIcon — status-aware SVG marker.
 *
 * Moving trucks with a heading render a directional arrow circle
 * (rotated to the direction of travel). All other states render a
 * teardrop pin with a truck silhouette.
 *
 * Results are memoized by (status, headingBucket) so a 30s refresh of
 * 50 vehicles doesn't allocate 50 fresh L.divIcon objects when nothing
 * has actually changed.  Heading is bucketed to 15° so a tiny GPS
 * jitter doesn't bust the cache.
 */
const ICON_CACHE = new Map<string, L.DivIcon>();
const HEADING_BUCKET_DEG = 15;

function iconKey(status: string, heading?: number | null): string {
  if (status === 'moving' && heading != null) {
    const bucket = Math.round(heading / HEADING_BUCKET_DEG) * HEADING_BUCKET_DEG;
    return `moving:${((bucket % 360) + 360) % 360}`;
  }
  return status;
}

function createIcon(status: string, heading?: number | null): L.DivIcon {
  const key = iconKey(status, heading);
  const cached = ICON_CACHE.get(key);
  if (cached) return cached;

  const FILL: Record<string, string> = {
    moving:  '#30d158',
    idle:    '#ff9f0a',
    stopped: '#ff453a',
  };
  const fill = FILL[status] ?? '#8e8e93';

  // ── Directional arrow for moving trucks ──────────────────────────
  if (status === 'moving' && heading != null) {
    const bucket = Math.round(heading / HEADING_BUCKET_DEG) * HEADING_BUCKET_DEG;
    const rot = ((bucket % 360) + 360) % 360;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36"
        style="transform:rotate(${rot}deg);transform-origin:50% 50%;display:block">
      <circle cx="18" cy="18" r="15" fill="${fill}" stroke="white" stroke-width="2.5"/>
      <path d="M18 7 L26.5 26 L18 21.5 L9.5 26 Z" fill="white" opacity="0.95"/>
    </svg>`;
    const icon = L.divIcon({
      className: `vehicle-pin ${status}`,
      html: svg,
      iconSize:    [36, 36],
      iconAnchor:  [18, 18],
      popupAnchor: [0, -20],
    });
    ICON_CACHE.set(key, icon);
    return icon;
  }

  // ── Teardrop pin with truck silhouette for idle / stopped ────────
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="38" height="47" viewBox="0 0 38 47" style="display:block">
    <path d="M19 1.5C9.3 1.5 2 9.1 2 18.5C2 28.2 19 45.5 19 45.5C19 45.5 36 28.2 36 18.5C36 9.1 28.7 1.5 19 1.5Z"
          fill="${fill}" stroke="white" stroke-width="2"/>
    <g transform="translate(8,11.5)" fill="white">
      <!-- trailer body -->
      <rect x="0" y="0" width="12" height="8" rx="1.5"/>
      <!-- cab -->
      <path d="M12 8 L12 2.5 Q12.5 0 14.5 0 L19 0 Q21 0 21 2.5 L21 8 Z"/>
      <!-- windshield (tinted) -->
      <path d="M13 2.8 Q13.5 1 15 1 L18.5 1 L20 2.8 Z" fill="${fill}" opacity="0.55"/>
      <!-- wheels -->
      <circle cx="3" cy="10.5" r="2.5"/>
      <circle cx="9.5" cy="10.5" r="2.5"/>
      <circle cx="18" cy="10.5" r="2.5"/>
    </g>
  </svg>`;
  const icon = L.divIcon({
    className: `vehicle-pin ${status}`,
    html: svg,
    iconSize:    [38, 47],
    iconAnchor:  [19, 45],
    popupAnchor: [0, -47],
  });
  ICON_CACHE.set(key, icon);
  return icon;
}

function truncate(s: string, max: number) {
  return s.length > max ? s.slice(0, max) + '…' : s;
}

/**
 * Build a status-themed cluster icon.  Bubble is green when every
 * truck inside is moving, red when all are stopped, blue otherwise.
 */
function makeClusterIcon(cluster: { getAllChildMarkers: () => L.Marker[]; getChildCount: () => number }) {
  const markers = cluster.getAllChildMarkers();
  const statuses = markers.map(m => {
    const cls = (m.options.icon as L.DivIcon | undefined)?.options.className ?? '';
    if (cls.includes('moving')) return 'moving';
    if (cls.includes('stopped')) return 'stopped';
    if (cls.includes('idle')) return 'idle';
    return 'unknown';
  });
  const all = (s: string) => statuses.length > 0 && statuses.every(x => x === s);
  let cls = 'marker-cluster-mixed';
  if (all('moving')) cls = 'marker-cluster-moving';
  else if (all('stopped')) cls = 'marker-cluster-stopped';
  const count = cluster.getChildCount();
  return L.divIcon({
    html: `<div class="vcluster-inner"><span>${count}</span></div>`,
    className: `marker-cluster ${cls}`,
    iconSize: L.point(44, 44, true),
  });
}

export function MapPage({ active, userPerms, onNavigate }: Props) {
  const units = useUnitSystem();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const geofenceRef = useRef<L.LayerGroup | null>(null);
  const markersRef = useRef<Record<string, L.Marker>>({});
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const firstLoadRef = useRef(true);
  const routeLayerRef = useRef<L.Polyline | null>(null);

  const [sheet, setSheet] = useState<SheetVehicle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ClassifiedError | null>(null);
  const [vehicleCount, setVehicleCount] = useState(0);
  const [primary, setPrimary] = useState<SheetVehicle | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [routeActive, setRouteActive] = useState(false);
  const [routeLoading, setRouteLoading] = useState(false);

  // ── Map initialization (once on mount) ───────────────────────────

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      zoomControl: false,         // we add it manually to bottomright
      attributionControl: true,
    }).setView([39.8283, -98.5795], 5);

    // Zoom control bottom-right so it never overlaps the top header or
    // left side. It will sit above the FABs which are further right.
    L.control.zoom({ position: 'topright' }).addTo(map);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map);

    const cluster = (L as unknown as { markerClusterGroup: (opts?: unknown) => L.MarkerClusterGroup }).markerClusterGroup({
      maxClusterRadius: 50,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
      disableClusteringAtZoom: 16,
      iconCreateFunction: makeClusterIcon,
    });
    map.addLayer(cluster);

    const geofences = L.layerGroup().addTo(map);

    mapRef.current = map;
    clusterRef.current = cluster;
    geofenceRef.current = geofences;

    Promise.all([loadVehicles(map, cluster), loadGeofences(geofences)])
      .finally(() => setLoading(false));

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
  }, []);

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

        const sheetData: SheetVehicle = {
          ...props,
          latitude: lat,
          longitude: lng,
          updated_at: props.updated_at ?? null,
        };

        if (markersRef.current[id]) {
          markersRef.current[id].setLatLng([lat, lng]);
          markersRef.current[id].setIcon(createIcon(props.status, props.heading));
        } else {
          const marker = L.marker([lat, lng], { icon: createIcon(props.status, props.heading) });
          marker.on('click', () => {
            haptics.selection();
            setSheet(sheetData);
          });
          markersRef.current[id] = marker;
          cluster.addLayer(marker);
        }
      });

      Object.keys(markersRef.current).forEach(id => {
        if (!activeIds.has(id)) {
          cluster.removeLayer(markersRef.current[id]);
          delete markersRef.current[id];
        }
      });

      setVehicleCount(features.length);
      setLastUpdated(new Date().toISOString());

      // Track the "primary" vehicle for the single-truck driver flow.
      if (features.length === 1) {
        const f = features[0];
        const [lng, lat] = f.geometry.coordinates;
        setPrimary({
          ...f.properties,
          latitude: lat,
          longitude: lng,
          updated_at: f.properties.updated_at ?? null,
        });
      } else if (features.length > 1) {
        setPrimary(null);
      }

      // Initial fit/center.  Single-truck driver gets a tighter zoom on
      // their truck instead of the whole-fleet bounding box.
      if (firstLoadRef.current && features.length > 0) {
        if (features.length === 1) {
          const [lng, lat] = features[0].geometry.coordinates;
          map.setView([lat, lng], 14);
        } else {
          const bounds = cluster.getBounds();
          if (bounds.isValid()) {
            map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
          }
        }
        firstLoadRef.current = false;
      }
      setError(null);
    } catch (e) {
      console.error('Failed to load vehicles:', e);
      setError(classifyError(e));
    }
  }

  async function loadGeofences(layer: L.LayerGroup) {
    try {
      const data = await apiJSON<{ features: GeofenceFeature[] }>('/api/fleet/geofences');
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

  const flyToVehicle = useCallback(() => {
    haptics.medium();
    const map = mapRef.current;
    if (!map) return;
    if (primary && primary.latitude != null && primary.longitude != null) {
      map.setView([primary.latitude, primary.longitude], 15, { animate: true });
    } else {
      const cluster = clusterRef.current;
      if (cluster) {
        const bounds = cluster.getBounds();
        if (bounds.isValid()) {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12, animate: true });
        }
      }
    }
  }, [primary]);

  /** Toggle today's GPS breadcrumb trail on/off for the primary vehicle. */
  const toggleRoute = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;
    // If route already shown, clear it.
    if (routeActive) {
      routeLayerRef.current?.remove();
      routeLayerRef.current = null;
      setRouteActive(false);
      return;
    }
    if (!primary) return;
    setRouteLoading(true);
    haptics.selection();
    try {
      const encoded = encodeURIComponent(primary.name);
      const data = await apiJSON<{ points: { lat: number; lng: number; speed_mph: number; time: string }[] }>(
        `/api/fleet/routes/${encoded}`
      );
      const pts = data.points ?? [];
      if (pts.length < 2) {
        haptics.error();
        return;
      }
      const latlngs = pts.map(p => [p.lat, p.lng] as [number, number]);
      const polyline = L.polyline(latlngs, {
        color: '#0a84ff',
        weight: 4,
        opacity: 0.8,
        lineJoin: 'round',
      });
      polyline.addTo(map);
      routeLayerRef.current = polyline;
      map.fitBounds(polyline.getBounds(), { padding: [40, 40], animate: true });
      setRouteActive(true);
      haptics.success();
    } catch {
      haptics.error();
    } finally {
      setRouteLoading(false);
    }
  }, [primary, routeActive]);

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div className="map-page">
      <div id="map-container" ref={containerRef} />

      {/* Top header — translucent gradient strip */}
      {!loading && !error && (
        <div className="map-header">
          <div className="map-header__title">
            {primary ? primary.name : `${vehicleCount} ${vehicleCount === 1 ? 'vehicle' : 'vehicles'}`}
          </div>
          {lastUpdated && (
            <div className="map-header__sub">
              Updated <RelativeTime iso={lastUpdated} />
            </div>
          )}
        </div>
      )}

      {/* ── Bottom controls zone ─────────────────────────────────────────
          Layout (bottom → top, all within .map-page which ends at tabbar):
            14px  — map-status-card base
            ~92px — card height
            10px  — gap
            112px — location FAB base
            46px  — FAB height
            10px  — gap
            168px — route replay FAB base
      ──────────────────────────────────────────────────────────────────── */}

      {/* Single-truck status card — full width, tappable */}
      {!loading && !error && primary && (
        <div
          className="map-status-card"
          onClick={() => { haptics.selection(); setSheet(primary); }}
          role="button"
          aria-label={`View details for ${primary.name}`}
        >
          <div className="map-status-card__row1">
            <span className="map-status-card__name">{primary.name}</span>
            <div className="map-status-card__right">
              <span className={`status-badge status-badge--solid ${primary.status}`}>
                {primary.status}
              </span>
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
                <path d="M3 1.5 L7 5 L3 8.5" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          </div>
          <div className="map-status-card__addr">
            {fmtSpeed(primary.speed_mph, units)} · {truncate(primary.address || 'Unknown', 52)}
          </div>
          {primary.fuel_percent != null && (
            <div className="map-status-card__fuel-track">
              <div
                className="map-status-card__fuel-fill"
                style={{
                  width: `${primary.fuel_percent}%`,
                  background: primary.fuel_percent < 15
                    ? 'var(--st-red, #ff453a)'
                    : primary.fuel_percent < 30
                    ? 'var(--st-orange, #ff9f0a)'
                    : 'var(--st-green, #30d158)',
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* PTI chip — visible only for drivers with an open inspection.
          Sits above the status card so it's the first thing a driver
          sees when they open the mini app on Monday morning. */}
      {!loading && !error && userPerms && onNavigate && (
        <PTIChip userPerms={userPerms} onTap={() => onNavigate('pti')} />
      )}

      {/* FAB group — right column, stacked above the status card */}
      {!loading && !error && (
        <div className="map-fab-group">
          {/* Route replay (only when primary vehicle known) */}
          {primary && (
            <button
              className={`map-fab${routeActive ? ' map-fab--active' : ''}`}
              onClick={toggleRoute}
              disabled={routeLoading}
              aria-label={routeActive ? "Hide today's route" : "Show today's route"}
            >
              {routeLoading ? (
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden
                  style={{ animation: 'spin 1s linear infinite' }}>
                  <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth="2.5"/>
                  <path d="M12 3 A9 9 0 0 1 21 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/>
                </svg>
              ) : routeActive ? (
                <Icon24Replay />
              ) : (
                <Icon24ReplayOutline />
              )}
            </button>
          )}

          {/* Fly-to / fit-fleet */}
          {vehicleCount > 0 && (
            <button
              className="map-fab"
              onClick={flyToVehicle}
              aria-label={primary ? 'Center on my vehicle' : 'Fit fleet'}
            >
              <Icon24TargetOutline />
            </button>
          )}
        </div>
      )}

      {loading && (
        <div className="centered" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <span className="skeleton" style={{ width: 48, height: 48, borderRadius: 24 }} />
        </div>
      )}

      {!loading && error && (() => {
        // Pick a kind-appropriate icon so screen readers announce the
        // right thing ("lock" → auth, "globe" → no connection, "warning"
        // → server / generic).
        const ErrIcon =
          error.kind === 'auth'    ? Icon24LockOutline
          : error.kind === 'network' ? Icon24GlobeOutline
          : Icon24WarningTriangleOutline;
        const header =
          error.kind === 'auth'    ? 'Session expired'
          : error.kind === 'server'  ? 'Server error'
          : error.kind === 'network' ? 'No connection'
          : 'Could not load map';
        return (
          <div className="centered" style={{ position: 'absolute', inset: 0 }}>
            <Placeholder
              header={header}
              description={error.message}
              action={error.kind === 'auth' ? null : (
                <button
                  className="retry-btn"
                  onClick={() => {
                    const map = mapRef.current;
                    const cluster = clusterRef.current;
                    const geo = geofenceRef.current;
                    if (!map || !cluster) return;
                    setError(null);
                    setLoading(true);
                    Promise.all([
                      loadVehicles(map, cluster),
                      geo ? loadGeofences(geo) : Promise.resolve(),
                    ]).finally(() => setLoading(false));
                  }}
                >Retry</button>
              )}
            >
              <ErrIcon width={48} height={48} aria-label={header} style={{ opacity: 0.4 }} />
            </Placeholder>
          </div>
        );
      })()}

      {/* Truck detail bottom sheet */}
      <BottomSheet
        open={!!sheet}
        onClose={() => setSheet(null)}
        title={sheet ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Icon24TruckOutline width={20} height={20} />
            {sheet.name}
          </span>
        ) : ''}
      >
        {sheet && (() => {
          // Icon-prefixed sheet rows.  Switched from emoji prefixes
          // (⚡ ⛽ 🧪 ⚠️ 🔑 📍 🕒) to VK icons for visual consistency
          // with the rest of the app and stable widths across iOS/Android.
          type Row = {
            key: string;
            icon: IconCmp;
            label: string;
            value: string;
          };
          const rows: Row[] = [
            { key: 'speed',    icon: Icon24SpeedometerMiddleOutline, label: 'Speed',    value: fmtSpeed(sheet.speed_mph, units) },
            { key: 'fuel',     icon: Icon24DropsOutline,             label: 'Fuel',     value: sheet.fuel_percent != null ? `${Math.round(sheet.fuel_percent)}%` : '—' },
            { key: 'def',      icon: Icon24WaterDropOutline,         label: 'DEF',      value: sheet.def_percent != null ? `${Math.round(sheet.def_percent)}%` : '—' },
            { key: 'faults',   icon: sheet.fault_count > 0 ? Icon24WarningTriangleOutline : Icon24CheckCircleOutline,
              label: 'Faults', value: sheet.fault_count > 0 ? String(sheet.fault_count) : 'None' },
            { key: 'engine',   icon: Icon24KeyOutline,               label: 'Engine',   value: sheet.engine_state },
            { key: 'location', icon: Icon24LocationOutline,          label: 'Location', value: truncate(sheet.address || 'Unknown', 80) },
            { key: 'updated',  icon: Icon24RecentOutline,            label: 'Updated',  value: sheet.updated_at ? new Date(sheet.updated_at).toLocaleString() : '—' },
          ];
          return (
            <>
              <div className="sheet-row sheet-row--header">
                <span className="sheet-row__label">{sheet.company || '—'}</span>
                <span className={`status-badge ${sheet.status}`}>{sheet.status}</span>
              </div>
              {rows.map(({ key, icon: Icon, label, value }) => (
                <div key={key} className="sheet-row">
                  <span className="sheet-row__label">
                    <Icon width={16} height={16} aria-hidden />
                    {label}
                  </span>
                  <span className="sheet-row__value">{value}</span>
                </div>
              ))}
              <button
                className="retry-btn"
                style={{ width: '100%', marginTop: 16 }}
                onClick={() => {
                  if (sheet.latitude != null && sheet.longitude != null) {
                    mapRef.current?.setView([sheet.latitude, sheet.longitude], 16, { animate: true });
                  }
                  setSheet(null);
                }}
              >Center on truck</button>
            </>
          );
        })()}
      </BottomSheet>
    </div>
  );
}
