// ── Custom scrollbars — one implementation for every scrolling surface
//    in the grid family ─────────────────────────────────────────────
//
// WHY custom at all: with pinned (frozen) columns a native bar spans the
// WHOLE container, which implies the pinned columns scroll too.  The
// convention (AG Grid's, and ours) is a bar over only the SCROLLABLE
// region.  The vertical one starts below the sticky header for the same
// reason — a native bar runs up alongside the column labels and their ⋮
// menus, so the rows read as scrolling "into" the header.
//
// Only the PAINTING is ours.  ``overflow-y`` stays ``auto`` so wheel,
// touch, keyboard and scroll-into-view keep working natively;
// ``overflow-x`` is ``hidden`` (a native x-bar reserves a track at the
// container's bottom even at height 0) with a wheel handler restoring
// trackpad swipe.
//
// EXTRACTED because the record list and the pivot matrix are two
// renderers inside ONE card.  Each had grown its own answer — the list a
// custom track, the matrix the native bar — and a scrollbar that looks
// different depending on which mode you're in reads as two products.
// What is genuinely shared is the TRACK geometry; what to inset by is
// local, because the two renderers have different DOM (the list knows
// its pinned columns from ``data-pin``, the matrix from its own sticky
// cells), so measurement deliberately stays with the caller.

import { useEffect, useState } from 'react';

import { cn } from '../../lib/utils';

/** Hide the native bars on a container whose scrollbars we draw. */
export const HIDE_NATIVE_SCROLLBAR =
  '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden';

export interface ScrollMetrics {
  scrollLeft: number;
  clientWidth: number;
  scrollWidth: number;
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

const ZERO: ScrollMetrics = {
  scrollLeft: 0, clientWidth: 0, scrollWidth: 0,
  scrollTop: 0, clientHeight: 0, scrollHeight: 0,
};

/**
 * Live scroll geometry for one container.
 *
 * ⚠️ Call this INSIDE a scrollbar, never in the surface that owns the
 * rows.  It sets state on every scroll frame, so a parent that
 * subscribes re-renders its whole subtree while you scroll — on a pivot
 * matrix that is ~22,000 cells reconciled per frame, which freezes the
 * tab.  The bars are two divs; that is the only tree allowed to
 * re-render at scroll rate.  Parents that need "is there overflow"
 * for a layout class use ``useOverflow`` instead, which doesn't watch
 * scrolling at all.
 *
 * Keyed on the ELEMENT, not on props: the table branch unmounts and
 * remounts (pivot on/off), and an effect keyed on anything else ends up
 * observing a detached node — at which point the metrics freeze and the
 * bars silently stop rendering.  Pass the node from a callback ref.
 */
function useScrollMetrics(el: HTMLElement | null): ScrollMetrics {
  const [metrics, setMetrics] = useState<ScrollMetrics>(ZERO);
  useEffect(() => {
    if (!el) return;
    const update = () => {
      setMetrics({
        scrollLeft: el.scrollLeft,
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
        scrollTop: el.scrollTop,
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
      });
    };
    update();
    // Coalesce to one update per frame.  Scroll fires far faster than
    // paint, and each extra call is a wasted render of the bar.
    let raf = 0;
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; update(); });
    };
    el.addEventListener('scroll', schedule, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    // Also re-measure when the inner table reflows (rows added, density
    // change widens cells, a column resized).
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener('scroll', schedule);
      ro.disconnect();
    };
  }, [el]);
  return metrics;
}

/** `deltaMode` units → CSS pixels.  Chrome and Safari report pixels, but
 *  **Firefox reports LINES** for a physical mouse wheel, so an
 *  un-normalised handler moved the grid 3px per notch there. */
const LINE_PX = 16;

/**
 * Trackpad horizontal swipe / shift+wheel → `scrollLeft`.
 *
 * The container is ``overflow-x: hidden`` (so no native bar reserves a
 * track at its bottom edge), which also means the browser ignores these
 * gestures — this hook is what puts them back.
 *
 * ⚠️ CALL IT EXACTLY ONCE PER CONTAINER, from whoever owns the scroll
 * element — never from a scrollbar.  It used to live inside
 * ``useScrollMetrics``, which BOTH bars call on the same element, so two
 * handlers each added the same delta and every trackpad swipe scrolled
 * TWICE AS FAR as it should.  The early ``return null`` in a bar that
 * isn't needed does not save you: hooks run before it, so even an
 * invisible bar had its handler installed.
 *
 * Sets no state, so calling it from a parent cannot re-render anything at
 * scroll rate.
 */
export function useWheelToHorizontal(el: HTMLElement | null) {
  useEffect(() => {
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const raw = e.shiftKey && e.deltaX === 0 ? e.deltaY : e.deltaX;
      if (raw === 0) return;
      // DOM_DELTA_LINE = 1, DOM_DELTA_PAGE = 2.
      const unit = e.deltaMode === 1 ? LINE_PX
        : e.deltaMode === 2 ? el.clientWidth
          : 1;
      el.scrollLeft += raw * unit;
    };
    // Passive: there is nothing to preventDefault — the browser already
    // won't scroll this axis.
    el.addEventListener('wheel', onWheel, { passive: true });
    return () => el.removeEventListener('wheel', onWheel);
  }, [el]);
}

/**
 * Does this container overflow?  Booleans only, and NO scroll listener —
 * a surface that needs to reserve space for a scrollbar re-renders when
 * overflow appears or disappears, not while you scroll.
 */
export function useOverflow(el: HTMLElement | null): { x: boolean; y: boolean } {
  const [state, setState] = useState({ x: false, y: false });
  useEffect(() => {
    if (!el) return;
    const measure = () => setState((prev) => {
      const next = {
        x: el.scrollWidth > el.clientWidth + 1,
        y: el.scrollHeight > el.clientHeight + 1,
      };
      return prev.x === next.x && prev.y === next.y ? prev : next;
    });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [el]);
  return state;
}

/** Shared thumb geometry for one axis. */
function geometry(track: number, visible: number, total: number, at: number) {
  const max = Math.max(0, total - visible);
  const ratio = total > 0 ? visible / total : 1;
  // Floor of 24px so the thumb stays grabbable on a very long table.
  const thumb = Math.max(24, Math.round(track * ratio));
  const thumbMax = Math.max(0, track - thumb);
  const offset = Math.round(thumbMax * (max > 0 ? at / max : 0));
  return { max, thumb, thumbMax, offset };
}

const TRACK = 'relative bg-muted/40 rounded-full cursor-pointer';
// ``touch-none`` so a drag on the thumb isn't stolen by the page's own
// pan gesture — on a touchscreen this bar is the ONLY way to scroll the
// horizontal axis, so losing the gesture loses the axis.
const THUMB = 'absolute bg-muted-foreground/50 hover:bg-muted-foreground/70'
  + ' rounded-full cursor-grab active:cursor-grabbing touch-none';

/**
 * Thumb drag, via POINTER CAPTURE.
 *
 * Capture routes every later event for this pointer to the thumb itself,
 * so the drag survives the pointer leaving the window and the listeners
 * die with the element. The previous version hung `pointermove` /
 * `pointerup` on `window` with no `pointercancel` branch and no unmount
 * cleanup: a pointerup the window never saw (a drag ending outside the
 * viewport, a touch cancelled by the OS, the bar unmounting mid-drag)
 * left a live `pointermove` handler that scrolled the grid whenever the
 * mouse moved, forever.
 *
 * `onMove` receives the pointer delta along the dragged axis.
 */
function makeThumbDrag(onMove: (delta: number) => void, axis: 'x' | 'y') {
  return (e: React.PointerEvent) => {
    e.preventDefault();
    const node = e.currentTarget as HTMLElement;
    const start = axis === 'x' ? e.clientX : e.clientY;
    node.setPointerCapture?.(e.pointerId);
    const move = (mv: PointerEvent) => {
      if (mv.pointerId !== e.pointerId) return;
      onMove((axis === 'x' ? mv.clientX : mv.clientY) - start);
    };
    const end = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return;
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', end);
      node.removeEventListener('pointercancel', end);
      // Throws if the element already lost capture (it unmounted, or the
      // browser released it) — that is the case we are cleaning up after.
      try { node.releasePointerCapture?.(e.pointerId); } catch { /* already gone */ }
    };
    node.addEventListener('pointermove', move);
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', end);
  };
}

/**
 * Horizontal scrollbar, spanning only the region that actually scrolls.
 *
 * ``flow`` places it as a normal-flow row BELOW the body instead of an
 * absolute overlay.  That matters when the container is a fixed-height
 * viewport: an overlay pinned to the container's bottom rides
 * permanently over whichever row sits at the edge, and padding can't
 * reserve space for it because that padding only pays out once you've
 * scrolled to the very end.
 */
export function ScrollbarH({
  el, insetLeft = 0, insetRight = 0, flow = false,
}: {
  el: HTMLElement | null;
  insetLeft?: number;
  insetRight?: number;
  flow?: boolean;
}) {
  const metrics = useScrollMetrics(el);
  const track = Math.max(0, metrics.clientWidth - insetLeft - insetRight);
  const needed = metrics.scrollWidth > metrics.clientWidth + 1;
  const { max, thumb, thumbMax, offset } =
    geometry(track, metrics.clientWidth, metrics.scrollWidth, metrics.scrollLeft);
  if (!needed || track <= 0) return null;

  const from = metrics.scrollLeft;
  const drag = makeThumbDrag((delta) => {
    if (!el || thumbMax <= 0) return;
    el.scrollLeft = Math.max(0, Math.min(max, from + (delta / thumbMax) * max));
  }, 'x');
  // Click the empty track to page in that direction — standard bar.
  const page = (e: React.MouseEvent) => {
    if (e.target !== e.currentTarget || thumbMax <= 0 || !el) return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const forward = e.clientX - rect.left > offset + thumb / 2;
    el.scrollLeft = Math.max(0, Math.min(max, metrics.scrollLeft + (forward ? track : -track)));
  };

  return (
    <div
      className={cn('h-2', flow ? 'shrink-0 my-1' : 'absolute bottom-1')}
      style={flow
        ? { marginLeft: insetLeft, marginRight: insetRight }
        : { left: insetLeft, right: insetRight }}
    >
      <div className={cn(TRACK, 'h-full w-full')} onClick={page}>
        <div
          className={cn(THUMB, 'top-0 bottom-0')}
          style={{ width: thumb, transform: `translateX(${offset}px)` }}
          onPointerDown={drag}
        />
      </div>
    </div>
  );
}

/**
 * Vertical scrollbar, starting BELOW the sticky header so it never runs
 * alongside the column labels or their ⋮ menus.  Invisible until the
 * pointer is over the surface, matching the app's slim-scrollbar
 * treatment (index.css) — hence the ``group-hover/grid`` hook, which
 * requires the wrapper to carry ``group/grid``.
 */
export function ScrollbarV({
  el, insetTop = 0,
}: {
  el: HTMLElement | null;
  insetTop?: number;
}) {
  const metrics = useScrollMetrics(el);
  const track = Math.max(0, metrics.clientHeight - insetTop);
  const needed = metrics.scrollHeight > metrics.clientHeight + 1;
  const { max, thumb, thumbMax, offset } =
    geometry(track, metrics.clientHeight, metrics.scrollHeight, metrics.scrollTop);
  if (!needed || track <= 0) return null;

  const from = metrics.scrollTop;
  const drag = makeThumbDrag((delta) => {
    if (!el || thumbMax <= 0) return;
    el.scrollTop = Math.max(0, Math.min(max, from + (delta / thumbMax) * max));
  }, 'y');
  const page = (e: React.MouseEvent) => {
    if (e.target !== e.currentTarget || thumbMax <= 0 || !el) return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const down = e.clientY - rect.top > offset + thumb / 2;
    el.scrollTop = Math.max(0, Math.min(max, metrics.scrollTop + (down ? track : -track)));
  };

  return (
    <div
      className="absolute right-0.5 w-2 opacity-0 group-hover/grid:opacity-100 transition-opacity"
      style={{ top: insetTop, height: track }}
    >
      <div className={cn(TRACK, 'h-full w-full')} onClick={page}>
        <div
          className={cn(THUMB, 'left-0 right-0')}
          style={{ height: thumb, transform: `translateY(${offset}px)` }}
          onPointerDown={drag}
        />
      </div>
    </div>
  );
}
