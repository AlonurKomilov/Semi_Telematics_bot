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
import { linksFor, type ProviderLink } from './links';
import { ageMs, describeAge, formatAge, stalenessOf } from './freshness';
import { getFlag, setFlag } from '../../prefs';
import { directionsUrl, followInGoogleMaps, getFollowPref, markFollowWarned, openInGoogleMaps, searchUrl, setFollowPref, wasFollowWarned } from './googleMaps';
import type { LiveVehiclesResponse, MapVehicleFeature, MapVehiclesResponse, VehicleStatus } from './types';

const REFRESH_MS = 30_000;
const LIVE_REFRESH_MS = 5_000;
const LIST_OPEN_KEY = 'liveMapListOpen';
/** The filter is a working preference, not a fresh decision every time:
 *  a dispatcher who watches Moving watched it yesterday too. */
const FILTER_KEY = 'liveMapFilter';
/** A vehicle chosen on google.com/maps, waiting for the panel to open.
 *  Storage rather than a message, so it works whether the panel was
 *  already open or is opening because of that very click. */
const PENDING_SELECT_KEY = 'pendingSelectVehicle';
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
  /** Whether the vehicle list has been ANSWERED, not whether it has rows.
   *  Without it an empty answer and a pending request draw the same
   *  thing — six shimmering skeleton rows — so a person whose account
   *  admits them no vehicle waits forever on a panel that is already
   *  finished.  A driver holding no truck assignment is exactly that
   *  person, and the vehicle wall now answers them with an empty list
   *  rather than the whole account. */
  const [answered, setAnswered] = useState(false);
  const [tileNotice, setTileNotice] = useState('');
  // The selected truck's provider links, fetched once per truck.
  const [links, setLinks] = useState<ProviderLink[]>([]);
  // Ages are read against ONE clock per render, so two rows can never
  // disagree by the milliseconds between their own Date.now() calls.
  // The 30-second reload re-renders, which is how the ages advance.
  const now = Date.now();
  // "Follow in Google Maps": a ref as well as state, because marker click
  // handlers are attached once and must read the CURRENT choice.
  const [follow, setFollow] = useState(false);
  const followRef = useRef(false);
  /** Shown once, the first time following is switched on: it replaces
   *  what is open in the person's Google Maps tab, and that is worth
   *  one sentence before it happens rather than an apology after. */
  const [followNotice, setFollowNotice] = useState('');
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
  // The latest feature per id — a marker's click handler was attached
  // when the marker was born and must not hand out that first fix.
  const latest = useRef<Map<string, MapVehicleFeature>>(new Map());

  const idOf = (f: MapVehicleFeature) => String(f.properties.id ?? f.properties.name);
  // The live poll runs from an interval closed over the first render, so
  // it reads the selection from a ref rather than stale state.
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selected ? idOf(selected) : null;
  const selectedRef = useRef<MapVehicleFeature | null>(null);
  selectedRef.current = selected;

  /** Where the vehicle IS right now: the marker, which the 5-second poll
   *  and the physics keep moving — not the 30-second list snapshot. */
  const liveLatLng = (f: MapVehicleFeature): [number, number] => {
    const m = markers.current.get(idOf(f))?.getLatLng();
    if (m) return [m.lat, m.lng];
    const [lng, lat] = f.geometry.coordinates;
    return [lat, lng];
  };

  /** Centre the map on a point.  Plain centring is correct now that the
   *  selected-vehicle card sits BELOW the map rather than over it —
   *  this used to offset by half the card's height to keep the truck
   *  out from behind it. */
  const centreOn = (lat: number, lng: number, opts: { zoom?: number; animate?: boolean } = {}) => {
    const m = map.current;
    if (!m) return;
    if (opts.zoom != null) m.setView([lat, lng], opts.zoom, { animate: opts.animate ?? false });
    else m.panTo([lat, lng], { animate: opts.animate ?? true });
  };

  /** One place a vehicle gets selected: the card, the map, and — with
   *  Google Maps in front and follow on — Google's pin. */
  const select = (f: MapVehicleFeature, pan: boolean) => {
    const cur = latest.current.get(idOf(f)) ?? f;
    setSelected(cur);
    // Links belong to the truck, not the selection: clear first so the
    // previous truck's door never appears under this one's name.
    setLinks([]);
    const rid = cur.properties.registry_id;
    void linksFor(rid).then((ls) => {
      // Ignore an answer that arrived after the person moved on.
      if (selectedIdRef.current === idOf(cur)) setLinks(ls);
    });
    setKeep(true);              // a fresh choice always starts centred
    const [lat, lng] = liveLatLng(cur);
    if (pan) centreOn(lat, lng, { zoom: 14 });
    if (followRef.current) void followInGoogleMaps(searchUrl(lat, lng));
  };
  const selectRef = useRef(select);
  selectRef.current = select;

  const toggleFollow = () => {
    const on = !follow;
    setFollow(on); followRef.current = on;
    void setFollowPref(on);
    if (!on) { setFollowNotice(''); return; }
    void wasFollowWarned().then((warned) => {
      if (warned) return;
      setFollowNotice('Selecting a vehicle will replace whatever is open in your Google Maps tab.');
      void markFollowWarned();
    });
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
      setAnswered(true);
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
    void chrome.storage.local.get(FILTER_KEY).then((got) => {
      const f = got[FILTER_KEY];
      if (f === 'all' || f === 'moving' || f === 'idle' || f === 'stopped') setFilter(f);
    });
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

  // The search narrows the SET; the status chips slice what the search
  // left.  Counting them off different sets put "All (100)" above a list
  // of three — two numbers for one thing, on one screen.
  const searched = useMemo(() => vehicles.filter((f) =>
    !search || f.properties.name.toLowerCase().includes(search.toLowerCase())),
  [vehicles, search]);
  const filtered = useMemo(() => searched.filter((f) =>
    filter === 'all' || vehicleStatus(f) === filter),
  [searched, filter]);
  const count = (s: Filter) => s === 'all' ? searched.length : searched.filter((f) => vehicleStatus(f) === s).length;

  const chooseFilter = (s: Filter) => {
    setFilter(s);
    void chrome.storage.local.set({ [FILTER_KEY]: s });
  };

  const focus = (f: MapVehicleFeature) => select(f, true);

  // A marker clicked on google.com/maps: the overlay writes the id and
  // the panel opens on it.  Through storage rather than a message so it
  // works either way round — the panel already open, or opening because
  // of that very click and mounting after the message would have gone.
  useEffect(() => {
    const take = (id: unknown) => {
      if (typeof id !== 'string' || !id) return;
      const f = vehicles.find((v) => idOf(v) === id);
      if (!f) return;
      void chrome.storage.local.remove(PENDING_SELECT_KEY);
      select(f, true);
    };
    void chrome.storage.local.get(PENDING_SELECT_KEY).then((got) => take(got[PENDING_SELECT_KEY]));
    const onChange = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && PENDING_SELECT_KEY in changes) take(changes[PENDING_SELECT_KEY].newValue);
    };
    chrome.storage.onChanged.addListener(onChange);
    return () => chrome.storage.onChanged.removeListener(onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles]);

  // Leaflet caches its container size, and TWO things change it: the
  // list opening or closing, and a vehicle being selected — the card
  // appears BELOW the map and shortens it.  Selecting centred the truck
  // against the taller container it was about to stop being, so the
  // truck ended up sitting low in the map instead of in the middle.
  // Re-measure once the layout has settled, then put it back.
  const cardShown = selected != null;
  useEffect(() => {
    const t = setTimeout(() => {
      const m = map.current;
      if (!m) return;
      m.invalidateSize();
      if (keepRef.current && selectedRef.current) {
        const [lat, lng] = liveLatLng(selectedRef.current);
        centreOn(lat, lng, { animate: false });
      }
    }, 180);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listOpen, cardShown]);

  const toggleList = () => {
    const open = !listOpen;
    setListOpen(open);
    void setFlag(LIST_OPEN_KEY, open);
  };

  return (
    // Regions are separated by AIR, not by a hairline: the column had no
    // gap at all while the search block had 6px inside it, so the space
    // within a group exceeded the space between groups and the whole
    // panel read as one flat run.  8 between, 6 within, 1 between rows.
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 8 }}>
      {/* The map and the list SHARE what the fixed rows leave, and the
          map takes the larger share.  With a flex-basis of auto the map
          was the only thing that could give, so selecting a vehicle
          squeezed it to its floor: the card, the search row, the chips
          and the list header are all fixed-height, and the list was
          holding 45%.  Now both give, in proportion. */}
      {/* ONE region absorbs change, and it is the map.  Both the map and
          the list used to be elastic, so a vehicle being selected — the
          card is ~140px — pushed the search box, the chips, the switch,
          the list header and every row downward.  Selecting is a rapid
          sequence; a layout that walks under it compounds. */}
      <div style={{ position: 'relative', flex: '1 1 auto', minHeight: 220 }}>
        <div ref={mapEl} style={{ position: 'absolute', inset: 0 }} />
      </div>
      {/* The selected vehicle sits BELOW the map, not over it: it grew
          from three lines to seven, and by then it was hiding more of
          the map than the map could spare — including, often, the very
          truck it describes. */}
      {selected && (() => {
          const [lat, lng] = liveLatLng(selected);
          const levels = levelsOf(selected.properties);
          return (
            <div className="sheet">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="row" style={{ gap: 6, minWidth: 0 }}>
                  <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {selected.properties.name}
                  </strong>
                  {/* Who supplies this truck, next to what it is called. */}
                  <SourceMarks sources={selected.properties.sources} source={selected.properties.source} links={links} />
                </span>
                <div className="row" style={{ gap: 6 }}>
                  {/* On: the map rides along. Off (you panned away): the
                      same control brings it back and re-engages. */}
                  {/* A pressed STATE, not an action: filled primary is
                      reserved for the one thing this card is for, and
                      two filled-blue controls with opposite roles side
                      by side made neither legible.  Same .btn size as
                      Close beside it. */}
                  <button className="btn" aria-pressed={keepInView}
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
      <div style={{ padding: '0 10px', display: 'grid', gap: 6 }}>
        <input className="input" placeholder="Search vehicles…" value={search} onChange={(e) => setSearch(e.target.value)} />
        {/* Two different kinds of control, so two different shapes.  One
            row of identical pills made a status FILTER and a behaviour
            SWITCH look like siblings: picking "Moving" narrows a list,
            pressing "Follow" changes what a Google Maps tab does, and
            the eye could not tell which was which. */}
        <div className="row" role="radiogroup" aria-label="Filter by status" style={{ flexWrap: 'wrap', gap: 6 }}>
          {(['all', 'moving', 'idle', 'stopped'] as Filter[]).map((s) => (
            <button key={s} className={`chip ${filter === s ? 'on' : ''}`} role="radio" aria-checked={filter === s}
                    onClick={() => chooseFilter(s)}>
              {s[0].toUpperCase() + s.slice(1)} ({count(s)})
            </button>
          ))}
        </div>
        <label className="row" style={{ gap: 8, cursor: 'pointer', minHeight: 24, padding: '2px 0' }}
               title="With Google Maps in front, selecting a vehicle replaces what is open in that tab">
          <input type="checkbox" role="switch" checked={follow} onChange={toggleFollow} />
          <span className="small">Follow in Google Maps</span>
        </label>
        {followNotice && <p className="muted small" style={{ margin: 0 }}>{followNotice}</p>}
        {error && <p style={{ color: 'var(--danger)', margin: 0 }}>{error}</p>}
        {tileNotice && <p className="muted" style={{ margin: 0 }}>{tileNotice}</p>}
      </div>
      {/* Header and rows are ONE region — one border, one fold — so the
          column's gap never lands between a group's name and its
          members.  The list gives no space when the card appears: it is
          the only thing here that already scrolls. */}
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0,
                    flex: listOpen ? '0 1 240px' : '0 0 auto',
                    borderTop: '1px solid var(--border)' }}>
        <button type="button" onClick={toggleList} aria-expanded={listOpen}
                className="row rowbtn"
                style={{ width: '100%', justifyContent: 'space-between', padding: '6px 10px',
                         background: 'var(--card)', border: 0, color: 'var(--fg)', cursor: 'pointer' }}>
          {/* A header that shares its rows' surface, padding and border
              reads as their first row.  A fill step says it owns them. */}
          <span style={{ fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>
            Vehicles <span className="muted" style={{ fontWeight: 400 }}>({filtered.length})</span>
          </span>
          <span className="muted" aria-hidden>{listOpen ? '▾' : '▴'}</span>
        </button>
        <div hidden={!listOpen}
             style={{ flex: 1, minHeight: 80, overflowY: 'auto' }}
             role="region" aria-label="Vehicles" tabIndex={0}>
        {filtered.map((f) => {
          const p = f.properties, status = vehicleStatus(f), warn = hasLowLevelWarning(p);
          return (
            <button key={idOf(f)} onClick={() => focus(f)} className="rowbtn"
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
        {/* A filter or a search emptied the list — which is NOT "you have
            no vehicles".  Saying nothing here sent people away believing
            their account was empty, so the constraint is named and the
            way out is one press. */}
        {!filtered.length && !!vehicles.length && (
          <div style={{ padding: '20px 12px', display: 'grid', gap: 6, justifyItems: 'center', textAlign: 'center' }}>
            <strong style={{ fontSize: 13 }}>No vehicles match</strong>
            <span className="muted small">
              {[filter !== 'all' ? `Status: ${filter}` : '', search ? `Search: “${search}”` : '']
                .filter(Boolean).join(' · ')}
            </span>
            <button className="btn" style={{ marginTop: 4 }}
                    onClick={() => { chooseFilter('all'); setSearch(''); }}>
              Clear filters
            </button>
          </div>
        )}
        {!vehicles.length && !error && answered && (
          <div style={{ padding: '20px 12px', display: 'grid', gap: 6, justifyItems: 'center', textAlign: 'center' }}>
            <strong style={{ fontSize: 13 }}>No vehicles to show</strong>
            <span className="muted small">
              Your 4truck account has not given this sign-in any vehicles yet. Ask whoever
              manages your account to assign one, then reopen the panel.
            </span>
          </div>
        )}
        {!vehicles.length && !error && !answered && Array.from({ length: 6 }, (_, i) => (
          <div key={i} style={{ padding: '10px', borderBottom: '1px solid var(--border)', display: 'grid', gap: 6 }}
               aria-hidden={i > 0} role={i === 0 ? 'status' : undefined}
               aria-label={i === 0 ? 'Loading vehicles' : undefined}>
            <div className="skel" style={{ width: `${45 - i * 3}%` }} />
            <div className="skel" style={{ width: `${80 - i * 5}%`, marginLeft: 18 }} />
          </div>
        ))}
        </div>
      </div>
    </div>
  );
}
