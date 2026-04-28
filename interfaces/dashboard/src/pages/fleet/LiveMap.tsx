import { useEffect, useRef, useState } from 'react';
import { apiJSON } from '../../api/client';
import { useLeafletMap } from '../../hooks/useLeafletMap';
import { usePoiLayers } from '../../hooks/usePoiLayers';
import PoiLayerPanel from '../../components/PoiLayerPanel';
import MapTypeControl from '../../components/MapTypeControl';
import { POI_LAYERS } from '../../config/poiLayers';
import type { PoiFeature } from '../../hooks/usePoiLayers';
import type { MapVehicleFeature, MapVehiclesResponse, MapVehicleProperties, LiveVehiclesResponse, LiveVehiclePosition } from '../../types';
import type L from 'leaflet';

const REFRESH_MS      = 30_000;   // full data refresh (fuel, DEF, status)
const LIVE_REFRESH_MS =  5_000;   // position-only fast refresh

// ── Per-vehicle continuous physics state ─────────────────────────────────────
//
// Instead of "lerp A→B" (which has a hard stop), each moving vehicle has a
// persistent physics state that is updated by each GPS fix and driven by a
// continuous requestAnimationFrame loop.
//
// When a new fix arrives:
//   • Bearing is computed from previous→current GPS fix (geometrically exact)
//   • Velocity is EMA-blended with the new target (smooth direction changes)
//   • Position is corrected toward the confirmed GPS fix:
//       <25 m drift → 60% correction (smooth)
//       ≥25 m drift → 80% correction (fast snap back to road)
//   • Target heading is updated (the loop smoothly rotates the icon toward it)
// Between fixes the loop simply advances lat/lng by velocity × dt every frame.
interface VehiclePhysics {
  lat: number;           // current rendered latitude
  lng: number;           // current rendered longitude
  prevLat: number;       // confirmed GPS lat from PREVIOUS poll
  prevLng: number;       // confirmed GPS lng from PREVIOUS poll
  velLat: number;        // velocity in degrees-lat per ms  (north/south)
  velLng: number;        // velocity in degrees-lng per ms  (east/west)
  headingDeg: number;    // currently rendered heading (rotates smoothly each frame)
  targetHeading: number; // latest GPS bearing  (physics loop approaches this)
  isMoving: boolean;
  engineState: string;   // 'On' | 'Idle' | 'Off' — kept fresh from 30 s full poll
  lastTs: number;        // previous rAF timestamp — used for dt calculation
}

/** Shortest signed angle difference, handles 359° → 1° wrap-around. */
function shortestAngleDiff(from: number, to: number): number {
  let diff = ((to - from) % 360 + 360) % 360;
  if (diff > 180) diff -= 360;
  return diff;
}

/** Convert speed (mph) + heading (°CW from N) → lat/lng velocity (deg/ms). */
function speedHeadingToVelocity(
  speedMph: number, headingDeg: number, atLat: number,
): { velLat: number; velLng: number } {
  const speedMs    = speedMph * 0.44704;                          // mph → m/s
  const headingRad = (headingDeg * Math.PI) / 180;
  const mPerDegLat = 111_111;
  const mPerDegLng = 111_111 * Math.cos((atLat * Math.PI) / 180);
  return {
    velLat: (Math.cos(headingRad) * speedMs) / mPerDegLat / 1000, // deg/ms
    velLng: (Math.sin(headingRad) * speedMs) / mPerDegLng / 1000,
  };
}

/**
 * Bearing (°CW from North) from point A → point B.
 * More accurate than Samsara's heading field, which can lag on turns.
 */
function bearingBetween(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLng  = toRad(lng2 - lng1);
  const y     = Math.sin(dLng) * Math.cos(toRad(lat2));
  const x     = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2))
              - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** Approximate distance in metres between two lat/lng points (flat-earth). */
function distMetres(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const mPerDegLat = 111_111;
  const mPerDegLng = 111_111 * Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  const dy = (lat2 - lat1) * mPerDegLat;
  const dx = (lng2 - lng1) * mPerDegLng;
  return Math.sqrt(dx * dx + dy * dy);
}

// ── Status helpers (outside component — stable references, usable in effects) ─

type VehicleStatus = 'moving' | 'idle' | 'stopped';
type VehicleId = string | number;

function vehicleStatus(f: MapVehicleFeature): VehicleStatus {
  const p = f.properties;
  if ((p.speed_mph || 0) > 0) return 'moving';
  if (p.engine_state === 'On') return 'idle';
  return 'stopped';
}

function statusColor(status: VehicleStatus): string {
  if (status === 'moving') return '#22c55e';
  if (status === 'idle')   return '#eab308';
  return '#ef4444';
}

/** Haversine distance between two points — returns miles. */
function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 3958.8;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

/** True when fuel OR DEF is below the warning threshold. */
function hasLowLevelWarning(p: MapVehicleProperties): boolean {
  return (p.fuel_percent != null && p.fuel_percent < 15) ||
         (p.def_percent  != null && p.def_percent  < 15);
}

/**
 * Vehicle map icon.
 * Moving (speed_mph > 0): solid filled triangle rotated to travel direction.
 * Stopped / Idle: solid filled circle dot.
 * Warn ring: red outline on dot only.
 */
function makeIcon(
  Leaf: typeof L,
  color: string,
  warn = false,
  speedMph = 0,
  heading?: number | null,
) {
  const isMoving = speedMph > 0;

  if (isMoving) {
    // Pure SVG triangle pointing up, rotated to GPS heading.
    // Uses a CSS animation class injected once at map init.
    const size = 18;
    const half = size / 2;
    const pts  = `${half},2 ${size - 1},${size - 2} 1,${size - 2}`;
    const rot  = typeof heading === 'number' ? heading : 0;
    const svg  = [
      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"`,
      ` class="vehicle-moving"`,
      ` style="overflow:visible;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))"`,
      ` viewBox="0 0 ${size} ${size}">`,
      `<polygon points="${pts}"`,
      ` fill="${color}" stroke="#fff" stroke-width="1.5"`,
      ` transform="rotate(${rot},${half},${half})"/>`,
      `</svg>`,
    ].join('');
    return Leaf.divIcon({
      className: '',
      html: svg,
      iconSize:   [size, size],
      iconAnchor: [half, half],
    });
  }

  // Pure circle dot for stopped / idle
  const ring = warn
    ? `position:absolute;inset:-3px;border-radius:50%;border:2px solid #ef4444;`
    : '';
  return Leaf.divIcon({
    className: '',
    html: [
      `<div style="position:relative;width:14px;height:14px">`,
      warn ? `<div style="${ring}"></div>` : '',
      `<div style="position:absolute;inset:0;border-radius:50%;`,
      `background:${color};border:2px solid #fff;`,
      `box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>`,
      `</div>`,
    ].join(''),
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });
}

export default function LiveMap() {
  const { mapRef, leafletMap, isReady, mapType, showLabels, setMapType, setShowLabels } = useLeafletMap();
  // Lift POI hook to this level so LiveMap can access allFeatures for nearest-POI
  const poiHook = usePoiLayers(leafletMap, isReady);

  // clusterRef holds either a MarkerClusterGroup (when plugin is available)
  // or a plain LayerGroup as fallback — both expose the same addTo/clearLayers API.
  const clusterRef = useRef<L.LayerGroup | null>(null);
  // markersRef stores one Marker per vehicle, keyed by vehicle id.
  // The filter effect controls which markers are visible in clusterRef.
  const markersRef = useRef<Map<VehicleId, L.Marker>>(new Map());

  const [vehicles, setVehicles]     = useState<MapVehicleFeature[]>([]);
  const [selected, setSelected]     = useState<MapVehicleProperties | null>(null);
  // Lat/lng of the selected vehicle for nearest-POI calculation
  const [selectedPos, setSelectedPos] = useState<[number, number] | null>(null);
  // Mirror selected id in a ref so the filter effect always sees current value
  const selectedIdRef = useRef<VehicleId | null>(null);
  // Latest positions from the fast live poll (id → position data)
  const livePosRef = useRef<Record<string, LiveVehiclePosition>>({});
  // requestAnimationFrame IDs for per-vehicle smooth position interpolation
  const animFramesRef = useRef<Map<string, number>>(new Map());
  // Continuous physics state per vehicle — position, velocity, heading
  const vehiclePhysRef = useRef<Map<string, VehiclePhysics>>(new Map());
  // Route line drawn from vehicle to a clicked POI
  const routeLineRef = useRef<L.Polyline | null>(null);
  // Key of the currently active (highlighted) POI row: "<layerId>-<index>"
  const [activePoiKey, setActivePoiKey] = useState<string | null>(null);
  // True while fetching road route from OSRM
  const [routeLoading, setRouteLoading] = useState(false);
  // Layers whose POI list is expanded beyond the default 3
  const [expandedLayers, setExpandedLayers] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch]         = useState('');

  // ── Map initialization + polling ──────────────────────────────────────────

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    const Leaf    = window.L as typeof L;
    const LExtra  = window.L as unknown as { markerClusterGroup?: (o?: object) => L.LayerGroup };

    // Use clustering when the plugin loaded successfully, plain layer otherwise
    const group = LExtra.markerClusterGroup
      ? LExtra.markerClusterGroup({
          maxClusterRadius: 50,
          disableClusteringAtZoom: 14,
          showCoverageOnHover: false,
          spiderfyOnMaxZoom: true,
        })
      : Leaf.layerGroup();

    group.addTo(leafletMap.current);
    clusterRef.current = group;

    // Inject CSS for pulsing animation on moving triangles
    // NOTE: No transition on .leaflet-marker-icon — we use rAF interpolation instead.
    if (!document.getElementById('vehicle-marker-css')) {
      const style = document.createElement('style');
      style.id = 'vehicle-marker-css';
      style.textContent = [
        '@keyframes vehicle-pulse {',
        '  0%,100% { opacity:1; }',
        '  50%      { opacity:.72; }',
        '}',
        '.vehicle-moving { animation: vehicle-pulse 2s ease-in-out infinite; }',
      ].join('\n');
      document.head.appendChild(style);
    }

    loadVehicles(Leaf);
    const timer     = setInterval(() => loadVehicles(Leaf), REFRESH_MS);
    const liveTimer = setInterval(() => livePositionPoll(Leaf), LIVE_REFRESH_MS);

    return () => {
      clearInterval(timer);
      clearInterval(liveTimer);
      // Cancel all in-flight position animations
      animFramesRef.current.forEach((id) => cancelAnimationFrame(id));
      animFramesRef.current.clear();
      vehiclePhysRef.current.clear();
      clusterRef.current?.remove();
      clusterRef.current = null;
      markersRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReady]);

  // ── Filter→map sync ────────────────────────────────────────────────────────
  // When a vehicle is selected: show ONLY that vehicle's marker (focus mode).
  // When no selection: show all that match statusFilter + search.

  useEffect(() => {
    const group = clusterRef.current;
    if (!group) return;
    group.clearLayers();
    const focusId = selectedIdRef.current;
    vehicles.forEach((f) => {
      const id: VehicleId = f.properties.id ?? f.properties.name;
      if (focusId !== null && id !== focusId) return;          // isolation mode
      const status = vehicleStatus(f);
      if (!focusId && statusFilter !== 'all' && status !== statusFilter) return;
      if (!focusId && search && !f.properties.name.toLowerCase().includes(search.toLowerCase())) return;
      const m = markersRef.current.get(id);
      if (m) m.addTo(group);
    });
  }, [statusFilter, search, vehicles, selected]);  // `selected` triggers re-run on focus change

  // ── Vehicle poll — delta-update positions without full clear/rebuild ────────

  async function loadVehicles(Leaf: typeof L) {
    try {
      const data = await apiJSON<MapVehiclesResponse>('/map/vehicles');
      const features = data.features || [];
      const seenIds = new Set<VehicleId>();

      features.forEach((f) => {
        const id: VehicleId = f.properties.id ?? f.properties.name;
        seenIds.add(id);

        const [lng, lat] = f.geometry.coordinates;
        const color   = statusColor(vehicleStatus(f));
        const warn    = hasLowLevelWarning(f.properties);
        const speed   = f.properties.speed_mph ?? 0;
        // Prefer the current physics heading (continuously updated) over the
        // stale heading from the 30 s poll, so the icon doesn't jump on refresh.
        const phys    = vehiclePhysRef.current.get(String(id));
        const heading = phys?.headingDeg ?? f.properties.heading;
        const icon    = makeIcon(Leaf, color, warn, speed, heading);

        // Keep engine_state fresh so the live poll can color idle vs stopped.
        const engineState = f.properties.engine_state ?? 'Off';
        if (phys) phys.engineState = engineState;

        if (markersRef.current.has(id)) {
          // Update existing marker in-place — no DOM remove/re-add
          const m = markersRef.current.get(id)!;
          // Skip position snap while the physics loop is running —
          // calling setLatLng every 30 s would teleport the vehicle visibly.
          if (!phys?.isMoving) m.setLatLng([lat, lng]);
          m.setIcon(icon);
          m.off('click').on('click', () => setSelected(f.properties));
        } else {
          // New vehicle — create marker but don't add to map yet;
          // the filter sync effect will add it if it matches the current filter.
          const m = Leaf.marker([lat, lng], { icon })
            .on('click', () => setSelected(f.properties));
          markersRef.current.set(id, m);
        }
      });

      // Remove markers for vehicles that disappeared from the fleet
      markersRef.current.forEach((m, id) => {
        if (!seenIds.has(id)) {
          m.remove();
          markersRef.current.delete(id);
        }
      });

      // Updating vehicles state triggers the filter sync effect above,
      // which re-adds exactly the markers that match the current filter.
      setVehicles(features);
    } catch { /* ignore poll errors silently */ }
  }

  // ── Continuous per-vehicle physics loop ──────────────────────────────────────
  //
  // Each moving vehicle has ONE persistent rAF loop that runs until the vehicle
  // stops.  The loop advances the marker's lat/lng by velocity × dt every frame
  // and smoothly rotates the heading icon toward the target bearing.
  //
  // GPS fixes (every 5 s) update the physics state — they don't restart the loop:
  //   • Velocity is EMA-blended (α=0.35) for smooth direction transitions
  //   • Position is nudged 35% toward the confirmed GPS fix (corrects drift)
  //   • Target heading is updated; the loop rotates 6% of remaining angle/frame
  //
  // This means the vehicle never stops between polls — there is no "lerp end
  // point" — and direction changes look like gradual curves, not kinks.

  function startPhysicsLoop(vid: string, Leaf: typeof L) {
    const prevRaf = animFramesRef.current.get(vid);
    if (prevRaf !== undefined) cancelAnimationFrame(prevRaf);

    // 6% of remaining heading difference per frame ≈ 1 s to turn 30° at 60 fps
    const HEADING_BLEND = 0.06;

    function frame(ts: number) {
      const p = vehiclePhysRef.current.get(vid);
      const m = markersRef.current.get(vid);
      if (!p || !m || !p.isMoving) {
        animFramesRef.current.delete(vid);
        return;
      }

      // Cap dt to 100 ms — prevents huge jump if the tab was hidden
      const dt = Math.min(ts - p.lastTs, 100);
      p.lastTs = ts;

      // Advance position by current velocity
      p.lat += p.velLat * dt;
      p.lng += p.velLng * dt;
      m.setLatLng([p.lat, p.lng]);

      // Smoothly rotate heading toward the GPS target (shortest-path)
      const diff = shortestAngleDiff(p.headingDeg, p.targetHeading);
      if (Math.abs(diff) > 0.2) {
        p.headingDeg += diff * HEADING_BLEND;
        // Update the SVG polygon transform directly — avoids a full icon rebuild
        const el = m.getElement();
        if (el) {
          const poly = el.querySelector('polygon');
          if (poly) poly.setAttribute('transform', `rotate(${p.headingDeg},9,9)`);
        }
      }

      animFramesRef.current.set(vid, requestAnimationFrame(frame));
    }

    const p = vehiclePhysRef.current.get(vid);
    if (p) p.lastTs = performance.now();
    animFramesRef.current.set(vid, requestAnimationFrame(frame));
  }

  // ── Live position fast poll — feeds the physics state every 5 s ──────────

  async function livePositionPoll(Leaf: typeof L) {
    try {
      const data = await apiJSON<LiveVehiclesResponse>('/map/vehicles/live');
      livePosRef.current = data.positions;

      Object.entries(data.positions).forEach(([vid, pos]) => {
        const m = markersRef.current.get(vid);
        if (!m) return;

        const nowMoving  = pos.speed_mph > 0;
        const headingDeg = pos.heading ?? 0;

        const existing = vehiclePhysRef.current.get(vid);

        if (!existing) {
          // ── First time we see this vehicle ──────────────────────────────
          const { velLat: initVelLat, velLng: initVelLng } =
            speedHeadingToVelocity(pos.speed_mph, headingDeg, pos.lat);
          vehiclePhysRef.current.set(vid, {
            lat: pos.lat, lng: pos.lng,
            prevLat: pos.lat, prevLng: pos.lng,
            velLat: initVelLat, velLng: initVelLng,
            headingDeg, targetHeading: headingDeg,
            isMoving: nowMoving,
            engineState: 'Off',   // will be overwritten by the next 30 s full poll
            lastTs: performance.now(),
          });
          const color = nowMoving ? '#22c55e' : '#ef4444';
          m.setIcon(makeIcon(Leaf, color, false, pos.speed_mph, headingDeg));
          if (nowMoving) startPhysicsLoop(vid, Leaf);
          return;
        }

        const wasMoving = existing.isMoving;
        existing.isMoving      = nowMoving;
        existing.targetHeading = headingDeg;

        if (nowMoving) {
          // ── Vehicle is moving ───────────────────────────────────────────
          //
          // Use bearing from previous→current GPS fix instead of Samsara's
          // heading field.  The track-derived bearing is geometrically exact
          // and always up-to-date; Samsara heading can lag on turns.
          const moved = distMetres(existing.prevLat, existing.prevLng, pos.lat, pos.lng);
          const trackBearing = moved > 2
            ? bearingBetween(existing.prevLat, existing.prevLng, pos.lat, pos.lng)
            : headingDeg;  // fell back to Samsara heading when barely moved
          existing.targetHeading = trackBearing;

          // Recompute target velocity from track bearing (not Samsara heading)
          const { velLat: tvl, velLng: tvg } = speedHeadingToVelocity(pos.speed_mph, trackBearing, pos.lat);

          // EMA-blend velocity — α=0.60 means new fix gets 60% weight.
          // Higher α = faster response to turns = vehicle follows road curves sooner.
          const α = 0.60;
          existing.velLat = α * tvl + (1 - α) * existing.velLat;
          existing.velLng = α * tvg + (1 - α) * existing.velLng;

          // Position correction: snap toward the confirmed GPS fix.
          // If the vehicle has drifted >25 m off the road, correct aggressively (80%).
          // Otherwise use 60% — smooth but keeps the marker close to the road.
          const drift = distMetres(existing.lat, existing.lng, pos.lat, pos.lng);
          const corrStrength = drift > 25 ? 0.80 : 0.60;
          existing.lat += (pos.lat - existing.lat) * corrStrength;
          existing.lng += (pos.lng - existing.lng) * corrStrength;

          // Remember this fix as "previous" for the next poll's bearing calculation
          existing.prevLat = pos.lat;
          existing.prevLng = pos.lng;

          // Only rebuild icon when transitioning stopped→moving (avoids flicker)
          if (!wasMoving) {
            m.setIcon(makeIcon(Leaf, '#22c55e', false, pos.speed_mph, existing.headingDeg));
            startPhysicsLoop(vid, Leaf);
          }
        } else {
          // ── Vehicle stopped ─────────────────────────────────────────────
          existing.velLat = 0;
          existing.velLng = 0;
          existing.lat    = pos.lat;
          existing.lng    = pos.lng;
          existing.prevLat = pos.lat;
          existing.prevLng = pos.lng;
          // Stop the rAF loop — the isMoving=false check in frame() will exit it
          // but we also cancel explicitly to be safe
          const prevRaf = animFramesRef.current.get(vid);
          if (prevRaf !== undefined) cancelAnimationFrame(prevRaf);
          animFramesRef.current.delete(vid);
          m.setLatLng([pos.lat, pos.lng]);
          if (wasMoving) {
            // engine_state kept fresh from 30 s poll:
            //   'On' or 'Idle' → engine still running → yellow dot
            //   'Off'          → truly stopped        → red dot
            const isIdle = existing.engineState === 'On' || existing.engineState === 'Idle';
            m.setIcon(makeIcon(Leaf, isIdle ? '#eab308' : '#ef4444', false, 0, null));
          }
        }
      });
    } catch { /* ignore live poll errors silently */ }
  }

  // ── Derived UI state ───────────────────────────────────────────────────────

  const filtered = vehicles.filter((f) => {
    const status = vehicleStatus(f);
    if (statusFilter !== 'all' && status !== statusFilter) return false;
    if (search && !f.properties.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const counts: Record<string, number> = { all: vehicles.length, moving: 0, idle: 0, stopped: 0 };
  vehicles.forEach((f) => { counts[vehicleStatus(f)]++; });

  // ── POI route-line handler ────────────────────────────────────────────────
  /** Fetch a road-following route from OSRM and draw it on the map.
   *  Falls back to a straight dashed line if the API is unreachable. */
  async function handlePoiClick(
    layerColor: string,
    poiLatLng: [number, number],
    key: string,
  ) {
    const map  = leafletMap.current;
    const Leaf = window.L as typeof L;
    if (!map || !selectedPos) return;

    // Remove any existing route line
    routeLineRef.current?.remove();
    routeLineRef.current = null;

    // Toggle off if same row clicked again
    if (activePoiKey === key) {
      setActivePoiKey(null);
      return;
    }

    setActivePoiKey(key);
    setRouteLoading(true);

    const [vLat, vLng] = selectedPos;
    const [pLat, pLng] = poiLatLng;
    let routeDrawn = false;

    try {
      // OSRM public routing API — returns GeoJSON road geometry
      const url =
        `https://router.project-osrm.org/route/v1/driving/` +
        `${vLng},${vLat};${pLng},${pLat}?overview=full&geometries=geojson`;
      const resp = await fetch(url);
      if (resp.ok) {
        const data = await resp.json() as {
          routes?: Array<{ geometry: { coordinates: [number, number][] } }>;
        };
        const coords = data.routes?.[0]?.geometry?.coordinates ?? [];
        if (coords.length) {
          // OSRM returns [lng, lat]; Leaflet expects [lat, lng]
          const latLngs = coords.map(([lng, lat]) => [lat, lng] as [number, number]);
          const line = Leaf.polyline(latLngs, {
            color: layerColor, weight: 4, opacity: 0.88, dashArray: '10, 6',
          });
          line.addTo(map);
          routeLineRef.current = line;
          map.fitBounds(line.getBounds(), { padding: [60, 60] });
          routeDrawn = true;
        }
      }
    } catch { /* fall through to straight-line fallback */ }

    setRouteLoading(false);

    if (!routeDrawn) {
      // Fallback: straight dashed line when OSRM is unreachable
      const line = Leaf.polyline(
        [selectedPos, poiLatLng],
        { color: layerColor, weight: 3, opacity: 0.9, dashArray: '12, 8' },
      );
      line.addTo(map);
      routeLineRef.current = line;
      map.fitBounds(Leaf.latLngBounds([selectedPos, poiLatLng]), { padding: [60, 60] });
    }
  }

  /** Clear route line and expanded state (called on Back / vehicle change). */
  function clearRouteLine() {
    routeLineRef.current?.remove();
    routeLineRef.current = null;
    setActivePoiKey(null);
    setExpandedLayers(new Set());
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* Map — relative so the POI panel can be absolutely positioned inside it */}
      <div className="flex-1 relative rounded-xl overflow-hidden border border-border z-0">
        <div ref={mapRef} className="absolute inset-0" />
        <PoiLayerPanel poiHook={poiHook} />
        <MapTypeControl
          mapType={mapType}
          showLabels={showLabels}
          setMapType={setMapType}
          setShowLabels={setShowLabels}
          isReady={isReady}
        />
      </div>

      {/* Side panel */}
      <div className="w-80 bg-card border border-border rounded-xl overflow-y-auto shrink-0">
        <div className="p-4 border-b border-border space-y-3">
          <h2 className="font-semibold">Vehicles ({filtered.length})</h2>
          <input
            type="text"
            placeholder="Search vehicles..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm placeholder-muted-foreground focus:outline-none focus:border-ring"
          />
          <div className="flex gap-1 flex-wrap">
            {[['all', 'All'], ['moving', 'Moving'], ['idle', 'Idle'], ['stopped', 'Stopped']].map(
              ([key, label]) => (
                <button
                  key={key}
                  onClick={() => setStatusFilter(key)}
                  className={`text-xs px-2 py-1 rounded ${statusFilter === key ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'}`}
                >
                  {label} ({counts[key]})
                </button>
              ),
            )}
          </div>
        </div>

        {selected ? (
          <div className="p-4 space-y-3 text-sm">
            <button
              onClick={() => {
                clearRouteLine();
                selectedIdRef.current = null;
                setSelected(null);
                setSelectedPos(null);
              }}
              className="text-primary hover:underline text-xs"
            >
              ← Back
            </button>
            <h3 className="font-bold text-lg leading-tight">{selected.name}</h3>
            <p className="text-muted-foreground text-xs">{selected.address || 'Unknown location'}</p>

            {/* Quick stats grid */}
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs border-t border-border pt-2">
              <span className="text-muted-foreground">Speed</span>
              <span>{selected.speed_mph ?? 0} mph</span>
              <span className="text-muted-foreground">Engine</span>
              <span>{selected.engine_state || 'Off'}</span>
              <span className="text-muted-foreground">Company</span>
              <span className="truncate">{selected.company || '—'}</span>
            </div>

            {/* Fuel & DEF level bars */}
            {(selected.fuel_percent != null || selected.def_percent != null) && (
              <div className="space-y-2 border-t border-border pt-2">
                {selected.fuel_percent != null && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className={selected.fuel_percent < 15 ? 'text-red-500 font-semibold' : 'text-muted-foreground'}>
                        ⛽ Fuel
                      </span>
                      <span className={selected.fuel_percent < 15 ? 'text-red-500 font-semibold' : ''}>
                        {Math.round(selected.fuel_percent)}%{selected.fuel_percent < 15 ? ' ⚠' : ''}
                      </span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${selected.fuel_percent < 15 ? 'bg-red-500' : selected.fuel_percent < 25 ? 'bg-yellow-500' : 'bg-green-500'}`}
                        style={{ width: `${Math.max(selected.fuel_percent, 2)}%` }}
                      />
                    </div>
                  </div>
                )}
                {selected.def_percent != null && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className={selected.def_percent < 15 ? 'text-red-500 font-semibold' : 'text-muted-foreground'}>
                        🧪 DEF
                      </span>
                      <span className={selected.def_percent < 15 ? 'text-red-500 font-semibold' : ''}>
                        {Math.round(selected.def_percent)}%{selected.def_percent < 15 ? ' ⚠ DERATE RISK' : ''}
                      </span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${selected.def_percent < 15 ? 'bg-red-500' : selected.def_percent < 25 ? 'bg-yellow-500' : 'bg-teal-500'}`}
                        style={{ width: `${Math.max(selected.def_percent, 2)}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Nearest POI section — top 3 per enabled layer, clickable to draw route */}
            {selectedPos && (
              <div className="border-t border-border pt-2">
                <p className="text-[10px] font-semibold text-muted-foreground tracking-wide mb-2">
                  NEAREST POI
                  {routeLoading && (
                    <span className="ml-2 text-[9px] text-muted-foreground animate-pulse">routing…</span>
                  )}
                  {activePoiKey && !routeLoading && (
                    <button
                      onClick={clearRouteLine}
                      className="ml-2 text-[9px] text-primary hover:underline normal-case font-normal"
                    >
                      clear route
                    </button>
                  )}
                </p>
                {POI_LAYERS.filter((def) => poiHook.enabled[def.id]).length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">Enable map layers to see nearest POI</p>
                ) : (
                  <div className="space-y-2.5">
                    {POI_LAYERS.map((def) => {
                      if (!poiHook.enabled[def.id]) return null;
                      const features: PoiFeature[] = poiHook.allFeatures[def.id] ?? [];

                      if (!features.length) {
                        return (
                          <div key={def.id} className="opacity-50">
                            <div className="flex items-center gap-1 mb-0.5">
                              <span className="text-[11px]">{def.icon}</span>
                              <span className="text-[10px] font-semibold tracking-wide" style={{ color: def.color }}>
                                {def.label}
                              </span>
                            </div>
                            <p className="text-[10px] text-muted-foreground italic pl-4">zoom in to load</p>
                          </div>
                        );
                      }

                      // Sort all by distance
                      const DEFAULT_N = 3;
                      const MAX_N = 10;
                      const isExpanded = expandedLayers.has(def.id);
                      const allSorted = features
                        .map((feat) => {
                          const [lng, lat] = feat.geometry.coordinates;
                          return { feat, dist: haversine(selectedPos[0], selectedPos[1], lat, lng), lat, lng };
                        })
                        .sort((a, b) => a.dist - b.dist)
                        .slice(0, MAX_N);
                      const sorted = isExpanded ? allSorted : allSorted.slice(0, DEFAULT_N);
                      const canExpand = allSorted.length > DEFAULT_N;

                      return (
                        <div key={def.id}>
                          {/* Layer header */}
                          <div className="flex items-center gap-1 mb-0.5">
                            <span className="text-[11px]">{def.icon}</span>
                            <span className="text-[10px] font-semibold tracking-wide" style={{ color: def.color }}>
                              {def.label}
                            </span>
                          </div>
                          {/* POI rows */}
                          <div className="space-y-0.5 pl-3">
                            {sorted.map(({ feat, dist, lat, lng }, i) => {
                              const poiKey = `${def.id}-${i}`;
                              const isActive = activePoiKey === poiKey;
                              const name = (feat.properties?.name as string) || def.label;
                              const distStr = dist < 0.1
                                ? `${(dist * 5280).toFixed(0)} ft`
                                : `${dist.toFixed(1)} mi`;
                              return (
                                <button
                                  key={poiKey}
                                  title={isActive ? 'Click to clear route' : 'Click to show route on map'}
                                  onClick={() => handlePoiClick(def.color, [lat, lng], poiKey)}
                                  className={`w-full flex items-center gap-2 text-xs py-0.5 px-1.5 rounded text-left transition-colors ${
                                    isActive
                                      ? 'bg-primary/10 ring-1 ring-primary/30'
                                      : 'hover:bg-muted/60'
                                  }`}
                                >
                                  <span className={`text-[9px] shrink-0 ${isActive ? 'text-primary' : 'text-muted-foreground'}`}>
                                    {isActive ? '→' : `${i + 1}.`}
                                  </span>
                                  <span className="flex-1 truncate">{name}</span>
                                  <span
                                    className="whitespace-nowrap font-medium tabular-nums shrink-0"
                                    style={{ color: def.color }}
                                  >
                                    {distStr}
                                  </span>
                                </button>
                              );
                            })}
                            {/* Show more / show less toggle */}
                            {canExpand && (
                              <button
                                onClick={() =>
                                  setExpandedLayers((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(def.id)) next.delete(def.id);
                                    else next.add(def.id);
                                    return next;
                                  })
                                }
                                className="w-full text-left text-[9px] text-primary hover:underline px-1.5 pt-0.5"
                              >
                                {isExpanded
                                  ? 'Show less'
                                  : `Show ${Math.min(allSorted.length - DEFAULT_N, MAX_N - DEFAULT_N)} more…`}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="divide-y divide-gray-800">
            {filtered.map((f) => {
              const p      = f.properties;
              const status = vehicleStatus(f);
              const warn   = hasLowLevelWarning(p);
              return (
                <button
                  key={p.id || p.name}
                  onClick={() => {
                    clearRouteLine();
                    setSelected(p);
                    const id: VehicleId = p.id ?? p.name;
                    selectedIdRef.current = id;
                    const [lng, lat] = f.geometry.coordinates;
                    setSelectedPos([lat, lng]);
                    leafletMap.current?.setView([lat, lng], 14);
                  }}
                  className="w-full text-left px-4 py-3 hover:bg-muted transition text-sm"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${warn ? 'ring-2 ring-red-500' : ''}`}
                      style={{ background: statusColor(status) }}
                    />
                    <span className="font-medium truncate flex-1">{p.name}</span>
                    {p.fuel_percent != null && p.fuel_percent < 15 && (
                      <span className="text-[10px] text-red-500">⛽⚠</span>
                    )}
                    {p.def_percent != null && p.def_percent < 15 && (
                      <span className="text-[10px] text-red-500">🧪⚠</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground ml-4 truncate">{p.address || '—'}</p>
                  {/* Mini fuel/DEF bars */}
                  {(p.fuel_percent != null || p.def_percent != null) && (
                    <div className="ml-4 mt-1.5 space-y-1">
                      {p.fuel_percent != null && (
                        <div className="flex items-center gap-1">
                          <span className="text-[9px] w-5 text-muted-foreground">⛽</span>
                          <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${p.fuel_percent < 15 ? 'bg-red-500' : p.fuel_percent < 25 ? 'bg-yellow-500' : 'bg-green-500'}`}
                              style={{ width: `${Math.max(p.fuel_percent, 2)}%` }}
                            />
                          </div>
                          <span className="text-[9px] text-muted-foreground w-7 text-right tabular-nums">
                            {Math.round(p.fuel_percent)}%
                          </span>
                        </div>
                      )}
                      {p.def_percent != null && (
                        <div className="flex items-center gap-1">
                          <span className="text-[9px] w-5 text-muted-foreground">🧪</span>
                          <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${p.def_percent < 15 ? 'bg-red-500' : p.def_percent < 25 ? 'bg-yellow-500' : 'bg-teal-500'}`}
                              style={{ width: `${Math.max(p.def_percent, 2)}%` }}
                            />
                          </div>
                          <span className="text-[9px] text-muted-foreground w-7 text-right tabular-nums">
                            {Math.round(p.def_percent)}%
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
