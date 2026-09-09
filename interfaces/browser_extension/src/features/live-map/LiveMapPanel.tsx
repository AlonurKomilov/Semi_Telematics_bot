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
import { iconSignature, makeIcon } from './icons';
import { applyFix, faceOf, hasLowLevelWarning, positionAt, shortestAngleDiff, statusColor, vehicleStatus, MAP_STATUS, type Phys } from './physics';
import { FALLBACK, TILES, shouldFallBack } from './tiles';
import { LOW_LEVEL_PCT, levelsOf } from './levels';
import SourceMarks from './SourceMarks';
import { linksFor, type ProviderLink } from './links';
import { forgetVehicle, inventoryFor, setItemStatus, verifyItem, type Onboard } from '../inventory/data';
import ItemRows from '../inventory/ItemRows';
import type { PanelFeatureProps } from '../../shell/registry';
import { PANEL_LIVE, PENDING_SELECT_KEY, readPendingSelect, sharedOrOwn,
         type PanelLiveReply } from '../maps-overlay/bridge';
import { ageMs, describeAge, formatAge, stalenessOf } from './freshness';
import { getFlag, setFlag } from '../../prefs';
import { DASHBOARD_BASE } from '../../connect';
import { directionsUrl, followInGoogleMaps, getFollowPref, markFollowWarned, openInGoogleMaps, searchUrl, setFollowPref, wasFollowWarned } from './googleMaps';
import type { LiveVehiclesResponse, MapVehicleFeature, MapVehiclesResponse, VehicleStatus } from './types';

const REFRESH_MS = 30_000;
const LIVE_REFRESH_MS = 5_000;
const LIST_OPEN_KEY = 'liveMapListOpen';
/** Whether the selected vehicle is shown in full or folded to one line.
 *  Remembered, like the list's own fold: somebody who works from the
 *  search box and the filters folded this away on purpose, and having
 *  to fold it again every morning is how a panel starts feeling
 *  disposable. */
const CARD_OPEN_KEY = 'liveMapCardOpen';
/** The folded part, named so the control that folds it can say so. */
const CARD_BODY_ID = 'live-map-vehicle-detail';
/** What is aboard the truck.  Folded by DEFAULT, unlike the card: the
 *  card answers "where is it and can it get there", which is why the
 *  panel is open; this answers "is the dashcam still on it", which is
 *  asked sometimes.  A section that arrives expanded would give back
 *  the height the card's own fold was built to save. */
const INV_OPEN_KEY = 'liveMapOnboardOpen';
const INV_BODY_ID = 'live-map-vehicle-onboard';
/** The filter is a working preference, not a fresh decision every time:
 *  a dispatcher who watches Moving watched it yesterday too. */
const FILTER_KEY = 'liveMapFilter';
type Filter = 'all' | VehicleStatus;

export default function LiveMapPanel({ abilities }: PanelFeatureProps) {
  // The same two verbs the Inventory feature offers.  Somebody peeking
  // at the map card who sees "Dashcam — Missing" should not have to
  // switch features to say so.
  const canWriteInventory = abilities.includes('inventory.write');
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const markers = useRef<Map<string, L.Marker>>(new Map());
  const phys = useRef<Map<string, Phys>>(new Map());
  /** ONE loop for every moving truck.  This used to be one loop per
   *  truck: thirty moving vehicles scheduled thirty callbacks a frame,
   *  each of them asking the DOM for its own arrow again. */
  const frame = useRef<number | null>(null);
  /** The picture each marker is currently wearing, so an unchanged one
   *  is left alone instead of being rebuilt. */
  const iconKeys = useRef<Map<string, string>>(new Map());
  /** Each marker's arrow, found once per element rather than once per
   *  frame.  ``setIcon`` replaces the element, so the element it was
   *  read from is part of the entry. */
  const arrows = useRef<Map<string, { host: HTMLElement; poly: Element | null }>>(new Map());
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
  // …and what is aboard it.  null covers three cases that all mean the
  // same thing for the surface — not asked yet, not permitted, not
  // answered — so the section simply is not there.
  const [onboard, setOnboard] = useState<Onboard | null>(null);
  const [invOpen, setInvOpen] = useState(false);
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
  const [cardOpen, setCardOpen] = useState(true);
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
  /** Whether each truck is low on fuel or DEF — from the list, which is
   *  the only feed that carries levels, so the fast poll can keep the
   *  warning ring instead of dropping it until the next refresh. */
  const warns = useRef<Map<string, boolean>>(new Map());

  const idOf = (f: MapVehicleFeature) => String(f.properties.id ?? f.properties.name);
  // The live poll runs from an interval closed over the first render, so
  // it reads the selection from a ref rather than stale state.
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selected ? idOf(selected) : null;
  const selectedRef = useRef<MapVehicleFeature | null>(null);
  selectedRef.current = selected;

  /** After a write: this truck's cached contents are a minute out of
   *  date the moment somebody flags an item, so they are dropped and
   *  read again rather than left to expire on their own. */
  const refreshOnboard = async (registryId: number | null | undefined) => {
    forgetVehicle(registryId);
    const ob = await inventoryFor(registryId);
    if (selectedRef.current?.properties.registry_id === registryId) setOnboard(ob);
  };

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
    // Same rule for what is aboard: clear first, and drop an answer that
    // belongs to a truck the person has already left.
    setOnboard(null);
    void inventoryFor(rid).then((ob) => {
      if (selectedIdRef.current === idOf(cur)) setOnboard(ob);
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

  // ── physics loop: ONE rAF for the whole fleet ──

  /** Turn a truck's arrow without asking the DOM to find it again. */
  function aimArrow(vid: string, m: L.Marker, deg: number) {
    const host = m.getElement();
    if (!host) return;
    let cached = arrows.current.get(vid);
    if (!cached || cached.host !== host) {
      cached = { host, poly: host.querySelector('polygon') };
      arrows.current.set(vid, cached);
    }
    cached.poly?.setAttribute('transform', `rotate(${deg},9,9)`);
  }

  /** Give a marker a picture only when the picture actually changes.
   *  Leaflet's setIcon destroys and rebuilds the element, so calling it
   *  on every refresh rebuilt the whole fleet twice a minute. */
  function applyIcon(vid: string, m: L.Marker, colour: string, warn: boolean,
                     moving: boolean, heading: number | null) {
    const key = iconSignature(colour, warn, moving);
    if (iconKeys.current.get(vid) === key) return;
    iconKeys.current.set(vid, key);
    m.setIcon(makeIcon(L, colour, warn, moving ? 1 : 0, heading));
    arrows.current.delete(vid);                 // the element is a new one
    if (moving && heading != null) aimArrow(vid, m, heading);
  }

  function pump(ts: number) {
    frame.current = null;
    let moving = false;
    for (const [vid, p] of phys.current) {
      if (!p.isMoving) continue;
      const m = markers.current.get(vid);
      if (!m) continue;
      moving = true;
      const at = positionAt(p, ts);
      p.lat = at.lat; p.lng = at.lng;
      m.setLatLng([p.lat, p.lng]);
      const diff = shortestAngleDiff(p.headingDeg, p.targetHeading);
      if (Math.abs(diff) > 0.2) {
        p.headingDeg += diff * 0.06;
        aimArrow(vid, m, p.headingDeg);
      }
    }
    // Stops the moment nothing is moving, so a parked fleet costs
    // nothing at all until the next fix says otherwise.
    if (moving) frame.current = requestAnimationFrame(pump);
  }
  function ensurePump() {
    if (frame.current === null) frame.current = requestAnimationFrame(pump);
  }

  async function loadVehicles() {
    try {
      const data = await apiJSON<MapVehiclesResponse>('/map/vehicles');
      // One row per provider id even if the feed repeats one: rows are
      // keyed by that id, and React answers a duplicate key by leaving
      // ghost rows behind that survive every filter — the "229" that
      // appeared three times under a search for "43".  First wins,
      // which is the row the server ranks first too.
      const unique: MapVehicleFeature[] = [];
      const seenIds = new Set<string>();
      for (const f of data.features ?? []) {
        const id = idOf(f);
        if (seenIds.has(id)) continue;
        seenIds.add(id);
        unique.push(f);
      }
      const seen = new Set<string>();
      for (const f of unique) {
        const id = idOf(f); seen.add(id);
        latest.current.set(id, f);
        const [lng, lat] = f.geometry.coordinates;
        const p = phys.current.get(id);
        if (p) p.engineState = f.properties.engine_state ?? 'Off';
        const warn = hasLowLevelWarning(f.properties);
        warns.current.set(id, warn);
        const { colour, moving } = faceOf(vehicleStatus(f), p);
        const heading = p?.headingDeg ?? f.properties.heading ?? null;
        const existing = markers.current.get(id);
        if (existing) {
          if (!p?.isMoving) existing.setLatLng([lat, lng]);
          applyIcon(id, existing, colour, warn, moving, heading);
        } else if (map.current) {
          const icon = makeIcon(L, colour, warn, moving ? 1 : 0, heading);
          const m = L.marker([lat, lng], { icon }).addTo(map.current).on('click', () => selectRef.current(f, false));
          markers.current.set(id, m);
          iconKeys.current.set(id, iconSignature(colour, warn, moving));
        }
      }
      markers.current.forEach((m, id) => {
        if (seen.has(id)) return;
        m.remove();
        markers.current.delete(id); latest.current.delete(id);
        iconKeys.current.delete(id); arrows.current.delete(id); warns.current.delete(id);
        phys.current.delete(id);
      });
      setVehicles(unique);
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

  /** The positions, through the worker when it can answer and straight
   *  from the API when it cannot.
   *
   *  Through the worker because the overlay on google.com/maps asks the
   *  same question on its own clock: one answer serves both, and both
   *  then show the same instant.  Straight from the API otherwise —
   *  sharing is an economy, and an economy must never be the reason a
   *  map stops moving. */
  async function livePositions(): Promise<LiveVehiclesResponse> {
    return sharedOrOwn<LiveVehiclesResponse>(
      () => new Promise((resolve) => {
        try {
          chrome.runtime.sendMessage({ type: PANEL_LIVE }, (reply: PanelLiveReply<LiveVehiclesResponse>) => {
            // A worker that was asleep and failed to wake leaves
            // lastError set and reply undefined: a quiet no.
            resolve(chrome.runtime.lastError || !reply ? { ok: false } : reply);
          });
        } catch {
          resolve({ ok: false });
        }
      }),
      () => apiJSON<LiveVehiclesResponse>('/map/vehicles/live'),
    );
  }

  async function livePoll() {
    try {
      const data = await livePositions();
      const now = performance.now();
      for (const [vid, pos] of Object.entries(data.positions ?? {})) {
        const m = markers.current.get(vid);
        if (!m) continue;
        const { phys: next, started, stopped } = applyFix(
          phys.current.get(vid), pos.lat, pos.lng, pos.speed_mph, pos.heading, now);
        phys.current.set(vid, next);
        const warn = warns.current.get(vid) ?? false;
        if (started || stopped) {
          // The face is derived from the same rule the list uses, so
          // the two feeds cannot draw two different trucks.
          const listed = latest.current.get(vid);
          const { colour, moving } = faceOf(listed ? vehicleStatus(listed) : 'stopped', next);
          if (stopped) m.setLatLng([pos.lat, pos.lng]);
          applyIcon(vid, m, colour, warn, moving, moving ? next.headingDeg : null);
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
      // One loop, started once, for whatever is moving now.
      ensurePump();
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
    void getFlag(CARD_OPEN_KEY, true).then(setCardOpen);
    void getFlag(INV_OPEN_KEY, false).then(setInvOpen);
    void chrome.storage.local.get(FILTER_KEY).then((got) => {
      const f = got[FILTER_KEY];
      if (f === 'all' || f === 'moving' || f === 'idle' || f === 'stopped') setFilter(f);
    });
    // The cleanup reads the SAME maps this effect created, so hold them
    // in locals — React warns that a ref may point elsewhere by then.
    const physMap = phys.current, markerMap = markers.current, latestMap = latest.current;
    const keyMap = iconKeys.current, arrowMap = arrows.current, warnMap = warns.current;
    void loadVehicles();
    const a = setInterval(loadVehicles, REFRESH_MS);
    const b = setInterval(livePoll, LIVE_REFRESH_MS);
    return () => {
      clearInterval(a); clearInterval(b);
      if (frame.current !== null) { cancelAnimationFrame(frame.current); frame.current = null; }
      physMap.clear(); markerMap.clear(); latestMap.clear();
      keyMap.clear(); arrowMap.clear(); warnMap.clear();
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

  // A name is only an identifier while it is unique, and on this account
  // it is not: two trucks called 001 and two called 103 sit in the list
  // at once.  The company tells them apart, and it has been arriving in
  // the payload all along.  Shown only when the account HAS more than
  // one — a single-company fleet would get the same word on every row,
  // which is noise, not identity.
  const multiCompany = useMemo(
    () => new Set(vehicles.map((f) => f.properties.company).filter(Boolean)).size > 1,
    [vehicles]);

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
    const take = (raw: unknown) => {
      const want = readPendingSelect(raw);
      if (!want) return;
      // By id, which is what this surface is keyed on; by name only if
      // the id finds nothing, so a click is never eaten by two feeds
      // disagreeing about which id a truck has.
      const f = vehicles.find((v) => idOf(v) === want.id)
        ?? (want.name ? vehicles.find((v) => String(v.properties.name) === want.name) : undefined);
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
  }, [listOpen, cardShown, cardOpen]);

  const toggleList = () => {
    const open = !listOpen;
    setListOpen(open);
    void setFlag(LIST_OPEN_KEY, open);
  };
  const toggleCard = () => {
    const open = !cardOpen;
    setCardOpen(open);
    void setFlag(CARD_OPEN_KEY, open);
  };
  const toggleInv = () => {
    const open = !invOpen;
    setInvOpen(open);
    void setFlag(INV_OPEN_KEY, open);
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
          truck it describes.

          And it FOLDS, from its own name.  Seven lines is the answer for
          somebody reading one truck; it is rent for somebody working
          from the search box and the filters, and they were paying it on
          every selection with no way to stop.  Folded, the card keeps
          the line that says which truck and what it is doing, and gives
          the rest of its height to the list.  Selecting another truck
          does NOT unfold it — a fold that any click undoes is not a
          fold. */}
      {selected && (() => {
          const [lat, lng] = liveLatLng(selected);
          const levels = levelsOf(selected.properties);
          const st = vehicleStatus(selected);
          const stLabel = st[0].toUpperCase() + st.slice(1);
          // What the card WARNS about, kept out of the fold.  Folding
          // may take away detail; it may not take away a warning — and
          // both of these are warnings the expanded card draws in
          // colour: a position too old to act on, and a tank low
          // enough to plan around.
          const age = ageMs(selected.properties.updated_at, now);
          const staleness = stalenessOf(age);
          const positionOld = staleness === 'stale' || staleness === 'very_stale';
          const lowLevels = levels.filter((l) => l.low);
          return (
            <div className="sheet">
              {/* Two groups that WRAP.  At the panel's narrowest (Chrome's
                  floor is 320px) the identity and the two actions do not
                  fit on one line; forced onto one they ellipsised the
                  name to "0.", folded "Keeping in view" over two lines
                  and pushed Close off the edge, and the row's min-content
                  width gave the whole panel a horizontal scrollbar.  Now
                  the actions drop to a second line, right-aligned, and
                  every word stays whole. */}
              <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 6 }}>
                <span className="row" style={{ gap: 6, minWidth: 0, flex: '1 1 auto' }}>
                  {/* The caret LEADS the thing it opens, and it WEARS
                      what a control wears here.

                      It began as a bare muted glyph against a bold name,
                      with the name itself as the target: a big target,
                      and nobody could find it.  A row that is not itself
                      a button has to carry the affordance in the control
                      — so this is a button of the same cloth as Close
                      beside it, with its own 24px square and its own
                      space.  The list's header can stay a bare glyph
                      because the whole bar there IS the button and says
                      so with its own fill. */}
                  <button type="button" className="btn" onClick={toggleCard}
                          aria-expanded={cardOpen} aria-controls={CARD_BODY_ID}
                          aria-label={cardOpen
                            ? 'Fold this vehicle to one line'
                            : 'Show this vehicle in full'}
                          title={cardOpen
                            ? 'Fold this vehicle to one line'
                            : 'Show this vehicle in full'}
                          style={{ width: 24, height: 24, minWidth: 24, padding: 0, flexShrink: 0,
                                   display: 'grid', placeItems: 'center', lineHeight: 1 }}>
                    <span aria-hidden>{cardOpen ? '▾' : '▴'}</span>
                  </button>
                  {/* minWidth 0 lets the ellipsis work at all — a flex
                      item's default minimum is its full text, which is
                      the width the whole panel was being stretched to. */}
                  <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                    {selected.properties.name}
                    {multiCompany && selected.properties.company && (
                      <span className="muted" style={{ fontWeight: 400 }}> · {selected.properties.company}</span>
                    )}
                  </strong>
                  {/* Folded, this is the one thing worth keeping: a name
                      and three buttons would say nothing about the truck
                      they belong to.  Expanded, the line below carries
                      it — it is never in both places at once. */}
                  {!cardOpen && (
                    <span className="row" style={{ gap: 4, flexShrink: 0, fontSize: 12 }}>
                      <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%',
                                                 background: statusColor(st), flexShrink: 0 }} />
                      <span style={{ fontWeight: 600 }}>{stLabel}</span>
                      {/* An age worth reading is one that says "do not
                          act on this".  A fresh one stays folded away
                          with the rest of the detail. */}
                      {positionOld && (
                        <span style={{ color: 'var(--warn)' }} title={describeAge(age)}>
                          {formatAge(age)} old
                        </span>
                      )}
                      {lowLevels.length > 0 && (
                        <span style={{ color: 'var(--danger)', fontWeight: 600 }}
                              title={lowLevels.map((l) => `${l.label} ${l.pct}%`).join(' · ')
                                     + ` — low below ${LOW_LEVEL_PCT}%`}>
                          {lowLevels.map((l) => l.label).join(' · ')} low
                        </span>
                      )}
                    </span>
                  )}
                  {/* Who supplies this truck, next to what it is called. */}
                  <SourceMarks sources={selected.properties.sources} source={selected.properties.source} links={links} />
                </span>
                <div className="row" style={{ gap: 6, flexShrink: 0, marginLeft: 'auto' }}>
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
                  {/* Folding and closing both make the card go away and
                      mean different things: this one CLEARS the choice,
                      so the next truck opens a full card again.  The
                      difference was legible only to whoever wrote it. */}
                  <button className="btn" title="Clear the selection — the next vehicle you pick opens in full"
                          onClick={() => { setKeep(false); setSelected(null); }}>Close</button>
                </div>
              </div>
              {/* Everything the fold takes away.  It stays in the DOM
                  rather than being unmounted so the control above can
                  name what it controls. */}
              <div id={CARD_BODY_ID} hidden={!cardOpen} style={{ display: 'grid', gap: 6 }}>
              {/* The card said what a truck's fuel was and never whether
                  it was moving.  Status lived in the list's dot and the
                  map's marker; the surface that describes ONE vehicle
                  had it nowhere. */}
              {(() => {
                const mph = selected.properties.speed_mph;
                const moving = st === 'moving' && typeof mph === 'number';
                return (
                  <p className="row" style={{ margin: 0, gap: 6, fontSize: 12 }}>
                    <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%',
                                               background: statusColor(st), flexShrink: 0 }} />
                    <span style={{ fontWeight: 600 }}>{stLabel}</span>
                    {moving && <span className="muted">{Math.round(mph)} mph</span>}
                  </p>
                );
              })()}
              <p className="muted" style={{ margin: 0 }}>{selected.properties.address || '—'}</p>
              <p style={{ margin: 0, fontSize: 12, color: positionOld ? 'var(--warn)' : 'var(--muted)' }}
                 title={describeAge(age)}>
                {staleness === 'unknown' ? 'No position time reported' : `Updated ${formatAge(age)} ago`}
                {staleness === 'very_stale' && ' — this is not a live position'}
              </p>
              {levels.map((l) => (
                // The threshold exists in code and was signalled only by
                // colour, and only once breached: at 60% nothing on the
                // surface said what "normal" was.
                <div key={l.key} title={`${l.label} ${l.pct}% — low below ${LOW_LEVEL_PCT}%`}>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 12 }}>
                    <span className={l.low ? '' : 'muted'} style={l.low ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{l.label}</span>
                    <span className={l.low ? '' : 'muted'} style={l.low ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{l.pct}%</span>
                  </div>
                  <div className={`bar ${l.low ? 'low' : ''}`}><i style={{ width: `${l.pct}%` }} /></div>
                </div>
              ))}
              <div className="row" style={{ flexWrap: 'wrap', rowGap: 6 }}>
                <button className="btn primary" onClick={() => void openInGoogleMaps(searchUrl(lat, lng))}>Open in Google Maps</button>
                <button className="btn" onClick={() => void openInGoogleMaps(directionsUrl(lat, lng))}>Directions</button>
              </div>
              {/* What is aboard this truck.

                  It sits AFTER the two actions on purpose: those are
                  what the card is for and they hold the position people
                  learned them in.  This is the answer to a question
                  asked standing next to the truck.

                  Rendered only when the truck HAS recorded items.  An
                  account that does not use Inventory would otherwise
                  carry an "Onboard 0" line on every selection forever,
                  and a truck with nothing recorded is something to fix
                  on the dashboard, not to report on a map. */}
              {onboard && onboard.items.length > 0 && (
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 6, display: 'grid', gap: 4 }}>
                  {/* The header carries a RESTING fill, not just the
                      .rowbtn hover.  The list's header gets away with a
                      bare glyph because its bar wears var(--card) on the
                      page ground; this bar is already ON var(--card), so
                      that step is invisible here and it needs one of its
                      own.  It buys two things at once: the bar reads as
                      a control, and it reads as the header OF the rows
                      under it rather than as the first of them. */}
                  <button type="button" onClick={toggleInv}
                          aria-expanded={invOpen} aria-controls={INV_BODY_ID}
                          className="row rowbtn"
                          title={invOpen ? 'Hide what is aboard' : 'Show what is aboard'}
                          style={{ gap: 6, background: 'rgba(255,255,255,.04)', border: 0, padding: '2px 4px',
                                   margin: '0 -4px', borderRadius: 4, minHeight: 24,
                                   color: 'var(--fg)', cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
                    {/* The caret LEADS what it opens — the card above and
                        the list below both point the same way. */}
                    <span aria-hidden style={{ width: 12, flexShrink: 0 }}>{invOpen ? '\u25be' : '\u25b4'}</span>
                    {/* The dashboard's nav, its permission row and the
                        command palette all call this "Onboard Inventory".
                        A shorter third name here would be a third name. */}
                    <span style={{ fontWeight: 600, fontSize: 12 }}>Onboard Inventory</span>
                    <span className="muted" style={{ fontSize: 12 }}>{onboard.items.length}</span>
                    {/* Folding may take away detail; it may not take away
                        a warning.  The card's own fold obeys the same
                        rule, and this is the whole reason the section is
                        worth having closed: one glance says whether
                        anything on this truck wants somebody. */}
                    {onboard.attention > 0 && (
                      <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--warn)', fontWeight: 600 }}>
                        {onboard.attention} need{onboard.attention === 1 ? 's' : ''} attention
                      </span>
                    )}
                  </button>
                  {/* The rows carry their own ceiling — see ItemRows. */}
                  <div id={INV_BODY_ID} hidden={!invOpen} style={{ display: 'grid', gap: 4 }}>
                    <ItemRows items={onboard.items}
                              onVerify={canWriteInventory ? async (id) => {
                                await verifyItem(id);
                                await refreshOnboard(selected.properties.registry_id);
                              } : undefined}
                              onStatus={canWriteInventory ? async (id, st) => {
                                await setItemStatus(id, st);
                                await refreshOnboard(selected.properties.registry_id);
                              } : undefined} />
                    {/* The panel can READ this and never write it — the
                        manage grant is deliberately outside the token's
                        scope, so a key living in a browser cannot mark a
                        dashcam missing.  That is the right call and it
                        leaves somebody standing at the truck with an
                        answer and nowhere to put it.  A quiet link, not
                        a third button: the two actions above are what
                        this card is for.

                        It goes to /inventory rather than the truck's own
                        page, because the truck page is gated on
                        can_view_vehicles — the one grant this reader may
                        not have, and the whole reason Inventory became a
                        feature of its own. */}
                    <button type="button" className="link"
                            onClick={() => { void chrome.tabs.create({ url: `${DASHBOARD_BASE}/inventory` }); }}
                            style={{ justifySelf: 'start', background: 'none', border: 0, padding: '2px 0',
                                     font: 'inherit', fontSize: 12, cursor: 'pointer', minHeight: 24 }}>
                      Manage on 4truck →
                    </button>
                  </div>
                </div>
              )}
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
                style={{ width: '100%', gap: 6, padding: '6px 10px',
                         background: 'var(--card)', border: 0, color: 'var(--fg)', cursor: 'pointer' }}>
          {/* The caret LEADS what it opens — the same rule the card
              above follows, and the one the browser's own <summary>
              uses.  It sat at the far right until the card gained a
              fold of its own; one panel with two folds pointing from
              opposite sides is two things to learn for one behaviour. */}
          <span className="muted" aria-hidden style={{ flexShrink: 0 }}>{listOpen ? '▾' : '▴'}</span>
          {/* A header that shares its rows' surface, padding and border
              reads as their first row.  A fill step says it owns them. */}
          <span style={{ fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>
            Vehicles <span className="muted" style={{ fontWeight: 400 }}>({filtered.length})</span>
          </span>
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
                <span title={warn ? `${status} — fuel or DEF below ${LOW_LEVEL_PCT}%` : status}
                      aria-label={status}
                      style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, background: statusColor(status),
                               boxShadow: warn ? `0 0 0 2px ${MAP_STATUS.danger}` : undefined }} />
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  <span style={{ fontWeight: 600 }}>{p.name}</span>
                  {multiCompany && p.company && (
                    <span className="muted" style={{ fontWeight: 400 }}> · {p.company}</span>
                  )}
                </span>
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
