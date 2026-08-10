import { useEffect, useMemo, useRef, useState } from 'react';
import {
  TableProperties, ChevronRight, ChevronDown, ArrowUp, ArrowDown,
} from 'lucide-react';

import { cn } from '../../../lib/utils';
import {
  ScrollbarH, ScrollbarV, HIDE_NATIVE_SCROLLBAR, useScrollRegion,
} from '../../scrolling';
import { EmptyState } from '../../shell';
import { Tip } from '../../tooltip';
import { Button } from '../../ui/button';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { formatAggDefault } from '../aggregation';
import { pivot, splitLeafId, type PivotModel } from './pivot';
import DrillDialog, { type DrillHandle } from './DrillDialog';

/** Rows of scroll before the window is recomputed.  Coarse on purpose —
 *  the overscan covers the gap, and each re-window is a real render. */
const WINDOW_BUCKET = 10;
/** Rows rendered beyond the viewport on each side, so a fast scroll
 *  never shows blank space before the next bucket lands. */
const OVERSCAN = 15;
/** First-paint row-height estimates, keyed by the density token.
 *
 *  Windowing needs a row height, and the real one comes from the DOM —
 *  which does not exist yet on the first paint.  Rather than render the
 *  whole report to find out (what this used to do), start from what the
 *  density already tells us: `min-h-6` content plus the token's own
 *  vertical padding.
 *
 *  Deliberately UNDER-estimates.  Too small means a slightly larger
 *  window — a few wasted rows; too large would mean too few rows and a
 *  visible gap at the bottom of the viewport for one frame. */
const EST_ROW_H: Record<string, number> = {
  'py-1': 28,
  'py-3': 40,
  'py-5': 56,
};
/** For a density token this file hasn't been told about. */
const EST_ROW_H_FALLBACK = 40;
/** First-paint viewport estimate — generous, for the same reason the row
 *  heights are conservative: over-estimating renders extra rows, while
 *  under-estimating would briefly show a short table on a tall screen. */
const EST_VIEWPORT = 900;
/** Joins a bucket path into one map key.  NUL because it cannot occur in
 *  a bucket value, so ["A B"] and ["A","B"] can never collide.  Named
 *  rather than inlined: written as a raw character it is INVISIBLE in
 *  the source and unmatchable by search. */
const PATH_KEY = '\u0000';

/**
 * The pivoted matrix — a READ-ONLY report view.
 *
 * Deliberately NOT the interactive grid: pinning, resize, per-column
 * menus, row selection and drag-reorder are meaningless on synthesized
 * aggregate rows, so this renders its own plain table instead of routing
 * pivot output back through DataGrid's renderer (which would mean
 * flag-disabling most of that file from inside it).
 *
 * The multi-level header is a plain ``rowSpan``/``colSpan`` <thead> — it
 * does NOT touch the grid's ``groupRuns`` bracket row, which is entangled
 * with drag-reorder and pin offsets and already works.
 *
 * Styling mirrors the grid on purpose so switching modes doesn't feel
 * like a different product: the agg fn sits as a muted micro-label under
 * each value header (MUI's "Gross / sum"), and the total row reuses the
 * footer-aggregation treatment (``bg-muted font-semibold text-primary``).
 */
export default function PivotView({
  rows, model, columns, padding, onModelChange, onOpenPanel, fill, onRowCount,
  onHiddenColumns,
  onCappedColumns,
}: {
  /** MUST be the same post-segment/filter/search rows the grid's own
   *  footer aggregation reduces, so the two can never disagree. */
  rows: Record<string, unknown>[];
  model: PivotModel;
  columns: AnyColumn[];
  /** The density padding classes the grid is currently using. */
  padding: string;
  /** Sorting writes back to the model, so the choice persists like the
   *  rest of the report's configuration. */
  onModelChange?: (next: PivotModel) => void;
  /** Opens the fields panel from the empty state.  An empty state that
   *  only DESCRIBES where the control is leaves the reader stranded when
   *  the panel happens to be closed — which is exactly when they see it. */
  onOpenPanel?: () => void;
  /** Own the grid's height: pin the caption to the top and scroll only
   *  the matrix beneath it. */
  fill?: boolean;
  /** How many report rows are visible (collapse applied).  Reported UP
   *  rather than drawn here, because the count belongs in the card's
   *  footer band — the same slot, shell and type as the pagination bar
   *  it replaces.  Drawn locally it sat inside the matrix column at a
   *  smaller size with no background, so switching modes visibly
   *  changed the shape of the card's bottom edge. */
  onRowCount?: (n: number) => void;
  /** How many column buckets ``hideEmptyColumns`` removed, so the footer
   *  can SAY so.  A matrix quietly missing columns is worse than one
   *  showing empty ones. */
  onHiddenColumns?: (n: number) => void;
  /** Column buckets withheld by the render cap — reported so the
   *  surface can SAY so.  A matrix quietly missing columns that
   *  HAVE data is the failure this whole path exists to avoid. */
  onCappedColumns?: (n: number) => void;
}) {
  const result = useMemo(
    () => pivot(rows, model, columns),
    [rows, model, columns],
  );
  const colByKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
  );

  // Collapsed groups, by path id.  Session state, not a preference: it's
  // a reading position, and restoring yesterday's half-open tree would be
  // more surprising than starting expanded.  Default = expanded, so the
  // data is visible before the operator has learned the chevron.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  // Opening a cell must NOT re-render the matrix, so the dialog owns its
  // own state and is reached imperatively.  Held as state here it cost
  // ~12% of a full mount per click to display a dialog that changes
  // nothing about the table — see DrillDialog's header.
  const drillRef = useRef<DrillHandle>(null);
  // Opt-in (default off), so an ordinary report carries no buttons at all.
  const canDrill = model.drillDown ?? false;
  // Display label per bucket path, so a drill can name a row's whole
  // ANCESTRY and not just its leaf.  "Bolt" alone stops identifying
  // anything the moment two companies each have a customer called that
  // — and a pivot exists precisely because bucket names recur under
  // different parents.  Labels, never the raw path: a month bucket's
  // key is `2026-01` while its header reads `Jan 2026`.
  const labelByPath = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of result.bodyRows) m.set(r.path.join(PATH_KEY), r.label);
    return m;
  }, [result.bodyRows]);
  const labelsFor = (path: string[]) => path.map(
    (bucket, d) => labelByPath.get(path.slice(0, d + 1).join(PATH_KEY)) ?? bucket,
  );
  // Column coordinates for leaf column ``i`` — the query path plus what
  // to call it.  The Total column passes its own (empty path, "Total"),
  // which is what makes it drillable at all: an empty colPath means
  // "every column", which is exactly what a Total re-aggregates over.
  const leafCoords = (i: number) => {
    const { colPath, valueKey } = splitLeafId(result.leafIds[i]);
    return { colPath, colLabel: colPath.join(' · '), valueKey };
  };
  const toggle = (key: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  // A row is hidden when ANY ancestor is collapsed — checking every
  // prefix (not just the parent) keeps a deep tree correct.
  const visibleRows = useMemo(() => result.bodyRows.filter((row) => {
    for (let d = 1; d < row.path.length; d += 1) {
      if (collapsed.has(row.path.slice(0, d).join(PATH_KEY))) return false;
    }
    return true;
  }), [result.bodyRows, collapsed]);

  // The REPORT's size, not the number of lines currently on screen.
  // Reporting visibleRows made a "total" that shrank every time a group
  // was collapsed — a total that moves when you fold a row is describing
  // the viewport, not the report.
  useEffect(() => {
    onRowCount?.(result.bodyRows.length);
  }, [result.bodyRows.length, onRowCount]);
  useEffect(() => {
    onHiddenColumns?.(result.hiddenColumns);
  }, [result.hiddenColumns, onHiddenColumns]);
  useEffect(() => {
    onCappedColumns?.(result.cappedColumns);
  }, [result.cappedColumns, onCappedColumns]);

  // Our own scroll container + the insets its bars need.  The shared
  // scrollbars own the track geometry; what to inset BY is local,
  // because the two renderers freeze different things — the record list
  // reads ``data-pin`` off its leaf header row, this table's frozen
  // edges are the corner cell and the Total group.
  // NO scroll subscription here.  The matrix is ~22,000 cells at 360
  // rows x 61 columns; re-rendering it on every scroll frame is what
  // froze the tab.  The bars watch the element themselves.
  // The scroll REGION contract owns the node, the a11y attributes, the
  // overflow on both axes, the overscroll containment and the
  // scroll-padding.  ``insets`` are measured below and fed back in, so
  // the padding tracks the frozen edges as they change.
  // ⚠️ Keyed by the model's VALUE, not its object identity.
  //
  // DataGrid rebuilds ``model`` whenever ``columns`` gets a new array
  // identity, and a page that builds its column config inline hands over
  // a fresh array on EVERY parent render — so an identity-keyed effect
  // threw the report back to the top on any unrelated state change
  // upstream (a tooltip opening, a poll landing).  The reader lost their
  // place for no reason they could see.
  //
  // Only the fields that change WHICH ROWS EXIST belong in the key.  The
  // pins and the drill toggle deliberately do not: freezing a column is
  // not a new list, and scrolling someone to the top for it would be the
  // same bug wearing a different hat.
  const listKey = useMemo(() => JSON.stringify([
    model.rows,
    model.columns,
    model.values,
    model.disabled ?? [],
    model.sort ?? null,
    model.hideEmptyColumns ?? false,
  ]), [model]);

  // What the REGION resets on.  ``rows`` by identity — it is the
  // post-filter/search array and cannot be serialised at ~22,000 cells —
  // paired with the model's VALUE key above.  Memoised so the region
  // sees one stable reference per real change.
  const listSignal = useMemo(() => [rows, listKey], [rows, listKey]);

  const [insets, setInsets] = useState({ left: 0, right: 0, top: 0 });
  // What makes the pane a REPORT the reader can navigate, rather than a
  // box that clips: focusable + named, overscroll contained, and the
  // scrollport padded away from the frozen edges.  That last one this
  // view never had — it pins a <thead>, a <tfoot> and both columns, and
  // already measured all three insets, so a tabbed-to cell landed behind
  // them (WCAG 2.4.11).  Feeding the measured values straight back keeps
  // the padding in step as pins are toggled.
  const region = useScrollRegion({
    label: 'Pivot report',
    // y follows ``fill``: with no owned viewport this view does not
    // scroll vertically at all — the page does.  x is ``auto`` so the
    // browser still owns the gesture: touch pan and keyboard both work,
    // which ``hidden`` had switched off on a 61-column matrix reachable
    // only by dragging an 8px thumb.  The painted bar stays; it simply
    // stops being the only way.
    axis: { y: fill ? 'auto' : 'visible', x: 'auto' },
    stickyTop: insets.top,
    pinnedLeft: insets.left,
    pinnedRight: insets.right,
    resetKey: listSignal,
  });
  const scrollEl = region.node;
  const setScrollEl = region.ref;
  // NO wheel bridge: with x ``auto`` the browser applies ``deltaX``
  // itself, so adding one would move the matrix twice per swipe.
  // Windowing geometry, measured from the DOM by the same
  // ResizeObserver — never from a scroll listener.
  const [box, setBox] = useState({ viewport: 0, rowH: 0 });
  useEffect(() => {
    const el = scrollEl;
    if (!el) return;
    const measure = () => {
      const head = el.querySelector<HTMLElement>('thead');
      const corner = el.querySelector<HTMLElement>('thead [data-pin="left"]');
      // ⚠️ LEAF ROW ONLY (`tr:last-child`).  ``data-pin="right"`` is on
      // BOTH the Total group cell and the leaf cells it spans, so summing
      // every match counted the frozen width TWICE — the group's own
      // width is already the sum of its leaves.  The scrollbar track was
      // then inset by about double, shrinking it and putting the thumb
      // out of step with the content whenever the Total column was
      // pinned.
      const rights = el.querySelectorAll<HTMLElement>(
        'thead tr:last-child [data-pin="right"]',
      );
      let right = 0;
      rights.forEach((c) => { right += c.offsetWidth; });
      const next = {
        left: corner?.offsetWidth ?? 0,
        right,
        top: head?.offsetHeight ?? 0,
      };
      setInsets((prev) => (
        prev.left === next.left && prev.right === next.right && prev.top === next.top
          ? prev : next
      ));
      // One rendered data row decides the window's arithmetic.  Rows are
      // single-line (`whitespace-nowrap`) and now share a min-height, so
      // one measurement is valid for all of them.
      const row = el.querySelector<HTMLElement>('tbody tr[data-prow]');
      const nextBox = {
        viewport: el.clientHeight,
        rowH: row?.offsetHeight ?? 0,
      };
      setBox((prev) => (
        prev.viewport === nextBox.viewport && prev.rowH === nextBox.rowH
          ? prev : nextBox
      ));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [scrollEl, padding, model]);

  // ── Row windowing ─────────────────────────────────────────────────
  // A wide report is ~22,000 cells; only ~30 rows are ever on screen.
  //
  // The subscription is QUANTISED on purpose: it re-windows once per
  // BUCKET of rows scrolled, not per frame, so a scroll produces a
  // handful of renders of ~2,000 cells instead of 60 renders of 22,000.
  // This is the sanctioned exception to "nothing outside a scrollbar
  // subscribes to scroll" — it subscribes to a coarse BUCKET, not to the
  // position.
  const [bucket, setBucket] = useState(0);
  // Measurement WINS; the estimate exists only to cover the first paint.
  //
  // Windowing used to be gated on ``box.rowH > 0``, and rowH starts at 0
  // because it comes from the DOM — so the first paint had no window and
  // rendered every row (~22,000 cells on a wide report), one row was
  // measured, and the second paint threw ~90% of that away.  It drew the
  // whole table just to find out how tall a row is, on EVERY mount:
  // every switch-on, every trip back from list mode.  Measured at ~8x
  // the cost of any other interaction in this view.
  //
  // We already know the answer well enough to start: density is a prop
  // with three possible values.  The estimates are deliberately LOW, so
  // the error mode is "rendered a few rows more than needed" — never a
  // gap at the bottom of the viewport.
  const rowH = box.rowH || EST_ROW_H[padding] || EST_ROW_H_FALLBACK;
  const viewport = box.viewport || EST_VIEWPORT;
  const windowing = !!fill;
  useEffect(() => {
    const el = scrollEl;
    if (!el || !windowing) return;
    const step = rowH * WINDOW_BUCKET;
    const onScroll = () => {
      const next = Math.max(0, Math.floor(el.scrollTop / step));
      setBucket((prev) => (prev === next ? prev : next));
    };
    onScroll();
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [scrollEl, windowing, rowH]);

  // The family rule is "scroll position resets when the list changes
  // IDENTITY", and datagrid/CLAUDE.md claims it holds in BOTH modes.  It
  // didn't: DataGrid's effect writes ``scrollContainerRef``, which is the
  // record list's scroller — the matrix owns a different element, so
  // changing a filter, the search or the tab left the report scrolled
  // mid-way through a set of rows that no longer meant the same thing.
  // The bucket resets with it, or the window would be computed for a
  // scroll position that no longer exists.

  // The REGION resets scrollTop (same ``listSignal``); this keeps the
  // windowing bucket in step with it.  Deliberately a paired local
  // effect rather than an ``onReset`` option on the hook: one caller does
  // not clear the module's bar of two real consumers, and that callback
  // is the first step onto the slope the whole design is guarding.
  useEffect(() => {
    setBucket(0);
  }, [listSignal]);

  const win = useMemo(() => {
    const all = visibleRows;
    if (!windowing) return { rows: all, from: 0, padTop: 0, padBottom: 0 };
    const perView = Math.max(1, Math.ceil(
      Math.max(0, viewport - insets.top) / rowH,
    ));
    // Below this there is nothing to win, and a spacer would only add
    // a chance to be wrong.
    if (all.length <= perView + OVERSCAN * 2) {
      return { rows: all, from: 0, padTop: 0, padBottom: 0 };
    }
    // Clamp the START against the list's own end.  Scroll deep into 360
    // rows, then collapse every group down to 4: the bucket is still ~30,
    // so an unclamped ``from`` lands past the end, the slice comes back
    // EMPTY, and you get a tall spacer with no rows behind it.
    const maxFrom = Math.max(0, all.length - perView - OVERSCAN);
    const from = Math.min(maxFrom, Math.max(0, bucket * WINDOW_BUCKET - OVERSCAN));
    const to = Math.min(all.length, from + perView + OVERSCAN * 2);
    return {
      rows: all.slice(from, to),
      from,
      padTop: from * rowH,
      padBottom: (all.length - to) * rowH,
    };
  }, [visibleRows, windowing, bucket, rowH, viewport, insets.top]);

  // Is the report SHORT enough to leave blank space under it?  Only then
  // does the slack-absorber row have a job (see its comment in <tbody>).
  //
  // Built from the container's height, the header's, and a DATA row's —
  // never from the container's overflow, and never from the absorber's
  // own height.  Anything that measured the absorber would be circular:
  // mounting the row could create the overflow that unmounts it.
  //
  // A rough estimate is fine in BOTH directions.  Slightly too eager and
  // the row mounts with no surplus to take, so it is 0 tall; slightly
  // too shy and the Total sits a few pixels above the bottom edge.
  // Neither can strand a row or hide a figure.
  const needsSlack = visibleRows.length * rowH + insets.top < viewport;

  if (result.empty) {
    // Only ROWS are required now: without a measure the report still
    // shows the groups and their counts, so the one thing that can't be
    // missing is what each line represents.
    return (
      <EmptyState
        icon={TableProperties}
        title="Choose a field to group by"
        // "Fields" was the old toolbar button's name.  That button is
        // gone — the panel is titled Pivot — so the copy was pointing at
        // a control that no longer exists anywhere on screen.  Name what
        // the reader can actually see, and ship the button rather than
        // only describing where to find it.
        description="In the Pivot panel, open Rows and pick what each line should represent, e.g. Customer. Add numbers to total under Values."
        action={onOpenPanel ? (
          <Button type="button" onClick={onOpenPanel}>Open Pivot</Button>
        ) : undefined}
      />
    );
  }

  /** Format one cell through the column's own ``aggFormat`` when it has
   *  one (currency, units) — the same formatter the footer total uses, so
   *  a pivoted number and a footer number never render differently. */
  const renderCell = (value: number | null, valueKey: string, fn: AggFn) => {
    if (value === null) {
      // An empty intersection.  A dash, never 0 — 0 would read as a real
      // measured zero.
      return <span className="text-muted-foreground/60">—</span>;
    }
    const col = colByKey.get(valueKey);
    return col?.aggFormat ? col.aggFormat(value, fn) : formatAggDefault(value, fn);
  };

  const leafCount = result.leafIds.length;
  // ``padding`` is the density token and it is VERTICAL ONLY
  // (py-1/py-3/py-5).  List mode still gets horizontal padding because it
  // renders through the primitives — ``TableCell`` is ``p-2``,
  // ``TableHead`` is ``px-2`` — but this view hand-rolls <td>/<th> and so
  // inherited none of it.  That is why adjacent columns' figures touched
  // ("—$47,200.00", "$28,$1,530,862.60"): there was no gutter at all.
  const cellPad = cn(padding, 'px-2');

  // Class strings for the cell grid, computed ONCE per render.
  //
  // These were built inside the row loop with ``cn`` — which is
  // ``twMerge(clsx(...))``, i.e. it PARSES class strings and resolves
  // conflicts — twice per cell.  At 360 rows x 62 columns that was
  // ~45,000 parses per render for what is really four invariant strings.
  const CELL_NUM = 'text-right tabular-nums whitespace-nowrap';
  const RULE = 'border-l border-border/50';
  // An empty intersection is now ONE element: the dash is a text child of
  // the <td>.  It used to be three (<td> > padded <span> > muted <span>),
  // and on a matrix that is ~90% dashes those two extra nodes per cell
  // were ~40,000 DOM nodes drawing nothing.
  const emptyCell = cn(cellPad, CELL_NUM, 'text-muted-foreground/60');
  const emptyCellRuled = cn(emptyCell, RULE);
  const valueCell = cn(CELL_NUM, 'p-0');
  const valueCellRuled = cn(valueCell, RULE);
  // Drill OFF.  The <td> is ``p-0`` (it expects a padded child), so the
  // padding rides here exactly as it does on the button — otherwise the
  // figures fuse with the next column, which is the defect this file has
  // already been bitten by twice.
  const valueText = cn(cellPad, 'block w-full text-right tabular-nums');
  const valueButton = cn(
    cellPad,
    'block w-full text-right tabular-nums cursor-pointer',
    // A cue AT REST — every non-empty figure drills down, and both
    // empty and filled cells read as plain text until hovered.
    'underline decoration-dotted decoration-border underline-offset-4',
    'hover:bg-primary/10 hover:text-primary hover:decoration-primary',
  );

  // The row-label column stays put during horizontal scroll — otherwise a
  // wide matrix scrolls the identity off screen and the numbers lose
  // their subject.  Plain sticky; deliberately not the grid's pin maths.
  //
  // Every pinned edge carries a SEAM: a crisp 1px inset line plus the
  // grid's own --pin-shadow token (theme-tuned).  Without it the
  // scrolled column slides under the pinned one with nothing marking
  // the boundary — headers fused into "DUFITU THEMI NSENG|TOTAL" and
  // digits glued across the join (layout-audit S1: two regions must
  // never read as one).  box-shadow, not border: these tables are
  // border-collapse, where borders don't reliably travel with sticky
  // cells.
  // Both frozen edges are opt-IN (default off).  Unpinned they become
  // ORDINARY cells — no position, no z-index, no opaque fill and no seam:
  // a cell that isn't frozen has nothing to occlude, and keeping bg-card
  // would only hide the row's own zebra stripe.
  const pinRows = model.pinRowLabels ?? false;
  const pinTotals = model.pinTotals ?? false;
  const stickyCol = pinRows
    ? 'sticky left-0 z-10 bg-card [box-shadow:inset_-1px_0_0_var(--border),var(--pin-shadow-right)]'
    : '';
  // ⚠️ The BAND fill is unconditional; only the freeze is opt-in.
  //
  // <thead> and <tfoot> are sticky whenever ``fill`` is on, pinning or
  // not — they float over the body rows by design.  So every cell in
  // them must carry its OWN opaque background: a sticky element with a
  // transparent cell lets whatever scrolls underneath show straight
  // through it.
  //
  // These two classes used to fold ``bg-muted`` in with the pin, which
  // was invisible while pinning defaulted ON and became a real defect
  // the moment it defaulted OFF: the header's and footer's rightmost
  // cells lost their fill, and body figures bled through the grand
  // total ("$1,600.00" over "$1,590,900.60").  Owner-reported.
  //
  // The BODY's equivalents (stickyCol / stickyTotalCell) are deliberately
  // the opposite — unpinned they must be fully ordinary, with no fill at
  // all, or ``bg-card`` would paint over the row's own zebra stripe.
  const BAND_FILL = 'bg-muted';
  const stickyHead = cn(
    BAND_FILL,
    pinRows && 'sticky left-0 z-20 [box-shadow:inset_-1px_0_0_var(--border),var(--pin-shadow-right)]',
  );
  // The Total column pins to the RIGHT edge for the same reason the row
  // label pins left: with 60 driver columns the figure you actually came
  // for would otherwise sit past the end of a long horizontal scroll.
  // Above the row-label column's z-index on purpose.  Both edges are
  // pinned, so on a narrow viewport they can meet — and when they do the
  // pinned SUMMARY should cleanly occlude the label rather than the two
  // interleaving and slicing a figure mid-glyph ("$190,384.0").  Still a
  // collision; this makes it a legible one.
  const stickyTotalCell = pinTotals
    ? 'sticky right-0 z-20 bg-card [box-shadow:inset_1px_0_0_var(--border),var(--pin-shadow-left)]'
    : '';
  const stickyTotalHead = cn(
    BAND_FILL,
    pinTotals && 'sticky right-0 z-30 [box-shadow:inset_1px_0_0_var(--border),var(--pin-shadow-left)]',
  );

  return (
    <div className={cn(fill && 'flex h-full flex-col min-h-0')}>
      {/* No caption band.  It used to explain the scope and why paging
          had gone, but the scope now reads off the tab strip ("All rows
          500") and the footer ("Total rows: 4") — the two places people
          already look — so the sentence was a third telling of the same
          fact, costing a row of height on every report. */}
      <div className={cn('relative group/grid', fill && 'flex flex-1 flex-col min-h-0')}>
      {/* The scroll REGION contract comes from components/scrolling —
          focusable, named, overscroll-contained, and padded away from the
          sticky chrome.  That last one this view never had: it pins a
          <thead>, a <tfoot> AND both edges, and it already measures all
          three insets, so a tabbed-to cell used to land behind them
          (WCAG 2.4.11).  The hook hands that back for free.
          Both axes are ``auto``; HIDE_NATIVE_SCROLLBAR removes the
          browser's own bars (``display: none``, so no track is reserved)
          while leaving the SCROLLING intact — wheel, touch and keyboard
          all keep working, and the painted bars do the drawing. */}
      <div
        ref={setScrollEl}
        {...region.props}
        className={cn(
          fill && 'flex-1 min-h-0',
          HIDE_NATIVE_SCROLLBAR,
        )}
      >
      {/* ``min-w-full``, NOT ``w-full``.  Pinned to the container width
          the table had no choice but to compress: 60 driver columns
          squeezed to a few characters each, headers wrapped to three
          lines, and there was nothing to scroll because nothing
          overflowed.  Allowed to take its natural width it overflows,
          and the wrapper's ``overflow-x-auto`` finally has a job — which
          matters most exactly when the fields panel narrows the grid. */}
      <table className={cn('min-w-full text-sm border-collapse', fill && 'h-full')}>
        {/* PINNED.  A three-level header (quarter > year > month > driver
            > measure) that scrolls away leaves every figure with no
            column identity, and there are 60 columns to guess between.
            List mode pins its header for the same reason; this view
            simply never did.  ``z-30`` clears every sticky cell in the
            body (max z-20), so the frozen Total column can't paint over
            the header it belongs under. */}
        <thead className={cn(fill && 'sticky top-0 z-30')}>
          {result.headerLevels.map((level, levelIdx) => {
            const isLeafLevel = levelIdx === result.headerLevels.length - 1;
            return (
              <tr key={levelIdx} className="bg-muted">
                {/* Corner cell — spans every header level, naming the
                    dimension the rows are broken down by. */}
                {levelIdx === 0 && (
                  <th
                    data-pin={pinRows ? 'left' : undefined}
                    rowSpan={result.headerLevels.length}
                    className={cn(
                      cellPad,
                      stickyHead,
                      'text-left align-bottom text-xs font-medium text-muted-foreground uppercase tracking-wide border-b border-border',
                    )}
                  >
                    {result.rowFieldLabel}
                  </th>
                )}
                {level.map((cell, i) => (
                  <th
                    key={`${levelIdx}-${i}-${cell.label}`}
                    colSpan={cell.span}
                    className={cn(
                      cellPad,
                      'text-right text-xs font-medium text-muted-foreground uppercase tracking-wide whitespace-nowrap',
                      'border-b border-border',
                      // A vertical rule at each column-group boundary so
                      // "North | South" reads as two blocks, not one run.
                      !isLeafLevel && 'border-l border-border first:border-l-0 text-left',
                      // ...and on the LEAF level too.  Teaching a rule in
                      // the group bands and dropping it here left 60
                      // identical "RATE sum" labels running together as
                      // one unbroken strip.
                      isLeafLevel && i > 0 && 'border-l border-border',
                    )}
                  >
                    {cell.aggFn ? (
                      // Clicking a measure header sorts rows BY that
                      // measure: desc (biggest first — the question people
                      // actually ask) -> asc -> back to label order.
                      //
                      // ``Tip``, not native ``title=`` (banned: unthemed,
                      // delayed, invisible on touch).  Affordable here
                      // because leaf headers are BOUNDED — one per leaf
                      // column, mounted once per model change — unlike the
                      // per-cell drill button, where ~2,000 tooltip roots
                      // would undo the render work this file just did.
                      // Tip renders its child untouched when the label is
                      // undefined, so the non-interactive case needs no
                      // second branch.
                      <Tip label={onModelChange ? `Sort rows by ${cell.label}` : undefined}>
                      <button
                        type="button"
                        onClick={() => {
                          if (!onModelChange) return;
                          const leaf = result.leafIds[i];
                          const cur = model.sort;
                          const next = cur?.leaf !== leaf
                            ? { leaf, dir: 'desc' as const }
                            : cur.dir === 'desc'
                              ? { leaf, dir: 'asc' as const }
                              : null;
                          onModelChange({ ...model, sort: next });
                        }}
                        className={cn(
                          // ``uppercase`` again, explicitly: preflight sets
                          // ``button { text-transform: none }``, which
                          // silently cancelled the <th>'s uppercase for
                          // exactly the headers that are sortable — so a
                          // measure read "Rate sum" here and "RATE sum"
                          // under Total, side by side.
                          'inline-flex flex-col items-end leading-tight w-full uppercase',
                          onModelChange && 'hover:text-foreground transition-colors cursor-pointer',
                          model.sort?.leaf === result.leafIds[i] && 'text-foreground',
                        )}
                      >
                        <span className="inline-flex items-center gap-1">
                          {model.sort?.leaf === result.leafIds[i] && (
                            model.sort.dir === 'desc'
                              ? <ArrowDown size={12} />
                              : <ArrowUp size={12} />
                          )}
                          {cell.label}
                        </span>
                        <span className="text-3xs font-normal lowercase">
                          {AGG_FN_LABELS[cell.aggFn].toLowerCase()}
                        </span>
                      </button>
                      </Tip>
                    ) : isLeafLevel ? cell.label : (
                      // ── Sticky group label ──────────────────────────
                      // A group band can be far wider than the viewport:
                      // one YEAR spanning sixty driver columns means that
                      // three screens into a horizontal scroll, the label
                      // saying WHICH year has long left the building and
                      // the numbers are unattributable.
                      //
                      // Sticking the text inside its own cell keeps it in
                      // view for exactly as long as the group is, and CSS
                      // does the hard part: the containing block is the
                      // <th>, so the label travels with the scroll,
                      // stops at its own group's right edge, and never
                      // trespasses into the next group.
                      //
                      // ``left`` clears the pinned row-label column
                      // (``insets.left``, already measured for the scroll
                      // region) — at left-0 the label would slide UNDER
                      // the frozen column, which has a higher z-index.
                      // Unpinned, there is nothing to clear.
                      <span
                        className="inline-block sticky"
                        style={{ left: (pinRows ? insets.left : 0) + 8 }}
                      >
                        {cell.label}
                      </span>
                    )}
                  </th>
                ))}
                {/* Total column group — pinned right, mirroring the Total
                    row.  Without it a 2-D pivot can show every driver's
                    contribution but never the company's own figure, which
                    is usually the number the reader came for. */}
                {result.totalLabels.length > 0 && (
                  isLeafLevel
                    ? result.totalLabels.map((label, i) => (
                      <th
                        key={`tot-${i}-${label}`}
                        data-pin={pinTotals ? 'right' : undefined}
                        className={cn(
                          cellPad, stickyTotalHead,
                          'text-right text-xs font-medium uppercase tracking-wide',
                          'border-b border-l border-border text-muted-foreground',
                        )}
                      >
                        <span className="inline-flex flex-col items-end leading-tight">
                          <span>{label}</span>
                          <span className="text-3xs font-normal normal-case">
                            {AGG_FN_LABELS[model.values[i].aggFn].toLowerCase()}
                          </span>
                        </span>
                      </th>
                    ))
                    : levelIdx === 0 && (
                      <th
                        data-pin={pinTotals ? 'right' : undefined}
                        rowSpan={result.headerLevels.length - 1}
                        colSpan={result.totalLabels.length}
                        className={cn(
                          cellPad, stickyTotalHead,
                          'text-left align-bottom text-xs font-medium uppercase tracking-wide',
                          'border-b border-l border-border text-muted-foreground',
                        )}
                      >
                        Total
                      </th>
                    )
                )}
              </tr>
            );
          })}
        </thead>

        <tbody>
          {/* SIZER ROW — the fix for width jitter under windowing.
              With only a window of rows in the DOM, auto table layout
              sizes each column from whichever rows happen to be rendered,
              so widths would shift every time you scrolled past a wider
              figure.  This row carries the widest candidate the WHOLE
              report holds, through the SAME cellPad classes and the SAME
              renderCell, so the browser's own sizing settles on a width
              that is a function of the data rather than of scroll
              position — no measurement, no pinning, nothing to drift.

              Zero-height and ``invisible``: visibility:hidden preserves
              layout (so the width still counts) while ``py-0 h-0`` plus a
              clipping span removes every trace of it.  It carries no
              ``data-prow``, so the row-height measurement can't pick it.
              Only mounted while windowing — an unwindowed matrix already
              has every row present to size from. */}
          {/* Mounted whenever this matrix owns its height — NOT only while
              windowing.  Gated on windowing it unmounted the moment you
              folded enough groups for the list to fit, and the widths
              then recomputed from the visible rows and jumped; folding
              back re-mounted it and they jumped again.  One extra
              zero-height row is a trivial price for widths that never
              move. */}
          {fill && (
            <tr aria-hidden className="h-0">
              <th className={cn(cellPad, 'py-0 h-0 text-left font-medium whitespace-nowrap')}>
                <span className="block h-0 overflow-hidden invisible">
                  <span
                    className="inline-flex items-center gap-1"
                    style={{ paddingLeft: (result.widestRow?.depth ?? 0) * 16 }}
                  >
                    <span aria-hidden className="w-6 shrink-0" />
                    {result.widestRow?.label ?? ''}
                    <span className="text-2xs font-normal tabular-nums">
                      ({(result.widestRow?.count ?? 0).toLocaleString()})
                    </span>
                  </span>
                </span>
              </th>
              {result.leafWidest.map((value, i) => (
                <td key={`sz-${result.leafIds[i]}`} className={cn(cellPad, 'py-0 h-0', CELL_NUM)}>
                  <span className="block h-0 overflow-hidden invisible">
                    {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                  </span>
                </td>
              ))}
              {result.totalWidest.map((value, i) => (
                <td key={`szt-${i}`} className={cn(cellPad, 'py-0 h-0', CELL_NUM, 'font-semibold')}>
                  <span className="block h-0 overflow-hidden invisible">
                    {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                  </span>
                </td>
              ))}
            </tr>
          )}
          {/* Spacers carry the height of the rows NOT rendered, so the
              scrollbar and the scroll position stay honest while only a
              window of rows exists in the DOM. */}
          {win.padTop > 0 && (
            <tr aria-hidden style={{ height: win.padTop }}>
              <td colSpan={leafCount + 1 + result.totalLabels.length} />
            </tr>
          )}
          {win.rows.map((row, i) => {
            // ABSOLUTE index, not the slice's.  Zebra keyed on the slice
            // index would make the stripes strobe as you scroll.
            const rowIdx = win.from + i;
            return (
            <tr
              key={row.key}
              data-prow
              className={cn(
                'border-b border-border group/prow transition-colors hover:bg-muted',
                rowIdx % 2 === 1 && 'bg-muted/30',
              )}
            >
              <th
                scope="row"
                className={cn(
                  cellPad,
                  stickyCol,
                  // NO ``relative`` here.  ``sticky`` and ``relative`` are
                  // the same tailwind-merge group (position), so adding
                  // ``relative`` for the zebra overlay silently REPLACED
                  // ``sticky`` and the frozen column stopped freezing —
                  // leaving a pinned header cell floating over unrelated
                  // data.  ``position: sticky`` is itself a positioned
                  // value, so it already forms the containing block the
                  // ``absolute inset-0`` overlay resolves against.
                  'text-left font-medium whitespace-nowrap transition-colors',
                  'group-hover/prow:bg-muted',
                )}
              >
                {/* The zebra tint is an OVERLAY, not a replacement.
                    ``bg-muted/30`` is 30% alpha, and applied directly it
                    won the cascade over the sticky cell's ``bg-card`` —
                    leaving the frozen column 70% TRANSPARENT, so the
                    scrolled columns underneath bled through it and the
                    text visibly overlapped ("4,72RMR (65)").  A pinned
                    cell must be fully opaque; the stripe rides on top of
                    that, which also matches the body's stripe exactly
                    (same alpha over the same card colour).  Hidden on
                    hover so the row's own hover fill isn't doubled. */}
                {pinRows && rowIdx % 2 === 1 && (
                  <span
                    aria-hidden
                    className="absolute inset-0 bg-muted/30 group-hover/prow:opacity-0 pointer-events-none"
                  />
                )}
                <span
                  // ``min-h-6`` is load-bearing for WINDOWING, not just
                  // rhythm: the fold button is taller than bare label
                  // text, so parent rows were ~6px taller than leaves.
                  // Windowing computes its spacer offsets from ONE row
                  // height, and mixed heights would drift the further you
                  // scrolled.  Every row is now padding + 24px.
                  className="relative inline-flex min-h-6 items-center gap-1"
                  // Nesting depth as indentation — the only cue that a
                  // row belongs to the group above it.
                  style={{ paddingLeft: row.depth * 16 }}
                >
                  {row.hasChildren ? (
                    <button
                      type="button"
                      onClick={() => toggle(row.key)}
                      aria-expanded={!collapsed.has(row.key)}
                      aria-label={collapsed.has(row.key) ? `Expand ${row.label}` : `Collapse ${row.label}`}
                      // p-1 + a 16px glyph = exactly 24x24: the WCAG
                      // 2.5.8 floor, and unlike 26px it sits on the 4px
                      // scale so it matches the row's own min-h-6.  It
                      // was 18x18 (p-0.5 + 14px) with no exception
                      // available — this is the ONLY way to fold a group.
                      className="shrink-0 -ml-1 p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    >
                      {collapsed.has(row.key)
                        ? <ChevronRight size={16} />
                        : <ChevronDown size={16} />}
                    </button>
                  ) : (
                    // Keep leaves aligned with their expandable siblings.
                    row.depth > 0 && <span aria-hidden className="w-6 shrink-0" />
                  )}
                  {row.label}
                  {/* How many source rows produced this line — the operator
                      can tell a 1-load average from a 40-load one. */}
                  <span className="text-2xs font-normal text-muted-foreground tabular-nums">
                    ({row.count.toLocaleString()})
                  </span>
                </span>
              </th>
              {row.cells.map((value, i) => (
                value === null
                  // The dash IS the cell — no wrapper, no inner span.
                  ? <td key={result.leafIds[i]} className={i > 0 ? emptyCellRuled : emptyCell}>—</td>
                  : (
                    <td
                      key={result.leafIds[i]}
                      // The rule is lighter than the header's: the body is
                      // data, not chrome, but the column boundary the
                      // header promises has to exist down here or a figure
                      // can't be traced to its heading.
                      className={i > 0 ? valueCellRuled : valueCell}
                    >
                      {/* Every non-empty number is a question: "which
                          rows?" — but only when the operator asked for
                          that. Off, the figure is a plain text node:
                          no button, no focus stop, no dotted rule
                          promising an interaction that won't happen. */}
                      {!canDrill ? (
                        <span className={valueText}>
                          {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                        </span>
                      ) : (
                      <button
                        type="button"
                        onClick={() => drillRef.current?.open({
                          rowPath: row.path,
                          rowLabels: labelsFor(row.path),
                          ...leafCoords(i),
                        })}
                        className={valueButton}
                        // No ``title`` and no ``aria-label``.  Native
                        // title= is banned, but the obvious replacement is
                        // worse here: aria-label REPLACES the accessible
                        // name, which today is the figure — so ~2,000
                        // cells in a numeric matrix would all announce the
                        // same sentence and a screen-reader user would
                        // lose the number itself.  The at-rest dotted
                        // underline is the affordance; the interaction is
                        // explained once, in the panel's InfoTip.
                      >
                        {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                      </button>
                      )}
                    </td>
                  )
              ))}
              {row.totals.map((value, i) => (
                <td
                  key={`tot-${i}`}
                  className={cn(
                    // ``p-0`` when drillable: the padding rides the button
                    // instead, exactly as it does on a leaf value cell.
                    canDrill && value !== null ? 'p-0' : cellPad,
                    stickyTotalCell,
                    // Same trap as the row-label column: no ``relative``,
                    // or this stops being a frozen column.
                    'text-right tabular-nums whitespace-nowrap font-semibold border-l border-border',
                    'transition-colors group-hover/prow:bg-muted',
                  )}
                >
                  {/* Opaque base + overlay stripe — same reason as the
                      row-label column: a 30%-alpha stripe applied
                      directly made the frozen Total column see-through,
                      so scrolled figures showed through it
                      ("$2,550$2,550.00"). */}
                  {pinTotals && rowIdx % 2 === 1 && (
                    <span
                      aria-hidden
                      className="absolute inset-0 bg-muted/30 group-hover/prow:opacity-0 pointer-events-none"
                    />
                  )}
                  {/* The Total column drills too, and must: it sits
                      inline with the body rows and looks exactly like
                      them, so "figures open their rows" with one silent
                      exception is a rule nobody can learn.  An empty
                      ``colPath`` IS the Total's own definition — every
                      column — so the same query answers it. */}
                  {canDrill && value !== null ? (
                    <button
                      type="button"
                      onClick={() => drillRef.current?.open({
                        rowPath: row.path,
                        rowLabels: labelsFor(row.path),
                        colPath: [],
                        colLabel: 'Total',
                        valueKey: model.values[i].key,
                      })}
                      className={cn(valueButton, 'relative')}
                    >
                      {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                    </button>
                  ) : (
                    <span className="relative">
                      {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                    </span>
                  )}
                </td>
              ))}
            </tr>
            );
          })}
          {win.padBottom > 0 && (
            <tr aria-hidden style={{ height: win.padBottom }}>
              <td colSpan={leafCount + 1 + result.totalLabels.length} />
            </tr>
          )}
          {/* Slack absorber.  ``sticky bottom-0`` on the totals row only
              pins it once the content OVERFLOWS — with four rows there
              is nothing to stick past, so the total sat directly under
              the last row with a large blank area beneath it, floating
              mid-card.  A row with ``height: 100%`` takes the leftover
              height (tables hand surplus to such a row), which pushes
              the totals to the bottom edge where a total belongs.
              ⚠️ It is MOUNTED ONLY WHEN THE REPORT IS SHORT.  It used to
              render always, on the claim that `height: 100%` collapses
              to nothing once the rows overflow — but percentage heights
              on table rows are UNDEFINED in CSS 2.1, so that was a bet on
              one browser's behaviour, and an owner-reported gap opened
              between the last row and the Total.  Now it simply is not
              there when it has no job.
              The gate reads measurements that do NOT include this row —
              the container's own height, the header's, and a DATA row's.
              That is deliberate: keying it off the container's overflow
              would be circular, because mounting the row could create the
              very overflow that unmounts it, and the two would
              flip-flop. */}
          {fill && result.bodyRows.length > 0 && needsSlack && (
            <tr aria-hidden className="h-full">
              <td colSpan={leafCount + 1 + result.totalLabels.length} />
            </tr>
          )}
          {result.bodyRows.length === 0 && (
            <tr>
              <td colSpan={leafCount + 1 + result.totalLabels.length} className={cn(cellPad, 'text-center text-muted-foreground')}>
                Nothing matches the current filters.
              </td>
            </tr>
          )}
        </tbody>

        {result.bodyRows.length > 0 && (
          /* PINNED, like list mode's footer.  The one figure the report
             exists to produce was reachable only by scrolling to the
             end — invisible at 4 rows, always invisible at 364. */
          <tfoot className={cn(fill && 'sticky bottom-0 z-30')}>
            {/* ``border-t-2`` — the comment here used to claim "weight +
                a top rule carry the emphasis" while no rule existed, so
                the row was separated from a bg-muted/30 zebra stripe by
                a barely-visible fill step. */}
            {/* ``bg-muted`` on the ROW as well as on every cell, mirroring
                <thead>'s own row.  Belt and braces on a sticky band: a
                cell added here later without a fill would otherwise go
                transparent and let the body scroll through it, which is
                exactly how the grand total broke. */}
            <tr className="border-t-2 border-border bg-muted">
              <th
                scope="row"
                // Blue is the INTERACTION colour here — a figure turns
                // primary on hover because it drills down.  This LABEL
                // never does (it names the band, it isn't a figure), so
                // it keeps weight + the top rule and stays uncoloured.
                className={cn(cellPad, stickyCol, 'text-left bg-muted font-semibold text-foreground')}
              >
                Total
              </th>
              {result.grandTotal.map((value, i) => (
                <td
                  key={result.leafIds[i]}
                  className={cn(
                    canDrill && value !== null ? 'p-0' : cellPad,
                    'bg-muted font-semibold text-foreground tabular-nums text-right whitespace-nowrap',
                  )}
                >
                  {/* The grand total drills as well — an EMPTY rowPath
                      means "every row", so the same query serves it.
                      Stopping at the body would leave the rule as
                      "figures open their rows, except in the footer",
                      which is the split this pass exists to remove. */}
                  {canDrill && value !== null ? (
                    <button
                      type="button"
                      onClick={() => drillRef.current?.open({
                        rowPath: [], rowLabels: [], ...leafCoords(i),
                      })}
                      className={valueButton}
                    >
                      {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                    </button>
                  ) : (
                    renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))
                  )}
                </td>
              ))}
              {/* Bottom-right corner: the whole report in one figure —
                  so both paths are empty, and the drill returns every
                  row the report was built from. */}
              {result.grandRowTotal.map((value, i) => (
                <td
                  key={`gtot-${i}`}
                  className={cn(
                    canDrill && value !== null ? 'p-0' : cellPad,
                    stickyTotalHead,
                    'font-semibold text-foreground tabular-nums text-right whitespace-nowrap border-l border-border',
                  )}
                >
                  {canDrill && value !== null ? (
                    <button
                      type="button"
                      onClick={() => drillRef.current?.open({
                        rowPath: [], rowLabels: [], colPath: [],
                        colLabel: 'Total', valueKey: model.values[i].key,
                      })}
                      className={valueButton}
                    >
                      {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                    </button>
                  ) : (
                    renderCell(value, model.values[i].key, model.values[i].aggFn)
                  )}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
      </div>
      <ScrollbarH
        el={scrollEl}
        insetLeft={insets.left}
        insetRight={insets.right}
        flow={!!fill}
      />
      {fill && (
        <ScrollbarV el={scrollEl} insetTop={insets.top} />
      )}
      </div>
      {/* The drill dialog owns its own open state (see DrillDialog):
          mounted here it re-rendered the whole matrix on every click.
          Not mounted at all when drilling is switched off. */}
      {canDrill && (
        <DrillDialog ref={drillRef} rows={rows} model={model} columns={columns} />
      )}


    </div>
  );
}

/** The agg fn behind leaf ``i`` — it rides the leaf header level. */
function leafAggFn(
  result: ReturnType<typeof pivot>,
  i: number,
): AggFn {
  const leafLevel = result.headerLevels[result.headerLevels.length - 1];
  return leafLevel[i]?.aggFn ?? 'sum';
}
