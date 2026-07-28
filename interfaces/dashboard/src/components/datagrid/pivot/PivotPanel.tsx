import { useMemo, useState } from 'react';
import {
  Search, X, Check, Plus, GripVertical, MoreVertical,
  ChevronDown, ChevronUp, Trash2, ArrowUp, ArrowDown,
  ChevronsUp, ChevronsDown,
} from 'lucide-react';
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, verticalListSortingStrategy, arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { cn } from '../../../lib/utils';
import { Input } from '../../ui/input';
import { Button } from '../../ui/button';
import { Switch } from '../../ui/switch';
import { ActionMenu, type MenuAction } from '../../ui/context-menu';
import { InfoTip } from '../../tooltip';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { offeredAggFns } from '../aggregation';
import type { PivotModel, PivotValueField } from './pivot';

/**
 * The pivot configuration panel — Rows / Columns / Values.
 *
 * Structured like MUI's: an UNASSIGNED POOL at the top holding every
 * field that isn't placed yet, then three collapsible sections holding
 * only what you've actually assigned, in the order they nest.
 *
 * The earlier design listed EVERY field inside EVERY section with a
 * checkbox, which had three problems that compound as a grid gains
 * columns: the same field appeared three times (once greyed with an "in
 * Rows" tag), the list was 3n rows long to express n decisions, and the
 * nesting order was displayed as a number with no way to change it —
 * you had to untick everything and re-tick in the order you wanted.
 *
 * Fields come from the column config: ``pivotable`` columns are offered
 * as dimensions (Rows / Columns), ``aggregable`` ones as Values — the
 * same opt-ins the grid's filters and footer totals already use.  A
 * field's legal destinations follow from that, so the menus never offer
 * a move that would be rejected.
 */

type Axis = 'rows' | 'columns' | 'values';

const AXIS_LABEL: Record<Axis, string> = {
  rows: 'Rows', columns: 'Columns', values: 'Values',
};
const AXIS_HINT: Record<Axis, string> = {
  rows: 'One line per value. Pick several to nest them.',
  // "Columns" already means TABLE columns in this app (the Manage-columns
  // popover), so the spreadsheet sense needs spelling out.
  columns: 'Spread across the top. Pick several to nest them.',
  values: 'The numbers to total.',
};

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
  // Session state: which sections are rolled up.  A reading posture, not
  // a preference — restoring yesterday's folded panel would hide fields
  // the user has since forgotten they assigned.
  const [folded, setFolded] = useState<Set<Axis>>(() => new Set());
  const toggleFold = (axis: Axis) => setFolded((prev) => {
    const next = new Set(prev);
    if (next.has(axis)) next.delete(axis); else next.add(axis);
    return next;
  });

  const byKey = useMemo(() => new Map(columns.map((c) => [c.key, c])), [columns]);
  const dimensions = useMemo(() => columns.filter((c) => c.pivotable), [columns]);
  const measures = useMemo(() => columns.filter((c) => c.aggregable), [columns]);

  /** Where a field may legally go.  A column can be BOTH a dimension and
   *  a measure (rare but legal), so this is a list, not a single value. */
  const axesFor = (key: string): Axis[] => {
    const col = byKey.get(key);
    if (!col) return [];
    const out: Axis[] = [];
    if (col.pivotable) out.push('rows', 'columns');
    if (col.aggregable) out.push('values');
    return out;
  };
  const axisOf = (key: string): Axis | null => {
    if (model.rows.includes(key)) return 'rows';
    if (model.columns.includes(key)) return 'columns';
    if (model.values.some((v) => v.key === key)) return 'values';
    return null;
  };
  const keysOn = (axis: Axis): string[] => (
    axis === 'values' ? model.values.map((v) => v.key) : model[axis]
  );

  // Every assignable field, deduped — a column that is both a dimension
  // and a measure must appear ONCE in the pool, not twice.
  const allFields = useMemo(() => {
    const seen = new Set<string>();
    return [...dimensions, ...measures].filter((c) => {
      if (seen.has(c.key)) return false;
      seen.add(c.key);
      return true;
    });
  }, [dimensions, measures]);

  const matches = (c: AnyColumn) =>
    !query.trim() || c.label.toLowerCase().includes(query.trim().toLowerCase());

  // The pool holds what ISN'T placed.  Search filters the pool only —
  // assigned fields stay visible in their sections whatever you type,
  // so a search can never hide (and strand) your own selections.
  const unassigned = allFields.filter((c) => axisOf(c.key) === null && matches(c));

  // ── Model edits ────────────────────────────────────────────────────

  const withoutKey = (m: PivotModel, key: string): PivotModel => ({
    ...m,
    rows: m.rows.filter((k) => k !== key),
    columns: m.columns.filter((k) => k !== key),
    values: m.values.filter((v) => v.key !== key),
  });

  const assign = (key: string, axis: Axis) => {
    const base = withoutKey(model, key);
    if (axis === 'values') {
      const col = byKey.get(key);
      const fn = offeredAggFns(col)[0] ?? 'sum';
      onChange({ ...base, values: [...base.values, { key, aggFn: fn }] });
      return;
    }
    onChange({ ...base, [axis]: [...base[axis], key] });
  };

  const remove = (key: string) => onChange(withoutKey(model, key));

  /** Reorder within an axis.  ``to`` may be an absolute index. */
  const moveTo = (axis: Axis, key: string, to: number) => {
    const cur = keysOn(axis);
    const from = cur.indexOf(key);
    if (from < 0) return;
    const bounded = Math.max(0, Math.min(cur.length - 1, to));
    if (bounded === from) return;
    if (axis === 'values') {
      onChange({ ...model, values: arrayMove(model.values, from, bounded) });
    } else {
      onChange({ ...model, [axis]: arrayMove(model[axis], from, bounded) });
    }
  };

  const setAggFn = (key: string, aggFn: AggFn) => onChange({
    ...model,
    values: model.values.map((v) => (v.key === key ? { ...v, aggFn } : v)),
  });

  const valueOf = (key: string): PivotValueField | undefined =>
    model.values.find((v) => v.key === key);

  // ── Drag ───────────────────────────────────────────────────────────
  // Reorder WITHIN a section.  Moving between sections is the ⋮ menu's
  // job (and the pool's + button): cross-container dnd-kit needs
  // collision handling and a drag overlay for a gesture the menu already
  // expresses unambiguously, on a panel where the lists are 1-3 items.
  const sensors = useSensors(useSensor(PointerSensor, {
    // A few pixels of slop so a click on the checkbox or the ⋮ isn't
    // swallowed as a micro-drag.
    activationConstraint: { distance: 4 },
  }));
  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const [axisA, keyA] = String(active.id).split(':') as [Axis, string];
    const [axisB, keyB] = String(over.id).split(':') as [Axis, string];
    if (axisA !== axisB) return;
    moveTo(axisA, keyA, keysOn(axisA).indexOf(keyB));
  };

  /** The per-field ⋮ menu — reorder, send to another axis, remove. */
  const fieldMenu = (axis: Axis, key: string): MenuAction[] => {
    const list = keysOn(axis);
    const at = list.indexOf(key);
    const last = list.length - 1;
    const targets = axesFor(key);
    return [
      {
        key: 'up', label: 'Move up', icon: <ArrowUp size={14} />,
        disabled: at <= 0, onSelect: () => moveTo(axis, key, at - 1),
      },
      {
        key: 'down', label: 'Move down', icon: <ArrowDown size={14} />,
        disabled: at >= last, onSelect: () => moveTo(axis, key, at + 1),
      },
      {
        key: 'top', label: 'Move to top', icon: <ChevronsUp size={14} />,
        disabled: at <= 0, separatorBefore: true,
        onSelect: () => moveTo(axis, key, 0),
      },
      {
        key: 'bottom', label: 'Move to bottom', icon: <ChevronsDown size={14} />,
        disabled: at >= last, onSelect: () => moveTo(axis, key, last),
      },
      // Where else this field can go.  A check marks where it IS, so the
      // menu doubles as "which axis am I on?" without closing it.
      ...targets.map((t, i) => ({
        key: `to-${t}`,
        label: AXIS_LABEL[t],
        icon: t === axis ? <Check size={14} /> : undefined,
        separatorBefore: i === 0,
        disabled: t === axis,
        onSelect: () => assign(key, t),
      })),
      {
        key: 'remove', label: 'Remove', icon: <Trash2 size={14} />,
        danger: true, separatorBefore: true, onSelect: () => remove(key),
      },
    ];
  };

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
            switch is "show it to me". */}
        <h3 className="text-sm font-semibold inline-flex items-center gap-2">
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
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Unassigned pool — everything not placed yet.  Takes the slack so
          the sections stay anchored to the bottom of the panel, which is
          where MUI keeps them and what stops the three headers walking up
          and down as fields are assigned. */}
      <div className="flex-1 min-h-0 overflow-y-auto border-b border-border">
        {unassigned.map((c) => (
          <div
            key={c.key}
            className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted/50 transition-colors group/field"
          >
            <span className="flex-1 min-w-0 truncate text-foreground">{c.label}</span>
            <ActionMenu
              items={axesFor(c.key).map((axis) => ({
                key: axis,
                label: `Add to ${AXIS_LABEL[axis]}`,
                onSelect: () => assign(c.key, axis),
              }))}
            >
              <button
                type="button"
                aria-label={`Add ${c.label}`}
                className="shrink-0 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <Plus size={14} />
              </button>
            </ActionMenu>
          </div>
        ))}
        {unassigned.length === 0 && (
          <p className="px-3 py-2 text-2xs text-muted-foreground italic">
            {query.trim()
              ? 'No unassigned field matches.'
              : 'Every field is assigned.'}
          </p>
        )}
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        {(['rows', 'columns', 'values'] as Axis[]).map((axis) => {
          const keys = keysOn(axis);
          const open = !folded.has(axis);
          return (
            <div key={axis} className="border-b border-border last:border-b-0">
              <button
                type="button"
                onClick={() => toggleFold(axis)}
                aria-expanded={open}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-muted/50 transition-colors"
              >
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {AXIS_LABEL[axis]}
                  {/* Values is the one axis a report cannot render without
                      — Rows is equally required, and saying so only on
                      one of them read as "Rows is optional". */}
                  {axis !== 'columns' && (
                    <span className="ml-1.5 normal-case tracking-normal font-normal text-2xs">
                      required
                    </span>
                  )}
                </span>
                <span className="inline-flex items-center gap-1.5 shrink-0">
                  <span className="text-2xs tabular-nums text-muted-foreground">{keys.length}</span>
                  {open ? <ChevronUp size={14} className="text-muted-foreground" />
                    : <ChevronDown size={14} className="text-muted-foreground" />}
                </span>
              </button>
              {open && (
                <>
                  {keys.length === 0 && (
                    <p className="px-3 pb-2 text-2xs text-muted-foreground italic">
                      {AXIS_HINT[axis]}
                    </p>
                  )}
                  <SortableContext
                    items={keys.map((k) => `${axis}:${k}`)}
                    strategy={verticalListSortingStrategy}
                  >
                    <div className="pb-1">
                      {keys.map((key) => (
                        <AssignedField
                          key={key}
                          id={`${axis}:${key}`}
                          label={byKey.get(key)?.label ?? key}
                          menu={fieldMenu(axis, key)}
                          onRemove={() => remove(key)}
                          trailing={axis === 'values' && (() => {
                            const picked = valueOf(key);
                            if (!picked) return null;
                            return (
                              <ActionMenu
                                items={offeredAggFns(byKey.get(key)).map((fn) => ({
                                  key: fn,
                                  label: AGG_FN_LABELS[fn],
                                  // A check on the ACTIVE function — the menu
                                  // listed them identically whichever was
                                  // running, so the only way to know what you
                                  // had picked was to close it again.
                                  icon: fn === picked.aggFn ? <Check size={14} /> : undefined,
                                  onSelect: () => setAggFn(key, fn),
                                }))}
                              >
                                <button
                                  type="button"
                                  className="px-1.5 py-0.5 rounded-full border border-border text-2xs text-muted-foreground hover:border-ring hover:text-foreground transition"
                                >
                                  {AGG_FN_LABELS[picked.aggFn].toLowerCase()}
                                </button>
                              </ActionMenu>
                            );
                          })()}
                        />
                      ))}
                    </div>
                  </SortableContext>
                </>
              )}
            </div>
          );
        })}
      </DndContext>
    </aside>
  );
}

/** One assigned field: drag handle · checkbox · label · [agg chip] · ⋮ */
function AssignedField({ id, label, menu, onRemove, trailing }: {
  id: string;
  label: string;
  menu: MenuAction[];
  onRemove: () => void;
  trailing?: React.ReactNode;
}) {
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 text-xs transition-colors hover:bg-muted/50',
        isDragging && 'opacity-60 bg-muted',
      )}
    >
      {/* The handle is its own hit target, NOT the whole row — the row
          carries a checkbox and a menu, and a drag that starts on either
          of those would eat the click. */}
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={`Reorder ${label}`}
        className="shrink-0 -ml-1 cursor-grab active:cursor-grabbing text-muted-foreground/60 hover:text-foreground transition-colors touch-none"
      >
        <GripVertical size={14} />
      </button>
      <input
        type="checkbox"
        checked
        onChange={onRemove}
        aria-label={`Remove ${label}`}
        className="shrink-0 cursor-pointer"
      />
      <span className="flex-1 min-w-0 truncate text-foreground">{label}</span>
      {trailing}
      <ActionMenu items={menu}>
        <button
          type="button"
          aria-label={`${label} options`}
          className="shrink-0 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        >
          <MoreVertical size={14} />
        </button>
      </ActionMenu>
    </div>
  );
}
