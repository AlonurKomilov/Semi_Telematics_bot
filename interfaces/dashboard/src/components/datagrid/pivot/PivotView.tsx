import { useEffect, useMemo, useState } from 'react';
import {
  TableProperties, ChevronRight, ChevronDown, ArrowUp, ArrowDown,
} from 'lucide-react';

import { cn } from '../../../lib/utils';
import { EmptyState } from '../../shell';
import { Button } from '../../ui/button';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '../../ui/dialog';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { formatAggDefault } from '../aggregation';
import {
  pivot, pivotCellRows, splitLeafId, type PivotModel, type PivotBodyRow,
} from './pivot';

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
  // Which cell the operator opened.  Rows are computed on demand — the
  // matrix would otherwise hold a row array per cell for a question asked
  // about one of them.
  const [drill, setDrill] = useState<{ row: PivotBodyRow; leafIdx: number } | null>(null);
  const toggle = (key: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  // A row is hidden when ANY ancestor is collapsed — checking every
  // prefix (not just the parent) keeps a deep tree correct.
  const visibleRows = useMemo(() => result.bodyRows.filter((row) => {
    for (let d = 1; d < row.path.length; d += 1) {
      if (collapsed.has(row.path.slice(0, d).join('\u0000'))) return false;
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
          <Button type="button" onClick={onOpenPanel}>Open pivot fields</Button>
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
  // The row-label column stays put during horizontal scroll — otherwise a
  // wide matrix scrolls the identity off screen and the numbers lose
  // their subject.  Plain sticky; deliberately not the grid's pin maths.
  const stickyCol = 'sticky left-0 z-10 bg-card';
  const stickyHead = 'sticky left-0 z-20 bg-muted';
  // The Total column pins to the RIGHT edge for the same reason the row
  // label pins left: with 60 driver columns the figure you actually came
  // for would otherwise sit past the end of a long horizontal scroll.
  // Above the row-label column's z-index on purpose.  Both edges are
  // pinned, so on a narrow viewport they can meet — and when they do the
  // pinned SUMMARY should cleanly occlude the label rather than the two
  // interleaving and slicing a figure mid-glyph ("$190,384.0").  Still a
  // collision; this makes it a legible one.
  const stickyTotalCell = 'sticky right-0 z-20 bg-card';
  const stickyTotalHead = 'sticky right-0 z-30 bg-muted';

  const sourceRows = rows.length;

  return (
    <div className={cn(fill && 'flex h-full flex-col min-h-0')}>
      {/* What this report covers, and why two familiar controls are gone.
          A control that vanishes on a mode switch owes the user a line —
          hiding beats greying, but SILENT removal reads as breakage.

          PINNED, not scrolled with the matrix: it explains what you are
          looking at, so it has to be readable at the point you might
          actually wonder — which is deep in the rows, not at the top. */}
      <p className={cn(
        'px-3 py-2 text-2xs text-muted-foreground border-b border-border',
        fill && 'shrink-0',
      )}>
        Summarising all {sourceRows.toLocaleString()} row{sourceRows === 1 ? '' : 's'} that
        match your current tab, filters and search — column layout and paging
        don't apply here.
      </p>
      <div className={cn('overflow-x-auto', fill && 'flex-1 min-h-0 overflow-y-auto')}>
      <table className="w-full text-sm border-collapse">
        <thead>
          {result.headerLevels.map((level, levelIdx) => {
            const isLeafLevel = levelIdx === result.headerLevels.length - 1;
            return (
              <tr key={levelIdx} className="bg-muted">
                {/* Corner cell — spans every header level, naming the
                    dimension the rows are broken down by. */}
                {levelIdx === 0 && (
                  <th
                    rowSpan={result.headerLevels.length}
                    className={cn(
                      padding,
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
                      padding,
                      'text-right text-xs font-medium text-muted-foreground uppercase tracking-wide',
                      'border-b border-border',
                      // A vertical rule at each column-group boundary so
                      // "North | South" reads as two blocks, not one run.
                      !isLeafLevel && 'border-l border-border first:border-l-0 text-left',
                    )}
                  >
                    {cell.aggFn ? (
                      // Clicking a measure header sorts rows BY that
                      // measure: desc (biggest first — the question people
                      // actually ask) -> asc -> back to label order.
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
                        title={onModelChange ? `Sort rows by ${cell.label}` : undefined}
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
                    ) : cell.label}
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
                        className={cn(
                          padding, stickyTotalHead,
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
                        rowSpan={result.headerLevels.length - 1}
                        colSpan={result.totalLabels.length}
                        className={cn(
                          padding, stickyTotalHead,
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
          {visibleRows.map((row, rowIdx) => (
            <tr
              key={row.key}
              className={cn(
                'border-b border-border group/prow transition-colors hover:bg-muted',
                rowIdx % 2 === 1 && 'bg-muted/30',
              )}
            >
              <th
                scope="row"
                className={cn(
                  padding,
                  stickyCol,
                  'text-left font-medium whitespace-nowrap transition-colors',
                  'group-hover/prow:bg-muted',
                  rowIdx % 2 === 1 && 'bg-muted/30',
                )}
              >
                <span
                  className="inline-flex items-center gap-1"
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
                      className="shrink-0 -ml-1 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    >
                      {collapsed.has(row.key)
                        ? <ChevronRight size={14} />
                        : <ChevronDown size={14} />}
                    </button>
                  ) : (
                    // Keep leaves aligned with their expandable siblings.
                    row.depth > 0 && <span aria-hidden className="w-[18px] shrink-0" />
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
                <td
                  key={result.leafIds[i]}
                  className={cn(padding, 'text-right tabular-nums whitespace-nowrap p-0')}
                >
                  {value === null ? (
                    <span className={cn(padding, 'block')}>
                      {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                    </span>
                  ) : (
                    // Every non-empty number is a question: "which rows?"
                    <button
                      type="button"
                      onClick={() => setDrill({ row, leafIdx: i })}
                      className={cn(
                        padding,
                        'block w-full text-right tabular-nums hover:bg-primary/10 hover:text-primary transition-colors cursor-pointer',
                      )}
                      title="Show the rows behind this number"
                    >
                      {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                    </button>
                  )}
                </td>
              ))}
              {row.totals.map((value, i) => (
                <td
                  key={`tot-${i}`}
                  className={cn(
                    padding, stickyTotalCell,
                    'text-right tabular-nums whitespace-nowrap font-semibold border-l border-border',
                    'transition-colors group-hover/prow:bg-muted',
                    rowIdx % 2 === 1 && 'bg-muted/30',
                  )}
                >
                  {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                </td>
              ))}
            </tr>
          ))}
          {result.bodyRows.length === 0 && (
            <tr>
              <td colSpan={leafCount + 1 + result.totalLabels.length} className={cn(padding, 'text-center text-muted-foreground')}>
                Nothing matches the current filters.
              </td>
            </tr>
          )}
        </tbody>

        {result.bodyRows.length > 0 && (
          <tfoot>
            <tr>
              <th
                scope="row"
                // Blue is the INTERACTION colour here — value cells turn
                // primary on hover because they drill down.  The total row
                // wore the same blue while being entirely unclickable, so
                // the one colour meant two things.  Weight + a top rule
                // carry the emphasis instead.
                className={cn(padding, stickyCol, 'text-left bg-muted font-semibold text-foreground')}
              >
                Total
              </th>
              {result.grandTotal.map((value, i) => (
                <td
                  key={result.leafIds[i]}
                  className={cn(
                    padding,
                    'bg-muted font-semibold text-foreground tabular-nums text-right whitespace-nowrap',
                  )}
                >
                  {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                </td>
              ))}
              {/* Bottom-right corner: the whole report in one figure. */}
              {result.grandRowTotal.map((value, i) => (
                <td
                  key={`gtot-${i}`}
                  className={cn(
                    padding, stickyTotalHead,
                    'font-semibold text-foreground tabular-nums text-right whitespace-nowrap border-l border-border',
                  )}
                >
                  {renderCell(value, model.values[i].key, model.values[i].aggFn)}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
      </div>
      {/* Row count of the REPORT (groups), distinct from the source-row
          count in the line above — an operator comparing the two can see
          how much the grouping collapsed. */}
      {drill && (() => {
        const { colPath, valueKey } = splitLeafId(result.leafIds[drill.leafIdx]);
        const sourceRows = pivotCellRows(rows, model, columns, drill.row.path, colPath);
        const measure = colByKey.get(valueKey);
        // Show the grid's own columns (not the synthetic pivot ones) —
        // these are real records again, so they should read like records.
        const showCols = columns.filter((c) => !c.key.includes('::')).slice(0, 6);
        return (
          <Dialog open onOpenChange={(o) => { if (!o) setDrill(null); }}>
            <DialogContent className="max-w-3xl">
              <DialogHeader>
                <DialogTitle>
                  {drill.row.label}
                  {colPath.length > 0 && ` · ${colPath.join(' · ')}`}
                </DialogTitle>
                <DialogDescription>
                  {sourceRows.length.toLocaleString()} row{sourceRows.length === 1 ? '' : 's'}
                  {measure ? ` behind this ${measure.label.toLowerCase()}` : ''}.
                </DialogDescription>
              </DialogHeader>
              {/* The scroll was always there; the AFFORDANCE wasn't.
                  With no visible scrollbar and no fade, a name clipped
                  to "BENOIT T…" reads as broken rather than scrollable.
                  A right-edge fade marks the cut, and ``min-w-max``
                  stops the w-full table from squeezing cells to the
                  point where every column truncates at once. */}
              <div className="relative">
                <div className="max-h-[60vh] overflow-auto">
                <table className="min-w-max w-full text-xs border-collapse">
                  <thead className="sticky top-0 bg-muted">
                    <tr>
                      {showCols.map((c) => (
                        <th key={c.key} className="px-2 py-1.5 text-left font-medium text-muted-foreground border-b border-border">
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
                {/* Marks the cut so a truncated name reads as "there is
                    more to the right", not as broken rendering.
                    pointer-events-none so it never blocks the scroll. */}
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background to-transparent"
                />
              </div>
            </DialogContent>
          </Dialog>
        );
      })()}


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
