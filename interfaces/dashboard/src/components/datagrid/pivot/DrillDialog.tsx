// ── Drill-down: the dialog ───────────────────────────────────────────
//
// The React half of the drill (the pure half is ``drill.ts``).
//
// ⚠️ IT OWNS ITS OWN OPEN STATE, ON PURPOSE — this is not a style
// choice, it is the whole reason the component exists separately.
//
// The state used to be a ``useState`` inside PivotView.  PivotView is a
// single 900-line component with no internal memo boundary, so setting
// it re-rendered the ENTIRE matrix to display a dialog that changes
// nothing about the matrix — measured at ~12% of a full mount to open
// and ~3% to close, essentially all of it wasted.  Repainting the wall
// because you hung a picture on it.
//
// Keeping the state down here means opening a cell re-renders these few
// nodes and nothing else.  The trigger reaches it through an imperative
// handle rather than a prop, because a prop would put the state back up
// in the parent and undo the point.

import { forwardRef, useImperativeHandle, useMemo, useState } from 'react';

import { cn } from '../../../lib/utils';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '../../ui/dialog';
import { ScrollRegion } from '../../scrolling';
import type { AnyColumn } from '../../../types';
import type { PivotModel } from './pivot';
import { pivotCellRows, type DrillTarget } from './drill';

/** What PivotView holds a ref to.  One verb: open this cell. */
export interface DrillHandle {
  open: (target: DrillTarget) => void;
}

interface Props {
  /** The same post-filter/search rows the matrix aggregated. */
  rows: Record<string, unknown>[];
  model: PivotModel;
  columns: AnyColumn[];
}

/** How many of the grid's own columns the dialog shows. */
const MAX_COLS = 6;
/** The sticky header's height, for the scrollport padding.
 *
 *  Expressed as CSS rather than a pixel constant, because the header is
 *  no longer a fixed height: its ``py-1.5`` rides ``--size-layout`` and
 *  its one line of text rides ``--size-text``.  A constant 30 was right
 *  while both were fixed; under the Size control it would under-reserve,
 *  and scroll-into-view would park a tabbed-to row BEHIND the sticky
 *  header — a WCAG 2.4.11 failure, not a cosmetic one.
 *
 *  Still no measurement and still no ResizeObserver: the same two tokens
 *  that size the header size this reservation, so it follows for free. */
const STICKY_HEAD = 'calc(0.75rem * var(--size-layout, 1) + 1.25rem * var(--size-text, 1))';

const DrillDialog = forwardRef<DrillHandle, Props>(function DrillDialog(
  { rows, model, columns }, ref,
) {
  const [target, setTarget] = useState<DrillTarget | null>(null);
  // ``open`` is stable, so a parent holding this ref never re-renders
  // because of it.
  useImperativeHandle(ref, () => ({ open: setTarget }), []);

  const colByKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
  );
  // Show the grid's own columns (not the synthetic pivot ones) — these
  // are real records again, so they should read like records.
  const showCols = useMemo(
    () => columns.filter((c) => !c.key.includes('::')).slice(0, MAX_COLS),
    [columns],
  );

  const view = useMemo(() => {
    if (!target) return null;
    return {
      measure: colByKey.get(target.valueKey),
      sourceRows: pivotCellRows(rows, model, columns, target.rowPath, target.colPath),
    };
  }, [target, colByKey, rows, model, columns]);

  if (!target || !view) return null;
  const { measure, sourceRows } = view;
  const hidden = columns.filter((c) => !c.key.includes('::')).length - showCols.length;
  // The row chain reads as a PATH (`›`), the column coordinate as a
  // separate fact (`·`) — so "which group" and "which column" stay
  // legible as two different things in one line.  With no row path at
  // all this is the grand total, which has to say so rather than open
  // with a bare column name.
  const rowTitle = target.rowLabels.length > 0
    ? target.rowLabels.join(' › ')
    : 'All rows';

  return (
    <Dialog open onOpenChange={(o) => { if (!o) setTarget(null); }}>
      <DialogContent size="3xl">
        <DialogHeader>
          <DialogTitle>
            {rowTitle}
            {target.colLabel && ` · ${target.colLabel}`}
          </DialogTitle>
          <DialogDescription>
            {sourceRows.length.toLocaleString()} row{sourceRows.length === 1 ? '' : 's'}
            {measure ? ` behind this ${measure.label.toLowerCase()}` : ''}.
            {/* A silent truncation reads as "these are all the columns".
                Say what was left out instead. */}
            {hidden > 0 && ` Showing the first ${MAX_COLS} columns of ${MAX_COLS + hidden}.`}
          </DialogDescription>
        </DialogHeader>
        {/* The scroll was always there; the AFFORDANCE wasn't.  With no
            visible scrollbar and no fade, a name clipped to "BENOIT T…"
            reads as broken rather than scrollable.  A right-edge fade
            marks the cut, and ``min-w-max`` stops the w-full table from
            squeezing cells to the point where every column truncates at
            once. */}
        <div className="relative">
          {/* A real scroll region, not a box that clips: the table has a
              sticky <thead>, so without ``scrollPaddingTop`` a tabbed-to
              cell lands BEHIND it (WCAG 2.4.11), and without
              ``tabIndex`` a keyboard user cannot scroll the rows at all
              (WCAG 2.1.1).  ``both`` because this table scrolls
              sideways too — the source rows are real records. */}
          <ScrollRegion
            axis="both"
            label="Rows behind this figure"
            stickyTop={STICKY_HEAD}
            className="max-h-[60vh]"
          >
            <table className="min-w-max w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  {showCols.map((c) => (
                    <th
                      key={c.key}
                      className="px-2 py-1.5 text-left font-medium text-muted-foreground border-b border-border"
                    >
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sourceRows.map((r, i) => (
                  <tr key={i} className={cn('border-b border-border', i % 2 === 1 && 'bg-muted/30')}>
                    {showCols.map((c) => (
                      <td key={c.key} className="px-2 py-1.5 whitespace-nowrap">
                        {c.render ? c.render(r[c.key], r) : String(r[c.key] ?? '—')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
          {/* Marks the cut so a truncated name reads as "there is more to
              the right", not as broken rendering.  pointer-events-none so
              it never blocks the scroll. */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background to-transparent"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
});

export default DrillDialog;
