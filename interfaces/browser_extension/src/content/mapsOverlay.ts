/**
 * 4truck vehicles, drawn on Google's own map.
 *
 * The side panel shows the live map beside the page.  This shows it ON
 * the page: a dispatcher planning a route in Google Maps sees their own
 * trucks in the same picture, without a second map to reconcile.
 *
 * HOW IT KNOWS WHERE TO DRAW.  Google keeps its map object private, so
 * the only camera an extension can read is the one Google writes into
 * its own URL (``@lat,lng,zoom``) and the canvas it drew on.  Web
 * Mercator turns those into pixels — projection.ts, where the
 * arithmetic is proven.
 *
 * HOW IT KEEPS UP WITH A HAND.  Google writes that URL when a gesture
 * SETTLES, not while it runs.  For a plain drag the pointer itself is
 * the truth — Google moves the map one CSS pixel per pixel of travel —
 * so the layer follows the pointer live and drops the translation the
 * instant the URL lands and markers are re-projected.  Everything a
 * pointer delta cannot describe — a fling, a zoom, an arrow key, a
 * zoom button — fades the layer until the camera settles: a marker
 * that vanishes for a third of a second reads as intentional, one that
 * slides into the wrong street reads as broken.  gesture.ts is the
 * rule; this file is the ears.
 *
 * WHERE IT LIVES IN THE PAGE.  Inside the map's own container, right
 * after the canvas — so Google's search panel, chips and controls,
 * which stack above the map, stack above the markers too.  A layer
 * pinned to the top of the page covered the panel with trucks.
 *
 * HOW IT MOVES.  The same way the panel's own map does, and from the
 * same two feeds: the thirty-second list carries addresses and levels,
 * and a five-second positions-only feed carries where everything is.
 * Between fixes a moving truck GLIDES along the tween (physics.ts,
 * shared with the panel) rather than teleporting twice a minute — the
 * overlay used to sit up to thirty seconds behind the panel showing the
 * same truck, which is the one thing a live map may not do.
 *
 * WHAT IT REFUSES TO DO.  It draws nothing when signed out, nothing in
 * Street View, nothing when it cannot find a canvas big enough to be a
 * map, and nothing the person has switched off.  Google's page is
 * unversioned: each of those is a state we will meet without warning,
 * and the right answer to each is to disappear rather than draw wrong.
 */
import {
  OPEN_PANEL, OVERLAY_LIVE, OVERLAY_VEHICLES,
  type LiveReply, type OverlayReply, type OverlayVehicle,
} from '../features/maps-overlay/bridge';
import { applyFix, positionAt, shortestAngleDiff, type Phys } from '../features/live-map/physics';
import {
  SETTLE_WAIT_MS, beginDrag, dragTransform, endDrag, isMapKey, moveDrag, type Drag,
} from '../features/maps-overlay/gesture';
import { OVERLAY_PREF_KEY, setOverlayPref } from '../features/maps-overlay/pref';
import { cameraFromUrl, isStreetView, isVisible, project, sameCamera, showsLabels, type Camera } from '../features/maps-overlay/projection';
import { colourFor, findMapCanvas, markerAt, sameSurface, type Surface } from '../features/maps-overlay/surface';

const ROOT_ID = '4truck-maps-overlay';
const CHIP_ID = '4truck-maps-chip';
/** A vehicle picked here, for the panel to open on.  Storage rather than
 *  a message: the panel may be closed at the moment of the click and
 *  would never receive one. */
const PENDING_SELECT_KEY = 'pendingSelectVehicle';
/** Positions are 30s fresh on the server; asking faster spends quota
 *  for numbers that have not changed. */
const POLL_MS = 30_000;
/** Positions only, the panel's own cadence.  Cheap enough to ask this
 *  often; the full list is not. */
const LIVE_POLL_MS = 5_000;
/** Google writes its URL on a settle, so watching it is a poll.  This
 *  is the latency between the hand lifting and markers landing, so it
 *  is short; the compare is one string. */
const URL_POLL_MS = 120;
/** A zoom or a fling that never changes the URL (it can happen at the
 *  zoom limits) must not leave the layer faded for ever. */
const QUIET_RESTORE_MS = 900;

let camera: Camera | null = null;
let surface: Surface | null = null;
let canvasEl: HTMLCanvasElement | null = null;
let vehicles: OverlayVehicle[] = [];
let enabled = true;
let root: HTMLDivElement | null = null;
const markers = new Map<string, HTMLDivElement>();
/** Whether the worker had a token the last time we asked.  Signed out,
 *  nothing of ours appears on Google's page at all — not even the
 *  switch.  A person who has not connected did not ask for this. */
let signedIn = false;
let chip: HTMLButtonElement | null = null;
/**
 * What the switch may honestly claim.
 *
 *   'loading' — no answer yet, or Google has not written a camera into
 *               its URL yet.  NOT zero: "none in view" for a state we
 *               simply do not know reads as "your trucks are not here",
 *               and a person who believes that stops looking.
 *   'ready'   — the count is real.
 *   'error'   — we asked and could not be told.
 */
let dataState: 'loading' | 'ready' | 'error' = 'loading';
/** Where each marker was last drawn, in layer pixels — so a click can
 *  be matched to a vehicle without the markers taking pointer events,
 *  which would stop a drag that begins on a truck. */
const drawnAt = new Map<string, { x: number; y: number }>();
/** Per-vehicle motion, shared with the panel's map.  A vehicle in here
 *  is drawn from its tween; one absent is drawn from the list. */
const phys = new Map<string, Phys>();
let animFrame: number | null = null;

// ── gesture state ──────────────────────────────────────────────────────
let drag: Drag | null = null;
let pointersDown = 0;
/** Waiting for Google to write the camera after an interaction. */
let settling = false;
let settleTimer: ReturnType<typeof setTimeout> | null = null;
let lastInteractionUrl = '';

/** Ask the worker; it holds the token and the host permission. */
function loadVehicles(): Promise<OverlayReply> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: OVERLAY_VEHICLES }, (reply: OverlayReply) => {
        // A worker that was asleep and failed to wake leaves lastError
        // set and reply undefined; that is a quiet no, not a crash.
        if (chrome.runtime.lastError || !reply) {
          resolve({ ok: false, reason: 'error', detail: chrome.runtime.lastError?.message ?? 'no reply' });
          return;
        }
        resolve(reply);
      });
    } catch {
      resolve({ ok: false, reason: 'error', detail: 'context gone' });
    }
  });
}

// ── the layer ──────────────────────────────────────────────────────────

/** The element the layer mounts in: the canvas's parent when it is
 *  positioned (Google's scene container is), else the document — the
 *  fallback covers the panel, but a mis-positioned layer would draw
 *  every marker in the wrong place, which is worse. */
function mountPoint(): { parent: HTMLElement; fixed: boolean } {
  const parent = canvasEl?.parentElement;
  if (parent && getComputedStyle(parent).position !== 'static') return { parent, fixed: false };
  return { parent: document.documentElement, fixed: true };
}

function ensureRoot(): HTMLDivElement {
  const { parent, fixed } = mountPoint();
  if (!root || !root.isConnected) {
    root = document.createElement('div');
    root.id = ROOT_ID;
    // No z-index on purpose: after the canvas in DOM order it paints above
    // the map and below everything Google gives a stacking level — the
    // panel, the chips, the zoom control.  pointer-events none so every
    // drag, wheel and click reaches Google's map exactly as before.
    root.style.cssText =
      'pointer-events:none;overflow:hidden;transition:opacity .15s;will-change:transform;contain:layout style';
    markers.clear();
  }
  if (root.parentElement !== parent) parent.appendChild(root);   // appendChild moves
  root.style.position = fixed ? 'fixed' : 'absolute';
  if (fixed) root.style.zIndex = '2147483000'; else root.style.removeProperty('z-index');
  return root;
}

function removeAll(): void {
  root?.remove();
  root = null;
  markers.clear();
  parts.clear();
  drawnAt.clear();
  setHoverCursor(false);
  if (animFrame !== null) { cancelAnimationFrame(animFrame); animFrame = null; }
}

// ── the switch on the map ──────────────────────────────────────────────
//
// Not everyone opens Google Maps to see trucks.  The switch lives where
// the trucks appear, so turning them off is one click there — not a
// trip to the panel's Settings.  It is NOT inside the layer: the layer
// translates with a drag, and a control that slides off with the map is
// a control nobody can hit.  Its own fixed element, top-right under
// Google's account button, where Google draws nothing.  Named by what it
// toggles — "vehicles" — never "Layer": Google's own Layers button sits
// on the same map, and one word meaning two things is how a person turns
// off the wrong one.

const STYLE_ID = '4truck-maps-style';
function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return;
  const st = document.createElement('style');
  st.id = STYLE_ID;
  st.textContent = '@keyframes fourtruck-spin{to{transform:rotate(360deg)}}';
  document.head.appendChild(st);
}

function ensureChip(): HTMLButtonElement {
  if (chip?.isConnected) return chip;
  ensureStyle();
  const el = document.createElement('button');
  el.id = CHIP_ID;
  el.type = 'button';
  el.setAttribute('role', 'switch');
  el.style.cssText =
    'position:fixed;top:112px;right:12px;z-index:2147483000;display:flex;align-items:center;gap:8px;' +
    'padding:6px 8px 6px 10px;border:0;border-radius:999px;cursor:pointer;' +
    'background:rgba(17,20,26,.92);color:#fff;font:600 12px/1 system-ui,sans-serif;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.35);white-space:nowrap';
  // The extension's OWN icon, not a square drawn to look like it: the
  // hand-made one had the wrong ground (a brighter blue than the real
  // asset), so the mark on Google's map was not the mark in the
  // toolbar.  One asset, one truth — declared web-accessible for this
  // host alone, since a content script cannot read a packaged file
  // without that.
  el.innerHTML =
    `<img src="${chrome.runtime.getURL('icons/icon32.png')}" alt="" width="16" height="16" ` +
      'style="display:block;border-radius:4px">' +
    '<span data-spin hidden style="width:12px;height:12px;border-radius:50%;border:2px solid rgba(255,255,255,.25);' +
      'border-top-color:#fff;animation:fourtruck-spin .7s linear infinite"></span>' +
    '<span data-label></span>' +
    '<span data-track style="position:relative;width:28px;height:16px;border-radius:999px;background:#4b5563;transition:background .15s">' +
      '<span data-knob style="position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;transition:transform .15s"></span>' +
    '</span>';
  el.addEventListener('click', () => { void setOverlayPref(!enabled); });
  document.documentElement.appendChild(el);
  chip = el;
  return el;
}

function hideChip(): void {
  chip?.remove();
  chip = null;
}

/** The switch says what it governs and what is on the map right now —
 *  and says "still looking" rather than "none" while it does not know. */
function updateChip(inView: number | null): void {
  const el = ensureChip();
  const label = el.querySelector<HTMLElement>('[data-label]')!;
  const spin = el.querySelector<HTMLElement>('[data-spin]')!;
  const track = el.querySelector<HTMLElement>('[data-track]')!;
  const knob = el.querySelector<HTMLElement>('[data-knob]')!;
  spin.hidden = true;
  if (enabled) {
    if (dataState === 'error') {
      label.textContent = 'can\u2019t reach 4truck';
    } else if (inView === null || dataState === 'loading') {
      // A spinner is a promise that something is coming; a zero is a
      // statement that nothing is there.  Only one of those is true
      // before the first answer.
      spin.hidden = false;
      label.textContent = 'loading\u2026';
    } else {
      // "in view", never a bare "N vehicles": the panel says how many the
      // account has, this says how many are on this screen, and the same
      // words for two numbers on one screen is how a person stops
      // trusting either.
      label.textContent = inView === 0 ? 'none in view' : `${inView} in view`;
    }
    el.setAttribute('aria-checked', 'true');
    el.setAttribute('aria-label', 'Show 4truck vehicles on this map — on');
    track.style.background = '#22c55e';
    knob.style.transform = 'translateX(12px)';
  } else {
    label.textContent = 'vehicles off';
    el.setAttribute('aria-checked', 'false');
    el.setAttribute('aria-label', 'Show 4truck vehicles on this map — off');
    track.style.background = '#4b5563';
    knob.style.transform = '';
  }
}

/** The panel draws an arrow while a truck moves and a dot when it does
 *  not, and the arrow points where the truck is going.  The overlay drew
 *  a dot for everything, so the same truck had two different faces on
 *  two of our own surfaces.  Same shapes here, in plain DOM — the panel
 *  builds them through Leaflet's divIcon, which this bundle has not got. */
const HALO = '#fff', SHADOW = 'rgba(0,0,0,.45)';
function glyphHtml(moving: boolean, colour: string): string {
  if (moving) {
    return '<svg data-glyph="arrow" width="18" height="18" viewBox="0 0 18 18" '
      + `style="overflow:visible;filter:drop-shadow(0 1px 2px ${SHADOW})">`
      + `<polygon data-arrow points="9,2 17,16 1,16" fill="${colour}" stroke="${HALO}" stroke-width="1.5"/></svg>`;
  }
  return `<span data-glyph="dot" style="display:block;width:12px;height:12px;border-radius:50%;`
    + `background:${colour};border:2px solid ${HALO};box-shadow:0 1px 3px ${SHADOW}"></span>`;
}

interface MarkerParts { el: HTMLDivElement; glyph: HTMLElement; name: HTMLElement; shape: string }
const parts = new Map<string, MarkerParts>();

function markerFor(v: OverlayVehicle): MarkerParts {
  const p = phys.get(v.id);
  const moving = !!p?.isMoving || v.status === 'moving';
  const shape = `${moving ? 'arrow' : 'dot'}:${colourFor(v.status)}`;
  let m = parts.get(v.id);
  if (!m || !m.el.isConnected) {
    const el = document.createElement('div');
    // Markers take no pointer events: a drag that starts on a truck must
    // still drag the map, so the click is hit-tested by us instead.
    el.style.cssText =
      'position:absolute;transform:translate(-50%,-50%);pointer-events:none;' +
      'display:flex;align-items:center;gap:4px;font:600 11px/1 system-ui,sans-serif;white-space:nowrap';
    el.innerHTML = `<span data-glyphwrap style="display:flex">${glyphHtml(moving, colourFor(v.status))}</span>`
      + '<span data-name style="background:rgba(17,20,26,.86);color:#fff;padding:2px 5px;border-radius:4px"></span>';
    ensureRoot().appendChild(el);
    m = { el, glyph: el.querySelector<HTMLElement>('[data-glyphwrap]')!,
          name: el.querySelector<HTMLElement>('[data-name]')!, shape };
    markers.set(v.id, el);
    parts.set(v.id, m);
  } else if (m.shape !== shape) {
    // Only when it actually changes: rewriting innerHTML every frame
    // would rebuild a hundred SVGs a second for nothing.
    m.glyph.innerHTML = glyphHtml(moving, colourFor(v.status));
    m.shape = shape;
  }
  m.name.textContent = v.name || v.id;
  // The dot alone below the label zoom: at national scale a hundred
  // name pills overlap into a block that says less than the dots do.
  m.name.hidden = !showsLabels(camera?.zoom ?? 0);
  if (moving && p) aimArrow(m, p.headingDeg);
  return m;
}

/** The arrow turns; the marker itself must not, or the name pill would
 *  spin with it. */
function aimArrow(m: MarkerParts, deg: number): void {
  const svg = m.glyph.firstElementChild as SVGElement | null;
  if (svg?.getAttribute('data-glyph') === 'arrow') {
    svg.style.transform = `rotate(${deg}deg)`;
  }
}

function draw(): void {
  // Street View is a photograph and a page with no map canvas is not a
  // map: nothing of ours belongs on either, the switch included.
  if (!signedIn || isStreetView(location.href) || !surface) { removeAll(); hideChip(); return; }
  // No camera yet means Google has not written one — a place page, a
  // fresh navigation.  That is "not known", never "none": the truck in
  // the owner's screenshot WAS in view while the switch said none were.
  if (!enabled || !camera) { removeAll(); updateChip(enabled ? null : 0); return; }

  const el = ensureRoot();
  const { fixed } = mountPoint();
  const base = fixed ? { left: 0, top: 0 } : el.parentElement!.getBoundingClientRect();
  el.style.left = `${surface.left - base.left}px`;
  el.style.top = `${surface.top - base.top}px`;
  el.style.width = `${surface.width}px`;
  el.style.height = `${surface.height}px`;

  const seen = new Set<string>();
  drawnAt.clear();
  const ts = performance.now();
  for (const v of vehicles) {
    const p = project(livePosition(v, ts), camera, surface);
    if (!isVisible(p, surface)) continue;
    seen.add(v.id);
    drawnAt.set(v.id, p);
    const m = markerFor(v);
    m.el.style.left = `${p.x}px`;
    m.el.style.top = `${p.y}px`;
  }
  // A marker for a vehicle that has left the view is removed, not
  // hidden: a hundred hidden nodes on every Google Maps tab is a cost
  // the person did not agree to.
  for (const [id, m] of markers) {
    if (!seen.has(id)) { m.remove(); markers.delete(id); parts.delete(id); }
  }
  updateChip(seen.size);
}

/** The camera settled: markers are re-projected and, in the SAME frame,
 *  the drag translation is dropped and the layer shown.  Doing these
 *  together is what makes a followed drag land without a jump. */
function settle(): void {
  settling = false;
  if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; }
  draw();
  if (root) { root.style.transform = ''; root.style.opacity = '1'; }
}

/** Something moved the camera in a way the pointer cannot describe.
 *  Hide until Google says where we are. */
function fadeUntilSettled(): void {
  if (root) root.style.opacity = '0';
  awaitSettle();
}

function awaitSettle(): void {
  settling = true;
  lastInteractionUrl = location.href;
  if (settleTimer) clearTimeout(settleTimer);
  // Google normally writes within ~300 ms.  Past this, either nothing
  // changed (a zoom at the limit, a drag that came back) or the write
  // is not coming; either way the honest state is the last camera we
  // know, shown, with no stale translation.
  settleTimer = setTimeout(() => {
    if (!settling) return;
    settle();
  }, drag ? SETTLE_WAIT_MS : QUIET_RESTORE_MS);
}

/** Where a vehicle IS at this instant: its tween while it is moving,
 *  the list's own fix when it is not. */
function livePosition(v: OverlayVehicle, ts: number): { lat: number; lng: number } {
  const p = phys.get(v.id);
  if (!p) return v;
  if (!p.isMoving) return { lat: p.lat, lng: p.lng };
  const at = positionAt(p, ts);
  p.lat = at.lat; p.lng = at.lng;
  return at;
}

/** One loop for every moving truck, not one per truck: the overlay
 *  repositions markers by writing a style, and a hundred of those in a
 *  frame is cheaper than a hundred scheduled callbacks.  It stops the
 *  moment nothing is moving, so a parked fleet costs nothing. */
function pump(): void {
  animFrame = null;
  if (!camera || !surface || !enabled) return;
  const ts = performance.now();
  let moving = false;
  for (const v of vehicles) {
    const p = phys.get(v.id);
    if (!p?.isMoving) continue;
    moving = true;
    // Turn toward the new bearing rather than snapping to it.
    p.headingDeg += shortestAngleDiff(p.headingDeg, p.targetHeading) * 0.12;
    const at = positionAt(p, ts);
    p.lat = at.lat; p.lng = at.lng;
    const m = parts.get(v.id);
    if (!m) continue;                    // off screen; the next draw places it
    const pt = project(at, camera, surface);
    drawnAt.set(v.id, pt);
    m.el.style.left = `${pt.x}px`;
    m.el.style.top = `${pt.y}px`;
    aimArrow(m, p.headingDeg);
  }
  if (moving) animFrame = requestAnimationFrame(pump);
}

function ensurePump(): void {
  if (animFrame === null) animFrame = requestAnimationFrame(pump);
}

/** The camera and the canvas, re-read.  True when either changed. */
function refreshView(): boolean {
  const nextCamera = cameraFromUrl(location.href);
  const found = findMapCanvas(Array.from(document.querySelectorAll('canvas')));
  const nextSurface = found?.surface ?? null;
  const canvasChanged = (found?.el ?? null) !== canvasEl;
  canvasEl = found?.el ?? null;
  const changed = canvasChanged || !sameCamera(camera, nextCamera) || !sameSurface(surface, nextSurface);
  camera = nextCamera;
  surface = nextSurface;
  return changed;
}

function loadFixes(): Promise<LiveReply> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: OVERLAY_LIVE }, (reply: LiveReply) => {
        resolve(chrome.runtime.lastError || !reply ? { ok: false } : reply);
      });
    } catch {
      resolve({ ok: false });
    }
  });
}

async function refreshFixes(): Promise<void> {
  if (!enabled || !signedIn) return;
  const reply = await loadFixes();
  if (!reply.ok) return;                 // the fast poll stays quiet
  const now = performance.now();
  const known = new Set(vehicles.map((v) => v.id));
  for (const f of reply.fixes) {
    if (!known.has(f.id)) continue;      // a truck the list has not caught up to
    const { phys: next } = applyFix(phys.get(f.id), f.lat, f.lng, f.speed_mph, f.heading, now);
    phys.set(f.id, next);
  }
  for (const id of [...phys.keys()]) if (!known.has(id)) phys.delete(id);
  ensurePump();
  if (!drag && !settling) draw();
}

async function refreshData(): Promise<void> {
  const reply = await loadVehicles();
  // Signed out, or the API said no.  Either way the honest thing is an
  // empty map rather than positions from ten minutes ago.
  signedIn = reply.ok || reply.reason !== 'signed-out';
  // A failed request and an empty screen look identical and mean
  // opposite things, so the switch says which one happened.
  dataState = reply.ok ? 'ready' : reply.reason === 'signed-out' ? 'loading' : 'error';
  vehicles = reply.ok ? reply.vehicles : [];
  if (!settling) draw();
}

// ── listening to the hand ──────────────────────────────────────────────

function onMap(target: EventTarget | null): boolean {
  if (!canvasEl || !(target instanceof Node)) return false;
  return target === canvasEl || (canvasEl.parentElement?.contains(target) ?? false);
}
/** A control that moves the camera without a drag: zoom buttons, the
 *  compass, a result card.  Any button over the map counts; naming
 *  Google's buttons by label would break on the next rename. */
function onMapControl(target: EventTarget | null): boolean {
  if (!surface || !(target instanceof Element) || root?.contains(target)) return false;
  const btn = target.closest('button');
  if (!btn) return false;
  const r = btn.getBoundingClientRect();
  return r.left >= surface.left && r.right <= surface.left + surface.width
      && r.top >= surface.top && r.bottom <= surface.top + surface.height;
}

function onPointerDown(e: PointerEvent): void {
  if (chip && e.target instanceof Node && chip.contains(e.target)) return;
  if (!root) return;
  if (onMap(e.target)) {
    pointersDown++;
    if (pointersDown > 1) {               // a second finger: pinch zoom
      drag = null;
      fadeUntilSettled();
      return;
    }
    drag = beginDrag(e.clientX, e.clientY, e.timeStamp);
    if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; }
    settling = false;
    return;
  }
  if (onMapControl(e.target)) fadeUntilSettled();
}

/** Google's canvas carries the cursor, because our layer cannot: it
 *  takes no pointer events, so a `cursor` on a marker never applies.
 *  The rule is ours and is removed again the moment the pointer leaves
 *  a truck, so the page is left as we found it. */
function setHoverCursor(hit: boolean): void {
  if (!canvasEl) return;
  const want = hit ? 'pointer' : '';
  if (canvasEl.style.cursor !== want) canvasEl.style.cursor = want;
}

function onPointerMove(e: PointerEvent): void {
  if (!root) return;
  if (!drag) {
    // Not dragging: say whether there is a truck under the pointer.
    if (enabled && signedIn && surface) {
      setHoverCursor(!!markerAt(drawnAt, e.clientX - surface.left, e.clientY - surface.top));
    }
    return;
  }
  drag = moveDrag(drag, e.clientX, e.clientY, e.timeStamp);
  root.style.transform = dragTransform(drag);
}

/** A press that did not move, landing on a truck: open the panel on it.
 *  The markers themselves stay pointer-events:none — taking the click
 *  would take the DRAG too, and a drag that starts on a truck must
 *  still move Google's map. */
function selectAt(clientX: number, clientY: number): void {
  if (!enabled || !signedIn) return;
  if (!surface) return;
  const id = markerAt(drawnAt, clientX - surface.left, clientY - surface.top);
  if (!id) return;
  // Written first, then the panel is asked to open: whichever arrives
  // first, the panel finds the choice waiting for it.
  void chrome.storage.local.set({ [PENDING_SELECT_KEY]: id });
  try { chrome.runtime.sendMessage({ type: OPEN_PANEL }); } catch { /* worker asleep; storage still carries it */ }
}

function onPointerUp(e?: PointerEvent): void {
  pointersDown = Math.max(0, pointersDown - 1);
  if (!drag) return;
  const release = endDrag(drag);
  const finished = drag;
  drag = null;
  if (!root) return;
  if (release.kind === 'still') {
    root.style.transform = '';
    if (e) selectAt(e.clientX, e.clientY);
    return;
  }
  // Keep following through the settle; the map is where the hand left
  // it until Google says otherwise.
  root.style.transform = dragTransform(finished);
  if (release.kind === 'flung') root.style.opacity = '0';
  awaitSettle();
}

function onWheel(e: WheelEvent): void {
  if (root && onMap(e.target)) fadeUntilSettled();
}

function onKeyDown(e: KeyboardEvent): void {
  if (!root || !isMapKey(e.key)) return;
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  fadeUntilSettled();
}

// ── lifecycle ──────────────────────────────────────────────────────────

function start(): void {
  chrome.storage.local.get(OVERLAY_PREF_KEY, (got) => {
    enabled = got[OVERLAY_PREF_KEY] !== false;
    refreshView();
    void refreshData().then(() => refreshFixes());
  });

  setInterval(() => {
    const changed = refreshView();
    if (!changed) return;
    // A camera write while we are following or fading is the settle
    // we were waiting for; otherwise it is a programmatic move (a
    // search result, a place card) and a plain redraw.
    if (settling && location.href !== lastInteractionUrl) settle();
    else if (!drag) draw();
  }, URL_POLL_MS);
  setInterval(() => { void refreshData(); }, POLL_MS);
  setInterval(() => { void refreshFixes(); }, LIVE_POLL_MS);
  window.addEventListener('resize', () => { if (refreshView() && !drag) draw(); }, { passive: true });

  // Capture on window: we see the gesture before Google's own handlers
  // and never interfere with them — every listener is passive.
  const opts: AddEventListenerOptions = { capture: true, passive: true };
  window.addEventListener('pointerdown', onPointerDown, opts);
  window.addEventListener('pointermove', onPointerMove, opts);
  window.addEventListener('pointerup', onPointerUp, opts);
  window.addEventListener('pointercancel', onPointerUp, opts);
  window.addEventListener('wheel', onWheel, opts);
  window.addEventListener('keydown', onKeyDown, { capture: true });

  // The panel's own switch reaches here without a reload.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !(OVERLAY_PREF_KEY in changes)) return;
    enabled = changes[OVERLAY_PREF_KEY].newValue !== false;
    if (enabled) { refreshView(); void refreshData(); } else draw();
  });
}

// `document_idle` already means the page has parsed; the guard is for a
// re-injection after an extension update, when the old layer is still
// in the DOM and a second one would double every marker.
document.getElementById(ROOT_ID)?.remove();
document.getElementById(CHIP_ID)?.remove();
start();
