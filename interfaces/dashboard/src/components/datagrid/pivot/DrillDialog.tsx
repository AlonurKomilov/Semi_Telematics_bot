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
import type { AnyColumn } from '../../../types';
import { splitLeafId, type PivotModel } from './pivot';
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
  /** `PivotResult.leafIds` — decodes a leafIdx into column path + measure. */
  leafIds: string[];
}

/** How many of the grid's own columns the dialog shows. */
const MAX_COLS = 6;

const DrillDialog = forwardRef<DrillHandle, Props>(function DrillDialog(
  { rows, model, columns, leafIds }, ref,
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
    const { colPath, valueKey } = splitLeafId(leafIds[target.leafIdx]);
    return {
      colPath,
      measure: colByKey.get(valueKey),
      sourceRows: pivotCellRows(rows, model, columns, target.rowPath, colPath),
    };
  }, [target, leafIds, colByKey, rows, model, columns]);

  if (!target || !view) return null;
  const { colPath, measure, sourceRows } = view;
  const hidden = columns.filter((c) => !c.key.includes('::')).length - showCols.length;

  return (
    <Dialog open onOpenChange={(o) => { if (!o) setTarget(null); }}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {target.rowLabel}
            {colPath.length > 0 && ` · ${colPath.join(' · ')}`}
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
          <div className="max-h-[60vh] overflow-auto">
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
          </div>
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
