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
  const setDimension = (axis: 'rows' | 'columns', key: string, on: boolean) => {
    const other = axis === 'rows' ? 'columns' : 'rows';
    onChange({
      ...model,
      [axis]: on ? [key] : [],
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
        <Section title="Rows" count={model.rows.length}>
          {dimensions.filter(match).map((c) => (
            <FieldRow
              key={c.key}
              label={c.label}
              checked={model.rows.includes(c.key)}
              onToggle={(on) => setDimension('rows', c.key, on)}
            />
          ))}
          {dimensions.filter(match).length === 0 && <Hint>No matching fields.</Hint>}
        </Section>

        <Section title="Columns" count={model.columns.length}>
          {dimensions.filter(match).map((c) => (
            <FieldRow
              key={c.key}
              label={c.label}
              checked={model.columns.includes(c.key)}
              onToggle={(on) => setDimension('columns', c.key, on)}
            />
          ))}
          {dimensions.filter(match).length === 0 && <Hint>No matching fields.</Hint>}
        </Section>

        <Section title="Values" count={model.values.length}>
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

function Section({ title, count, children }: {
  title: string; count: number; children: React.ReactNode;
}) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {title}
        </span>
        <span className="text-2xs tabular-nums text-muted-foreground">{count}</span>
      </div>
      <div className="pb-1">{children}</div>
    </div>
  );
}

function FieldRow({ label, checked, onToggle, trailing }: {
  label: string;
  checked: boolean;
  onToggle: (on: boolean) => void;
  trailing?: React.ReactNode;
}) {
  return (
    <label className={cn(
      'flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-muted/50 transition-colors',
    )}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="shrink-0"
      />
      <span className="flex-1 min-w-0 truncate text-foreground">{label}</span>
      {trailing}
    </label>
  );
}

const Hint = ({ children }: { children: React.ReactNode }) => (
  <p className="px-3 py-1.5 text-2xs text-muted-foreground italic">{children}</p>
);
