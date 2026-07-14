import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, FlaskConical, Fuel, TriangleAlert } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useLeafletMap } from '../../hooks/useLeafletMap';
import { usePoiLayers } from '../../hooks/usePoiLayers';
import { useShellConfig } from '../../hooks/useShellConfig';
import PoiLayerPanel from '@/features/live-map/PoiLayerPanel';
import MapTypeControl from '@/features/live-map/MapTypeControl';
import { POI_LAYERS } from '../../config/poiLayers';
import { MAP_STATUS } from '../../config/mapColors';
import type { PoiFeature } from '../../hooks/usePoiLayers';
import type { MapVehicleFeature, MapVehiclesResponse, MapVehicleProperties, LiveVehiclesResponse, LiveVehiclePosition } from '../../types';
import type L from 'leaflet';
import { PageLayoutHost } from '../../features/_lib/PageLayoutHost';
import { LIVE_MAP_SECTIONS } from '../../features/live-map/registry';
import PoiIcon from '../../features/live-map/PoiIcon';
import { Tip } from '../../components/tooltip';
import { LIVE_MAP_LAYOUTS } from '../../features/live-map/layouts';

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
//       — Replaces the previous dead-reckoning approach which extrapolated
//       at constant velocity from the latest fix.  On road curves, the
//       extrapolation overshoots the road on the OUTSIDE of the turn for ~5 s
//       then snaps back when the next fix arrives — a visible jitter the user
//       reported.  Interpolation tween-drives the marker between two known
//       on-road points so it always traces the actual GPS path.
//   • Per-frame lerp:  pos = from + (to − from) × progress, progress ∈ [0,1]
//   • Heading also tweens smoothly (6% of remaining angle/frame).
// On every new GPS fix the loop rebases:
//   from = currently rendered pos, to = new fix, startMs = now,
//   duration = elapsed since previous fix (clamped 1500–8000 ms).
// This makes the trade-off ~5 s of motion latency in exchange for no overshoot.
interface VehiclePhysics {
  // ── Currently rendered position (read by external code) ────────────────
  lat: number;
  lng: number;
  // ── Animation segment endpoints ────────────────────────────────────────
  fromLat: number;
  fromLng: number;
  toLat: number;
  toLng: number;
  startMs: number;       // performance.now() when this segment began
  duration: number;      // ms over which to tween from→to (~last poll interval)
  lastFixMs: number;     // perf.now() of last received GPS fix (for next segment's duration)
  // ── Heading state ──────────────────────────────────────────────────────
  headingDeg: number;    // currently rendered heading (rotates smoothly each frame)
  targetHeading: number; // latest GPS bearing  (physics loop approaches this)
  // ── Status ─────────────────────────────────────────────────────────────
  isMoving: boolean;
  engineState: string;   // 'On' | 'Idle' | 'Off' — kept fresh from 30 s full poll
}

/** Shortest signed angle difference, handles 359° → 1° wrap-around. */
function shortestAngleDiff(from: number, to: number): number {
  let diff = ((to - from) % 360 + 360) % 360;
  if (diff > 180) diff -= 360;
  return diff;
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
  // Trust the authoritative status computed server-side from CAN-bus
  // engineStates (On/Idle/Off) merged with speed.  Only fall back to a
  // local heuristic when the field is missing (very old payloads or
  // tests).  Without this, a truck with engine_state='Idle' but speed=0
  // was being classified 'stopped' on the dashboard while the backend
  // reported 'idle' — leaving the Idle bucket empty.
  if (p.status === 'moving' || p.status === 'idle' || p.status === 'stopped') {
    return p.status;
  }
  if ((p.speed_mph || 0) > 0) return 'moving';
  if (p.engine_state === 'On' || p.engine_state === 'Idle') return 'idle';
  return 'stopped';
}

function statusColor(status: VehicleStatus): string {
  if (status === 'moving') return MAP_STATUS.ok;
  if (status === 'idle')   return MAP_STATUS.warn;
  return MAP_STATUS.danger;
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
    ? `position:absolute;inset:-3px;border-radius:50%;border:2px solid ${MAP_STATUS.danger};`
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
  // Live count of vehicles, read by markerClusterGroup's maxClusterRadius
  // callback so clustering dynamically turns OFF for small fleets (<200) and
  // ON for large ones — without recreating the group.
  const vehicleCountRef = useRef<number>(0);
  // Threshold above which we cluster.  Below this, every truck renders as
  // its own marker at every zoom.
  const CLUSTER_THRESHOLD = 200;

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
  // 30-day safety-event heat layer toggle.  Auto-on when the active
  // persona is Safety so a safety manager opening the map immediately
  // sees where incidents cluster; any persona can still flip it via
  // the side-panel checkbox.  The actual layer lifecycle lives in
  // SafetyEventOverlay — this page just owns the toggle state.
  const { isSafetyView } = useShellConfig();
  const [heatOn, setHeatOn] = useState(isSafetyView);

  // ── Map initialization + polling ──────────────────────────────────────────

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    const Leaf    = window.L as typeof L;
    const LExtra  = window.L as unknown as { markerClusterGroup?: (o?: object) => L.LayerGroup };

    // Use clustering when the plugin loaded successfully, plain layer otherwise
    const group = LExtra.markerClusterGroup
      ? LExtra.markerClusterGroup({
          // Radius is a callback so it adapts to fleet size at runtime.
          //   - Fleet < 200  → radius 0  → MarkerCluster keeps every marker
          //                                separate at every zoom level
          //                                (effectively disables clustering).
          //   - Fleet >= 200 → radius 50 → standard clustering to keep big
          //                                fleets responsive.
          maxClusterRadius: () =>
            vehicleCountRef.current >= CLUSTER_THRESHOLD ? 50 : 0,
          // Keep clustering enabled at city zooms — at z14 a yard with 200
          // trucks renders as 200 separate markers and Leaflet drops frames.
          // z16 (street level) is a better cutoff; spiderfy handles the rest.
          disableClusteringAtZoom: 16,
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

  // The 30-day safety-event heatmap and any persona-specific layers
  // live in pages/live-map/overlays — see their files for behaviour.
  // The host owns ``heatOn`` so the side-panel checkbox stays
  // co-located with the rest of the map controls, but the layer
  // lifecycle (fetch / draw / cleanup) is the overlay's job.

  // ── Filter→map sync ────────────────────────────────────────────────────────
  // When a vehicle is selected: show ONLY that vehicle's marker (focus mode).
  // When no selection: show all that match statusFilter + search.

  useEffect(() => {
    const group = clusterRef.current;
    if (!group) return;
    // Keep cluster radius callback in sync with current fleet size before
    // we re-add markers, otherwise the first render after crossing the
    // threshold uses the stale value.
    vehicleCountRef.current = vehicles.length;
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
  // stops.  The loop interpolates the marker's lat/lng between (fromLat,fromLng)
  // and (toLat,toLng) over `duration` ms, and smoothly rotates the heading icon
  // toward the latest target bearing.
  //
  // GPS fixes (every ~5 s) update the segment endpoints — they don't restart
  // the loop.  Because both endpoints are GPS-confirmed on-road positions, the
  // marker traces the road faithfully on curves: there is no straight-line
  // extrapolation that could overshoot the outside of a turn.

  function startPhysicsLoop(vid: string, _Leaf: typeof L) {
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

      // Linear interpolation along the segment between consecutive GPS fixes.
      // Clamping to [0,1] holds the marker at the latest fix when a new one
      // is overdue (network hiccup) instead of overshooting past it.
      const tNorm = p.duration > 0 ? Math.min(1, (ts - p.startMs) / p.duration) : 1;
      p.lat = p.fromLat + (p.toLat - p.fromLat) * tNorm;
      p.lng = p.fromLng + (p.toLng - p.fromLng) * tNorm;
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
          const nowMs = performance.now();
          vehiclePhysRef.current.set(vid, {
            lat: pos.lat, lng: pos.lng,
            fromLat: pos.lat, fromLng: pos.lng,
            toLat:   pos.lat, toLng:   pos.lng,
            startMs: nowMs,
            duration: 0,             // no segment yet — next fix sets this
            lastFixMs: nowMs,
            headingDeg, targetHeading: headingDeg,
            isMoving: nowMoving,
            engineState: 'Off',      // will be overwritten by the next 30 s full poll
          });
          const color = nowMoving ? MAP_STATUS.ok : MAP_STATUS.danger;
          m.setIcon(makeIcon(Leaf, color, false, pos.speed_mph, headingDeg));
          if (nowMoving) startPhysicsLoop(vid, Leaf);
          return;
        }

        const wasMoving = existing.isMoving;
        existing.isMoving = nowMoving;

        if (nowMoving) {
          // ── Vehicle is moving ───────────────────────────────────────────
          //
          // Heading: prefer the bearing derived from the GPS track (prev fix
          // → current fix) over Samsara's heading field, which can lag on
          // turns.  Track bearing is geometrically exact for the segment we
          // are about to animate, which keeps the icon aligned with motion.
          const moved = distMetres(existing.toLat, existing.toLng, pos.lat, pos.lng);
          const trackBearing = moved > 2
            ? bearingBetween(existing.toLat, existing.toLng, pos.lat, pos.lng)
            : headingDeg;  // fell back to Samsara heading when barely moved
          existing.targetHeading = trackBearing;

          // Rebase the animation segment to start from the current rendered
          // position and end at the new GPS fix.  Duration is the actual time
          // since the last fix (clamped to a sane range so a paused tab or
          // missed poll doesn't produce a jarring instant jump or a glacial
          // crawl).  Marker glides along the GPS-traced path — no straight-
          // line extrapolation that could overshoot road curves.
          const nowMs = performance.now();
          existing.fromLat = existing.lat;
          existing.fromLng = existing.lng;
          existing.toLat   = pos.lat;
          existing.toLng   = pos.lng;
          existing.startMs = nowMs;
          existing.duration = Math.min(8000, Math.max(1500, nowMs - existing.lastFixMs));
          existing.lastFixMs = nowMs;

          // Only rebuild icon when transitioning stopped→moving (avoids flicker)
          if (!wasMoving) {
            m.setIcon(makeIcon(Leaf, MAP_STATUS.ok, false, pos.speed_mph, existing.headingDeg));
            startPhysicsLoop(vid, Leaf);
          }
        } else {
          // ── Vehicle stopped ─────────────────────────────────────────────
          existing.lat = pos.lat;
          existing.lng = pos.lng;
          existing.fromLat = pos.lat;
          existing.fromLng = pos.lng;
          existing.toLat = pos.lat;
          existing.toLng = pos.lng;
          existing.lastFixMs = performance.now();
          // Stop the rAF loop — the isMoving=false check in frame() will exit
          // it but we also cancel explicitly to be safe
          const prevRaf = animFramesRef.current.get(vid);
          if (prevRaf !== undefined) cancelAnimationFrame(prevRaf);
          animFramesRef.current.delete(vid);
          m.setLatLng([pos.lat, pos.lng]);
          if (wasMoving) {
            // engine_state kept fresh from 30 s poll:
            //   'On' or 'Idle' → engine still running → yellow dot
            //   'Off'          → truly stopped        → red dot
            const isIdle = existing.engineState === 'On' || existing.engineState === 'Idle';
            m.setIcon(makeIcon(Leaf, isIdle ? MAP_STATUS.warn : MAP_STATUS.danger, false, 0, null));
          }
        }
      });
    } catch { /* ignore live poll errors silently */ }
  }

  // ── Derived UI state ───────────────────────────────────────────────────────

  // Memo: filter + status counts in a single pass over ``vehicles``.
  // Without the memo this body ran on every render (including every
  // animation frame the parent re-rendered for) and re-walked the
  // fleet twice for one filtered list + one counts object.
  const { filtered, counts } = useMemo(() => {
    const c: Record<string, number> = { all: vehicles.length, moving: 0, idle: 0, stopped: 0 };
    const needle = search.toLowerCase();
    const out: typeof vehicles = [];
    for (const f of vehicles) {
      const status = vehicleStatus(f);
      c[status]++;
      if (statusFilter !== 'all' && status !== statusFilter) continue;
      if (needle && !f.properties.name.toLowerCase().includes(needle)) continue;
      out.push(f);
    }
    return { filtered: out, counts: c };
  }, [vehicles, statusFilter, search]);

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
        <PoiLayerPanel poiHook={poiHook} leafletMap={leafletMap} />
        <MapTypeControl
          mapType={mapType}
          showLabels={showLabels}
          setMapType={setMapType}
          setShowLabels={setShowLabels}
          isReady={isReady}
        />
        {/* Pattern B section host — mounts the active persona's
            overlay layout from LIVE_MAP_LAYOUTS.  Each overlay
            receives the same sectionProps (map handle, vehicles,
            selected, heatOn); overlays that imperatively attach
            Leaflet layers do so via useEffect and render null.  See
            features/live-map/registry.ts for the section list and
            features/live-map/layouts.ts for the per-persona ordering. */}
        <PageLayoutHost
          registry={LIVE_MAP_SECTIONS}
          layouts={LIVE_MAP_LAYOUTS}
          sectionProps={{
            leafletMap,
            isReady,
            vehicles,
            selected,
            heatOn,
          }}
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
          {/* safety-event heat layer toggle */}
          <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={heatOn}
              onChange={(e) => setHeatOn(e.target.checked)}
              className="accent-primary"
            />
            Show 30-day safety heat
          </label>
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
              className="text-primary hover:underline text-xs inline-flex items-center gap-1"
            >
              <ArrowLeft size={12} /> Back
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
                      <span className={`inline-flex items-center gap-1 ${selected.fuel_percent < 15 ? 'text-danger font-semibold' : 'text-muted-foreground'}`}>
                        <Fuel size={14} /> Fuel
                      </span>
                      <span className={`inline-flex items-center gap-1 ${selected.fuel_percent < 15 ? 'text-danger font-semibold' : ''}`}>
                        {Math.round(selected.fuel_percent)}%
                        {selected.fuel_percent < 15 && <TriangleAlert size={12} />}
                      </span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${selected.fuel_percent < 15 ? 'bg-danger' : selected.fuel_percent < 25 ? 'bg-warn' : 'bg-ok'}`}
                        style={{ width: `${Math.max(selected.fuel_percent, 2)}%` }}
                      />
                    </div>
                  </div>
                )}
                {selected.def_percent != null && (
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span className={`inline-flex items-center gap-1 ${selected.def_percent < 15 ? 'text-danger font-semibold' : 'text-muted-foreground'}`}>
                        <FlaskConical size={14} /> DEF
                      </span>
                      <span className={`inline-flex items-center gap-1 ${selected.def_percent < 15 ? 'text-danger font-semibold' : ''}`}>
                        {Math.round(selected.def_percent)}%
                        {selected.def_percent < 15 && <><TriangleAlert size={12} /> DERATE RISK</>}
                      </span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${selected.def_percent < 15 ? 'bg-danger' : selected.def_percent < 25 ? 'bg-warn' : 'bg-ok'}`}
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
                <p className="text-3xs font-semibold text-muted-foreground tracking-wide mb-2">
                  NEAREST POI
                  {routeLoading && (
                    <span className="ml-2 text-3xs text-muted-foreground animate-pulse">routing…</span>
                  )}
                  {activePoiKey && !routeLoading && (
                    <button
                      onClick={clearRouteLine}
                      className="ml-2 text-3xs text-primary hover:underline normal-case font-normal"
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
                              <PoiIcon icon={def.icon} size={12} color={def.color} />
                              <span className="text-3xs font-semibold tracking-wide" style={{ color: def.color }}>
                                {def.label}
                              </span>
                            </div>
                            <p className="text-3xs text-muted-foreground italic pl-4">zoom in to load</p>
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
                            <PoiIcon icon={def.icon} size={12} color={def.color} />
                            <span className="text-3xs font-semibold tracking-wide" style={{ color: def.color }}>
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
                                <Tip key={poiKey} label={isActive ? 'Click to clear route' : 'Click to show route on map'}>
                                <button
                                  onClick={() => handlePoiClick(def.color, [lat, lng], poiKey)}
                                  className={`w-full flex items-center gap-2 text-xs py-0.5 px-1.5 rounded text-left transition-colors ${
                                    isActive
                                      ? 'bg-primary/10 ring-1 ring-primary/30'
                                      : 'hover:bg-muted/60'
                                  }`}
                                >
                                  <span className={`text-3xs shrink-0 ${isActive ? 'text-primary' : 'text-muted-foreground'}`}>
                                    {isActive ? <ArrowRight size={12} /> : `${i + 1}.`}
                                  </span>
                                  <span className="flex-1 truncate">{name}</span>
                                  <span
                                    className="whitespace-nowrap font-medium tabular-nums shrink-0"
                                    style={{ color: def.color }}
                                  >
                                    {distStr}
                                  </span>
                                </button>
                                </Tip>
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
                                className="w-full text-left text-3xs text-primary hover:underline px-1.5 pt-0.5"
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
          <div className="divide-y divide-border">
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
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${warn ? 'ring-2 ring-danger' : ''}`}
                      style={{ background: statusColor(status) }}
                    />
                    <span className="font-medium truncate flex-1">{p.name}</span>
                    {p.fuel_percent != null && p.fuel_percent < 15 && (
                      <Fuel size={12} className="text-danger shrink-0" aria-label="Low fuel" />
                    )}
                    {p.def_percent != null && p.def_percent < 15 && (
                      <FlaskConical size={12} className="text-danger shrink-0" aria-label="Low DEF" />
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground ml-4 truncate">{p.address || '—'}</p>
                  {/* Mini fuel/DEF bars */}
                  {(p.fuel_percent != null || p.def_percent != null) && (
                    <div className="ml-4 mt-1.5 space-y-1">
                      {p.fuel_percent != null && (
                        <div className="flex items-center gap-1">
                          <Fuel size={12} className="w-5 text-muted-foreground shrink-0" />
                          <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${p.fuel_percent < 15 ? 'bg-danger' : p.fuel_percent < 25 ? 'bg-warn' : 'bg-ok'}`}
                              style={{ width: `${Math.max(p.fuel_percent, 2)}%` }}
                            />
                          </div>
                          <span className="text-3xs text-muted-foreground w-7 text-right tabular-nums">
                            {Math.round(p.fuel_percent)}%
                          </span>
                        </div>
                      )}
                      {p.def_percent != null && (
                        <div className="flex items-center gap-1">
                          <FlaskConical size={12} className="w-5 text-muted-foreground shrink-0" />
                          <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${p.def_percent < 15 ? 'bg-danger' : p.def_percent < 25 ? 'bg-warn' : 'bg-ok'}`}
                              style={{ width: `${Math.max(p.def_percent, 2)}%` }}
                            />
                          </div>
                          <span className="text-3xs text-muted-foreground w-7 text-right tabular-nums">
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
