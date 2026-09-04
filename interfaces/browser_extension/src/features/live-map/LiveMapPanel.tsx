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
import { levelsOf } from './levels';
import SourceMarks from './SourceMarks';
import { ageMs, describeAge, formatAge, stalenessOf } from './freshness';
import { getFlag, setFlag } from '../../prefs';
import { directionsUrl, followInGoogleMaps, getFollowPref, openInGoogleMaps, searchUrl, setFollowPref } from './googleMaps';
import type { LiveVehiclesResponse, MapVehicleFeature, MapVehiclesResponse, VehicleStatus } from './types';

const REFRESH_MS = 30_000;
const LIVE_REFRESH_MS = 5_000;
const LIST_OPEN_KEY = 'liveMapListOpen';
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
  // Ages are read against ONE clock per render, so two rows can never
  // disagree by the milliseconds between their own Date.now() calls.
  // The 30-second reload re-renders, which is how the ages advance.
  const now = Date.now();
  // "Follow in Google Maps": a ref as well as state, because marker click
  // handlers are attached once and must read the CURRENT choice.
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  // The map is the point of the panel; the list is the index to it.
  // Collapsing gives the map the whole strip, and the choice sticks.
  const [listOpen, setListOpen] = useState(true);
  // Keep the chosen truck in view as it drives.  Centring once was not
  // enough: a truck at highway speed leaves the frame in a couple of
  // minutes and the panel quietly becomes a map of where it USED to be.
  // The person's own hand wins — one drag and the map stays put.
  const [keepInView, setKeepInView] = useState(true);
  const keepRef = useRef(true);
  const setKeep = (on: boolean) => { keepRef.current = on; setKeepInView(on); };
  const sheetEl = useRef<HTMLDivElement>(null);
  // The latest feature per id — a marker's click handler was attached
  // when the marker was born and must not hand out that first fix.
  const latest = useRef<Map<string, MapVehicleFeature>>(new Map());

  const idOf = (f: MapVehicleFeature) => String(f.properties.id ?? f.properties.name);
  // The live poll runs from an interval closed over the first render, so
  // it reads the selection from a ref rather than stale state.
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selected ? idOf(selected) : null;

  /** Where the vehicle IS right now: the marker, which the 5-second poll
   *  and the physics keep moving — not the 30-second list snapshot. */
  const liveLatLng = (f: MapVehicleFeature): [number, number] => {
    const m = markers.current.get(idOf(f))?.getLatLng();
    if (m) return [m.lat, m.lng];
    const [lng, lat] = f.geometry.coordinates;
    return [lat, lng];
  };

  /** Put a point in the middle of the map a person can actually SEE —
   *  the selected-vehicle card covers the lower strip, so a truck
   *  centred the naive way sits behind it. */
  const centreOn = (lat: number, lng: number, opts: { zoom?: number; animate?: boolean } = {}) => {
    const m = map.current;
    if (!m) return;
    const zoom = opts.zoom ?? m.getZoom();
    const cardH = sheetEl.current?.offsetHeight ?? 0;
    // Push the map's centre DOWN by half the card, so the truck rides
    // that much above it — the same trick as a bottom sheet's inset.
    const pt = m.project([lat, lng], zoom).add([0, cardH / 2]);
    const target = m.unproject(pt, zoom);
    if (opts.zoom != null) m.setView(target, zoom, { animate: opts.animate ?? false });
    else m.panTo(target, { animate: opts.animate ?? true });
  };

  /** One place a vehicle gets selected: the card, the map, and — with
   *  Google Maps in front and follow on — Google's pin. */
  const select = (f: MapVehicleFeature, pan: boolean) => {
    const cur = latest.current.get(idOf(f)) ?? f;
    setSelected(cur);
    setKeep(true);              // a fresh choice always starts centred
    const [lat, lng] = liveLatLng(cur);
    // The card is measured AFTER it renders, so centre on the next frame.
    if (pan) requestAnimationFrame(() => centreOn(lat, lng, { zoom: 14 }));
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
        // The followed truck pulls the map along — a glide every five
        // seconds, not a jump, and never while the person is reading a
        // spot they panned to themselves.
        if (keepRef.current && selectedIdRef.current === vid) {
          centreOn(pos.lat, pos.lng, { animate: true });
        }
      }
    } catch { /* the 30s poll surfaces errors; the fast one stays quiet */ }
  }

  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { zoomControl: false }).setView([39.5, -98.35], 4);
    // Top-right: the selected-vehicle card owns the bottom of the map,
    // and zoom buttons half-hidden behind it are worse than no buttons.
    L.control.zoom({ position: 'topright' }).addTo(m);
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
    // ``dragstart`` fires ONLY for a hand on the map — our own panTo and
    // setView don't raise it.  That makes it the honest signal for "the
    // person took over", with no flag to keep in sync.
    m.on('dragstart', () => { if (keepRef.current) setKeep(false); });
    map.current = m;
    void getFollowPref().then((on) => { setFollow(on); followRef.current = on; });
    void getFlag(LIST_OPEN_KEY, true).then(setListOpen);
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

  // Leaflet caches its container size; after the list opens or closes the
  // map is a different height and would render tiles for the old one.
  useEffect(() => {
    const t = setTimeout(() => map.current?.invalidateSize(), 180);
    return () => clearTimeout(t);
  }, [listOpen]);

  const toggleList = () => {
    const open = !listOpen;
    setListOpen(open);
    void setFlag(LIST_OPEN_KEY, open);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* The map takes whatever the list leaves — all of it when the
          list is collapsed.  The selected vehicle floats over its lower
          edge instead of pushing it up. */}
      <div style={{ position: 'relative', flex: '1 1 auto', minHeight: 200 }}>
        <div ref={mapEl} style={{ position: 'absolute', inset: 0 }} />
        {selected && (() => {
          const [lat, lng] = liveLatLng(selected);
          const levels = levelsOf(selected.properties);
          return (
            <div className="sheet" ref={sheetEl}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {selected.properties.name}
                </strong>
                <div className="row" style={{ gap: 6 }}>
                  {/* On: the map rides along. Off (you panned away): the
                      same control brings it back and re-engages. */}
                  <button className={`chip ${keepInView ? 'on' : ''}`} aria-pressed={keepInView}
                          title={keepInView
                            ? 'The map follows this vehicle — drag the map to stop'
                            : 'Bring this vehicle back into view and follow it again'}
                          onClick={() => {
                            if (keepInView) { setKeep(false); return; }
                            setKeep(true);
                            centreOn(lat, lng, { animate: true });
                          }}>
                    {keepInView ? 'Keeping in view' : 'Keep in view'}
                  </button>
                  <button className="btn" onClick={() => { setKeep(false); setSelected(null); }}>Close</button>
                </div>
              </div>
              <p className="muted" style={{ margin: 0 }}>{selected.properties.address || '—'}</p>
              <SourceMarks sources={selected.properties.sources} source={selected.properties.source} />
              {(() => {
                const age = ageMs(selected.properties.updated_at, now);
                const s = stalenessOf(age);
                const old = s === 'stale' || s === 'very_stale';
                return (
                  <p style={{ margin: 0, fontSize: 12, color: old ? 'var(--warn)' : 'var(--muted)' }}
                     title={describeAge(age)}>
                    {s === 'unknown' ? 'No position time reported' : `Updated ${formatAge(age)} ago`}
                    {s === 'very_stale' && ' — this is not a live position'}
                  </p>
                );
              })()}
              {levels.map((l) => (
                <div key={l.key}>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 12 }}>
                    <span className={l.low ? '' : 'muted'} style={l.low ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{l.label}</span>
                    <span className={l.low ? '' : 'muted'} style={l.low ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{l.pct}%</span>
                  </div>
                  <div className={`bar ${l.low ? 'low' : ''}`}><i style={{ width: `${l.pct}%` }} /></div>
                </div>
              ))}
              <div className="row">
                <button className="btn primary" onClick={() => void openInGoogleMaps(searchUrl(lat, lng))}>Open in Google Maps</button>
                <button className="btn" onClick={() => void openInGoogleMaps(directionsUrl(lat, lng))}>Directions</button>
              </div>
            </div>
          );
        })()}
      </div>
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
      <button type="button" onClick={toggleList} aria-expanded={listOpen}
              className="row"
              style={{ width: '100%', justifyContent: 'space-between', padding: '6px 10px', background: 'none',
                       border: 0, borderBottom: '1px solid var(--border)', color: 'var(--fg)', cursor: 'pointer' }}>
        <span style={{ fontWeight: 600 }}>
          Vehicles <span className="muted" style={{ fontWeight: 400 }}>({filtered.length})</span>
        </span>
        <span className="muted" aria-hidden>{listOpen ? '▾' : '▴'}</span>
      </button>
      <div hidden={!listOpen}
           style={{ flex: '0 1 45%', minHeight: 96, overflowY: 'auto' }}
           role="region" aria-label="Vehicles" tabIndex={0}>
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
                {(() => {
                  // A stale fix says so in the row, so the list can be
                  // scanned for "what is actually reporting" without
                  // opening anything.
                  const s = stalenessOf(ageMs(p.updated_at, now));
                  if (s === 'fresh') return null;
                  const age = ageMs(p.updated_at, now);
                  return (
                    <span style={{ color: 'var(--warn)', fontSize: 12 }} title={describeAge(age)}>
                      {s === 'unknown' ? '· no fix' : `· ${formatAge(age)}`}
                    </span>
                  );
                })()}
                {p.fuel_percent != null && <span className="muted">⛽ {Math.round(p.fuel_percent)}%</span>}
              </div>
              <p className="muted" style={{ margin: '2px 0 0 18px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.address || '—'}</p>
            </button>
          );
        })}
        {/* Rows in the shape of the answer, not the word "Loading" —
            the panel is a strip, and a lone sentence in it reads as an
            empty account rather than a pending request. */}
        {!vehicles.length && !error && Array.from({ length: 6 }, (_, i) => (
          <div key={i} style={{ padding: '10px', borderBottom: '1px solid var(--border)', display: 'grid', gap: 6 }}
               aria-hidden={i > 0} role={i === 0 ? 'status' : undefined}
               aria-label={i === 0 ? 'Loading vehicles' : undefined}>
            <div className="skel" style={{ width: `${45 - i * 3}%` }} />
            <div className="skel" style={{ width: `${80 - i * 5}%`, marginLeft: 18 }} />
          </div>
        ))}
      </div>
    </div>
  );
}
