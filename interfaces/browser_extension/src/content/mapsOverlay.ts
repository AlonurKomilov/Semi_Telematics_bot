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
 * Mercator turns those into pixels — see projection.ts, which is where
 * the arithmetic is proven.
 *
 * WHAT THAT COSTS, said plainly because everything here is shaped by
 * it: Google rewrites the URL when a gesture ENDS, not while it runs.
 * Markers therefore hold still during a drag and land in place when the
 * hand lifts.  Chasing that with a transform guess would put trucks in
 * the wrong street mid-gesture, which is worse than holding still.
 *
 * WHAT IT REFUSES TO DO.  It draws nothing when signed out, nothing in
 * Street View (an overlay of trucks on a photograph is nonsense),
 * nothing when it cannot find a canvas big enough to be a map, and
 * nothing the person has switched off.  Google's page is unversioned:
 * every one of those is a state we will meet without warning, and the
 * right answer to each is to disappear rather than to draw wrong.
 */
import { OVERLAY_VEHICLES, type OverlayReply, type OverlayVehicle } from '../features/maps-overlay/bridge';
import { cameraFromUrl, isStreetView, isVisible, project, sameCamera, type Camera } from '../features/maps-overlay/projection';
import { colourFor, findMapSurface, sameSurface, type Surface } from '../features/maps-overlay/surface';
import { OVERLAY_PREF_KEY } from '../features/maps-overlay/pref';

const ROOT_ID = '4truck-maps-overlay';
const PREF_KEY = OVERLAY_PREF_KEY;
/** Positions are 30s fresh on the server; asking faster spends quota
 *  for numbers that have not changed. */
const POLL_MS = 30_000;
/** Google writes its URL on a settle, so watching it is a poll.  A
 *  quarter second is under the eye's tolerance for a marker landing. */
const URL_POLL_MS = 250;

let camera: Camera | null = null;
let surface: Surface | null = null;
let vehicles: OverlayVehicle[] = [];
let enabled = true;
let root: HTMLDivElement | null = null;
const markers = new Map<string, HTMLDivElement>();

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

function ensureRoot(): HTMLDivElement {
  if (root?.isConnected) return root;
  const el = document.createElement('div');
  el.id = ROOT_ID;
  // pointer-events none on the layer: Google's map must keep every drag,
  // scroll and click it would have had.  Only the markers take a click.
  el.style.cssText = 'position:fixed;z-index:2147483000;pointer-events:none;overflow:hidden';
  document.documentElement.appendChild(el);
  root = el;
  return el;
}

function removeAll(): void {
  root?.remove();
  root = null;
  markers.clear();
}

function markerFor(v: OverlayVehicle): HTMLDivElement {
  let el = markers.get(v.id);
  if (!el) {
    el = document.createElement('div');
    el.style.cssText =
      'position:absolute;transform:translate(-50%,-50%);pointer-events:auto;' +
      'display:flex;align-items:center;gap:4px;font:600 11px/1 system-ui,sans-serif;white-space:nowrap';
    el.innerHTML =
      '<span data-dot style="width:12px;height:12px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.5)"></span>' +
      '<span data-name style="background:rgba(17,20,26,.86);color:#fff;padding:2px 5px;border-radius:4px"></span>';
    ensureRoot().appendChild(el);
    markers.set(v.id, el);
  }
  const dot = el.querySelector<HTMLElement>('[data-dot]')!;
  const name = el.querySelector<HTMLElement>('[data-name]')!;
  dot.style.background = colourFor(v.status);
  name.textContent = v.name || v.id;
  el.title = `${v.name || v.id} — ${v.status}`;
  return el;
}

function draw(): void {
  if (!enabled || isStreetView(location.href)) { removeAll(); return; }
  if (!camera || !surface) { removeAll(); return; }

  const el = ensureRoot();
  el.style.left = `${surface.left}px`;
  el.style.top = `${surface.top}px`;
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
}

/** The camera and the canvas, re-read.  True when either moved enough
 *  to be worth a redraw. */
function refreshView(): boolean {
  const nextCamera = cameraFromUrl(location.href);
  const nextSurface = findMapSurface(Array.from(document.querySelectorAll('canvas')));
  const changed = !sameCamera(camera, nextCamera) || !sameSurface(surface, nextSurface);
  camera = nextCamera;
  surface = nextSurface;
  return changed;
}

async function refreshData(): Promise<void> {
  const reply = await loadVehicles();
  // Signed out, or the API said no.  Either way the honest thing is an
  // empty map rather than positions from ten minutes ago.
  vehicles = reply.ok ? reply.vehicles : [];
  draw();
}

function start(): void {
  chrome.storage.local.get(PREF_KEY, (got) => {
    enabled = got[PREF_KEY] !== false;
    refreshView();
    void refreshData();
  });

  setInterval(() => { if (refreshView()) draw(); }, URL_POLL_MS);
  setInterval(() => { void refreshData(); }, POLL_MS);
  window.addEventListener('resize', () => { if (refreshView()) draw(); }, { passive: true });

  // The panel's own switch reaches here without a reload.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !(PREF_KEY in changes)) return;
    enabled = changes[PREF_KEY].newValue !== false;
    if (enabled) { refreshView(); void refreshData(); } else removeAll();
  });
}

// `document_idle` already means the page has parsed; the guard is for a
// re-injection after an extension update, when the old layer is still
// in the DOM and a second one would double every marker.
document.getElementById(ROOT_ID)?.remove();
start();
