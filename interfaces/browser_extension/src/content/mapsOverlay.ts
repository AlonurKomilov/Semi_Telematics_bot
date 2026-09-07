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
 * WHAT IT REFUSES TO DO.  It draws nothing when signed out, nothing in
 * Street View, nothing when it cannot find a canvas big enough to be a
 * map, and nothing the person has switched off.  Google's page is
 * unversioned: each of those is a state we will meet without warning,
 * and the right answer to each is to disappear rather than draw wrong.
 */
import { OVERLAY_VEHICLES, type OverlayReply, type OverlayVehicle } from '../features/maps-overlay/bridge';
import {
  SETTLE_WAIT_MS, beginDrag, dragTransform, endDrag, isMapKey, moveDrag, type Drag,
} from '../features/maps-overlay/gesture';
import { OVERLAY_PREF_KEY, setOverlayPref } from '../features/maps-overlay/pref';
import { cameraFromUrl, isStreetView, isVisible, project, sameCamera, type Camera } from '../features/maps-overlay/projection';
import { colourFor, findMapCanvas, sameSurface, type Surface } from '../features/maps-overlay/surface';

const ROOT_ID = '4truck-maps-overlay';
const CHIP_ID = '4truck-maps-chip';
/** Positions are 30s fresh on the server; asking faster spends quota
 *  for numbers that have not changed. */
const POLL_MS = 30_000;
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

function ensureChip(): HTMLButtonElement {
  if (chip?.isConnected) return chip;
  const el = document.createElement('button');
  el.id = CHIP_ID;
  el.type = 'button';
  el.setAttribute('role', 'switch');
  el.style.cssText =
    'position:fixed;top:112px;right:12px;z-index:2147483000;display:flex;align-items:center;gap:8px;' +
    'padding:6px 8px 6px 10px;border:0;border-radius:999px;cursor:pointer;' +
    'background:rgba(17,20,26,.92);color:#fff;font:600 12px/1 system-ui,sans-serif;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.35);white-space:nowrap';
  el.innerHTML =
    '<span style="display:inline-grid;place-items:center;width:16px;height:16px;border-radius:4px;background:#3b82f6;font-size:11px">4</span>' +
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

/** The switch says what it governs and what is on the map right now. */
function updateChip(inView: number): void {
  const el = ensureChip();
  const label = el.querySelector<HTMLElement>('[data-label]')!;
  const track = el.querySelector<HTMLElement>('[data-track]')!;
  const knob = el.querySelector<HTMLElement>('[data-knob]')!;
  if (enabled) {
    label.textContent = inView === 0 ? 'no vehicles in view' : `${inView} vehicle${inView === 1 ? '' : 's'}`;
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

function markerFor(v: OverlayVehicle): HTMLDivElement {
  let el = markers.get(v.id);
  if (!el) {
    el = document.createElement('div');
    // Markers take no pointer events either: a drag that starts on a
    // truck must drag the map, and nothing here answers a click yet.
    el.style.cssText =
      'position:absolute;transform:translate(-50%,-50%);pointer-events:none;' +
      'display:flex;align-items:center;gap:4px;font:600 11px/1 system-ui,sans-serif;white-space:nowrap';
    el.innerHTML =
      '<span data-dot style="width:12px;height:12px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.5)"></span>' +
      '<span data-name style="background:rgba(17,20,26,.86);color:#fff;padding:2px 5px;border-radius:4px"></span>';
    ensureRoot().appendChild(el);
    markers.set(v.id, el);
  }
  el.querySelector<HTMLElement>('[data-dot]')!.style.background = colourFor(v.status);
  el.querySelector<HTMLElement>('[data-name]')!.textContent = v.name || v.id;
  return el;
}

function draw(): void {
  // Street View is a photograph and a page with no map canvas is not a
  // map: nothing of ours belongs on either, the switch included.
  if (!signedIn || isStreetView(location.href) || !surface) { removeAll(); hideChip(); return; }
  if (!enabled || !camera) { removeAll(); updateChip(0); return; }

  const el = ensureRoot();
  const { fixed } = mountPoint();
  const base = fixed ? { left: 0, top: 0 } : el.parentElement!.getBoundingClientRect();
  el.style.left = `${surface.left - base.left}px`;
  el.style.top = `${surface.top - base.top}px`;
  el.style.width = `${surface.width}px`;
  el.style.height = `${surface.height}px`;

  const seen = new Set<string>();
  for (const v of vehicles) {
    const p = project(v, camera, surface);
    if (!isVisible(p, surface)) continue;
    seen.add(v.id);
    const m = markerFor(v);
    m.style.left = `${p.x}px`;
    m.style.top = `${p.y}px`;
  }
  // A marker for a vehicle that has left the view is removed, not
  // hidden: a hundred hidden nodes on every Google Maps tab is a cost
  // the person did not agree to.
  for (const [id, m] of markers) {
    if (!seen.has(id)) { m.remove(); markers.delete(id); }
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

async function refreshData(): Promise<void> {
  const reply = await loadVehicles();
  // Signed out, or the API said no.  Either way the honest thing is an
  // empty map rather than positions from ten minutes ago.
  signedIn = reply.ok || reply.reason !== 'signed-out';
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

function onPointerMove(e: PointerEvent): void {
  if (!drag || !root) return;
  drag = moveDrag(drag, e.clientX, e.clientY, e.timeStamp);
  root.style.transform = dragTransform(drag);
}

function onPointerUp(): void {
  pointersDown = Math.max(0, pointersDown - 1);
  if (!drag) return;
  const release = endDrag(drag);
  const finished = drag;
  drag = null;
  if (!root) return;
  if (release.kind === 'still') {
    root.style.transform = '';
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
    void refreshData();
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
