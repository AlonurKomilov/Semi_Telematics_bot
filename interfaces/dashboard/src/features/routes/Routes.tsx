import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Route as RouteIcon } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useLeafletMap } from '../../hooks/useLeafletMap';
import { usePoiLayers } from '../../hooks/usePoiLayers';
import PoiLayerPanel from '@/features/live-map/PoiLayerPanel';
import { PageHeader } from '../../components/shell';
import type { RouteReplayResponse, DispatchVehicle, DispatchVehiclesResponse, RoutePoint } from '../../types';
import type L from 'leaflet';
import { MAP_STATUS } from '../../config/mapColors';
import { useTimezone } from '../../hooks/useTimezone';
import { todayInTimeZone, formatTime } from '../../utils/datetime';

function speedColor(mph: number): string {
  if (mph > 70) return MAP_STATUS.danger;  // over 70
  if (mph > 50) return MAP_STATUS.warn;    // 50–70
  if (mph > 10) return MAP_STATUS.ok;      // cruising
  return MAP_STATUS.neutral;               // stopped/crawling
}

export default function Routes() {
  const { t } = useTranslation();
  // Route replay defaults to "today" in the account's timezone, and the
  // date picker caps at it — UTC would offer/allow tomorrow late in the
  // day for western fleets.
  const tz = useTimezone();
  const today = () => todayInTimeZone(tz);
  const { mapRef, leafletMap, isReady } = useLeafletMap();
  const poiHook = usePoiLayers(leafletMap, isReady);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const [vehicles, setVehicles] = useState<DispatchVehicle[]>([]);
  const [vehicleName, setVehicleName] = useState('');
  const [date, setDate] = useState(today);
  const [route, setRoute] = useState<RouteReplayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Initialise layer group once the shared map is ready
  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    const Leaf = window.L as typeof L;
    layerRef.current = Leaf.layerGroup().addTo(leafletMap.current);
    return () => {
      layerRef.current?.remove();
      layerRef.current = null;
    };
  }, [isReady, leafletMap]);

  // Load vehicle list
  useEffect(() => {
    apiJSON<DispatchVehiclesResponse>('/routes')
      .then((d) => setVehicles(d.vehicles || []))
      .catch(() => {});
  }, []);

  // Draw route on map
  const drawRoute = useCallback((Leaf: typeof L, points: RoutePoint[]) => {
    if (!layerRef.current || !leafletMap.current) return;
    layerRef.current.clearLayers();
    if (points.length === 0) return;

    // Draw speed-colored polyline segments
    for (let i = 1; i < points.length; i++) {
      const prev = points[i - 1];
      const cur = points[i];
      Leaf.polyline(
        [[prev.lat, prev.lng], [cur.lat, cur.lng]],
        { color: speedColor(cur.speed_mph), weight: 3, opacity: 0.8 },
      ).addTo(layerRef.current);
    }

    // Start marker
    const first = points[0];
    Leaf.circleMarker([first.lat, first.lng], {
      radius: 7, color: MAP_STATUS.ok, fillColor: MAP_STATUS.ok, fillOpacity: 1,
    })
      .bindPopup(`<b>Start</b><br>${formatTime(first.time, { timeZone: tz })}`)
      .addTo(layerRef.current);

    // End marker
    const last = points[points.length - 1];
    Leaf.circleMarker([last.lat, last.lng], {
      radius: 7, color: MAP_STATUS.danger, fillColor: MAP_STATUS.danger, fillOpacity: 1,
    })
      .bindPopup(`<b>End</b><br>${formatTime(last.time, { timeZone: tz })}`)
      .addTo(layerRef.current);

    // Fit bounds
    const lats = points.map((p) => p.lat);
    const lngs = points.map((p) => p.lng);
    leafletMap.current.fitBounds([
      [Math.min(...lats), Math.min(...lngs)],
      [Math.max(...lats), Math.max(...lngs)],
    ], { padding: [30, 30] });
  }, []);

  async function fetchRoute() {
    if (!vehicleName) return;
    setLoading(true);
    setError('');
    setRoute(null);
    try {
      const data = await apiJSON<RouteReplayResponse>(
        `/routes/${encodeURIComponent(vehicleName)}?date=${date}`,
      );
      setRoute(data);
      const Leaf = window.L as typeof L;
      drawRoute(Leaf, data.points || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load route');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <PageHeader
        icon={RouteIcon}
        title={t('pages.routes_title')}
        description={t('pages.routes_desc')}
      />

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Vehicle</label>
          <select
            value={vehicleName}
            onChange={(e) => setVehicleName(e.target.value)}
            className="bg-muted border border-border rounded px-3 py-2 text-sm text-foreground/80 min-w-[200px]"
          >
            <option value="">Select vehicle...</option>
            {vehicles.map((v) => (
              <option key={v.name} value={v.name}>{v.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Date</label>
          <input
            type="date"
            value={date}
            max={today()}
            onChange={(e) => setDate(e.target.value)}
            className="bg-muted border border-border rounded px-3 py-2 text-sm text-foreground/80"
          />
        </div>
        <button
          onClick={fetchRoute}
          disabled={loading || !vehicleName}
          className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-lg text-sm font-medium transition"
        >
          {loading ? 'Loading...' : 'Load Route'}
        </button>
      </div>

      {error && <p className="text-destructive text-sm mb-3">{error}</p>}

      {/* Summary card */}
      {route && (
        <div className="grid grid-cols-3 gap-4 mb-4">
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Total Distance</p>
            <p className="text-xl font-bold">{route.total_miles?.toFixed(1) ?? '—'} mi</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">Max Speed</p>
            <p className="text-xl font-bold">{route.max_speed_mph?.toFixed(0) ?? '—'} mph</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground">GPS Points</p>
            <p className="text-xl font-bold">{route.point_count ?? 0}</p>
          </div>
        </div>
      )}

      {/* Speed legend */}
      <div className="flex items-center gap-4 mb-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full bg-gray-500 inline-block" /> &lt;10 mph</span>
        {/* Legend swatches reference the same MAP_STATUS colours the
            speed-coloured polyline uses (config/mapColors.ts) so the key
            can't drift from the route it describes. */}
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full inline-block" style={{ background: MAP_STATUS.ok }} /> 10-50 mph</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full inline-block" style={{ background: MAP_STATUS.warn }} /> 50-70 mph</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-full inline-block" style={{ background: MAP_STATUS.danger }} /> &gt;70 mph</span>
      </div>

      {/* Map */}
      <div className="relative h-[calc(100vh-22rem)] rounded-xl border border-border overflow-hidden z-0">
        <div ref={mapRef} className="absolute inset-0" />
        <PoiLayerPanel poiHook={poiHook} leafletMap={leafletMap} />
      </div>
    </div>
  );
}
