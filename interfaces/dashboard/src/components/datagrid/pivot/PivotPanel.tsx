import { useMemo, useState } from 'react';
import { Search, X } from 'lucide-react';

import { cn } from '../../../lib/utils';
import { Input } from '../../ui/input';
import { Button } from '../../ui/button';
import { ActionMenu } from '../../ui/context-menu';
import { InfoTip } from '../../tooltip';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { offeredAggFns } from '../aggregation';
import type { PivotModel, PivotValueField } from './pivot';

/**
 * The pivot configuration panel — Rows / Columns / Values.
 *
 * Phase 1 is CHECKBOX pickers, not drag-and-drop: with one row field and
 * one column field there is nothing to reorder, so DnD would be machinery
 * without a job.  The model already stores arrays, so multi-level (and
 * then reordering) can arrive without changing the persisted shape.
 *
 * Fields come from the column config: ``pivotable`` columns are offered
 * as dimensions (Rows / Columns), ``aggregable`` ones as Values — the
 * same opt-ins the grid's filters and footer totals already use.
 */
export default function PivotPanel({
  columns, model, onChange, onClose,
}: {
  columns: AnyColumn[];
  model: PivotModel;
  onChange: (next: PivotModel) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');

  const dimensions = useMemo(
    () => columns.filter((c) => c.pivotable),
    [columns],
  );
  const measures = useMemo(
    () => columns.filter((c) => c.aggregable),
    [columns],
  );
  const match = (c: AnyColumn) =>
    !query.trim() || c.label.toLowerCase().includes(query.trim().toLowerCase());

  // Rows and Columns are mutually exclusive: the same field on both axes
  // would pivot a dimension against itself (one populated diagonal).
  //
  // BOTH axes accept several fields, nesting in pick order: columns
  // become header levels, rows become an expand/collapse tree.
  const setDimension = (axis: 'rows' | 'columns', key: string, on: boolean) => {
    const other = axis === 'rows' ? 'columns' : 'rows';
    const cur = model[axis];
    const nextAxis = on ? [...cur, key] : cur.filter((k) => k !== key);
    onChange({
      ...model,
      [axis]: nextAxis,
      [other]: model[other].filter((k) => k !== key),
    });
  };

  const toggleValue = (col: AnyColumn, on: boolean) => {
    if (!on) {
      onChange({ ...model, values: model.values.filter((v) => v.key !== col.key) });
      return;
    }
    const fn = offeredAggFns(col)[0] ?? 'sum';
    onChange({ ...model, values: [...model.values, { key: col.key, aggFn: fn }] });
  };

  const setAggFn = (key: string, aggFn: AggFn) => {
    onChange({
      ...model,
      values: model.values.map((v) => (v.key === key ? { ...v, aggFn } : v)),
    });
  };

  const valueOf = (key: string): PivotValueField | undefined =>
    model.values.find((v) => v.key === key);

  return (
    <aside className="w-80 shrink-0 border-l border-border bg-card flex flex-col max-h-[32rem]">
      <div className="flex items-center justify-between gap-2 p-3 border-b border-border">
        <h3 className="text-sm font-semibold inline-flex items-center gap-1.5">
          Pivot
          <InfoTip
            size={12}
            label="Summarise the rows currently in view. Filters, search and tabs still apply — pivot reports on what they left."
          />
        </h3>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close pivot panel">
          <X size={16} />
        </Button>
      </div>

      <div className="p-3 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search fields"
            className="h-8 pl-7 text-xs"
            aria-label="Search fields"
          />
        </div>
      </div>

      <div className="overflow-y-auto flex-1">
        <Section
          title="Rows"
          hint="One line per value. Pick several to nest them."
          count={model.rows.length}
        >
          {dimensions.filter(match).map((c) => {
            const at = model.rows.indexOf(c.key);
            return (
              <FieldRow
                key={c.key}
                label={c.label}
                checked={at >= 0}
                disabledReason={model.columns.includes(c.key) ? 'in Columns' : undefined}
                onToggle={(on) => setDimension('rows', c.key, on)}
                trailing={at >= 0 && model.rows.length > 1 && (
                  <span className="shrink-0 text-2xs tabular-nums text-muted-foreground">
                    {at + 1}
                  </span>
                )}
              />
            );
          })}
          {dimensions.filter(match).length === 0 && <Hint>No matching fields.</Hint>}
        </Section>

        <Section
          title="Columns"
          hint="Spread across the top. Pick several to nest them."
          count={model.columns.length}
        >
          {dimensions.filter(match).map((c) => {
            const at = model.columns.indexOf(c.key);
            return (
              <FieldRow
                key={c.key}
                label={c.label}
                checked={at >= 0}
                disabledReason={model.rows.includes(c.key) ? 'in Rows' : undefined}
                onToggle={(on) => setDimension('columns', c.key, on)}
                // Nesting order is the pick order and it changes the
                // header shape, so it has to be visible.
                trailing={at >= 0 && model.columns.length > 1 && (
                  <span className="shrink-0 text-2xs tabular-nums text-muted-foreground">
                    {at + 1}
                  </span>
                )}
              />
            );
          })}
          {dimensions.filter(match).length === 0 && <Hint>No matching fields.</Hint>}
        </Section>

        <Section title="Values" hint="The numbers to total." count={model.values.length} required>
          {measures.filter(match).map((c) => {
            const picked = valueOf(c.key);
            return (
              <FieldRow
                key={c.key}
                label={c.label}
                checked={!!picked}
                onToggle={(on) => toggleValue(c, on)}
                trailing={picked && (
                  // The agg chip mirrors the column ⋮ → Aggregate menu, so
                  // the vocabulary is identical in both places.
                  <ActionMenu
                    items={offeredAggFns(c).map((fn) => ({
                      key: fn,
                      label: AGG_FN_LABELS[fn],
                      onSelect: () => setAggFn(c.key, fn),
                    }))}
                  >
                    <button
                      type="button"
                      className="px-1.5 py-0.5 rounded-full border border-border text-2xs text-muted-foreground hover:border-ring hover:text-foreground transition"
                    >
                      {AGG_FN_LABELS[picked.aggFn].toLowerCase()}
                    </button>
                  </ActionMenu>
                )}
              />
            );
          })}
          {measures.filter(match).length === 0 && <Hint>No matching fields.</Hint>}
        </Section>
      </div>
    </aside>
  );
}

function Section({ title, hint, count, required, children }: {
  title: string;
  /** Plain-language gloss.  "Columns" already means TABLE columns in this
   *  app (the Manage-columns popover), so the spreadsheet sense needs
   *  spelling out or an operator reads the wrong thing. */
  hint: string;
  count: number;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-baseline justify-between gap-2 px-3 pt-2">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
          {required && (
            <span className="ml-1.5 normal-case tracking-normal font-normal text-2xs">
              required
            </span>
          )}
        </span>
        <span className="text-2xs tabular-nums text-muted-foreground">{count}</span>
      </div>
      <p className="px-3 pb-1.5 text-2xs text-muted-foreground">{hint}</p>
      <div className="pb-1">{children}</div>
    </div>
  );
}

function FieldRow({ label, checked, onToggle, trailing, disabledReason }: {
  label: string;
  checked: boolean;
  onToggle: (on: boolean) => void;
  trailing?: React.ReactNode;
  /** Why this field can't be picked HERE (it's on the other axis).
   *  Shown inline so the exclusivity is visible BEFORE the click —
   *  silently clearing the other section's checkbox reads as a bug. */
  disabledReason?: string;
}) {
  const disabled = !!disabledReason;
  return (
    <label className={cn(
      'flex items-center gap-2 px-3 py-1.5 text-xs transition-colors',
      disabled
        ? 'cursor-not-allowed opacity-50'
        : 'cursor-pointer hover:bg-muted/50',
    )}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onToggle(e.target.checked)}
        className="shrink-0"
      />
      <span className="flex-1 min-w-0 truncate text-foreground">{label}</span>
      {disabledReason && (
        <span className="shrink-0 text-2xs text-muted-foreground italic">{disabledReason}</span>
      )}
      {trailing}
    </label>
  );
}

const Hint = ({ children }: { children: React.ReactNode }) => (
  <p className="px-3 py-1.5 text-2xs text-muted-foreground italic">{children}</p>
);
