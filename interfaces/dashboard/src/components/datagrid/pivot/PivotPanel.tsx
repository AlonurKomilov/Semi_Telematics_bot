import { useMemo, useState } from 'react';
import { Search, X, Check } from 'lucide-react';

import { cn } from '../../../lib/utils';
import { Input } from '../../ui/input';
import { Button } from '../../ui/button';
import { Switch } from '../../ui/switch';
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
  columns, model, onChange, onClose, enabled, onEnabledChange,
  width, onWidthChange, fill,
}: {
  columns: AnyColumn[];
  model: PivotModel;
  onChange: (next: PivotModel) => void;
  onClose: () => void;
  /** Is the grid currently PIVOTED?  Configuring and switching on are
   *  two different acts — you can open this panel, set the report up,
   *  and only then flip it on (the MUI model). */
  enabled: boolean;
  onEnabledChange: (next: boolean) => void;
  /** Panel width in px, owned + persisted by the grid.  The panel takes
   *  space FROM the table, so how that space is split is a judgement
   *  only the reader can make: a deep field list wants a wide panel, a
   *  wide matrix wants a narrow one. */
  width: number;
  onWidthChange: (next: number) => void;
  /** Stretch to the grid's height instead of capping at 32rem — under
   *  ``fillHeight`` a fixed cap leaves the panel floating short of a
   *  much taller card. */
  fill?: boolean;
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
    <aside
      className={cn(
        'shrink-0 border-l border-border bg-card flex flex-col relative',
        fill ? 'min-h-0' : 'max-h-[32rem]',
      )}
      style={{ width }}
    >
      {/* Drag the left edge to trade panel width against table width —
          MUI's panel resizes, and here the tension is real: the fields
          list wants to be wide, the matrix behind it wants the room
          back.  A 4px hit strip sitting ON the border (-left-0.5) so the
          cursor changes exactly where the eye expects the seam. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize pivot panel"
        className="absolute inset-y-0 -left-0.5 w-1 cursor-col-resize hover:bg-primary/40 active:bg-primary/60 z-10"
        onPointerDown={(e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startWidth = width;
          const move = (mv: PointerEvent) => {
            // Dragging LEFT widens the panel, so the delta is inverted.
            // Clamped: below ~15rem the field labels wrap to nothing
            // useful, and past 40rem the panel is eating the report it
            // exists to configure.
            const next = Math.round(startWidth + (startX - mv.clientX));
            onWidthChange(Math.max(240, Math.min(640, next)));
          };
          const up = () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
          };
          window.addEventListener('pointermove', move);
          window.addEventListener('pointerup', up);
        }}
      />
      <div className="flex items-center justify-between gap-2 p-3 border-b border-border">
        {/* The switch, not the toolbar button, is what pivots the grid.
            Opening this panel is "let me set a report up"; flipping the
            switch is "show it to me" — so a click on the toolbar icon
            can no longer replace the row list before you've said what
            you wanted summarised. */}
        <h3 className="text-sm font-semibold inline-flex items-center gap-2">
          {/* ``sm`` — the default md (h-6 w-11) towered over the 14px
              title beside it.  This sits on a panel header, not a
              settings row. */}
          <Switch
            size="sm"
            checked={enabled}
            onCheckedChange={onEnabledChange}
            aria-label="Pivot the grid"
          />
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
                  // A check on the ACTIVE function: the menu listed
                  // Sum · Average · Max identically whichever one was
                  // running, so the only way to know what you had picked
                  // was to close the menu and read the chip behind it.
                  <ActionMenu
                    items={offeredAggFns(c).map((fn) => ({
                      key: fn,
                      label: AGG_FN_LABELS[fn],
                      icon: fn === picked.aggFn ? <Check size={14} /> : undefined,
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
