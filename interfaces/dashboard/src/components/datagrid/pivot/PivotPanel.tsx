import { useMemo, useState } from 'react';
import {
  Search, X, Check, Plus, GripVertical, MoreVertical,
  ChevronDown, ChevronUp, ArrowUp, ArrowDown,
  ChevronsUp, ChevronsDown, ListTree,
} from 'lucide-react';
import {
  DndContext, DragOverlay, pointerWithin, closestCorners, PointerSensor,
  KeyboardSensor, useSensor, useSensors, useDroppable,
  type DragEndEvent, type DragStartEvent, type DragOverEvent,
  type CollisionDetection,
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, verticalListSortingStrategy, arrayMove,
  sortableKeyboardCoordinates,
} from '@dnd-kit/sortable';

import { cn } from '../../../lib/utils';
import { Input } from '../../ui/input';
import { Button } from '../../ui/button';
import { Switch } from '../../ui/switch';
import { ActionMenu, type MenuAction } from '../../ui/context-menu';
import { InfoTip, Tip } from '../../tooltip';
import { toneClasses } from '../../../lib/status';
import { AGG_FN_LABELS } from '../../../types';
import type { AnyColumn, AggFn } from '../../../types';
import { offeredAggFns } from '../aggregation';
import { insertionIndex } from './pivot';
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
  // No column dimension means no Total column to freeze, so the control
  // that governs it would be a dead end.
  const hasColumnDim = model.columns.some((k) => !(model.disabled ?? []).includes(k));
  // Report-wide, not zone-scoped: drilling governs every figure in the
  // matrix, so its control sits in the header rather than in VALUES.
  const drillOn = model.drillDown ?? false;

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

  const isOff = (key: string) => (model.disabled ?? []).includes(key);
  /** The checkbox SWITCHES A FIELD OFF; it does not unassign it.  It used
   *  to remove, which meant unticking a field made it jump back to the
   *  pool — losing its position in the nesting order and, for a measure,
   *  its aggregation — and unticking your only measure blanked the whole
   *  report.  Off is a temporary "show me without this", which is what
   *  the tick shape promises.  Removing is the ⋮ menu, or a drag to the
   *  pool. */
  const setActive = (key: string, on: boolean) => {
    const cur = model.disabled ?? [];
    onChange({
      ...model,
      disabled: on ? cur.filter((k) => k !== key) : [...cur, key],
    });
  };

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

  const sensors = useSensors(
    useSensor(PointerSensor, {
      // A few pixels of slop so a click on the checkbox, the chip or the ⋮
      // isn't swallowed as a micro-drag.
      activationConstraint: { distance: 4 },
    }),
    // dnd-kit ships ``aria-describedby`` on every handle promising that
    // space bar picks the item up.  Without this sensor that promise was
    // a lie: the instruction was read out and nothing happened, which is
    // worse than no instruction (WCAG 2.1.1).
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  /**
   * Pointer precision when there IS a pointer; rectangles when there
   * isn't.
   *
   * ``pointerWithin`` alone was right for the mouse — rect overlap lit
   * up whichever zone the dragged BOX intersected, which is not where
   * the user is aiming.  But a KEYBOARD drag has no pointer coordinate
   * at all, so pointerWithin returned nothing on every arrow press: the
   * item picked up, announced itself, and then could never find a
   * target.  Pick-up that can't move is worse than no keyboard drag.
   *
   * So: try the pointer, and fall back to geometry when there is none.
   * dnd-kit's documented composition — and the fallback is exactly the
   * right answer for keyboard, where "nearest by rectangle" IS the
   * user's intent.
   */
  const collisionDetection: CollisionDetection = (args) => {
    const byPointer = pointerWithin(args);
    return byPointer.length > 0 ? byPointer : closestCorners(args);
  };

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
    if (overKey === key) { setDrop(null); return; }
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
    if (target.zone === from && from === 'pool') return;   // pool → pool
    // ONE path for every drop, and it matches what the line drew.
    // ``place`` removes then inserts, so the index has to be the
    // insert-before position corrected for that removal — see
    // ``insertionIndex``.  Routing same-list moves through arrayMove
    // instead was the bug: its ``to`` is a FINAL index, not an
    // insert-before one, so dragging a field downward landed it one
    // slot past the line.
    const at = target.zone === from ? keysIn(target.zone).indexOf(key) : -1;
    place(key, target.zone, insertionIndex(at, target.index));
  };

  const onDragCancel = () => { setDragKey(null); setDrop(null); };

  // Screen-reader announcements.  dnd-kit's defaults read out the raw
  // ids we invented for it — "Draggable item rows:customer was dropped
  // over droppable area rows:company_code" — which describes our data
  // model, not the user's task.  These say what moved, where it went,
  // and where it sits in the nesting order, because on this panel
  // POSITION is meaning.
  const nameOf = (id: string | number) => {
    const raw = String(id);
    return byKey.get(raw.slice(raw.indexOf(':') + 1))?.label ?? raw;
  };
  const zoneNameOf = (id: string | number) => {
    const zone = zoneOfDroppable(String(id));
    if (!zone) return null;
    return zone === 'pool' ? 'Available fields' : AXIS_LABEL[zone];
  };
  const placeIn = (id: string | number) => {
    const zone = zoneOfDroppable(String(id));
    if (!zone) return null;
    const list = keysIn(zone);
    const raw = String(id);
    const at = raw.startsWith('zone:') ? list.length : list.indexOf(raw.slice(raw.indexOf(':') + 1));
    const name = zoneNameOf(id);
    return list.length > 1 && at >= 0
      ? `${name}, position ${at + 1} of ${list.length}`
      : name;
  };
  const announcements = {
    onDragStart: ({ active }: { active: { id: string | number } }) =>
      `Picked up ${nameOf(active.id)}. Use the arrow keys to move it between Available fields, Rows, Columns and Values, then press space to drop.`,
    onDragOver: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) =>
      (over
        ? `${nameOf(active.id)} is over ${placeIn(over.id)}.`
        : `${nameOf(active.id)} is not over a drop target.`),
    onDragEnd: ({ active, over }: { active: { id: string | number }; over: { id: string | number } | null }) =>
      (over
        ? `${nameOf(active.id)} moved to ${placeIn(over.id)}.`
        : `${nameOf(active.id)} was not moved.`),
    onDragCancel: ({ active }: { active: { id: string | number } }) =>
      `Move cancelled. ${nameOf(active.id)} stayed where it was.`,
  };

  /** The per-field ⋮ menu — reorder, send to another axis, remove. */
  /** A checkable menu item.  The icon slot must be filled in BOTH states
   *  — `MenuActionList` renders `{icon}{label}` with no reserved column,
   *  so an unchecked item would sit left of a checked one and the label
   *  would visibly jump sideways each time you toggled it. */
  const check = (on: boolean) => (
    on ? <Check size={14} /> : <span aria-hidden className="inline-block w-3.5 shrink-0" />
  );

  /** The zone's OWN settings — they govern the column this zone renders,
   *  not any field in it.
   *
   *  Here rather than on a field's ⋮ because ROWS draws ONE merged label
   *  column ("Company / Customer" is a single cell): a pin on Company
   *  would silently govern Customer, and it would move to a different
   *  field's menu whenever the zone was reordered.  A setting that
   *  relocates for a reason unrelated to itself cannot be found twice.
   *
   *  In a zone ⋮ rather than an inline switch so the panel matches list
   *  mode, where Pin and Hide are exactly this: a column's ⋮ actions. */
  const zoneMenu = (axis: Axis): MenuAction[] => {
    const keys = keysOn(axis);
    if (keys.length === 0) return [];
    const toggle = (patch: Partial<PivotModel>) => onChange({ ...model, ...patch });
    // "Pin" is the product's OWN word for this — list mode's column ⋮
    // has a Pin submenu (Pin to Left / Pin to Right).  Freezing a column
    // against an edge is one concept, so it gets one name; an earlier
    // draft said "Keep row labels in view", which was a second name for
    // something already named and made a returning user relearn it.
    //
    // The OBJECT is named too, unlike list mode.  There you opened that
    // column's own ⋮, so a bare "Pin" was unambiguous; here the Values
    // zone holds a field AND generates the Total column, so "Pin" alone
    // could read as "pin Rate".
    if (axis === 'rows') {
      const on = model.pinRowLabels ?? false;
      return [{
        key: 'pin-rows', label: 'Pin row labels', icon: check(on),
        onSelect: () => toggle({ pinRowLabels: !on }),
      }];
    }
    if (axis === 'columns') {
      // NOT shortened to list mode's "Hide".  That hides ONE column the
      // operator picked; this prunes every bucket that came out empty.
      // Same word, different act — sharing it would be a false friend.
      const on = !!model.hideEmptyColumns;
      return [{
        key: 'hide-empty', label: 'Hide columns with no values', icon: check(on),
        onSelect: () => toggle({ hideEmptyColumns: !on }),
      }];
    }
    // No column dimension means no Total column to freeze, so the item
    // would be a dead end — offered and then unable to do anything.
    if (!hasColumnDim) return [];
    const on = model.pinTotals ?? false;
    return [{
      key: 'pin-totals', label: 'Pin Total column', icon: check(on),
      onSelect: () => toggle({ pinTotals: !on }),
    }];
  };

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
      // Verb-first and explicit.  Bare "Rows" / "Columns" sat among
      // "Move up" / "Move down" as two unlabelled nouns — the same list
      // mixing commands with destinations, with nothing saying which.
      ...axesFor(key).map((t, i) => ({
        key: `to-${t}`,
        label: t === axis ? `In ${AXIS_LABEL[t]}` : `Move to ${AXIS_LABEL[t]}`,
        icon: t === axis ? <Check size={14} /> : undefined,
        separatorBefore: i === 0,
        disabled: t === axis,
        onSelect: () => place(key, t),
      })),
      {
        // NOT ``danger``.  This returns the field to the available list —
        // fully reversible, nothing is destroyed.  Red-and-trash for a
        // reversible act spends the warning vocabulary we need for the
        // acts that really are irreversible.
        key: 'remove', label: 'Remove from report', icon: <X size={14} />,
        separatorBefore: true, onSelect: () => remove(key),
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
        // S5 (proportion & placement): the grab strip was w-1 — a 4px
        // target.  Now 8px (-4..+4): verification showed a wider
        // inward reach sits ON TOP of the pool grips at z-10 (cursor
        // flicker, 2px of every grip stolen), so the strip stops 4px
        // short of the grip box at x=8.  Honestly documented as BELOW
        // the 24px floor with no WCAG exception available (the grip
        // circles reach x=7) — an edge separator has nowhere to grow;
        // paint appears on hover only, so nothing thickens at rest.
        className="absolute inset-y-0 -left-1 w-2 cursor-col-resize hover:bg-primary/30 active:bg-primary/50 z-10"
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
            // The tail used to ASSERT "Click any figure to see the rows
            // behind it."  Drilling now defaults off, so for every new
            // report that sentence was false — the user clicked, nothing
            // happened, and the only help on the surface was what misled
            // them.  It names the control instead, which also carries the
            // discoverability that a default-off feature otherwise loses.
            label="Summarise the rows currently in view. Filters, search and tabs still apply — pivot reports on what they left. Use “Open the rows behind a figure” above to click into any number."
          />
          {/* Report-wide, so it sits with the pivot switch rather than in
              a zone: drilling governs every figure in the matrix — leaf
              cells, the Total column and the footer alike.
              A pressed BUTTON, not a switch, by owner decision.  It is
              still a binary, so it carries ``aria-pressed`` and paints
              its on-state with fill — the same active-button convention
              the toolbar's own Pivot icon uses. */}
          <Tip label={enabled
            ? (drillOn
                ? 'Figures are clickable — turn off to make the report read-only'
                : 'Open the rows behind a figure — make every figure clickable')
            : 'Turn pivot on first — there is no report to drill into yet'}
          >
            <Button
              variant={drillOn ? 'default' : 'ghost'}
              size="icon-sm"
              disabled={!enabled}
              aria-pressed={drillOn}
              aria-label="Open the rows behind a figure"
              onClick={() => onChange({ ...model, drillDown: !drillOn })}
            >
              <ListTree size={16} />
            </Button>
          </Tip>
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
              className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={onDragCancel}
        accessibility={{ announcements }}
      >
        {/* Unassigned pool — takes the slack so the sections stay
            anchored to the bottom, which is what stops the three headers
            walking up and down as fields are assigned.  It is also a drop
            target: dragging a field back here unassigns it.

            COMPOSITION (layout audit): the pool is deliberately BARE
            while the three zones below are boxed — enclosure is what
            says "take from here, drop into there"; the heading names
            the region (same words the screen-reader announcements
            already use). */}
        <div className="flex items-center justify-between px-3 pt-2 pb-1">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Available fields
          </span>
          <span className="text-2xs tabular-nums text-muted-foreground">{poolKeys.length}</span>
        </div>
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
                      className="shrink-0 p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
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

        {/* Each zone is an ENCLOSED region — border + tint + radius —
            with more air between zones (space-y-2) than inside them
            (rows' own py-1.5).  A hairline under a caps label can't
            separate three adjacent stacks of near-identical rows; the
            eye needs the box (Gestalt common region). */}
        <div className="shrink-0 p-2 space-y-2">
        {AXES.map((axis) => {
          const keys = keysOn(axis);
          const open = !folded.has(axis);
          const isTarget = drop?.zone === axis;
          return (
            <DropZone
              key={axis}
              zone={axis}
              active={isTarget}
              className="rounded-md border border-border bg-muted/30 overflow-hidden"
            >
              {/* The header is a BAND, not the first row: its own fill
                  (and a hairline under it while open) puts the control
                  that governs the zone on a different plane than the
                  rows it governs — the S1 region-anatomy rule. */}
              {/* A ROW, not a button — the band now carries a fold
                  control AND a settings menu, and a <button> may not
                  contain another button.  The fold target keeps the
                  whole remaining width so the band still reads as one
                  click surface. */}
              <div
                className={cn(
                  'flex items-stretch bg-muted/70 transition-colors',
                  open && 'border-b border-border',
                )}
              >
              <button
                type="button"
                onClick={() => toggleFold(axis)}
                aria-expanded={open}
                aria-label={`${AXIS_LABEL[axis]} — ${open ? 'collapse' : 'expand'}`}
                className="flex-1 min-w-0 flex items-center justify-between gap-2 px-3 py-2 hover:bg-muted transition-colors"
              >
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {AXIS_LABEL[axis]}
                  {/* ROWS only.  Values carried this too, and the marker
                      stayed behind when the transform stopped requiring
                      them: with no measure the report still shows the
                      groups and their counts (pivot.ts gates on active
                      ROWS alone).  A "required" badge on a field the
                      engine renders happily without is the UI
                      contradicting the thing it controls.  A chip, not
                      grey text the same colour as the heading:
                      warn-toned while unmet, quiet once satisfied. */}
                  {axis === 'rows' && (
                    <span className={cn(
                      'ml-1.5 px-1.5 py-0.5 rounded border normal-case tracking-normal font-medium text-3xs',
                      keys.length === 0
                        ? toneClasses('warn')
                        : 'border-border text-muted-foreground',
                    )}>
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
              {/* The zone's own settings, in the zone's own menu — the
                  same place list mode keeps Pin and Hide (a column's ⋮).
                  They govern the COLUMN the zone renders, which is why
                  they cannot hang off a field: ROWS draws ONE merged
                  label column, so a per-field pin would silently govern
                  its neighbours, and it would hop to another field's
                  menu the moment you reordered the zone. */}
              {zoneMenu(axis).length > 0 && (
                <ActionMenu items={zoneMenu(axis)}>
                  <button
                    type="button"
                    aria-label={`${AXIS_LABEL[axis]} settings`}
                    className="shrink-0 px-2 flex items-center text-muted-foreground/70 hover:text-foreground hover:bg-muted transition-colors"
                  >
                    <MoreVertical size={14} />
                  </button>
                </ActionMenu>
              )}
              </div>
              {open && (
                <>
                  {keys.length === 0 && (
                    <div className={cn(
                      'mx-2 my-2 min-h-9 flex items-center rounded border border-dashed px-2.5',
                      isTarget ? 'border-primary bg-primary/5' : 'border-border',
                    )}>
                      <p className={cn(
                        'text-2xs italic',
                        isTarget ? 'text-primary' : 'text-muted-foreground',
                      )}>
                        {isTarget ? `Drop to add to ${AXIS_LABEL[axis]}` : AXIS_HINT[axis]}
                      </p>
                    </div>
                  )}
                  {/* Zone SETTINGS used to sit here as inline
                      switches, directly above the field rows.  They are
                      in the zone's ⋮ now (see zoneMenu): they govern the
                      column the zone renders, which is what list mode
                      puts on a column's ⋮ as Pin / Hide. */}
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
                          checked={!isOff(key)}
                          showLineBefore={isTarget && drop.index === i}
                          showLineAfter={isTarget && drop.index >= keys.length && i === keys.length - 1}
                          onToggle={(on) => setActive(key, on)}
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
        </div>

        {/* The dragged field rides the cursor.  Without it the row simply
            vanishes from its list and reappears somewhere else on drop,
            which reads as a glitch rather than a move. */}
        <DragOverlay dropAnimation={null}>
          {dragKey && (
            // Compact and nudged down-right of the pointer: a full-width
            // pill sat exactly on the zone heading it was hovering, so
            // the one label you need to read ("COLUMNS") was the one
            // thing hidden. Elevation + a slight lift make the pick-up
            // state unmistakable.
            <div className="flex w-fit max-w-full translate-x-3 translate-y-3 scale-[1.02] items-center gap-2 px-3 py-1.5 text-xs rounded-md border border-primary bg-card shadow-xl cursor-grabbing">
              <GripVertical size={14} className="shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-foreground">
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
  id, label, assigned, checked, menu, onToggle, trailing,
  showLineBefore, showLineAfter,
}: {
  id: string;
  label: string;
  assigned?: boolean;
  /** Is this field CONTRIBUTING?  Unticked fields stay assigned. */
  checked?: boolean;
  menu?: MenuAction[];
  onToggle?: (on: boolean) => void;
  trailing?: React.ReactNode;
  /** The 2px insertion rule — the "line showing where the drag is
   *  going".  A ring alone tells you the section; only this tells you
   *  the POSITION, which is the whole point when order is nesting. */
  showLineBefore?: boolean;
  showLineAfter?: boolean;
}) {
  // ``transform``/``transition`` are deliberately NOT applied.  The
  // sortable strategy shuffles rows to open a gap, which is a second
  // indicator competing with our insertion line — and the two disagree
  // exactly where it matters most.  The strategy computes its gap from
  // dnd-kit's own index within THIS list, and since we never mutate the
  // model on hover (so an abandoned drag costs nothing), a list the
  // field is being dragged INTO doesn't know it is coming: the gap
  // opens in the wrong place, or not at all, while the line says
  // something else.  One indicator, correct in every case: rows hold
  // still, the line moves.  MUI's panel behaves the same way.
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({ id });
  return (
    <div
      ref={setNodeRef}
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
          className="shrink-0 p-1 -ml-1 rounded cursor-grab active:cursor-grabbing text-muted-foreground/60 hover:text-foreground hover:bg-muted transition-colors touch-none"
        >
          <GripVertical size={14} />
        </button>
        {assigned ? (
          <input
            type="checkbox"
            checked={!!checked}
            onChange={(e) => onToggle?.(e.target.checked)}
            aria-label={`Include ${label} in the report`}
            className="shrink-0 cursor-pointer accent-primary"
          />
        ) : (
          // Reserved checkbox column: pool rows have no tick, but the
          // slot holds its width so labels sit on ONE x in every
          // region — otherwise the shift reads as misalignment.
          <span className="shrink-0 w-3.5" aria-hidden />
        )}
        {/* An off field stays legible — it is still assigned, and you
            need to read it to decide whether to switch it back on. */}
        <span className={cn(
          'flex-1 min-w-0 truncate',
          checked === false ? 'text-muted-foreground line-through' : 'text-foreground',
        )}>
          {label}
        </span>
        {trailing}
        {menu && (
          <ActionMenu items={menu}>
            <button
              type="button"
              aria-label={`${label} options`}
              className="shrink-0 p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
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
