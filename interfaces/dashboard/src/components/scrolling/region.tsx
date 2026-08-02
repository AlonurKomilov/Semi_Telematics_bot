// ── The scroll REGION contract ───────────────────────────────────────
//
// A `overflow-y-auto` div is not a scroll region.  It is a box that
// clips, and four things a reader needs are missing from it by default.
// This module is the one place that knows what they are.
//
// The app had 57 scrolling surfaces and 2 that got this right, both in
// the grid family — so every panel, drawer and list built since has been
// re-deciding it from scratch, and mostly deciding wrong.
//
// ⚠️ NOT everything that scrolls wants this.  The dividing line, and the
// reason it matters, is in ./CLAUDE.md: if the user READS AND NAVIGATES
// the content it is a region; if it is a short menu, dropdown or picker
// list, a plain overflow div is correct and wrapping it in a landmark
// makes the page noisier for a screen-reader user, not clearer.

import { useCallback, useEffect, useRef, useState } from 'react';

/** What one axis does.  CSS models overflow as two INDEPENDENT axes, and
 *  so does this — see `axis` below for why that matters. */
type Overflow = 'auto' | 'hidden' | 'visible';

// ⚠️ The contract's scrolling properties are INLINE STYLE, not classes.
//
// As classes they were clobberable and — worse — ambiguously so.  A
// caller passing `className="overflow-hidden"` produces
// `overflow-hidden overflow-y-auto` from tailwind-merge, because those
// are different merge groups and it keeps both; which axis then wins is
// decided by the ORDER TAILWIND EMITS ITS CSS, not by the order in the
// class attribute.  That is unknowable from the call site, invisible in
// jsdom, and the losing outcome is a focusable, named region a keyboard
// user can enter and cannot scroll — a fresh WCAG 2.1.1 defect made by
// the component built to prevent it.
//
// Inline style outranks every class, so the contract simply cannot be
// deleted by accident.  Overflow goes through `axis`, chaining through
// `allowScrollChaining`: both typed, both greppable.  The caller keeps
// `className` entirely to itself for layout.

export interface ScrollRegionOptions {
  /** Accessible name.  Given one, the pane becomes a `region` landmark a
   *  screen reader can list and jump into.  WITHOUT a label it stays
   *  focusable but unlabelled — an unnamed landmark is worse than none,
   *  so `role` is only applied when there is something to call it. */
  label?: string;
  /** What each axis does.  Default `'y'`.
   *
   *  The shorthands cover the common cases; the OBJECT form exists
   *  because scrolling is two-dimensional and the enum could only name
   *  three of nine combinations — missing the one every surface with
   *  PAINTED scrollbars needs: `{ y: 'auto', x: 'hidden' }`.  That is
   *  not a quirk of one grid, it is this module's own documented
   *  precondition for `useWheelToHorizontal`, so the contract has to be
   *  able to state it. */
  axis?: 'y' | 'x' | 'both' | { y?: Overflow; x?: Overflow };
  /** Height of sticky chrome overlaying the TOP of the scrollport (a
   *  sticky header). */
  stickyTop?: number;
  /** Width of frozen content at the left / right edges (pinned columns). */
  pinnedLeft?: number;
  pinnedRight?: number;
  /** Height of sticky chrome at the BOTTOM (a totals row, an action bar). */
  stickyBottom?: number;
  /** When this value changes the pane returns to the top, because the
   *  content it holds is now a DIFFERENT list.
   *
   *  Compared by the effect's own dependency identity, so pass either a
   *  primitive or a reference you know is memo-stable — an object rebuilt
   *  each render resets on every parent render, which is exactly the bug
   *  that made the pivot report jump to row 1 on unrelated state changes.
   *  `undefined` (the default) means never reset. */
  resetKey?: unknown;
  /** Opt out of `overscroll-contain`.  Almost always wrong: without it a
   *  wheel that reaches this pane's end keeps going and scrolls the page
   *  behind it, which on a modal reads as the app coming apart. */
  allowScrollChaining?: boolean;
}

function resolveAxis(axis: NonNullable<ScrollRegionOptions['axis']>) {
  if (axis === 'y') return { y: 'auto' as const, x: undefined };
  if (axis === 'x') return { y: undefined, x: 'auto' as const };
  if (axis === 'both') return { y: 'auto' as const, x: 'auto' as const };
  return { y: axis.y, x: axis.x };
}

/**
 * The four things a plain `overflow` div is missing, and why:
 *
 * 1. **`tabIndex={0}`** — a plain overflow div is NOT focusable, so a
 *    keyboard user cannot scroll it at all; everything past the first
 *    screen is mouse-only (WCAG 2.1.1, Level A).  This is the single
 *    most common defect: 2 of 57 surfaces had it.
 * 2. **`role="region"` + name** — so it can be found and entered rather
 *    than being an anonymous box.  Only when a label is supplied.
 * 3. **`overscroll-contain`** — stops the scroll chaining to whatever is
 *    behind when you reach the end.  Used ZERO times in this codebase
 *    before this module.
 * 4. **`scroll-padding`** — the browser's own scroll-into-view puts a
 *    tabbed-to element at the scrollport's literal edge, which is BEHIND
 *    any sticky header or frozen column.  The focused thing is then "in
 *    view" and invisible (WCAG 2.4.11).
 *
 * Returns `{ ref, node, nodeRef, props }` rather than rendering, because
 * the two hardest consumers — DataGrid and PivotView — own their own
 * container and compose their own classes onto it.  A wrapper component
 * they could not use would not be a single source of truth; it would be
 * a second one.  `<ScrollRegion>` below is that wrapper, built on this,
 * for everyone else.
 */
export function useScrollRegion(opts: ScrollRegionOptions = {}) {
  const {
    label, axis = 'y', stickyTop, pinnedLeft, pinnedRight, stickyBottom,
    resetKey, allowScrollChaining = false,
  } = opts;

  // The live node, published through a CALLBACK ref rather than a plain
  // ref object.  Consumers that unmount and remount their scroller (the
  // grid does exactly this when pivot toggles) would otherwise leave any
  // effect here observing a detached node — the metrics freeze and the
  // scrollbars silently stop rendering.  State makes the effects
  // re-attach whenever the element is replaced, for any reason.
  //
  // ``nodeRef`` is returned too: a consumer that reads its container
  // imperatively inside a callback (DataGrid's column autosize does a
  // ``querySelector`` from it) would otherwise have to put ``node`` in
  // that callback's deps and churn an identity handed down into every
  // per-column menu.
  const [node, setNode] = useState<HTMLElement | null>(null);
  const nodeRef = useRef<HTMLElement | null>(null);
  const ref = useCallback((el: HTMLElement | null) => {
    nodeRef.current = el;
    setNode(el);
  }, []);

  const { y, x } = resolveAxis(axis);

  // Position only means something relative to the list you were reading.
  // Keeping it across a change of list drops the reader into the middle
  // of content they have not seen, and with a sticky header nothing on
  // screen changes shape to tell them it happened.
  //
  // Skips its FIRST run.  A fresh element is already at 0, so a mount
  // reset can only ever destroy someone else's work: React flushes
  // layout effects before passive ones, so a parent restoring a saved
  // offset in ``useLayoutEffect`` would be written and then zeroed.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (resetKey === undefined) return;
    const el = nodeRef.current;
    if (!el) return;
    // Reset the axes this region actually owns.  Resetting only the
    // vertical would make ``axis="x"`` accept a resetKey and do nothing.
    if (y === 'auto' && el.scrollTop !== 0) el.scrollTop = 0;
    if (x === 'auto' && el.scrollLeft !== 0) el.scrollLeft = 0;
  }, [resetKey, y, x]);

  const props = {
    tabIndex: 0,
    ...(label ? { role: 'region', 'aria-label': label } : null),
    style: {
      overflowY: y,
      overflowX: x,
      overscrollBehavior: allowScrollChaining ? undefined : 'contain',
      scrollPaddingTop: stickyTop || undefined,
      scrollPaddingBottom: stickyBottom || undefined,
      scrollPaddingLeft: pinnedLeft || undefined,
      scrollPaddingRight: pinnedRight || undefined,
    } as React.CSSProperties,
  };

  return { ref, node, nodeRef, props };
}

/**
 * The 80% case: a pane that scrolls its children.
 *
 * Thin over `useScrollRegion`, deliberately — one implementation means
 * the wrapper and the hook cannot drift into two behaviours.
 *
 * `className` is the caller's alone — the contract lives in inline style
 * (see the note above `ScrollRegionOptions`), so no layout class can
 * delete it. `style` is merged with the contract LAST for the same
 * reason.
 */
export function ScrollRegion({
  children, className, style,
  label, axis, stickyTop, pinnedLeft, pinnedRight, stickyBottom,
  resetKey, allowScrollChaining,
  ...rest
}: ScrollRegionOptions & React.HTMLAttributes<HTMLDivElement>) {
  const { ref, props } = useScrollRegion({
    label, axis, stickyTop, pinnedLeft, pinnedRight, stickyBottom,
    resetKey, allowScrollChaining,
  });
  return (
    // ``rest`` LAST for the ordinary div attributes (id, onScroll,
    // data-*, aria-describedby) — a wrapper that silently swallowed them
    // would send callers straight back to a hand-rolled div, which is the
    // outcome this module exists to prevent.  The contract's own
    // className and style are merged above, so they cannot be reached
    // this way: overflow goes through ``axis``, chaining through
    // ``allowScrollChaining``.
    <div
      ref={ref}
      {...props}
      className={className}
      style={{ ...style, ...props.style }}
      {...rest}
    >
      {children}
    </div>
  );
}
