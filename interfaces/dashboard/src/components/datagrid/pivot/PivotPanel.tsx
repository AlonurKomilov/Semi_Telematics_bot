import { useMemo, useState } from 'react';
import {
  Search, X, Check, Plus, GripVertical, MoreVertical,
  ChevronDown, ChevronUp, Trash2, ArrowUp, ArrowDown,
  ChevronsUp, ChevronsDown,
} from 'lucide-react';
import {
  DndContext, DragOverlay, closestCorners, PointerSensor, useSensor, useSensors,
  useDroppable,
  type DragEndEvent, type DragStartEvent, type DragOverEvent,
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
 * Fields come from the column config: ``pivotable`` columns are offered
 * as dimensions (Rows / Columns), ``aggregable`` ones as Values — the
 * same opt-ins the grid's filters and footer totals already use.  A
 * field's legal destinations follow from that, so neither the menus nor
 * the drop targets ever offer a move that would be rejected.
 *
 * DRAG runs between every list (pool ↔ rows ↔ columns ↔ values) as well
 * as within one.  Two cues, both MUI's: the dragged field rides the
 * cursor in a DragOverlay, and the list under it takes a ring plus an
 * insertion LINE at the exact index the drop would land on.  Nothing is
 * committed until drop — hovering never mutates the model, so a drag
 * abandoned halfway leaves the report exactly as it was.
 */

type Axis = 'rows' | 'columns' | 'values';
/** ``pool`` is a real drop target — dragging a field out of a section
 *  and into the pool is how you unassign it by gesture. */
type Zone = Axis | 'pool';

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
const AXES: Axis[] = ['rows', 'columns', 'values'];

/** Item ids are ``<zone>:<key>``; container ids are ``zone:<zone>``. */
const itemId = (zone: Zone, key: string) => `${zone}:${key}`;
const zoneId = (zone: Zone) => `zone:${zone}`;

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
  /** Panel width in px, owned + persisted by the grid. */
  width: number;
  onWidthChange: (next: number) => void;
  /** Stretch to the grid's height instead of capping at 32rem. */
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

  // Live drag state.  ``drop`` is where the item WOULD land — the zone
  // draws its ring and the line from this, and nothing else reads it.
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ zone: Zone; index: number } | null>(null);

  const byKey = useMemo(() => new Map(columns.map((c) => [c.key, c])), [columns]);
  const dimensions = useMemo(() => columns.filter((c) => c.pivotable), [columns]);
  const measures = useMemo(() => columns.filter((c) => c.aggregable), [columns]);

  /** Where a field may legally go.  A column can be BOTH a dimension and
   *  a measure, so this is a list, not a single value. */
  const axesFor = (key: string): Axis[] => {
    const col = byKey.get(key);
    if (!col) return [];
    const out: Axis[] = [];
    if (col.pivotable) out.push('rows', 'columns');
    if (col.aggregable) out.push('values');
    return out;
  };
  const zoneOf = (key: string): Zone => {
    if (model.rows.includes(key)) return 'rows';
    if (model.columns.includes(key)) return 'columns';
    if (model.values.some((v) => v.key === key)) return 'values';
    return 'pool';
  };
  const keysOn = (axis: Axis): string[] => (
    axis === 'values' ? model.values.map((v) => v.key) : model[axis]
  );

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
  // assigned fields stay visible in their sections whatever you type, so
  // a search can never hide (and strand) your own selections.
  const poolKeys = allFields
    .filter((c) => zoneOf(c.key) === 'pool' && matches(c))
    .map((c) => c.key);

  const keysIn = (zone: Zone): string[] => (zone === 'pool' ? poolKeys : keysOn(zone));

  // ── Model edits ────────────────────────────────────────────────────

  const withoutKey = (m: PivotModel, key: string): PivotModel => ({
    ...m,
    rows: m.rows.filter((k) => k !== key),
    columns: m.columns.filter((k) => k !== key),
    values: m.values.filter((v) => v.key !== key),
  });

  /** Place ``key`` on ``zone`` at ``index`` (end when omitted). */
  const place = (key: string, zone: Zone, index?: number) => {
    const base = withoutKey(model, key);
    if (zone === 'pool') { onChange(base); return; }
    if (zone === 'values') {
      const fn = valueOf(key)?.aggFn ?? offeredAggFns(byKey.get(key))[0] ?? 'sum';
      const next = [...base.values];
      next.splice(index ?? next.length, 0, { key, aggFn: fn });
      onChange({ ...base, values: next });
      return;
    }
    const next = [...base[zone]];
    next.splice(index ?? next.length, 0, key);
    onChange({ ...base, [zone]: next });
  };

  const remove = (key: string) => onChange(withoutKey(model, key));

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

  function valueOf(key: string): PivotValueField | undefined {
    return model.values.find((v) => v.key === key);
  }

  // ── Drag ───────────────────────────────────────────────────────────

  const sensors = useSensors(useSensor(PointerSensor, {
    // A few pixels of slop so a click on the checkbox, the chip or the ⋮
    // isn't swallowed as a micro-drag.
    activationConstraint: { distance: 4 },
  }));

  /** Which zone does a droppable id belong to — an item or a container. */
  const zoneOfDroppable = (id: string): Zone | null => {
    if (id.startsWith('zone:')) return id.slice(5) as Zone;
    const zone = id.slice(0, id.indexOf(':'));
    return (zone === 'pool' || AXES.includes(zone as Axis)) ? zone as Zone : null;
  };

  /** May ``key`` be dropped on ``zone``?  The pool always accepts (it's
   *  "unassign"); an axis only accepts a field it can legally hold, so a
   *  customer name never becomes a measure. */
  const accepts = (key: string, zone: Zone) =>
    zone === 'pool' || axesFor(key).includes(zone);

  const onDragStart = (e: DragStartEvent) => {
    const id = String(e.active.id);
    setDragKey(id.slice(id.indexOf(':') + 1));
  };

  const onDragOver = (e: DragOverEvent) => {
    const { active, over } = e;
    if (!over) { setDrop(null); return; }
    const key = String(active.id).slice(String(active.id).indexOf(':') + 1);
    const overId = String(over.id);
    const zone = zoneOfDroppable(overId);
    if (!zone || !accepts(key, zone)) { setDrop(null); return; }
    // Over an ITEM → insert at its index.  Over the container itself →
    // append.  Computed rather than mutated: the model is untouched
    // until drop, so abandoning a drag costs nothing.
    if (overId.startsWith('zone:')) {
      setDrop({ zone, index: keysIn(zone).length });
      return;
    }
    const overKey = overId.slice(overId.indexOf(':') + 1);
    const idx = keysIn(zone).indexOf(overKey);
    setDrop({ zone, index: idx < 0 ? keysIn(zone).length : idx });
  };

  const onDragEnd = (e: DragEndEvent) => {
    const active = String(e.active.id);
    const key = active.slice(active.indexOf(':') + 1);
    const from = zoneOf(key);
    const target = drop;
    setDragKey(null);
    setDrop(null);
    if (!target || !accepts(key, target.zone)) return;
    if (target.zone === from && from !== 'pool') {
      moveTo(from as Axis, key, target.index);
      return;
    }
    if (target.zone === from) return;      // pool → pool is a no-op
    place(key, target.zone, target.index);
  };

  const onDragCancel = () => { setDragKey(null); setDrop(null); };

  /** The per-field ⋮ menu — reorder, send to another axis, remove. */
  const fieldMenu = (axis: Axis, key: string): MenuAction[] => {
    const list = keysOn(axis);
    const at = list.indexOf(key);
    const last = list.length - 1;
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
      ...axesFor(key).map((t, i) => ({
        key: `to-${t}`,
        label: AXIS_LABEL[t],
        icon: t === axis ? <Check size={14} /> : undefined,
        separatorBefore: i === 0,
        disabled: t === axis,
        onSelect: () => place(key, t),
      })),
      {
        key: 'remove', label: 'Remove', icon: <Trash2 size={14} />,
        danger: true, separatorBefore: true, onSelect: () => remove(key),
      },
    ];
  };

  const aggChip = (key: string) => {
    const picked = valueOf(key);
    if (!picked) return null;
    return (
      <ActionMenu
        items={offeredAggFns(byKey.get(key)).map((fn) => ({
          key: fn,
          label: AGG_FN_LABELS[fn],
          // A check on the ACTIVE function — the menu listed them
          // identically whichever was running, so the only way to know
          // what you had picked was to close it again.
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
  };

  return (
    <aside
      className={cn(
        'shrink-0 border-l border-border bg-card flex flex-col relative',
        fill ? 'min-h-0' : 'max-h-[32rem]',
      )}
      style={{ width }}
    >
      {/* Drag the left edge to trade panel width against table width. */}
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

      <DndContext
        sensors={sensors}
        // corners, not centre: the lists are short rows, and centre
        // detection makes the boundary between two adjacent sections
        // feel arbitrary at this row height.
        collisionDetection={closestCorners}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={onDragCancel}
      >
        {/* Unassigned pool — takes the slack so the sections stay
            anchored to the bottom, which is what stops the three headers
            walking up and down as fields are assigned.  It is also a drop
            target: dragging a field back here unassigns it. */}
        <DropZone
          zone="pool"
          active={drop?.zone === 'pool' && dragKey !== null && zoneOf(dragKey) !== 'pool'}
          className="flex-1 min-h-0 overflow-y-auto border-b border-border"
        >
          <SortableContext
            items={poolKeys.map((k) => itemId('pool', k))}
            strategy={verticalListSortingStrategy}
          >
            {poolKeys.map((key, i) => (
              <FieldRow
                key={key}
                id={itemId('pool', key)}
                label={byKey.get(key)?.label ?? key}
                showLineBefore={drop?.zone === 'pool' && drop.index === i}
                trailing={(
                  <ActionMenu
                    items={axesFor(key).map((axis) => ({
                      key: axis,
                      label: `Add to ${AXIS_LABEL[axis]}`,
                      onSelect: () => place(key, axis),
                    }))}
                  >
                    <button
                      type="button"
                      aria-label={`Add ${byKey.get(key)?.label ?? key}`}
                      className="shrink-0 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    >
                      <Plus size={14} />
                    </button>
                  </ActionMenu>
                )}
              />
            ))}
          </SortableContext>
          {poolKeys.length === 0 && (
            <p className="px-3 py-2 text-2xs text-muted-foreground italic">
              {query.trim() ? 'No unassigned field matches.' : 'Every field is assigned.'}
            </p>
          )}
        </DropZone>

        {AXES.map((axis) => {
          const keys = keysOn(axis);
          const open = !folded.has(axis);
          const isTarget = drop?.zone === axis;
          return (
            <DropZone
              key={axis}
              zone={axis}
              active={isTarget}
              className="border-b border-border last:border-b-0"
            >
              <button
                type="button"
                onClick={() => toggleFold(axis)}
                aria-expanded={open}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-muted/50 transition-colors"
              >
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {AXIS_LABEL[axis]}
                  {/* Rows and Values are BOTH required — a report can't
                      render without either.  Marking only one implied
                      the other was optional. */}
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
                    <p className={cn(
                      'px-3 pb-2 text-2xs italic',
                      isTarget ? 'text-primary' : 'text-muted-foreground',
                    )}>
                      {isTarget ? `Drop to add to ${AXIS_LABEL[axis]}` : AXIS_HINT[axis]}
                    </p>
                  )}
                  <SortableContext
                    items={keys.map((k) => itemId(axis, k))}
                    strategy={verticalListSortingStrategy}
                  >
                    <div className="pb-1">
                      {keys.map((key, i) => (
                        <FieldRow
                          key={key}
                          id={itemId(axis, key)}
                          label={byKey.get(key)?.label ?? key}
                          assigned
                          showLineBefore={isTarget && drop.index === i}
                          showLineAfter={isTarget && drop.index >= keys.length && i === keys.length - 1}
                          onRemove={() => remove(key)}
                          menu={fieldMenu(axis, key)}
                          trailing={axis === 'values' ? aggChip(key) : undefined}
                        />
                      ))}
                    </div>
                  </SortableContext>
                </>
              )}
            </DropZone>
          );
        })}

        {/* The dragged field rides the cursor.  Without it the row simply
            vanishes from its list and reappears somewhere else on drop,
            which reads as a glitch rather than a move. */}
        <DragOverlay dropAnimation={null}>
          {dragKey && (
            <div className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-md border border-primary bg-card shadow-lg">
              <GripVertical size={14} className="text-muted-foreground" />
              <span className="truncate text-foreground">
                {byKey.get(dragKey)?.label ?? dragKey}
              </span>
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </aside>
  );
}

/** A droppable list.  Rings when it's the pending destination — the
 *  section-level half of "where will this land?", with the insertion
 *  line inside carrying the exact index. */
function DropZone({ zone, active, className, children }: {
  zone: Zone;
  active: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const { setNodeRef } = useDroppable({ id: zoneId(zone) });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        className,
        'transition-colors',
        active && 'ring-1 ring-inset ring-primary bg-primary/5',
      )}
    >
      {children}
    </div>
  );
}

/** One field row — in the pool or assigned to an axis. */
function FieldRow({
  id, label, assigned, menu, onRemove, trailing, showLineBefore, showLineAfter,
}: {
  id: string;
  label: string;
  assigned?: boolean;
  menu?: MenuAction[];
  onRemove?: () => void;
  trailing?: React.ReactNode;
  /** The 2px insertion rule — the "line showing where the drag is
   *  going".  A ring alone tells you the section; only this tells you
   *  the POSITION, which is the whole point when order is nesting. */
  showLineBefore?: boolean;
  showLineAfter?: boolean;
}) {
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn('relative', isDragging && 'opacity-40')}
    >
      {showLineBefore && <Rule edge="top" />}
      <div className="flex items-center gap-2 px-3 py-1.5 text-xs transition-colors hover:bg-muted/50">
        {/* The handle is its own hit target, NOT the whole row — the row
            carries a checkbox, a chip and a menu, and a drag starting on
            any of those would eat the click. */}
        <button
          type="button"
          {...attributes}
          {...listeners}
          aria-label={`Reorder ${label}`}
          className="shrink-0 -ml-1 cursor-grab active:cursor-grabbing text-muted-foreground/60 hover:text-foreground transition-colors touch-none"
        >
          <GripVertical size={14} />
        </button>
        {assigned && (
          <input
            type="checkbox"
            checked
            onChange={onRemove}
            aria-label={`Remove ${label}`}
            className="shrink-0 cursor-pointer"
          />
        )}
        <span className="flex-1 min-w-0 truncate text-foreground">{label}</span>
        {trailing}
        {menu && (
          <ActionMenu items={menu}>
            <button
              type="button"
              aria-label={`${label} options`}
              className="shrink-0 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <MoreVertical size={14} />
            </button>
          </ActionMenu>
        )}
      </div>
      {showLineAfter && <Rule edge="bottom" />}
    </div>
  );
}

const Rule = ({ edge }: { edge: 'top' | 'bottom' }) => (
  <div
    className={cn(
      'absolute left-2 right-2 h-0.5 bg-primary rounded-full pointer-events-none z-10',
      edge === 'top' ? 'top-0 -translate-y-px' : 'bottom-0 translate-y-px',
    )}
  />
);
