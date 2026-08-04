// ── How much room is left below me? ─────────────────────────────────
//
// A surface that wants its own viewport — a table whose body scrolls
// inside its card instead of making the whole PAGE 250 rows tall — needs
// a definite height. Flexbox can give it one, but only if every ancestor
// up to the scroll region cooperates (`h-full flex flex-col min-h-0`),
// and CSS cannot be inverted: a child has no way to impose that on its
// parents.
//
// So the contract lived on the PAGE, and it was demonstrably not paid:
// **3 of 40 grid surfaces** had it. A convention every future page author
// must remember drifts by definition. This measures instead, so the
// component can answer the question itself and no page has to know.
//
// It fails OPEN. No scroll ancestor, no layout (jsdom), too little room —
// every one of those returns `null`, which means "grow naturally and let
// the page scroll", i.e. exactly the behaviour that came before. A
// measurement that goes wrong loses the improvement; it cannot produce a
// broken layout.

import { useLayoutEffect, useRef, useState } from 'react';

/** Below this there is no point clamping — a viewport smaller than a
 *  few rows is worse than letting the page scroll. Pages that stack
 *  charts and KPI cards above their table fall through here on their
 *  own, which is correct: those pages ARE taller than a screen. */
const FLOOR = 256;
/** Applied to the floor in whichever direction preserves the current
 *  decision, so a layout sitting exactly on the boundary can't flap
 *  between clamped and natural on every resize tick. */
const HYSTERESIS = 16;

/** The nearest ancestor that actually scrolls. In this app that is the
 *  shell's one content region — every shell wraps `<Outlet />` in the
 *  same `h-full overflow-y-auto` div — but a grid inside a drawer or a
 *  dialog finds that surface's scroller instead, and clamps to it. */
function findScrollParent(el: HTMLElement): HTMLElement | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') return node;
  }
  return null;
}

/**
 * The height `el` can take without pushing its scroll region past the
 * bottom of the screen — or `null` when it should just grow.
 *
 * Apply it as **`max-height`**, never `height`: a short table then stays
 * short instead of becoming a tall box with empty space under three rows.
 *
 * @param enabled pass `false` where an internally-scrolling table would
 *   be wrong — a table inside a chat message, where the conversation is
 *   what the reader scrolls.
 */
export function useFittedHeight(
  el: HTMLElement | null,
  enabled = true,
): number | null {
  const [height, setHeight] = useState<number | null>(null);
  // What we last WROTE, read inside the observer without re-subscribing.
  const applied = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (!el || !enabled || typeof ResizeObserver === 'undefined') {
      if (applied.current !== null) { applied.current = null; setHeight(null); }
      return;
    }
    const region = findScrollParent(el);
    if (!region) return;

    let frame = 0;
    const measure = () => {
      frame = 0;
      // Scroll-INVARIANT: ``rect.top`` moves as the region scrolls and
      // ``scrollTop`` moves by the same amount the other way, so this is
      // the element's offset within the region's CONTENT. Without the
      // ``scrollTop`` term the available height would grow as the reader
      // scrolled down.
      const offset = el.getBoundingClientRect().top
        - region.getBoundingClientRect().top
        + region.scrollTop;
      // The gap under the card IS the region's own bottom padding (the
      // shell's ``p-4 lg:p-6``). Read it rather than hard-coding a
      // constant that silently disagrees the day the shell is restyled.
      const gap = parseFloat(getComputedStyle(region).paddingBottom) || 0;
      const available = region.clientHeight - offset - gap;

      const clamped = applied.current !== null;
      const limit = clamped ? FLOOR - HYSTERESIS : FLOOR + HYSTERESIS;
      if (available < limit) {
        if (clamped) { applied.current = null; setHeight(null); }
        return;
      }
      const next = Math.floor(available);
      // The loop-breaker. Clamping the card makes the page shorter,
      // which fires the page-root observer, which measures again — and
      // gets the same answer, because neither the region's height nor
      // this element's top depends on this element's height. The write
      // is skipped, and the cascade stops on the second tick.
      if (applied.current !== null && Math.abs(next - applied.current) <= 1) return;
      applied.current = next;
      setHeight(next);
    };

    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();

    const ro = new ResizeObserver(schedule);
    ro.observe(region);
    // The parent catches the surface being resized around us.
    if (el.parentElement) ro.observe(el.parentElement);
    // The page root catches what neither of the others can: content
    // arriving ABOVE us after first paint — a hero strip or a KPI row
    // resolving from its own query — which moves this element DOWN
    // without changing the region's size or our parent's.
    if (region.firstElementChild) ro.observe(region.firstElementChild);
    return () => { ro.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, [el, enabled]);

  return height;
}
