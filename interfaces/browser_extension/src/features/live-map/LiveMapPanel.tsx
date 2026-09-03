/**
 * Live Map, in the side panel.  The dashboard's map with its list rail,
 * on bundled Leaflet + OpenStreetMap (v1, free), plus the two things
 * Google does better handed off to Google: satellite/Street View and
 * directions.  v2 adds Google's engine as a Map Type without touching
 * the list, the physics or the polls.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import { apiJSON } from '../../api/client';
import { makeIcon } from './icons';
import { applyFix, hasLowLevelWarning, positionAt, shortestAngleDiff, statusColor, vehicleStatus, MAP_STATUS, type Phys } from './physics';
import { FALLBACK, TILES, shouldFallBack } from './tiles';
import { directionsUrl, followInGoogleMaps, getFollowPref, openInGoogleMaps, searchUrl, setFollowPref } from './googleMaps';
import type { LiveVehiclesResponse, MapVehicleFeature, MapVehiclesResponse, VehicleStatus } from './types';

const REFRESH_MS = 30_000;
const LIVE_REFRESH_MS = 5_000;
type Filter = 'all' | VehicleStatus;

export default function LiveMapPanel() {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const markers = useRef<Map<string, L.Marker>>(new Map());
  const phys = useRef<Map<string, Phys>>(new Map());
  const frames = useRef<Map<string, number>>(new Map());
  const [vehicles, setVehicles] = useState<MapVehicleFeature[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<MapVehicleFeature | null>(null);
  const [error, setError] = useState('');
  const [tileNotice, setTileNotice] = useState('');
  // "Follow in Google Maps": a ref as well as state, because marker click
  // handlers are attached once and must read the CURRENT choice.
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  // The latest feature per id — a marker's click handler was attached
  // when the marker was born and must not hand out that first fix.
  const latest = useRef<Map<string, MapVehicleFeature>>(new Map());

  const idOf = (f: MapVehicleFeature) => String(f.properties.id ?? f.properties.name);

  /** Where the vehicle IS right now: the marker, which the 5-second poll
   *  and the physics keep moving — not the 30-second list snapshot. */
  const liveLatLng = (f: MapVehicleFeature): [number, number] => {
    const m = markers.current.get(idOf(f))?.getLatLng();
    if (m) return [m.lat, m.lng];
    const [lng, lat] = f.geometry.coordinates;
    return [lat, lng];
  };

  /** One place a vehicle gets selected: the card, the map, and — with
   *  Google Maps in front and follow on — Google's pin. */
  const select = (f: MapVehicleFeature, pan: boolean) => {
    const cur = latest.current.get(idOf(f)) ?? f;
    setSelected(cur);
    const [lat, lng] = liveLatLng(cur);
    if (pan) map.current?.setView([lat, lng], 14);
    if (followRef.current) void followInGoogleMaps(searchUrl(lat, lng));
  };
  const selectRef = useRef(select);
  selectRef.current = select;

  const toggleFollow = () => {
    const on = !follow;
    setFollow(on); followRef.current = on;
    void setFollowPref(on);
  };

  // ── physics loop: one rAF per moving truck ──
  function startLoop(vid: string) {
    const prev = frames.current.get(vid);
    if (prev !== undefined) cancelAnimationFrame(prev);
    const frame = (ts: number) => {
      const p = phys.current.get(vid), m = markers.current.get(vid);
      if (!p || !m || !p.isMoving) { frames.current.delete(vid); return; }
      const at = positionAt(p, ts);
      p.lat = at.lat; p.lng = at.lng;
      m.setLatLng([p.lat, p.lng]);
      const diff = shortestAngleDiff(p.headingDeg, p.targetHeading);
      if (Math.abs(diff) > 0.2) {
        p.headingDeg += diff * 0.06;
        m.getElement()?.querySelector('polygon')?.setAttribute('transform', `rotate(${p.headingDeg},9,9)`);
      }
      frames.current.set(vid, requestAnimationFrame(frame));
    };
    frames.current.set(vid, requestAnimationFrame(frame));
  }

  async function loadVehicles() {
    try {
      const data = await apiJSON<MapVehiclesResponse>('/map/vehicles');
      const seen = new Set<string>();
      for (const f of data.features ?? []) {
        const id = idOf(f); seen.add(id);
        latest.current.set(id, f);
        const [lng, lat] = f.geometry.coordinates;
        const p = phys.current.get(id);
        if (p) p.engineState = f.properties.engine_state ?? 'Off';
        const icon = makeIcon(L, statusColor(vehicleStatus(f)), hasLowLevelWarning(f.properties),
                              f.properties.speed_mph ?? 0, p?.headingDeg ?? f.properties.heading);
        const existing = markers.current.get(id);
        if (existing) {
          if (!p?.isMoving) existing.setLatLng([lat, lng]);
          existing.setIcon(icon);
        } else if (map.current) {
          const m = L.marker([lat, lng], { icon }).addTo(map.current).on('click', () => selectRef.current(f, false));
          markers.current.set(id, m);
        }
      }
      markers.current.forEach((m, id) => { if (!seen.has(id)) { m.remove(); markers.current.delete(id); latest.current.delete(id); } });
      setVehicles(data.features ?? []);
      setError('');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not load vehicles';
      // A 403 here is the permission gate: this role has no live map,
      // or the token was minted before the server renamed the scope.
      // Both end the same way — connect again.
      setError(/insufficient permissions|scoped to the live map/i.test(msg)
        ? 'This connection cannot read the live map — your role may not include it, or the connection is out of date. Disconnect and connect again.'
        : msg);
    }
  }

  async function livePoll() {
    try {
      const data = await apiJSON<LiveVehiclesResponse>('/map/vehicles/live');
      const now = performance.now();
      for (const [vid, pos] of Object.entries(data.positions ?? {})) {
        const m = markers.current.get(vid);
        if (!m) continue;
        const { phys: next, started, stopped } = applyFix(
          phys.current.get(vid), pos.lat, pos.lng, pos.speed_mph, pos.heading, now);
        phys.current.set(vid, next);
        if (started) {
          m.setIcon(makeIcon(L, MAP_STATUS.ok, false, pos.speed_mph, next.headingDeg));
          startLoop(vid);
        } else if (stopped) {
          const f = frames.current.get(vid);
          if (f !== undefined) { cancelAnimationFrame(f); frames.current.delete(vid); }
          m.setLatLng([pos.lat, pos.lng]);
          const idle = next.engineState === 'On' || next.engineState === 'Idle';
          m.setIcon(makeIcon(L, idle ? MAP_STATUS.warn : MAP_STATUS.danger, false, 0, null));
        } else if (!next.isMoving) {
          m.setLatLng([pos.lat, pos.lng]);
        }
      }
    } catch { /* the 30s poll surfaces errors; the fast one stays quiet */ }
  }

  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { zoomControl: false }).setView([39.5, -98.35], 4);
    L.control.zoom({ position: 'bottomright' }).addTo(m);
    const t = TILES.standard;
    const tiles = L.tileLayer(t.url, { attribution: t.attr, maxZoom: t.maxZoom }).addTo(m);
    // A grey map is a failed source, not a slow one: count this view's
    // errors against its loads and switch sources once, out loud.
    let errors = 0, loads = 0, fellBack = false;
    tiles.on('tileload', () => { loads += 1; });
    tiles.on('tileerror', () => {
      errors += 1;
      if (fellBack || !shouldFallBack(errors, loads)) return;
      fellBack = true;
      tiles.remove();
      L.tileLayer(FALLBACK.url, { attribution: FALLBACK.attr, maxZoom: FALLBACK.maxZoom }).addTo(m);
      setTileNotice('OpenStreetMap is not answering from here — showing Esri street tiles.');
    });
    m.on('movestart', () => { if (!fellBack) { errors = 0; loads = 0; } });
    map.current = m;
    void getFollowPref().then((on) => { setFollow(on); followRef.current = on; });
    // The cleanup reads the SAME maps this effect created, so hold them
    // in locals — React warns that a ref may point elsewhere by then.
    const framesMap = frames.current, physMap = phys.current, markerMap = markers.current, latestMap = latest.current;
    void loadVehicles();
    const a = setInterval(loadVehicles, REFRESH_MS);
    const b = setInterval(livePoll, LIVE_REFRESH_MS);
    return () => {
      clearInterval(a); clearInterval(b);
      framesMap.forEach((id) => cancelAnimationFrame(id));
      framesMap.clear(); physMap.clear(); markerMap.clear(); latestMap.clear();
      m.remove(); map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => vehicles.filter((f) =>
    (filter === 'all' || vehicleStatus(f) === filter) &&
    (!search || f.properties.name.toLowerCase().includes(search.toLowerCase()))),
  [vehicles, filter, search]);
  const count = (s: Filter) => s === 'all' ? vehicles.length : vehicles.filter((f) => vehicleStatus(f) === s).length;

  const focus = (f: MapVehicleFeature) => select(f, true);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div ref={mapEl} style={{ height: '40%', minHeight: 180 }} />
      <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', display: 'grid', gap: 6 }}>
        <input className="input" placeholder="Search vehicles…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
          {(['all', 'moving', 'idle', 'stopped'] as Filter[]).map((s) => (
            <button key={s} className={`chip ${filter === s ? 'on' : ''}`} onClick={() => setFilter(s)}>
              {s[0].toUpperCase() + s.slice(1)} ({count(s)})
            </button>
          ))}
          <button className={`chip ${follow ? 'on' : ''}`} onClick={toggleFollow} aria-pressed={follow}
                  title="With Google Maps in front, selecting a vehicle moves Google's pin to it">
            Follow in Google Maps
          </button>
        </div>
        {error && <p style={{ color: 'var(--danger)', margin: 0 }}>{error}</p>}
        {tileNotice && <p className="muted" style={{ margin: 0 }}>{tileNotice}</p>}
      </div>
      {selected && (() => {
        const [lat, lng] = liveLatLng(selected);
        return (
          <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', display: 'grid', gap: 6 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{selected.properties.name}</strong>
              <button className="btn" onClick={() => setSelected(null)}>Close</button>
            </div>
            <p className="muted" style={{ margin: 0 }}>{selected.properties.address || '—'}</p>
            <div className="row">
              <button className="btn primary" onClick={() => void openInGoogleMaps(searchUrl(lat, lng))}>Open in Google Maps</button>
              <button className="btn" onClick={() => void openInGoogleMaps(directionsUrl(lat, lng))}>Directions</button>
            </div>
          </div>
        );
      })()}
      <div style={{ flex: 1, overflowY: 'auto' }} role="region" aria-label="Vehicles" tabIndex={0}>
        {filtered.map((f) => {
          const p = f.properties, status = vehicleStatus(f), warn = hasLowLevelWarning(p);
          return (
            <button key={idOf(f)} onClick={() => focus(f)}
              style={{ width: '100%', textAlign: 'left', padding: '8px 10px', background: 'none', border: 0,
                       borderBottom: '1px solid var(--border)', color: 'var(--fg)', cursor: 'pointer', minHeight: 24 }}>
              <div className="row">
                <span style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, background: statusColor(status),
                               boxShadow: warn ? `0 0 0 2px ${MAP_STATUS.danger}` : undefined }} />
                <span style={{ fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                {p.fuel_percent != null && <span className="muted">⛽ {Math.round(p.fuel_percent)}%</span>}
              </div>
              <p className="muted" style={{ margin: '2px 0 0 18px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.address || '—'}</p>
            </button>
          );
        })}
        {!vehicles.length && !error && <p className="muted" style={{ padding: 12 }}>Loading vehicles…</p>}
      </div>
    </div>
  );
}
