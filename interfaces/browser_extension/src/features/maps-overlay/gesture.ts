/**
 * Following a hand across a map whose camera we cannot read until it
 * stops.
 *
 * Google writes ``@lat,lng,zoom`` into its URL when a gesture SETTLES.
 * Between the finger going down and that write, the only truth about
 * where the map is comes from the pointer itself — and for a plain
 * drag in 2D that truth is exact: Google moves the map one CSS pixel
 * per pixel of pointer travel.  So a drag is followed live, by
 * translating the whole layer by the same delta; when the URL lands,
 * markers are re-projected and the translation is dropped in the same
 * frame.  Nothing jumps.
 *
 * Everything that is NOT a plain drag — a fling after release, a wheel
 * or pinch zoom, a zoom button, an arrow key — animates the camera in
 * a way no pointer delta describes.  For those the layer FADES until
 * the camera settles.  A marker that vanishes for three hundred
 * milliseconds during a zoom reads as intentional; a marker sliding
 * into the wrong street reads as broken.  This module is the rule that
 * decides which of the two a gesture gets.  Pure; the DOM listeners
 * that feed it live in the content script.
 */

export interface Drag {
  startX: number;
  startY: number;
  dx: number;
  dy: number;
  /** Pointer speed, px/ms, smoothed — what decides whether release
   *  becomes a fling. */
  vx: number;
  vy: number;
  lastX: number;
  lastY: number;
  lastT: number;
}

/** Above this release speed Google keeps the map moving after the
 *  finger lifts, and our frozen translation would drift off it. */
export const FLING_SPEED = 0.35;      // px/ms
/** Below this total travel nothing moved that a camera write would
 *  record; the layer can simply be restored. */
export const MOVED_PX = 2;
/** How long to wait for Google's URL after a real move before
 *  concluding it is not coming.  Google writes within ~300 ms; this is
 *  generous so a slow tab never snaps markers to a stale camera. */
export const SETTLE_WAIT_MS = 1500;
/** Weight of the newest sample in the speed estimate.  High enough to
 *  see a flick in the last few events, low enough that one jittery
 *  sample does not read as a fling. */
const SMOOTHING = 0.6;

export function beginDrag(x: number, y: number, t: number): Drag {
  return { startX: x, startY: y, dx: 0, dy: 0, vx: 0, vy: 0, lastX: x, lastY: y, lastT: t };
}

export function moveDrag(d: Drag, x: number, y: number, t: number): Drag {
  const dt = Math.max(1, t - d.lastT);
  const ivx = (x - d.lastX) / dt;
  const ivy = (y - d.lastY) / dt;
  return {
    ...d,
    dx: x - d.startX,
    dy: y - d.startY,
    vx: d.vx * (1 - SMOOTHING) + ivx * SMOOTHING,
    vy: d.vy * (1 - SMOOTHING) + ivy * SMOOTHING,
    lastX: x, lastY: y, lastT: t,
  };
}

export type Release =
  /** Nothing moved: restore the layer now, no camera write is coming. */
  | { kind: 'still' }
  /** A plain drag: keep the translation and wait for the URL; drop the
   *  translation the moment the markers are re-projected. */
  | { kind: 'panned' }
  /** A flick: the map will keep moving on its own; fade until the URL. */
  | { kind: 'flung' };

export function endDrag(d: Drag): Release {
  if (Math.abs(d.dx) < MOVED_PX && Math.abs(d.dy) < MOVED_PX) return { kind: 'still' };
  const speed = Math.hypot(d.vx, d.vy);
  return speed >= FLING_SPEED ? { kind: 'flung' } : { kind: 'panned' };
}

/** Keys Google pans or zooms the map with when the map has focus. */
export function isMapKey(key: string): boolean {
  return key === 'ArrowUp' || key === 'ArrowDown' || key === 'ArrowLeft' || key === 'ArrowRight'
      || key === '+' || key === '-' || key === '=' || key === '_';
}

/** The CSS for a followed drag — one translate for the whole layer,
 *  so a hundred markers cost one style write per move event, and 3d
 *  so the browser composites it instead of re-laying out. */
export function dragTransform(d: Drag | null): string {
  return d ? `translate3d(${d.dx}px, ${d.dy}px, 0)` : '';
}
