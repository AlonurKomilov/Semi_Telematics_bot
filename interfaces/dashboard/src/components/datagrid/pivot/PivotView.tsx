import { useMemo } from 'react';
import { TableProperties } from 'lucide-react';

import { cn } from '../../../lib/utils';
import { EmptyState } from '../../shell';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { formatAggDefault } from '../aggregation';
import { pivot, type PivotModel } from './pivot';

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
  rows, model, columns, padding,
}: {
  /** MUST be the same post-segment/filter/search rows the grid's own
   *  footer aggregation reduces, so the two can never disagree. */
  rows: Record<string, unknown>[];
  model: PivotModel;
  columns: AnyColumn[];
  /** The density padding classes the grid is currently using. */
  padding: string;
}) {
  const result = useMemo(
    () => pivot(rows, model, columns),
    [rows, model, columns],
  );
  const colByKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
  );

  if (result.empty) {
    return (
      <EmptyState
        icon={TableProperties}
        title="Choose what to summarise"
        description="Pick a field to group rows by and at least one value to measure."
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

  return (
    <div className="overflow-x-auto">
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
                      <span className="inline-flex flex-col items-end leading-tight">
                        <span>{cell.label}</span>
                        <span className="text-3xs font-normal normal-case">
                          {AGG_FN_LABELS[cell.aggFn].toLowerCase()}
                        </span>
                      </span>
                    ) : cell.label}
                  </th>
                ))}
              </tr>
            );
          })}
        </thead>

        <tbody>
          {result.bodyRows.map((row, rowIdx) => (
            <tr
              key={row.key}
              className={cn(
                'border-b border-border',
                rowIdx % 2 === 1 && 'bg-muted/30',
              )}
            >
              <th
                scope="row"
                className={cn(
                  padding,
                  stickyCol,
                  'text-left font-medium whitespace-nowrap',
                  rowIdx % 2 === 1 && 'bg-muted/30',
                )}
              >
                {row.label}
                {/* How many source rows produced this line — the operator
                    can tell a 1-load average from a 40-load one. */}
                <span className="ml-1.5 text-2xs font-normal text-muted-foreground tabular-nums">
                  ({row.count.toLocaleString()})
                </span>
              </th>
              {row.cells.map((value, i) => (
                <td
                  key={result.leafIds[i]}
                  className={cn(padding, 'text-right tabular-nums whitespace-nowrap')}
                >
                  {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                </td>
              ))}
            </tr>
          ))}
          {result.bodyRows.length === 0 && (
            <tr>
              <td colSpan={leafCount + 1} className={cn(padding, 'text-center text-muted-foreground')}>
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
                className={cn(padding, stickyCol, 'text-left bg-muted font-semibold text-primary')}
              >
                Total
              </th>
              {result.grandTotal.map((value, i) => (
                <td
                  key={result.leafIds[i]}
                  className={cn(
                    padding,
                    'bg-muted font-semibold text-primary tabular-nums text-right whitespace-nowrap',
                  )}
                >
                  {renderCell(value, result.leafValueKeys[i], leafAggFn(result, i))}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
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
