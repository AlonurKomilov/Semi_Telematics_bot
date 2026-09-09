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
  ACTIVE_FEATURE_KEY, OPEN_PANEL, OVERLAY_LIVE, OVERLAY_VEHICLES, PENDING_SELECT_KEY,
  type LiveReply, type OverlayReply, type OverlayVehicle,
} from '../features/maps-overlay/bridge';
import { applyFix, positionAt, shortestAngleDiff, type Phys } from '../features/live-map/physics';
import { ageMs, describeAge, formatAge, stalenessOf } from '../features/live-map/freshness';
import {
  SETTLE_WAIT_MS, beginDrag, dragTransform, endDrag, isMapKey, moveDrag, type Drag,
} from '../features/maps-overlay/gesture';
import { OVERLAY_PREF_KEY, setOverlayPref } from '../features/maps-overlay/pref';
import { cameraDrawable, cameraFromUrl, isStreetView, isVisible, project, sameCamera, showsLabels, type Camera } from '../features/maps-overlay/projection';
import { cardAnchor, colourFor, findMapCanvas, markerAt, needsRemeasure, sameSurface, type Surface } from '../features/maps-overlay/surface';

const ROOT_ID = '4truck-maps-overlay';
const CHIP_ID = '4truck-maps-chip';
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
/** How stale the measured box may get before it is re-read even though
 *  nothing reported a change.  Google's markup is unversioned, so this
 *  is the slack that keeps a missed resize from lasting; see
 *  ``needsRemeasure``. */
const GEOMETRY_RECHECK_MS = 2_000;

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
/** The truck whose card is open, or null.  A selection, not a filter:
 *  every other marker keeps being drawn exactly as before. */
let cardId: string | null = null;
let card: HTMLDivElement | null = null;
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

// ── what this injection owns ───────────────────────────────────────────
//
// An extension update does not reload the pages the old copy is running
// in: it INVALIDATES the copy.  Its timers keep firing, its listeners
// keep running, and every message it sends throws — for as long as the
// tab stays open.  A person who leaves Google Maps open all day and
// updates the extension was left with one orphan per update, each still
// measuring the page eight times a second.  So the timers and listeners
// are held, and the first tick that finds the context gone takes them
// all with it.
const timers: ReturnType<typeof setInterval>[] = [];
const listeners = new AbortController();
type PrefListener = Parameters<typeof chrome.storage.onChanged.addListener>[0];
let onPrefChanged: PrefListener | null = null;
let canvasWatch: ResizeObserver | null = null;

/** Set when something that can move or resize the map's box happened.
 *  Until one does, the box measured last time is still the box. */
let geometryDirty = true;
/** The URL names a view this overlay cannot draw (3D, Street View).
 *  Shown on the switch in words, so a wait that will never end is not
 *  dressed as loading. */
let viewUndrawable = false;
let measuredUrl = '';
let measuredAt = -Infinity;
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

function closeCard(): void {
  card?.remove();
  card = null;
  cardId = null;
}

function removeAll(): void {
  closeCard();
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
    } else if (inView === null && viewUndrawable) {
      label.textContent = '3D view \u2014 switch to Map';
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
    // left/top are fixed at zero and the position rides in a transform:
    // writing left/top puts every marker through layout on every frame,
    // while a transform is handed to the compositor.  With a hundred
    // trucks in view that is the difference between a moving map and a
    // stuttering one.  ``placeAt`` is the only writer.
    el.style.cssText =
      'position:absolute;left:0;top:0;transform:translate(-50%,-50%);pointer-events:none;' +
      'will-change:transform;display:flex;align-items:center;gap:4px;' +
      'font:600 11px/1 system-ui,sans-serif;white-space:nowrap';
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

/** The marker's centre, in layer pixels.  The trailing translate keeps
 *  the glyph centred on the point, which is what the base style used to
 *  say on its own. */
function placeAt(m: MarkerParts, x: number, y: number): void {
  m.el.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`;
}

/** The arrow turns; the marker itself must not, or the name pill would
 *  spin with it. */
function aimArrow(m: MarkerParts, deg: number): void {
  const svg = m.glyph.firstElementChild as SVGElement | null;
  if (svg?.getAttribute('data-glyph') === 'arrow') {
    svg.style.transform = `rotate(${deg}deg)`;
  }
}

// ── the card on the map ────────────────────────────────────────────────
//
// Clicking a truck used to open the side panel: correct, and a trip out
// of the map somebody is in the middle of reading.  The card answers the
// question that made them click — which truck, doing what, how fresh,
// how full — where they asked it, and keeps the panel one button away
// for everything else.
//
// It takes pointer events (the markers deliberately do not, so a drag
// that starts on a truck still drags Google's map); it lives INSIDE the
// layer, so it rides a drag and fades with a gesture exactly as the
// markers do; and it is anchored above its marker, flipped and clamped
// by ``cardAnchor`` so it never hangs off the edge.

const CARD_W = 220;

function cardHtml(v: OverlayVehicle, ts: number): string {
  const age = ageMs(v.updated_at, ts);
  const st = stalenessOf(age);
  const old_ = st === 'stale' || st === 'very_stale';
  const moving = v.status === 'moving' && v.speed_mph > 0;
  return ''
    + '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">'
    +   `<span aria-hidden style="width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:${colourFor(v.status)}"></span>`
    +   `<strong style="flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px">${esc(v.name || v.id)}`
    +     (v.company ? `<span style="font-weight:400;opacity:.6"> · ${esc(v.company)}</span>` : '')
    +   '</strong>'
    +   '<button data-close aria-label="Close" style="all:unset;cursor:pointer;padding:2px 4px;line-height:1;'
    +     'color:rgba(255,255,255,.55);font-size:14px">\u00d7</button>'
    + '</div>'
    + '<div style="font-size:11px;margin-bottom:6px">'
    +   `<span style="font-weight:600;text-transform:capitalize">${esc(v.status)}</span>`
    +   (moving ? `<span style="opacity:.6"> \u00b7 ${Math.round(v.speed_mph)} mph</span>` : '')
    +   (age === null
        ? '<span style="opacity:.6"> \u00b7 no position time</span>'
        : `<span style="${old_ ? 'color:#fbbf24' : 'opacity:.6'}" title="${esc(describeAge(age))}"> \u00b7 ${esc(formatAge(age))} old</span>`)
    + '</div>'
    // What is aboard, when the panel is on Inventory — and only then,
    // because that is the only time the worker sends it.  Counts, never
    // contents: "1 needs attention" is the answer to "is this truck
    // right"; WHICH item it is belongs behind the button.
    + (v.inventory_total == null ? '' :
        '<div style="font-size:11px;margin-bottom:6px">'
        + `<span style="opacity:.6">${v.inventory_total} item${v.inventory_total === 1 ? '' : 's'}</span>`
        + (v.inventory_attention
            ? `<span style="color:#fbbf24;font-weight:600"> \u00b7 ${v.inventory_attention} need${v.inventory_attention === 1 ? 's' : ''} attention</span>`
            : '<span style="opacity:.6"> \u00b7 all settled</span>')
        + '</div>')
    // Fuel, DEF, the address, the faults: all one button away, in the
    // panel, which is where they live — see bridge.ts on what does not
    // cross into a page we do not own.
    + '<button data-panel style="all:unset;box-sizing:border-box;display:block;width:100%;text-align:center;'
    +   'cursor:pointer;margin-top:2px;padding:6px 8px;border-radius:6px;background:#2563eb;color:#fff;'
    +   `font:600 11px/1 system-ui,sans-serif">${esc(panelButtonLabel())}</button>`;
}

/** Which feature the panel will land on, so the button can say what it
 *  actually opens.  Read from storage as the person switches, because a
 *  card promising "levels & more" that opens an inventory list is a bug
 *  the person meets before we do. */
let panelFeature = 'live-map';

function panelButtonLabel(): string {
  return panelFeature === 'inventory'
    ? "Open in 4truck to see what is aboard"
    : 'Open in 4truck for levels & more';
}

function esc(v: string): string {
  return String(v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));
}

/** Draw or move the open card.  Called from every place that moves a
 *  marker, so the card follows its truck rather than floating where the
 *  truck used to be. */
function placeCard(): void {
  if (!cardId || !surface || !root) { closeCard(); return; }
  const v = vehicles.find((x) => x.id === cardId);
  const at = drawnAt.get(cardId);
  // Off screen, filtered away, or gone from the list: the card goes too
  // — a card for a truck that is not on the map describes nothing.
  if (!v || !at) { closeCard(); return; }
  if (!card || !card.isConnected) {
    card = document.createElement('div');
    card.id = '4truck-maps-card';
    card.style.cssText =
      'position:absolute;pointer-events:auto;width:' + CARD_W + 'px;box-sizing:border-box;'
      + 'padding:8px 10px;border-radius:10px;background:rgba(17,20,26,.94);color:#fff;'
      + 'font:400 12px/1.35 system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.45);'
      + 'backdrop-filter:blur(2px)';
    // The card is ours; a click inside it is never a map gesture.
    card.addEventListener('pointerdown', (e) => e.stopPropagation());
    card.addEventListener('click', (e) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-close]')) { closeCard(); return; }
      const picked = cardId ? vehicles.find((x) => x.id === cardId) : undefined;
      if (t?.closest('[data-panel]') && picked) openInPanel(picked);
    });
    root.appendChild(card);
  }
  card.innerHTML = cardHtml(v, performance.now());
  // Measured after the content is in: a two-line unit number makes a
  // taller card, and a card placed against last frame's height sits
  // wrong by exactly that difference.
  const box = { width: CARD_W, height: card.offsetHeight || 120 };
  const pos = cardAnchor(at, box, surface);
  card.style.left = `${pos.left}px`;
  card.style.top = `${pos.top}px`;
}

function openInPanel(v: OverlayVehicle): void {
  // Written first, then the panel is asked to open: whichever arrives
  // first, the panel finds the choice waiting for it.
  //
  // Three fields, not one id: Live Map resolves the map's id, and
  // Inventory — which has no map and cannot ask for one, since
  // /map/vehicles needs the location grant its reader may not hold —
  // resolves the unit number inside its company.
  void chrome.storage.local.set({
    [PENDING_SELECT_KEY]: { id: v.id, name: v.name || '', company: v.company || '' },
  });
  try { chrome.runtime.sendMessage({ type: OPEN_PANEL }); } catch { /* worker asleep; storage still carries it */ }
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
    placeAt(markerFor(v), p.x, p.y);
  }
  // A marker for a vehicle that has left the view is removed, not
  // hidden: a hundred hidden nodes on every Google Maps tab is a cost
  // the person did not agree to.
  for (const [id, m] of markers) {
    if (!seen.has(id)) { m.remove(); markers.delete(id); parts.delete(id); }
  }
  updateChip(seen.size);
  placeCard();
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
    placeAt(m, pt.x, pt.y);
    aimArrow(m, p.headingDeg);
  }
  if (moving) {
    // The open card belongs to its truck, not to a spot on the glass.
    if (cardId) placeCard();
    animFrame = requestAnimationFrame(pump);
  }
}

function ensurePump(): void {
  if (animFrame === null) animFrame = requestAnimationFrame(pump);
}

/** Watch the canvas's own box.  Google's panel opening, a sidebar, a
 *  zoom of the browser: all of them resize the map without a window
 *  resize, and this is how that arrives as an event rather than as a
 *  measurement taken eight times a second on the chance it happened. */
function watchCanvas(): void {
  canvasWatch?.disconnect();
  canvasWatch = null;
  if (!canvasEl || typeof ResizeObserver === 'undefined') return;
  canvasWatch = new ResizeObserver(() => { geometryDirty = true; });
  canvasWatch.observe(canvasEl);
}

/** The camera and the canvas, re-read.  True when either changed.
 *
 *  Reading the canvas means reading rectangles, and a rectangle is a
 *  forced layout.  ``needsRemeasure`` is the rule for when that is
 *  worth doing; on a still page the answer is no, and this costs one
 *  string comparison. */
function refreshView(): boolean {
  const url = location.href;
  const now = performance.now();
  if (!needsRemeasure({
    url, measuredUrl, connected: !!canvasEl?.isConnected,
    geometryDirty, measuredAt, now, recheckMs: GEOMETRY_RECHECK_MS,
  })) return false;
  measuredUrl = url;
  measuredAt = now;
  geometryDirty = false;

  // The canvas first: a satellite URL carries metres, not a zoom, and
  // becomes a camera only against the canvas's height.
  const found = findMapCanvas(Array.from(document.querySelectorAll('canvas')));
  const nextSurface = found?.surface ?? null;
  const nextCamera = cameraFromUrl(url, nextSurface?.height);
  // Coordinates the parser cannot turn into a camera — a tilted 3D
  // view, a Street View pose — are not "not yet"; they are "not here".
  viewUndrawable = !nextCamera && /@-?\d/.test(url) && !cameraDrawable(url);
  const canvasChanged = (found?.el ?? null) !== canvasEl;
  canvasEl = found?.el ?? null;
  if (canvasChanged) watchCanvas();
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
  if (!id) {
    // A press on empty map closes the card, the way every map dismisses
    // a popup — without swallowing the press, which is Google's.
    closeCard();
    return;
  }
  cardId = id;
  placeCard();
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
  if (e.key === 'Escape' && cardId) { closeCard(); return; }
  if (!root || !isMapKey(e.key)) return;
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  fadeUntilSettled();
}

// ── lifecycle ──────────────────────────────────────────────────────────

/** True while this injection can still reach its extension.  An update
 *  or a reload leaves the page's copy running with no way back: the id
 *  goes undefined and every message throws. */
function contextAlive(): boolean {
  try { return !!chrome.runtime?.id; } catch { return false; }
}

/** Give the page back exactly as we found it.
 *
 *  Two ticks can both find the context gone before either has cleared
 *  the other's timer, so this says so once and means it. */
let torn = false;
function teardown(): void {
  if (torn) return;
  torn = true;
  for (const t of timers) clearInterval(t);
  timers.length = 0;
  listeners.abort();
  // chrome.storage.onChanged takes no AbortSignal, so it is the one
  // listener that must be removed by hand — and it is the one that
  // could undo all of this: left registered, an orphan still hears the
  // panel's switch, and its handler puts the layer it had just removed
  // back into the page.
  if (onPrefChanged) {
    try { chrome.storage.onChanged.removeListener(onPrefChanged); } catch { /* context gone */ }
    onPrefChanged = null;
  }
  canvasWatch?.disconnect();
  canvasWatch = null;
  removeAll();
  hideChip();
}

/**
 * Every repeating job runs through here, and so meets the two questions
 * that decide whether it should run at all:
 *
 *   Is the extension still there?  If not this copy is an orphan, and
 *   the only useful thing left to do is remove itself.
 *
 *   Is anybody looking?  A tab in the background has no map on screen,
 *   so measuring it, drawing on it and polling for it are all spent on
 *   nobody.  Five open route tabs used to poll as five.
 */
function repeat(job: () => void, everyMs: number): void {
  timers.push(setInterval(() => {
    if (!contextAlive()) { teardown(); return; }
    if (document.hidden) return;
    job();
  }, everyMs));
}

function start(): void {
  chrome.storage.local.get(OVERLAY_PREF_KEY, (got) => {
    enabled = got[OVERLAY_PREF_KEY] !== false;
    refreshView();
    void refreshData().then(() => refreshFixes());
  });

  repeat(() => {
    const changed = refreshView();
    if (!changed) return;
    // A camera write while we are following or fading is the settle
    // we were waiting for; otherwise it is a programmatic move (a
    // search result, a place card) and a plain redraw.
    if (settling && location.href !== lastInteractionUrl) settle();
    else if (!drag) draw();
  }, URL_POLL_MS);
  repeat(() => { void refreshData(); }, POLL_MS);
  repeat(() => { void refreshFixes(); }, LIVE_POLL_MS);

  const signal = listeners.signal;
  // A tab that was hidden asked for nothing while it was away, so what
  // it holds is as old as the time it spent there.  Coming back is a
  // refresh, not a resume.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden || !contextAlive()) return;
    geometryDirty = true;
    refreshView();
    void refreshData().then(() => refreshFixes());
  }, { signal });
  window.addEventListener('resize', () => {
    geometryDirty = true;
    if (refreshView() && !drag) draw();
  }, { passive: true, signal });

  // Capture on window: we see the gesture before Google's own handlers
  // and never interfere with them — every listener is passive.
  const opts: AddEventListenerOptions = { capture: true, passive: true, signal };
  window.addEventListener('pointerdown', onPointerDown, opts);
  window.addEventListener('pointermove', onPointerMove, opts);
  window.addEventListener('pointerup', onPointerUp, opts);
  window.addEventListener('pointercancel', onPointerUp, opts);
  window.addEventListener('wheel', onWheel, opts);
  window.addEventListener('keydown', onKeyDown, { capture: true, signal });

  // Which feature the card's button will land on.  Read once, then kept
  // in step below — the person switches in the panel, not on this page.
  void chrome.storage.local.get(ACTIVE_FEATURE_KEY).then((got) => {
    const f = got[ACTIVE_FEATURE_KEY];
    if (!torn && typeof f === 'string' && f) panelFeature = f;
  });

  // The panel's own switch reaches here without a reload.
  onPrefChanged = (changes, area) => {
    if (torn || area !== 'local') return;
    if (ACTIVE_FEATURE_KEY in changes) {
      const f = changes[ACTIVE_FEATURE_KEY].newValue;
      panelFeature = typeof f === 'string' && f ? f : 'live-map';
      // An open card is showing the old promise; rewrite it in place.
      if (cardId) placeCard();
    }
    if (!(OVERLAY_PREF_KEY in changes)) return;
    enabled = changes[OVERLAY_PREF_KEY].newValue !== false;
    if (enabled) { geometryDirty = true; refreshView(); void refreshData(); } else draw();
  };
  chrome.storage.onChanged.addListener(onPrefChanged);
}

// `document_idle` already means the page has parsed; the guard is for a
// re-injection after an extension update, when the old layer is still
// in the DOM and a second one would double every marker.
document.getElementById(ROOT_ID)?.remove();
document.getElementById(CHIP_ID)?.remove();
start();
