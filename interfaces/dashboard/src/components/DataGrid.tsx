import { Fragment, useState, useMemo, useEffect, useRef, useCallback, useLayoutEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getGroupedRowModel,
  getExpandedRowModel,
  flexRender,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
  type ColumnOrderState,
  type ColumnPinningState,
  type ColumnSizingState,
  type GroupingState,
  type ExpandedState,
  type Header,
  type Cell,
  type Row,
} from '@tanstack/react-table';
import {
  ChevronUp, ChevronDown, ChevronLeft, ChevronRight, Rows3, Rows2, Rows4,
  Search, X, Columns3, Download, Copy, Filter as FilterIcon, ArrowUpDown,
  CornerUpRight,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Menu as MenuPrimitive } from '@base-ui/react/menu';
import { createPortal } from 'react-dom';
import {
  DndContext, DragOverlay, PointerSensor, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent, type DragOverEvent,
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, horizontalListSortingStrategy, arrayMove,
  type SortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Input } from './ui/input';
import {
  TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';
import { cn } from '../lib/utils';
import type { AnyColumn } from '../types';
import ColumnFilterMenu from './ColumnFilterMenu';
import ColumnHeaderMenu from './ColumnHeaderMenu';
import ManageColumnsMenu from './ManageColumnsMenu';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from './ui/select';
import { Button } from './ui/button';
import { Tip } from './tooltip';
import { exportRowsAsCsv, buildTsv, writeToClipboard } from '../lib/csv';
import { useUserPreference } from '../hooks/useUserPreference';

type Density = 'compact' | 'default' | 'roomy';

/** Density is a whole-table treatment, not just body padding — each
 *  step also moves the header height and (for compact) the type size
 *  so the three states are unmistakable at a glance:
 *    compact → tight cells + short header + text-xs (max rows/screen)
 *    default → the balanced reading layout
 *    roomy   → generous spacing for demo / low-vision reading
 */
const DENSITY_PADDING: Record<Density, string> = {
  compact: 'py-1',
  default: 'py-3',
  roomy: 'py-5',
};
const DENSITY_HEADER: Record<Density, string> = {
  compact: 'h-8',
  default: 'h-10',
  roomy: 'h-12',
};
const DENSITY_TEXT: Record<Density, string> = {
  compact: 'text-xs',
  default: 'text-sm',
  roomy: 'text-sm',
};
const DENSITY_GROUP_ROW: Record<Density, string> = {
  compact: 'py-1',
  default: 'py-2',
  roomy: 'py-3',
};
const DENSITY_CYCLE: readonly Density[] = ['compact', 'default', 'roomy'];
const DENSITY_ICONS: Record<Density, typeof Rows3> = {
  compact: Rows4,
  default: Rows3,
  roomy: Rows2,
};
const DENSITY_LABELS: Record<Density, string> = {
  compact: 'Compact',
  default: 'Default',
  roomy: 'Roomy',
};

/** Pre-server-sync storage key — read once as the preference default
 *  so an operator's old device-local choice migrates into the synced
 *  per-user preference instead of resetting to Default. */
const LEGACY_DENSITY_KEY = '4truck.table.density';
function readLegacyDensity(): Density {
  try {
    const v = localStorage.getItem(LEGACY_DENSITY_KEY);
    if (v === 'compact' || v === 'default' || v === 'roomy') return v;
  } catch { /* ignore */ }
  return 'default';
}

/** One button in the bulk-action bar (the top toolbar strip shown when
 *  rows are selected).  DataGrid renders these from the ``bulkActions``
 *  prop and hands each ``onRun`` the currently-selected ORIGINAL rows —
 *  never tanstack row ids — so pages read their own domain fields. */
export interface BulkAction {
  label: string;
  icon?: LucideIcon;
  /** Visual weight of the button (default = neutral).  ``danger``
   *  paints the icon red. */
  tone?: 'default' | 'danger';
  /** window.confirm prompt before running; receives the selection
   *  count.  Omit for actions that need no confirmation. */
  confirm?: (count: number) => string;
  /** When present, the button opens a MENU of these options instead of
   *  running directly; the chosen option's ``value`` is passed to
   *  ``onRun`` (e.g. "Change status ▾" → pending / in-progress / done). */
  options?: { value: string; label: string }[];
  /** Run against the selected ORIGINAL rows.  ``value`` is the chosen
   *  menu option for a dropdown action, undefined for a plain one. */
  onRun: (rows: Record<string, unknown>[], value?: string) => void | Promise<void>;
}

interface DataGridProps {
  columns: AnyColumn[];
  data: Record<string, unknown>[];
  onRowClick?: (row: Record<string, unknown>) => void;
  searchKey?: string | string[];
  stickyHeader?: string;
  searchPlaceholder?: string;
  headerToolbar?: React.ReactNode;
  /** When set, the table participates in the "Manage columns" + drag-
   *  reorder + visibility-persistence layer.  Operators can hide
   *  columns and rearrange them; the layout survives reload.
   *  ``tableId`` is the localStorage namespace (e.g. ``"maintenance"``,
   *  ``"team-mgmt"``).  Omit on tables that should keep the legacy
   *  fixed-layout behaviour. */
  tableId?: string;
  /** Optional content rendered at the LEADING edge of the first
   *  VISIBLE column — in both the header and every body cell.  Used
   *  to attach a bulk-select checkbox (or row-number, expand toggle,
   *  …) to whichever column is currently leftmost.  When the operator
   *  pins / reorders columns, the leading content follows — so a
   *  pinned-first Priority gets the checkbox even though Vehicle had
   *  it before.  Parent owns the selection state; DataGrid just
   *  places the React nodes parent renders. */
  /** Checkbox bulk-SELECTION.  When true, DataGrid owns the checkbox
   *  column itself — header select-all (with indeterminate), a per-row
   *  box, and a group select-all when grouped — all feeding the SAME
   *  selection set as modifier-click.  Pages no longer hand-roll
   *  ``firstColumnLeading`` checkboxes; that prop stays for genuinely
   *  non-selection leading content (row number, expand toggle). */
  bulkSelection?: boolean;
  /** Mirror the selection out to the page (e.g. AI page context).
   *  Called with the selected ORIGINAL rows whenever it changes. */
  onBulkSelectionChange?: (rows: Record<string, unknown>[]) => void;
  /** Buttons for the bulk-action bar (the top selection strip), shown before the
   *  built-in Copy/Clear when 1+ rows are selected. */
  bulkActions?: BulkAction[];
  /** Per-row accessible label for the select checkbox (screen readers).
   *  Without it every box announces the generic "Select row"; pass a
   *  row-identifying string (e.g. the unit number or reference). */
  bulkRowLabel?: (row: Record<string, unknown>) => string;
  /** Gate which rows are selectable — a row returning false shows no
   *  checkbox and is excluded from select-all (e.g. Alerts: only
   *  ackable alerts).  Omit → every row is selectable. */
  isRowSelectable?: (row: Record<string, unknown>) => boolean;
  /** CONTROLLED selection.  Pass BOTH to let a page own the selection
   *  set (keyed by the row's ``id`` string) — e.g. a shared context
   *  that other components read.  Omit both for the default
   *  DataGrid-owned (uncontrolled) selection. */
  selectedIds?: Set<string>;
  onSelectedIdsChange?: (next: Set<string>) => void;
  firstColumnLeading?: {
    header: () => React.ReactNode;
    cell: (row: Record<string, unknown>) => React.ReactNode;
    /** Optional node rendered at the leading edge of a ROW-GROUP
     *  header row (when the operator groups rows by a column).
     *  Receives the group's value and its leaf rows — used for a
     *  group-level bulk-select checkbox ("select all alerts on this
     *  vehicle") with indeterminate state. */
    groupHeader?: (value: unknown, rows: Record<string, unknown>[]) => React.ReactNode;
  };
  /** Custom content for ROW-GROUP header rows (row grouping, not the
   *  column-bracket grouping).  Receives the group value + leaf rows;
   *  replaces the default "<value> (N)" label.  Use for rich group
   *  summaries — e.g. Alerts renders severity count badges + the
   *  latest-seen timestamp per vehicle. */
  rowGroupHeader?: (value: unknown, rows: Record<string, unknown>[]) => React.ReactNode;
  /** Column key the table starts row-grouped by (until the operator
   *  picks otherwise — their choice persists and wins).  Used by the
   *  Alerts "by vehicle" view to open pre-grouped on vehicle_name.
   *  Reset-to-defaults returns to this, not to ungrouped. */
  defaultRowGroup?: string;
  /** Toggle the toolbar strip (Search / Export / Columns / density).
   *  Set ``false`` on tables that are pure display surfaces (billing
   *  summary lines, form-embedded parts tables, small settings rows)
   *  where the chrome would feel like clutter.  Defaults to ``true``
   *  so all existing consumers get the toolbar unchanged.  When
   *  disabled the ManageColumnsMenu popover is also skipped since
   *  its trigger lives in the toolbar. */
  enableToolbar?: boolean;
  /** Toggle the pagination footer (Show-per-page · range · prev/next)
   *  AND the underlying page-slicing.  Set ``false`` for short lists
   *  where paginating 5–20 rows would just add noise; DataGrid then
   *  renders every filtered row.  Defaults to ``true``. */
  enablePagination?: boolean;
  /** Segment tabs rendered above the toolbar — mutually-exclusive
   *  lifecycle slices of the SAME dataset with live counts (Active /
   *  Archive, pipeline stages).  The FIRST tab is the default and
   *  every page load starts there (selection is session-only, not
   *  persisted — see the segment-state comment in the component).
   *  Pass a module-level constant (not an inline literal) so the
   *  array identity is stable across renders. */
  segments?: DataGridSegment[];
}

// ── Server-side preference keys ───────────────────────────────
//
// One row per (user, key) in user_preferences (Postgres-backed,
// optionally Redis-cached on the read path).  The dashboard's
// useUserPreference hook handles the round-trip; we just construct
// the key strings here.  Keys are namespaced per-table so two
// DataGrids on the same page don't clobber each other.
const visibilityKey = (id: string | undefined) =>
  id ? `table.${id}.visibility` : '';
const orderKey      = (id: string | undefined) =>
  id ? `table.${id}.order` : '';
const pinningKey    = (id: string | undefined) =>
  id ? `table.${id}.pinning` : '';
const pageSizeKey   = (id: string | undefined) =>
  id ? `table.${id}.pageSize` : '';
const groupsKey     = (id: string | undefined) =>
  id ? `table.${id}.groups` : '';
const rowGroupKey   = (id: string | undefined) =>
  id ? `table.${id}.rowGroup` : '';
const colWidthsKey  = (id: string | undefined) =>
  id ? `table.${id}.colWidths` : '';

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100, 250];
const DEFAULT_PAGE_SIZE = 25;

/** One segment tab — a predefined slice of the grid's dataset,
 *  rendered as a tab with a live count above the toolbar.  Tabs are
 *  for the ONE dominant lifecycle dimension of a dataset (Active /
 *  Archive, pipeline stages); anything finer belongs in column
 *  filters.  A tab without ``match`` shows every row. */
export interface DataGridSegment {
  key: string;
  label: string;
  match?: (row: Record<string, unknown>) => boolean;
  /** Hide the count badge (e.g. on an "All" tab where the number is
   *  noise).  Defaults to showing it. */
  showCount?: boolean;
}

/** One folder-style segment tab.
 *
 *  The active tab's silhouette — rounded top corners, vertical sides,
 *  and the two CONCAVE bottom fillets that flare into the toolbar —
 *  is drawn as a SINGLE svg outline (one fill path + one stroke path)
 *  sized to the tab's measured box, NOT as CSS border + gradient
 *  corner hacks.  One continuous path means the border is a single
 *  crisp stroke with no junction seams, and measuring the box makes
 *  it correct at any label width.  Inactive tabs render no svg — just
 *  text with a hover tint — so the card's top border runs unbroken
 *  beneath them ("closed folders").
 *
 *  Geometry (svg units = px; the svg overhangs the button by RF on
 *  each side for the fillets and 2px below to cover the card border
 *  the active tab overlaps).  Fillets use a quadratic Bézier whose
 *  control point is the tab's own bottom corner, which pulls the
 *  curve into a clean concave sweep tangent to both the vertical wall
 *  and the horizontal card border. */
function SegmentTab({
  label, count, showCount, active, onClick,
}: {
  label: string;
  count: number;
  showCount: boolean;
  active: boolean;
  onClick: () => void;
}) {
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = btnRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.offsetWidth, h: el.offsetHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // The silhouette must track the operator's Corners preset like
  // every other component: read the live ``--radius`` token and
  // re-read whenever the theme attributes on <html> change (the
  // Sharp / Rounded / Pill picker stamps data-radius there — a CSS
  // var change alone doesn't re-render React).
  const [radiusPx, setRadiusPx] = useState(10);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const read = () => {
      const raw = getComputedStyle(root).getPropertyValue('--radius').trim();
      let px = 10;
      if (raw.endsWith('rem')) {
        px = parseFloat(raw) * (parseFloat(getComputedStyle(root).fontSize) || 16);
      } else if (raw.endsWith('px')) {
        px = parseFloat(raw);
      }
      setRadiusPx(Number.isFinite(px) ? px : 10);
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ['class', 'data-theme', 'data-radius', 'style'] });
    return () => mo.disconnect();
  }, []);

  const { w: W, h: H } = box;
  // Top-corner radius = the ``rounded-md`` formula (--radius − 2px),
  // matching what the inactive tabs' ``rounded-t-md`` resolves to —
  // Sharp ≈ 2px, default ≈ 8px, Pill ≈ 14px — capped so tall radii
  // can't exceed the tab's own box.  The concave fillet scales WITH
  // the corner (a big soft flare next to a sharp square corner reads
  // as a mismatch) but stays ≤ 8px so Pill doesn't grow huge feet.
  const RT = Math.max(2, Math.min(radiusPx - 2, H > 0 ? Math.floor(H / 2) : 12));
  const RF = Math.max(2, Math.min(RT, 8));   // fillet radius (concave flare)
  const vw = W + 2 * RF;   // svg width: button + a fillet on each side
  const vh = H + 2;        // fill dips below the button to cover the card border
  // Baseline for the fillet tails: the CENTRE of the card's 1px top
  // border row.  The strip overlaps the card by exactly 1px, so that
  // border row is the button box's last pixel row [H-1, H) — centre
  // H - 0.5.  Ending the stroke there makes each curve's horizontal
  // tangent COLLINEAR with the card border it hands off to; ending at
  // H (as before) put the tails ~1px below the border line, which
  // rendered as a small step/hook at both outer ends.
  const B = H - 0.5;
  // Outline (open bottom): left fillet → left side → top corners →
  // right side → right fillet.  Only built once the box is measured.
  const outline = W > 0 && H > 0
    ? [
        `M 0 ${B}`,                                 // left fillet outer, on the border line
        `Q ${RF} ${B} ${RF} ${B - RF}`,             // concave fillet up to the wall
        `L ${RF} ${RT}`,                            // up the left side
        `Q ${RF} 0 ${RF + RT} 0`,                   // top-left corner
        `L ${RF + W - RT} 0`,                       // across the top
        `Q ${RF + W} 0 ${RF + W} ${RT}`,            // top-right corner
        `L ${RF + W} ${B - RF}`,                    // down the right side
        `Q ${RF + W} ${B} ${vw} ${B}`,              // concave fillet down to outer
      ].join(' ')
    : '';

  return (
    <button
      ref={btnRef}
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        // No extra bottom-margin on the active tab: the strip already
        // overlaps the card border by exactly 1px, and the outline
        // geometry above assumes precisely that (baseline B rides the
        // border row's centre).  A second -mb-px here shifted the
        // whole silhouette 1px past the border and broke the joint.
        'relative inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition-colors',
        active
          ? 'text-foreground'
          : 'rounded-t-md text-muted-foreground hover:text-foreground hover:bg-muted/40',
      )}
    >
      {active && outline && (
        <svg
          aria-hidden
          width={vw}
          height={vh}
          viewBox={`0 0 ${vw} ${vh}`}
          className="absolute top-0 pointer-events-none"
          style={{ left: -RF, overflow: 'visible' }}
        >
          {/* Fill: outline closed along the (hidden) bottom edge. */}
          <path
            d={`${outline} L ${vw} ${vh} L 0 ${vh} Z`}
            style={{ fill: 'var(--muted)' }}
          />
          {/* Stroke: same outline, open bottom, single crisp 1px line. */}
          <path
            d={outline}
            style={{ fill: 'none', stroke: 'var(--border)', strokeWidth: 1 }}
          />
        </svg>
      )}
      <span className="relative z-10 inline-flex items-center gap-1.5">
        {label}
        {showCount && (
          <span
            className={cn(
              'tabular-nums text-2xs px-1.5 py-0.5 rounded-full',
              active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
            )}
          >
            {count.toLocaleString()}
          </span>
        )}
      </span>
    </button>
  );
}

/** One contiguous run of same-group (or ungrouped) columns in the
 *  bracket header row.  ``memberIds`` are the leaf column ids in
 *  render order — used to move the whole block on group drag. */
type GroupRun = {
  label: string | null;
  span: number;
  sticky?: React.CSSProperties;
  firstId: string;
  memberIds: string[];
};

// No-shift sorting strategy for the group bracket row.  The default
// horizontal strategy slides sibling cells around during a drag —
// but the member COLUMNS below don't move until drop, so a sliding
// bracket band over static columns reads as broken.  Instead the row
// stays put; feedback comes from the DragOverlay chip (what you're
// holding) and the insertion indicator (where it will land).
const noShiftStrategy: SortingStrategy = () => null;

// Does ``row`` pass ``col``'s active filter value?  Mirrors the
// tanstack ``filterFn`` wiring inside the component (select /
// range / date-range shapes) but works on RAW data rows — used by
// the faceted-options computation, which needs to ask "which rows
// survive every OTHER column's filter" without going through the
// table instance.  Keep the two in sync when filter semantics
// change.
function rowPassesColFilter(
  row: Record<string, unknown>,
  col: AnyColumn,
  fv: unknown,
): boolean {
  if (col.filterMode === 'range') {
    const range = fv as [number | null, number | null] | undefined;
    if (!range || (range[0] == null && range[1] == null)) return true;
    const raw = row[col.key];
    const n = typeof raw === 'number' ? raw : Number(raw);
    if (!Number.isFinite(n)) return false;
    if (range[0] != null && n < range[0]) return false;
    if (range[1] != null && n > range[1]) return false;
    return true;
  }
  if (col.filterMode === 'date-range') {
    const range = fv as [string | null, string | null] | undefined;
    if (!range || (!range[0] && !range[1])) return true;
    const t = new Date(String(row[col.key] ?? '')).getTime();
    if (!Number.isFinite(t)) return false;
    if (range[0]) {
      const fromT = new Date(range[0]).getTime();
      if (Number.isFinite(fromT) && t < fromT) return false;
    }
    if (range[1]) {
      const toT = new Date(range[1] + 'T23:59:59.999').getTime();
      if (Number.isFinite(toT) && t > toT) return false;
    }
    return true;
  }
  const selected = fv as string[] | undefined;
  if (!selected || selected.length === 0) return true;
  const hay = col.filterValue
    ? col.filterValue(row)
    : String(row[col.key] ?? '');
  return selected.includes(hay);
}

export default function DataGrid({
  columns, data: sourceData, onRowClick, searchKey, stickyHeader, searchPlaceholder,
  headerToolbar, tableId, firstColumnLeading, rowGroupHeader, defaultRowGroup,
  enableToolbar = true, enablePagination = true, segments,
  bulkSelection = false, onBulkSelectionChange, bulkActions, bulkRowLabel,
  isRowSelectable, selectedIds: controlledSelectedIds, onSelectedIdsChange,
}: DataGridProps) {
  const { t } = useTranslation();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  // Density is a personal reading preference — deliberately GLOBAL
  // across every table (one key, no tableId namespace) and synced
  // server-side so it follows the operator across devices like the
  // rest of the layout preferences.  Guarded against corrupt stored
  // values so a bad payload can't strand the table without padding.
  const {
    value: densityPref,
    setValue: setDensity,
  } = useUserPreference<Density>('table.density', readLegacyDensity());
  const density: Density = DENSITY_CYCLE.includes(densityPref)
    ? densityPref
    : 'default';

  // ── Segment tabs ────────────────────────────────────────────
  //
  // Tabs slice ``sourceData`` BEFORE anything else the grid does —
  // search, column filters, uniques, sort, export and pagination all
  // operate within the active segment, so every number the grid
  // shows agrees with the tab the operator is on.  Counts, however,
  // are computed from the UNSLICED data so the inactive tab's badge
  // stays live ("Archive 13" while you're on Active).
  //
  // Deliberately SESSION-ONLY state (plain useState, not
  // useUserPreference): every page load starts on the FIRST tab —
  // the working set (Active / Pending pipeline).  Persisting the
  // last-viewed tab meant an operator who peeked at Archive
  // yesterday reopened the page "missing" their active rows today,
  // which reads as data loss, not as a remembered preference.
  // Column layout / density stay persisted; WHICH SLICE you're
  // looking at resets to the default like the page's filters do.
  const [segmentPref, setSegmentPref] = useState<string>(segments?.[0]?.key ?? '');
  const activeSegment = useMemo(() => {
    if (!segments?.length) return null;
    // Selected key may reference a tab that no longer exists
    // (config changed) — fall back to the first.
    return segments.find(s => s.key === segmentPref) ?? segments[0];
  }, [segments, segmentPref]);
  const segmentCounts = useMemo(() => {
    if (!segments?.length) return {};
    const counts: Record<string, number> = {};
    for (const seg of segments) {
      counts[seg.key] = seg.match
        ? sourceData.filter(seg.match).length
        : sourceData.length;
    }
    return counts;
  }, [segments, sourceData]);
  const data = useMemo(() => {
    if (!activeSegment?.match) return sourceData;
    return sourceData.filter(activeSegment.match);
  }, [sourceData, activeSegment]);

  // ── Column-layout state (visibility / order / pinning) ─────
  //
  // Persisted server-side via useUserPreference so layouts follow the
  // user across devices.  Without a ``tableId`` the hook is passed an
  // empty key and degrades to in-memory ``useState`` (since the 3-dot
  // menu / drag / Columns popover are also gated behind ``tableId``,
  // those tables stay on the legacy fixed-layout behaviour).
  const {
    value: columnVisibility,
    setValue: setColumnVisibility,
  } = useUserPreference<VisibilityState>(visibilityKey(tableId), {});
  // Effective visibility = column-level ``defaultHidden`` overlaid by
  // the operator's persisted choices.  Persisted always wins where
  // set, so unhiding a defaultHidden column sticks; Reset clears
  // persisted and the defaults reappear.  All downstream reads
  // (tanstack state, hiddenCount badge, manage popover, ColumnHeader
  // toggle logic) use this rather than the raw persisted map.
  const effectiveVisibility = useMemo<VisibilityState>(() => {
    const defaults: VisibilityState = {};
    for (const col of columns) {
      if (col.defaultHidden) defaults[col.key] = false;
    }
    return { ...defaults, ...columnVisibility };
  }, [columns, columnVisibility]);
  const {
    value: columnOrder,
    setValue: setColumnOrder,
  } = useUserPreference<ColumnOrderState>(orderKey(tableId), []);
  const {
    value: columnPinning,
    setValue: setColumnPinning,
  } = useUserPreference<ColumnPinningState>(
    pinningKey(tableId),
    { left: [], right: [] },
  );
  // ColumnSizing is populated by a per-header ResizeObserver below
  // (see ColumnHeaderCell).  tanstack uses these values inside
  // ``column.getStart('left') / getAfter('right')`` to compute the
  // ``left``/``right`` offset for each pinned column.  Without
  // measured sizes those helpers fall back to ``getSize()`` which
  // defaults to **150px** for every column — that off-by-much value
  // is what caused pinned columns past the first to render at the
  // wrong sticky offset (empty gap on one side + overlap of the
  // next column).  Not persisted: measurements are recomputed on
  // every mount from the real DOM, so storing them would just be
  // stale ballast.
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const reportColumnWidth = useCallback((id: string, width: number) => {
    setColumnSizing(prev => prev[id] === width ? prev : { ...prev, [id]: width });
  }, []);

  // ── User column widths (drag-to-resize) ─────────────────────
  //
  // Empty until the operator's FIRST manual resize.  At that moment
  // we snapshot the currently-measured widths of every visible
  // column (so nothing visually jumps) and flip the table to
  // ``table-layout: fixed`` — from then on the operator's widths are
  // authoritative and persist per-user.  Reset-to-defaults clears
  // back to auto layout.
  const {
    value: userWidths,
    setValue: setUserWidths,
  } = useUserPreference<Record<string, number>>(colWidthsKey(tableId), {});
  const hasUserWidths = Object.keys(userWidths).length > 0;
  // What tanstack sees: measured widths (for pinned offsets on auto-
  // layout tables) overlaid by the operator's explicit widths.
  const effectiveSizing = useMemo<ColumnSizingState>(
    () => ({ ...columnSizing, ...userWidths }),
    [columnSizing, userWidths],
  );

  // ── Column-group overrides (user-managed grouping) ─────────
  //
  // Groups start from the column config's ``group`` field; operators
  // can then regroup / ungroup per column via the 3-dot menu.  Over-
  // rides persist per-user: ``{ colKey: 'GroupName' }`` assigns,
  // ``{ colKey: null }`` explicitly ungroups a config-grouped column.
  const {
    value: groupOverrides,
    setValue: setGroupOverrides,
  } = useUserPreference<Record<string, string | null>>(groupsKey(tableId), {});
  const effectiveGroupByKey = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const col of columns) {
      m.set(
        col.key,
        col.key in groupOverrides ? groupOverrides[col.key] : (col.group ?? null),
      );
    }
    return m;
  }, [columns, groupOverrides]);
  // All distinct group names currently in effect — feeds the 3-dot
  // menu's "Add to <group>" list.
  const groupNames = useMemo(() => {
    const names = new Set<string>();
    for (const g of effectiveGroupByKey.values()) if (g) names.add(g);
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [effectiveGroupByKey]);

  // Assign a column to a named group (existing or new) and slide it
  // adjacent to that group's members so the bracket forms immediately
  // — an assignment that leaves the column far away would render a
  // second fragmentary bracket, which reads as a bug.
  const assignGroup = useCallback((key: string, name: string) => {
    setGroupOverrides(prev => ({ ...prev, [key]: name }));
    setColumnOrder(prev => {
      const base = prev.length ? prev : columns.map(c => c.key);
      // Membership from the CURRENT effective map (the just-assigned
      // key is excluded — its override lands on the next render).
      const members = base.filter(
        k => k !== key && effectiveGroupByKey.get(k) === name,
      );
      if (members.length === 0) return prev.length ? prev : base;
      const without = base.filter(k => k !== key);
      const lastIdx = Math.max(...members.map(m => without.indexOf(m)));
      const next = [...without];
      next.splice(lastIdx + 1, 0, key);
      return next;
    });
  }, [columns, effectiveGroupByKey, setGroupOverrides, setColumnOrder]);

  const ungroupColumn = useCallback((key: string) => {
    setGroupOverrides(prev => ({ ...prev, [key]: null }));
  }, [setGroupOverrides]);

  // ── Row grouping (collapse rows under a column's values) ────
  //
  // Distinct from the column-bracket grouping above: this groups the
  // DATA ROWS by one column's value ("group rows by Vehicle") with
  // collapsible group-header rows.  Single-level in v1.  The choice
  // persists per-user; expansion state is session-only (a persisted
  // expansion map would go stale as data changes).
  const {
    value: rowGroupPref,
    setValue: setRowGroupPref,
  } = useUserPreference<string | null>(rowGroupKey(tableId), defaultRowGroup ?? null);
  // Drop a stale pref if the column was removed from the config.
  const rowGroupBy = rowGroupPref && columns.some(c => c.key === rowGroupPref)
    ? rowGroupPref
    : null;
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const grouping = useMemo<GroupingState>(
    () => (rowGroupBy ? [rowGroupBy] : []),
    [rowGroupBy],
  );
  const toggleRowGroup = useCallback((key: string) => {
    setRowGroupPref(prev => (prev === key ? null : key));
    setExpanded({});
  }, [setRowGroupPref]);

  // ── Pagination ───────────────────────────────────────────────
  //
  // Page size persists per-user (lives in user_preferences via
  // useUserPreference) so the operator's choice follows them across
  // sessions / devices.  Page index is in-memory only — it resets
  // whenever the filter / search / sort changes so a narrowed view
  // doesn't start halfway through.
  const {
    value: pageSize,
    setValue: setPageSize,
  } = useUserPreference<number>(pageSizeKey(tableId), DEFAULT_PAGE_SIZE);
  const [pageIndex, setPageIndex] = useState(0);
  useEffect(() => {
    setPageIndex(0);
  }, [columnFilters, globalFilter, sorting, rowGroupBy]);

  // Reconcile stored ids with the current column config.  Stale ids
  // (column renamed / removed) are dropped from order/visibility/
  // pinning; new ids appended to order in declaration order so a
  // freshly-added column appears at the end rather than being silently
  // absent.  Without this sweep the three localStorage blobs would
  // accumulate dead keys forever (cosmetic / size only — tanstack
  // ignores unknown ids — but worth keeping tidy).
  useEffect(() => {
    if (!tableId) return;
    const allIds = new Set(columns.map(c => c.key));
    setColumnOrder((prev) => {
      // Empty = "use declaration order".  Don't pre-seed — that would
      // lock the operator into whatever the column array looked like
      // on first render, so any later code-level reorder would never
      // reach them.  Only populate once they actually drag a column.
      if (prev.length === 0) return prev;
      const validPrev = prev.filter(id => allIds.has(id));
      const missing   = columns.map(c => c.key).filter(id => !validPrev.includes(id));
      const next = [...validPrev, ...missing];
      const same = next.length === prev.length && next.every((id, i) => id === prev[i]);
      return same ? prev : next;
    });
    setColumnVisibility((prev) => {
      const cleaned: VisibilityState = {};
      let changed = false;
      for (const [id, v] of Object.entries(prev)) {
        if (allIds.has(id)) cleaned[id] = v;
        else changed = true;
      }
      return changed ? cleaned : prev;
    });
    setColumnPinning((prev) => {
      const left  = (prev.left  ?? []).filter(id => allIds.has(id));
      const right = (prev.right ?? []).filter(id => allIds.has(id));
      const sameLeft  = left.length  === (prev.left?.length  ?? 0);
      const sameRight = right.length === (prev.right?.length ?? 0);
      return sameLeft && sameRight ? prev : { left, right };
    });
  }, [columns, tableId]);

  const tableColumns = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      columns.map((col) => {
        const def: ColumnDef<Record<string, unknown>> = {
          id: col.key,
          accessorKey: col.key,
          // ``headerRender`` wins over the plain string label when
          // the column wants a rich header (e.g. the bulk-select
          // column's master "select all" checkbox).  tanstack's
          // ``header`` accepts either a string or a render function.
          header: col.headerRender
            ? () => col.headerRender!() as React.ReactNode
            : col.label,
          enableSorting: col.sortable !== false,
          enableColumnFilter: col.filterable === true,
          // ``locked`` columns are structural — opt them out of the
          // user-toggleable layers (hide / drag / pin user-side).
          // They're force-pinned-left below; the table still pins them
          // via columnPinning, but the operator can't unpin via UI.
          enableHiding: col.locked !== true,
          enablePinning: col.locked !== true,
          cell: ({ getValue, row }) =>
            col.render
              ? col.render(getValue(), row.original)
              : (getValue() as React.ReactNode) ?? '—',
        };
        if (col.sortKey) {
          def.sortingFn = (a, b) => {
            const av = col.sortKey!(a.original);
            const bv = col.sortKey!(b.original);
            if (typeof av === 'number' && typeof bv === 'number') {
              return av - bv;
            }
            return String(av).localeCompare(String(bv));
          };
        }
        if (col.filterable) {
          // Filter shape depends on ``filterMode``:
          //   * default 'select' → filterValue is ``string[]`` (multi-
          //     select); row keeps if the hay matches ANY selected.
          //   * 'range' → filterValue is ``[min|null, max|null]``; row
          //     keeps if its numeric value falls in the inclusive
          //     bounds.  Either bound can be null (one-sided range).
          //   * 'date-range' → filterValue is ``[isoFrom|null,
          //     isoTo|null]`` (YYYY-MM-DD).  The row's date must fall
          //     within the inclusive range.  The "To" bound extends to
          //     23:59:59.999 of that day so operators typing
          //     "2025-11-15 → 2025-11-15" get rows from that whole
          //     day, not midnight-only.
          const isRange = col.filterMode === 'range';
          const isDateRange = col.filterMode === 'date-range';
          def.filterFn = (row, _colId, filterValue) => {
            if (isRange) {
              const range = filterValue as [number | null, number | null] | undefined;
              if (!range || (range[0] == null && range[1] == null)) return true;
              const raw = row.getValue(col.key);
              const n = typeof raw === 'number' ? raw : Number(raw);
              if (!Number.isFinite(n)) return false;
              if (range[0] != null && n < range[0]) return false;
              if (range[1] != null && n > range[1]) return false;
              return true;
            }
            if (isDateRange) {
              const range = filterValue as [string | null, string | null] | undefined;
              if (!range || (!range[0] && !range[1])) return true;
              const raw = row.getValue(col.key);
              const t = new Date(String(raw ?? '')).getTime();
              if (!Number.isFinite(t)) return false;
              if (range[0]) {
                const fromT = new Date(range[0]).getTime();
                if (Number.isFinite(fromT) && t < fromT) return false;
              }
              if (range[1]) {
                // End-of-day upper bound so a single-day filter keeps
                // the whole day of data.
                const toT = new Date(range[1] + 'T23:59:59.999').getTime();
                if (Number.isFinite(toT) && t > toT) return false;
              }
              return true;
            }
            const selected = filterValue as string[] | undefined;
            if (!selected || selected.length === 0) return true;
            const hay = col.filterValue
              ? col.filterValue(row.original)
              : String(row.getValue(col.key) ?? '');
            return selected.includes(hay);
          };
        }
        return def;
      }),
    [columns],
  );

  const searchKeys = useMemo(() => {
    if (!searchKey) return [];
    return Array.isArray(searchKey) ? searchKey : [searchKey];
  }, [searchKey]);
  const hasSearch = searchKeys.length > 0;

  // Effective pinning = operator's choices + locked columns prepended
  // to the left side.  Locked columns (e.g. a bulk-select checkbox)
  // must always sit at the left edge regardless of what else the
  // operator has pinned; without this they'd shift to wherever
  // ``columnOrder`` puts them once ANY other column gets pinned-left.
  const lockedLeftIds = useMemo(
    () => columns.filter(c => c.locked).map(c => c.key),
    [columns],
  );
  const effectivePinning = useMemo<ColumnPinningState>(() => {
    if (lockedLeftIds.length === 0) return columnPinning;
    const userLeft = (columnPinning.left ?? []).filter(id => !lockedLeftIds.includes(id));
    return {
      left: [...lockedLeftIds, ...userLeft],
      right: columnPinning.right ?? [],
    };
  }, [columnPinning, lockedLeftIds]);

  const table = useReactTable({
    data,
    columns: tableColumns,
    // Stable row identity for BULK grids only: key on the row's own
    // ``id`` so a checkbox selection survives sort (index ids would
    // re-point to different rows).  Left undefined otherwise, so every
    // non-bulk grid keeps tanstack's default index ids unchanged — the
    // Ctrl/Cmd-click Copy selection behaves exactly as before.
    getRowId: bulkSelection
      ? (row, index) => {
          const rid = (row as Record<string, unknown>).id;
          return rid == null ? String(index) : String(rid);
        }
      : undefined,
    state: {
      sorting,
      globalFilter: hasSearch ? globalFilter : undefined,
      columnFilters,
      columnVisibility: effectiveVisibility,
      columnOrder,
      columnPinning: effectivePinning,
      columnSizing: effectiveSizing,
      pagination: { pageIndex, pageSize },
      grouping,
      expanded,
    },
    // Drag-to-resize on header edges.  'onChange' applies the new
    // width live while dragging (vs. a ghost bar on 'onEnd').
    enableColumnResizing: true,
    columnResizeMode: 'onChange',
    // Floor keeps every column wide enough for an ellipsized label +
    // the 3-dot menu — below ~60px the header affordances collide.
    defaultColumn: { minSize: 60, maxSize: 1000 },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    // When the operator pins/unpins, persist only their own choices
    // (strip out the auto-added locked ids so they don't get
    // written to localStorage / Redis as "user-pinned").
    onColumnPinningChange: (updater) => {
      setColumnPinning(prev => {
        const next = typeof updater === 'function' ? updater(prev) : updater;
        return {
          left: (next.left ?? []).filter(id => !lockedLeftIds.includes(id)),
          right: next.right ?? [],
        };
      });
    },
    // Resize drags land here (the ResizeObserver measurements write
    // directly to ``setColumnSizing`` instead, so this path is user-
    // intent only).  The updater's base is ``effectiveSizing``, which
    // already carries every visible column's measured width — so
    // persisting the WHOLE map on first resize is the snapshot that
    // freezes the table's current look before fixed layout kicks in.
    onColumnSizingChange: (updater) => {
      const next = typeof updater === 'function' ? updater(effectiveSizing) : updater;
      setUserWidths(next);
    },
    onPaginationChange: (updater) => {
      const next = typeof updater === 'function'
        ? updater({ pageIndex, pageSize })
        : updater;
      setPageIndex(next.pageIndex);
      if (next.pageSize !== pageSize) setPageSize(next.pageSize);
    },
    onExpandedChange: setExpanded,
    onGroupingChange: (updater) => {
      const next = typeof updater === 'function' ? updater(grouping) : updater;
      setRowGroupPref(next[0] ?? null);
    },
    // Keep expansion across data refetches (polling tables would
    // otherwise collapse every group on each poll tick).
    autoResetExpanded: false,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    // Omit the pagination row model when pagination is disabled —
    // ``getRowModel()`` then returns every filtered / sorted row
    // instead of a page slice.  The ``pagination`` state above is
    // still tracked (harmless) so toggling ``enablePagination`` back
    // on works instantly.
    ...(enablePagination ? { getPaginationRowModel: getPaginationRowModel() } : {}),
    globalFilterFn: hasSearch
      ? (row, _colId, filterValue) => {
          const needle = String(filterValue).toLowerCase();
          if (!needle) return true;
          return searchKeys.some(k => {
            const v = row.original[k];
            return v
              ? String(v).toLowerCase().includes(needle)
              : false;
          });
        }
      : undefined,
  });

  const rowCount = table.getRowModel().rows.length;

  // ── Row range-selection (Ctrl/Cmd + click, Shift + click) ──
  //
  // Independent of the existing per-row ``onRowClick`` (plain click
  // still opens the edit drawer when configured).  Modifier-clicks
  // build up a selection set; once any rows are picked, a top selection-strip
  // action bar offers Copy (TSV → clipboard, ready to paste into
  // Excel / Sheets) and Clear.  Selection is keyed by tanstack
  // ``row.id`` (string), which is stable across filter/sort.
  // Selection is CONTROLLED when the page passes selectedIds +
  // onSelectedIdsChange (it owns the set — e.g. Alerts' shared
  // context); otherwise DataGrid owns it internally.  ``setSelectedRowIds``
  // routes writes to the right place and accepts the same
  // value-or-updater shape as a useState setter, so every call site is
  // unchanged.
  const [internalSelectedIds, setInternalSelectedIds] = useState<Set<string>>(new Set());
  const isControlledSelection = controlledSelectedIds !== undefined;
  const selectedRowIds = isControlledSelection ? controlledSelectedIds : internalSelectedIds;
  const setSelectedRowIds = useCallback(
    // NOTE: in controlled mode the updater form reads the render-closure
    // ``controlledSelectedIds`` (not a live React queue), so calling this
    // with an updater TWICE synchronously in one event would stomp — fine
    // for every current caller (each fires once per handler), but a
    // double-call controlled consumer would need a real prevRef.
    (updater: Set<string> | ((prev: Set<string>) => Set<string>)) => {
      if (isControlledSelection) {
        const next = typeof updater === 'function'
          ? updater(controlledSelectedIds as Set<string>) : updater;
        onSelectedIdsChange?.(next);
      } else {
        setInternalSelectedIds(updater);
      }
    },
    [isControlledSelection, controlledSelectedIds, onSelectedIdsChange],
  );
  const lastClickedIdRef = useRef<string | null>(null);
  // Drop selection when the rendered slice no longer contains any
  // of the selected rows (e.g. operator added a filter that hides
  // them) — otherwise the selection strip lies about what's selected.
  useEffect(() => {
    if (selectedRowIds.size === 0) return;
    // Keep ids still present in the FILTERED set (every page, flat leaf
    // rows — pre-grouping/pagination), dropping only rows the new
    // filter actually hides.  Using the paginated getRowModel() here
    // would wrongly drop cross-page select-all picks on the next
    // keystroke, and wipe leaf selections while grouped (that model
    // yields group rows, not leaf ids).
    const filteredIds = new Set(table.getFilteredRowModel().rows.map(r => r.id));
    let changed = false;
    const kept = new Set<string>();
    for (const id of selectedRowIds) {
      if (filteredIds.has(id)) kept.add(id);
      else changed = true;
    }
    if (changed) setSelectedRowIds(kept);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columnFilters, globalFilter]);

  const handleRowClick = (
    e: React.MouseEvent,
    rowId: string,
    rowOriginal: Record<string, unknown>,
  ) => {
    // Cmd / Ctrl click — toggle a single row in/out of selection.
    // Shift click — range-extend from the last clicked row.
    // No modifier — fire the page's onRowClick (open edit drawer)
    // and clear any existing selection so the row-open never
    // happens mid-multi-select.
    const isToggle = e.metaKey || e.ctrlKey;
    const isRange  = e.shiftKey;
    if (isToggle) {
      e.preventDefault();
      setSelectedRowIds(prev => {
        const next = new Set(prev);
        if (next.has(rowId)) next.delete(rowId);
        else next.add(rowId);
        return next;
      });
      lastClickedIdRef.current = rowId;
      return;
    }
    if (isRange && lastClickedIdRef.current) {
      e.preventDefault();
      const rows = table.getRowModel().rows;
      const fromIdx = rows.findIndex(r => r.id === lastClickedIdRef.current);
      const toIdx   = rows.findIndex(r => r.id === rowId);
      if (fromIdx === -1 || toIdx === -1) return;
      const [a, b] = fromIdx < toIdx ? [fromIdx, toIdx] : [toIdx, fromIdx];
      setSelectedRowIds(prev => {
        const next = new Set(prev);
        // Skip synthetic group-header rows — they carry no original
        // data and Copy would choke on their ids.
        for (let i = a; i <= b; i++) {
          if (!rows[i].getIsGrouped()) next.add(rows[i].id);
        }
        return next;
      });
      return;
    }
    // Plain click — forward to caller.  In modifier-select (Copy) mode
    // a stray plain click resets the in-progress selection; with
    // checkbox bulkSelection the selection is deliberate and must
    // survive opening a row to view it.
    if (!bulkSelection && selectedRowIds.size > 0) setSelectedRowIds(new Set());
    lastClickedIdRef.current = rowId;
    onRowClick?.(rowOriginal);
  };

  const copySelectedRows = async () => {
    if (selectedRowIds.size === 0) return;
    const rows = table.getRowModel().rows
      .filter(r => selectedRowIds.has(r.id))
      .map(r => r.original as Record<string, unknown>);
    const visibleColIds = table.getVisibleLeafColumns().map(c => c.id);
    const colByKey = new Map(columns.map(c => [c.key, c]));
    const copyCols = visibleColIds
      .map(id => colByKey.get(id))
      .filter((c): c is AnyColumn => Boolean(c));
    await writeToClipboard(buildTsv(copyCols, rows));
  };

  // ── Checkbox bulk-selection ──────────────────────────────────────
  //
  // Checkboxes feed the SAME ``selectedRowIds`` set as modifier-click,
  // so there is ONE selection and ONE bar — never two systems
  // fighting.  Select-all spans the whole FILTERED set (every page),
  // matching what operators expect from a header checkbox.
  const selectableRows = () =>
    table.getFilteredRowModel().rows.filter(r =>
      !r.getIsGrouped()
      && (!isRowSelectable || isRowSelectable(r.original as Record<string, unknown>)));
  const allRowsSelected = (() => {
    if (!bulkSelection) return false;
    const rows = selectableRows();
    return rows.length > 0 && rows.every(r => selectedRowIds.has(r.id));
  })();
  const someRowsSelected = bulkSelection && selectedRowIds.size > 0 && !allRowsSelected;

  const cbClass = 'cursor-pointer accent-primary align-middle';
  const renderSelectAll = () => (
    <input
      type="checkbox"
      checked={allRowsSelected}
      ref={el => { if (el) el.indeterminate = someRowsSelected; }}
      onClick={e => e.stopPropagation()}
      onChange={e =>
        setSelectedRowIds(e.target.checked
          ? new Set(selectableRows().map(r => r.id))
          : new Set())}
      className={cbClass}
      aria-label="Select all rows"
    />
  );
  const renderRowBox = (rowId: string, original: Record<string, unknown>) => (
    <input
      type="checkbox"
      checked={selectedRowIds.has(rowId)}
      onClick={e => e.stopPropagation()}
      onChange={e =>
        setSelectedRowIds(prev => {
          const next = new Set(prev);
          if (e.target.checked) next.add(rowId); else next.delete(rowId);
          return next;
        })}
      className={cbClass}
      aria-label={bulkRowLabel ? `Select ${bulkRowLabel(original)}` : 'Select row'}
    />
  );
  const renderGroupBox = (ids: string[]) => {
    // No selectable leaves in this group (all excluded by
    // isRowSelectable) → no checkbox, matching the per-row hide.
    if (ids.length === 0) return null;
    const all = ids.length > 0 && ids.every(id => selectedRowIds.has(id));
    const some = !all && ids.some(id => selectedRowIds.has(id));
    return (
      <input
        type="checkbox"
        checked={all}
        ref={el => { if (el) el.indeterminate = some; }}
        onClick={e => e.stopPropagation()}
        onChange={e =>
          setSelectedRowIds(prev => {
            const next = new Set(prev);
            ids.forEach(id => { if (e.target.checked) next.add(id); else next.delete(id); });
            return next;
          })}
        className={cbClass}
        aria-label="Select group"
      />
    );
  };

  // Resolve the selection to ORIGINAL rows (via the core model, so
  // rows on other pages still resolve) for the action handlers + the
  // page mirror callback.
  const selectedOriginals = (): Record<string, unknown>[] => {
    const byId = new Map(table.getCoreRowModel().rows.map(r => [r.id, r.original]));
    return [...selectedRowIds]
      .map(id => byId.get(id))
      .filter((r): r is Record<string, unknown> => r != null);
  };

  useEffect(() => {
    onBulkSelectionChange?.(selectedOriginals());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRowIds]);

  // The selection bar's content — count + icon actions + Copy + Clear.
  // Rendered INSIDE the toolbar row's left slot (in place of the page's
  // headerToolbar) when 1+ rows are selected, so bulk actions share the
  // same bar as Search / Filter / Columns / Export rather than stacking
  // a second strip below it.
  const selectionBarContent = (
    <div className="flex items-center gap-0.5">
      <CornerUpRight size={14} className="text-muted-foreground mr-1 shrink-0" aria-hidden="true" />
      <span className="text-xs font-medium text-foreground mr-1">
        {selectedRowIds.size} {selectedRowIds.size === 1 ? 'row' : 'rows'} selected
      </span>
      {bulkActions && bulkActions.length > 0 && (
        <span className="h-4 w-px bg-border mx-1.5" aria-hidden="true" />
      )}
      {bulkActions?.map((action) => {
        const Icon = action.icon;
        const btnCls = action.tone === 'danger'
          ? 'text-destructive hover:bg-destructive/10 hover:text-destructive'
          : 'text-muted-foreground hover:text-foreground';
        const inner = Icon
          ? <Icon />
          : <span className="text-xs px-1 font-medium">{action.label}</span>;
        if (action.options) {
          return (
            <MenuPrimitive.Root key={action.label}>
              <MenuPrimitive.Trigger
                render={(props) => (
                  <Tip label={action.label}>
                    <Button
                      {...props}
                      type="button"
                      variant="ghost"
                      size={Icon ? 'icon' : 'xs'}
                      className={btnCls}
                      aria-label={action.label}
                    >
                      {inner}
                      {!Icon && <ChevronDown size={12} className="opacity-60" />}
                    </Button>
                  </Tip>
                )}
              />
              <MenuPrimitive.Portal>
                <MenuPrimitive.Positioner align="start" sideOffset={4} className="z-50 outline-none">
                  <MenuPrimitive.Popup className="min-w-44 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
                    {action.options.map((opt) => (
                      <MenuPrimitive.Item
                        key={opt.value}
                        onClick={() => runBulkAction(action, opt.value)}
                        className="w-full flex items-center px-3 py-1.5 text-xs cursor-pointer outline-none data-[highlighted]:bg-accent text-foreground text-left"
                      >
                        {opt.label}
                      </MenuPrimitive.Item>
                    ))}
                  </MenuPrimitive.Popup>
                </MenuPrimitive.Positioner>
              </MenuPrimitive.Portal>
            </MenuPrimitive.Root>
          );
        }
        return (
          <Tip key={action.label} label={action.label}>
            <Button
              type="button"
              variant="ghost"
              size={Icon ? 'icon' : 'xs'}
              className={btnCls}
              onClick={() => runBulkAction(action)}
              aria-label={action.label}
            >
              {inner}
            </Button>
          </Tip>
        );
      })}
      <span className="h-4 w-px bg-border mx-1.5" aria-hidden="true" />
      <Tip label="Copy to clipboard (Excel / Sheets)">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-foreground"
          onClick={copySelectedRows}
          aria-label="Copy selected rows"
        >
          <Copy />
        </Button>
      </Tip>
      <Tip label="Clear selection">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-foreground"
          onClick={() => setSelectedRowIds(new Set())}
          aria-label="Clear selection"
        >
          <X />
        </Button>
      </Tip>
    </div>
  );

  const runBulkAction = async (action: BulkAction, value?: string) => {
    const rows = selectedOriginals();
    if (rows.length === 0) return;
    if (action.confirm && !window.confirm(action.confirm(rows.length))) return;
    try {
      await action.onRun(rows, value);
      // Act → selection resets (the acted rows usually leave the list).
      setSelectedRowIds(new Set());
    } catch (err) {
      // A well-behaved onRun catches + toasts its own errors; this is
      // the safety net so a throwing consumer can't leave the bar
      // stuck with no feedback.  Selection is kept so the user can
      // retry the same rows.
      // eslint-disable-next-line no-console
      console.error('bulk action failed', err);
    }
  };

  // FACETED filter options — each select-mode column's dropdown lists
  // the values present in the rows that pass every OTHER column's
  // active filter (plus the global search).  So filtering State=MI
  // narrows the City dropdown to Michigan cities with MI-only counts,
  // filtering Company=G1 narrows the Status dropdown to statuses that
  // exist within G1, and so on — options always reflect the current
  // context instead of dead-end combinations.
  //
  // The column's OWN filter is excluded from its context (standard
  // faceted rule — otherwise picking one value would immediately hide
  // every sibling option and multi-select would be impossible).
  //
  // Values the operator has ALREADY selected stay visible even when
  // the other filters drive their count to 0 — hiding them would make
  // the tick impossible to remove.
  const uniquesByCol = useMemo(() => {
    const out: Record<string, {
      options: Array<{ value: string; label: string }>;
      counts: Record<string, number>;
    }> = {};
    const filterByKey = new Map(columnFilters.map(f => [f.id, f.value]));
    const colByKey = new Map(columns.map(c => [c.key, c]));
    const needle = hasSearch ? globalFilter.trim().toLowerCase() : '';
    for (const col of columns) {
      if (!col.filterable || col.filterMode === 'range' || col.filterMode === 'date-range') continue;
      // Rows surviving every filter EXCEPT this column's own.
      const contextRows = data.filter(row => {
        if (needle) {
          const hit = searchKeys.some(k => {
            const v = row[k];
            return v ? String(v).toLowerCase().includes(needle) : false;
          });
          if (!hit) return false;
        }
        for (const [key, fv] of filterByKey) {
          if (key === col.key) continue;
          const other = colByKey.get(key);
          if (!other) continue;
          if (!rowPassesColFilter(row, other, fv)) return false;
        }
        return true;
      });
      const counts: Record<string, number> = {};
      const labelByValue: Record<string, string> = {};
      for (const row of contextRows) {
        const v = col.filterValue
          ? col.filterValue(row)
          : String((row as Record<string, unknown>)[col.key] ?? '');
        const lab = col.filterLabel ? col.filterLabel(row) : v;
        counts[v] = (counts[v] ?? 0) + 1;
        if (!(v in labelByValue)) labelByValue[v] = lab;
      }
      // Re-add selected values the context filtered out (count 0) so
      // the operator can still untick them.  Label resolved from the
      // full dataset; falls back to the raw value.
      const selected = (filterByKey.get(col.key) as string[] | undefined) ?? [];
      for (const v of selected) {
        if (v in counts) continue;
        counts[v] = 0;
        const src = data.find(row => (
          col.filterValue
            ? col.filterValue(row)
            : String((row as Record<string, unknown>)[col.key] ?? '')
        ) === v);
        labelByValue[v] = src && col.filterLabel ? col.filterLabel(src) : v;
      }
      out[col.key] = {
        options: Object.keys(counts).map(v => ({ value: v, label: labelByValue[v] })),
        counts,
      };
    }
    return out;
  }, [columns, data, columnFilters, globalFilter, hasSearch, searchKeys]);

  // For each ``filterMode: 'range'`` column, compute the min/max
  // bounds from the data (unless the column config specified them
  // explicitly).  These drive the placeholder text + step of the
  // range-filter inputs and give the operator a sense of "what
  // range am I bounding?".
  const rangesByCol = useMemo(() => {
    const out: Record<string, { min: number; max: number; step: number; unit: string }> = {};
    for (const col of columns) {
      if (!col.filterable || col.filterMode !== 'range') continue;
      const explicit = col.filterRange ?? {};
      let min = explicit.min ?? Infinity;
      let max = explicit.max ?? -Infinity;
      if (explicit.min == null || explicit.max == null) {
        for (const row of data) {
          const raw = (row as Record<string, unknown>)[col.key];
          const n = typeof raw === 'number' ? raw : Number(raw);
          if (!Number.isFinite(n)) continue;
          if (explicit.min == null && n < min) min = n;
          if (explicit.max == null && n > max) max = n;
        }
      }
      // Fallbacks when a column has no numeric data yet — keep the
      // popover usable rather than showing Infinity/-Infinity.
      if (!Number.isFinite(min)) min = 0;
      if (!Number.isFinite(max)) max = 100;
      out[col.key] = {
        min, max,
        step: explicit.step ?? 1,
        unit: explicit.unit ?? '',
      };
    }
    return out;
  }, [columns, data]);

  // For each ``filterMode: 'date-range'`` column, compute the earliest
  // + latest date in the data.  Used as placeholder text on the
  // From / To inputs so the operator sees the data's actual span
  // ("Data range: 2024-05-01 – 2026-06-30").  Bounds are ISO
  // YYYY-MM-DD; the underlying values in the data can be full ISO
  // timestamps (Date parses either).
  const dateBoundsByCol = useMemo(() => {
    const out: Record<string, { min: string; max: string }> = {};
    for (const col of columns) {
      if (!col.filterable || col.filterMode !== 'date-range') continue;
      let minT = Infinity, maxT = -Infinity;
      for (const row of data) {
        const raw = (row as Record<string, unknown>)[col.key];
        if (raw == null || raw === '') continue;
        const t = new Date(String(raw)).getTime();
        if (!Number.isFinite(t)) continue;
        if (t < minT) minT = t;
        if (t > maxT) maxT = t;
      }
      const toIso = (t: number) => Number.isFinite(t)
        ? new Date(t).toISOString().slice(0, 10)
        : '';
      out[col.key] = { min: toIso(minT), max: toIso(maxT) };
    }
    return out;
  }, [columns, data]);

  // ── Manage-columns popover state ──────────────────────────
  //
  // Anchored on the small Columns3 trigger button in the toolbar AND
  // openable from inside the per-column 3-dot menu.  Using a single
  // controlled instance keeps the anchor stable regardless of which
  // entry point opens it.
  const [manageOpen, setManageOpen] = useState(false);
  const manageAnchorRef = useRef<HTMLButtonElement | null>(null);
  // Toolbar Filter / Sort popovers — list the ACTIVE filters / sorts
  // with per-item clear + clear-all, mirroring the Columns pattern so
  // operators have one place to see and undo everything narrowing
  // the table.
  const [filterMenuOpen, setFilterMenuOpen] = useState(false);
  const filterBtnRef = useRef<HTMLButtonElement | null>(null);
  const [sortMenuOpen, setSortMenuOpen] = useState(false);
  const sortBtnRef = useRef<HTMLButtonElement | null>(null);

  // Human-readable one-liner for an active column filter, matching
  // the filter's mode: selected labels for select mode, bounds for
  // range / date-range.
  const describeFilter = useCallback((id: string, value: unknown): string => {
    const col = columns.find(c => c.key === id);
    if (col?.filterMode === 'range') {
      const [a, b] = (value as [number | null, number | null]) ?? [null, null];
      const unit = col.filterRange?.unit ? ` ${col.filterRange.unit}` : '';
      return `${a ?? '−∞'} – ${b ?? '+∞'}${unit}`;
    }
    if (col?.filterMode === 'date-range') {
      const [a, b] = (value as [string | null, string | null]) ?? [null, null];
      return `${a ?? '…'} → ${b ?? '…'}`;
    }
    const vals = (value as string[]) ?? [];
    const opts = uniquesByCol[id]?.options ?? [];
    return vals.map(v => opts.find(o => o.value === v)?.label ?? v).join(', ');
  }, [columns, uniquesByCol]);
  const hiddenCount = useMemo(
    () => columns.filter(c => effectiveVisibility[c.key] === false).length,
    [columns, effectiveVisibility],
  );

  // ── Active filters / sort / search as removable chips ─────────────
  //
  // A row of pills below the toolbar (auto-shown only when something is
  // active) — the always-visible, one-click-remove counterpart to the
  // Filter/Sort popovers: "what's limiting my view?" is now readable at
  // a glance, and each ✕ clears just that constraint.  Adding filters
  // still happens in the column ⋮ menus.
  const trimmedGlobal = hasSearch ? globalFilter.trim() : '';
  const chipCount = columnFilters.length + sorting.length + (trimmedGlobal ? 1 : 0);
  const chipCls =
    'inline-flex items-center gap-1 pl-2 pr-0.5 py-0.5 rounded-md border border-border '
    + 'bg-background text-xs text-foreground';
  const chipX = 'ml-0.5 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted';
  const filterChipRow = chipCount === 0 ? null : (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 bg-muted/30 border-b border-border">
      {trimmedGlobal && (
        <span className={chipCls}>
          <Search size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
          <span className="truncate max-w-[16rem]">“{trimmedGlobal}”</span>
          <button type="button" onClick={() => setGlobalFilter('')}
            aria-label="Clear search" className={chipX}>
            <X size={12} />
          </button>
        </span>
      )}
      {columnFilters.map((f) => {
        const col = columns.find(c => c.key === f.id);
        return (
          <span key={`f-${f.id}`} className={chipCls}>
            <FilterIcon size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
            <Tip label={`${col?.label || f.id}: ${describeFilter(f.id, f.value)}`}>
              <span className="truncate max-w-[18rem]">
                <span className="font-medium">{col?.label || f.id}</span>
                {': '}{describeFilter(f.id, f.value)}
              </span>
            </Tip>
            <button type="button"
              onClick={() => setColumnFilters(prev => prev.filter(x => x.id !== f.id))}
              aria-label={`Clear ${col?.label || f.id} filter`} className={chipX}>
              <X size={12} />
            </button>
          </span>
        );
      })}
      {sorting.map((s) => {
        const col = columns.find(c => c.key === s.id);
        return (
          <span key={`s-${s.id}`} className={chipCls}>
            <ArrowUpDown size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
            <Tip label={`Sorted by ${col?.label || s.id} · ${s.desc ? 'descending' : 'ascending'}`}>
              <span className="truncate max-w-[18rem]">
                Sorted by <span className="font-medium">{col?.label || s.id}</span>
                {s.desc ? ' ↓' : ' ↑'}
              </span>
            </Tip>
            <button type="button"
              onClick={() => setSorting(prev => prev.filter(x => x.id !== s.id))}
              aria-label={`Clear ${col?.label || s.id} sort`} className={chipX}>
              <X size={12} />
            </button>
          </span>
        );
      })}
      {chipCount >= 2 && (
        <button type="button"
          onClick={() => { setColumnFilters([]); setSorting([]); setGlobalFilter(''); }}
          className="ml-0.5 px-1.5 py-0.5 text-2xs text-muted-foreground hover:text-foreground">
          Clear all
        </button>
      )}
    </div>
  );

  const visibleHeaderGroups = table.getHeaderGroups();
  // Sortable context needs the list of column ids currently rendered
  // (after visibility filter) so drag swaps reorder the right slice.
  const visibleColIds = useMemo(
    () => visibleHeaderGroups[0]?.headers.map(h => h.column.id) ?? [],
    [visibleHeaderGroups],
  );

  // ── Grouped header row ──────────────────────────────────────
  //
  // Columns can carry a ``group`` label; CONSECUTIVE visible columns
  // sharing one get a spanning bracket cell in an extra row above the
  // normal headers ("Location" over Street / City / State).  Purely
  // presentational: computed from the render-order leaf headers, so
  // reorder / pin / hide just reshapes the runs — a member dragged
  // away from its siblings gets its own (or no) bracket, nothing
  // breaks.  Sticky offsets are applied to a run only when EVERY
  // member is pinned to the same side, so a fully-pinned group
  // bracket scrolls with its columns.
  const groupRuns = useMemo(() => {
    const headers = visibleHeaderGroups[0]?.headers ?? [];
    const runs: GroupRun[] = [];
    for (const h of headers) {
      const label = effectiveGroupByKey.get(h.column.id) ?? null;
      const prev = runs[runs.length - 1];
      // Merge ONLY labelled (grouped) columns into shared runs.
      // Ungrouped columns each stay their own single-column run so a
      // dragged group can land between ANY two individual columns —
      // one big ungrouped run would make the group jump across the
      // whole block at once (its only drop slots are run edges).
      if (prev && label !== null && prev.label === label) {
        prev.span += 1;
        prev.memberIds.push(h.column.id);
        continue;
      }
      runs.push({ label, span: 1, firstId: h.column.id, memberIds: [h.column.id] });
    }
    // Second pass: sticky styling for fully-pinned runs.
    let idx = 0;
    for (const run of runs) {
      const members = headers.slice(idx, idx + run.span);
      idx += run.span;
      const sides = new Set(members.map(m => m.column.getIsPinned()));
      if (sides.size === 1) {
        const side = [...sides][0];
        if (side === 'left') {
          run.sticky = {
            position: 'sticky',
            left: members[0].column.getStart('left'),
            zIndex: 2,
          };
        } else if (side === 'right') {
          run.sticky = {
            position: 'sticky',
            right: members[members.length - 1].column.getAfter('right'),
            zIndex: 2,
          };
        }
      }
    }
    return runs;
  }, [visibleHeaderGroups, effectiveGroupByKey]);
  const hasGroupRow = groupRuns.some(r => r.label != null);

  // Live drag-state for visual feedback.  ``groupDrag`` powers the
  // bracket row's overlay chip + insertion indicator; ``leafDragId``
  // powers the column-label chip on single-column drags.
  const [groupDrag, setGroupDrag] = useState<{
    activeId: string | null; overId: string | null;
  }>({ activeId: null, overId: null });
  const [leafDragId, setLeafDragId] = useState<string | null>(null);

  // Whole-group drag — the bracket row is its own sortable strip
  // whose items are the RUNS.  Dropping run A onto run B reorders at
  // the run level, then the leaf order is rebuilt so each run's
  // member columns travel together as a block.  Hidden columns keep
  // their existing slots (only visible keys are re-sequenced).
  const handleGroupDragEnd = (event: DragEndEvent) => {
    setGroupDrag({ activeId: null, overId: null });
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIdx = groupRuns.findIndex(r => `grp:${r.firstId}` === active.id);
    const toIdx   = groupRuns.findIndex(r => `grp:${r.firstId}` === over.id);
    if (fromIdx === -1 || toIdx === -1) return;
    // Pinned / locked columns live at the table edges regardless of
    // order — moving a group into or out of them would be visually
    // confusing, so those drops no-op (same rule as leaf drag).
    const immovable = new Set([
      ...(columnPinning.left ?? []),
      ...(columnPinning.right ?? []),
      ...lockedLeftIds,
    ]);
    if (groupRuns[fromIdx].memberIds.some(id => immovable.has(id))) return;
    if (groupRuns[toIdx].memberIds.some(id => immovable.has(id))) return;
    const newVisible = arrayMove(groupRuns, fromIdx, toIdx).flatMap(r => r.memberIds);
    setColumnOrder(prev => {
      const base = prev.length ? prev : columns.map(c => c.key);
      const visibleSet = new Set(newVisible);
      let vi = 0;
      return base.map(k => (visibleSet.has(k) ? newVisible[vi++] : k));
    });
  };

  const sensors = useSensors(
    // 5px activation distance — short label clicks still open the
    // filter popover; only an actual drag triggers reorder.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    setLeafDragId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    // Don't drop onto pinned OR locked columns — both end up at one
    // edge of the table regardless of ``columnOrder``, so the
    // dragged centre column would jump to a confusing position.
    // Locked columns are also force-pinned-left, so this check is
    // belt-and-braces (the pinned check would catch them too).
    const lockedIds = new Set(lockedLeftIds);
    const pinned = new Set([
      ...(columnPinning.left  ?? []),
      ...(columnPinning.right ?? []),
    ]);
    if (pinned.has(over.id as string) || lockedIds.has(over.id as string)) return;
    if (lockedIds.has(active.id as string)) return;
    setColumnOrder((prev) => {
      const base = prev.length ? prev : columns.map(c => c.key);
      const oldIdx = base.indexOf(active.id as string);
      const newIdx = base.indexOf(over.id as string);
      if (oldIdx === -1 || newIdx === -1) return prev;
      const next = arrayMove(base, oldIdx, newIdx);
      // Group-contiguity guard — reject any reorder that would
      // FRAGMENT a column group.  This makes groups behave as units:
      // a member can reorder freely WITHIN its group (contiguity
      // preserved → allowed), but dragging it outside the group, or
      // dropping an outside column into the middle of a group, would
      // split the bracket — those drops no-op instead.  Checked over
      // the VISIBLE columns only, since hidden columns sitting
      // between members in the raw order array don't fragment the
      // rendered bracket.
      const visibleNext = next.filter(
        k => effectiveGroupByKey.has(k) && effectiveVisibility[k] !== false,
      );
      const seenGroups = new Set<string>();
      let prevGroup: string | null = null;
      for (const k of visibleNext) {
        const g = effectiveGroupByKey.get(k) ?? null;
        if (g && g !== prevGroup) {
          if (seenGroups.has(g)) return prev;   // group split in two → reject
          seenGroups.add(g);
        }
        prevGroup = g;
      }
      return next;
    });
  };

  const padding = DENSITY_PADDING[density];

  // ── Custom horizontal scrollbar ─────────────────────────────
  //
  // Native scrollbar spans the whole container including under the
  // pinned columns, which is misleading: pinned columns DON'T scroll.
  // The container uses ``overflow-x: hidden`` so the native bar is
  // gone entirely; we drive horizontal scrolling via this custom
  // scrollbar (drag) and a wheel handler (trackpad).

  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const [scrollMetrics, setScrollMetrics] = useState({
    scrollLeft: 0, clientWidth: 0, scrollWidth: 0,
  });
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const update = () => {
      setScrollMetrics({
        scrollLeft: el.scrollLeft,
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
      });
    };
    update();
    el.addEventListener('scroll', update, { passive: true });
    // Trackpad horizontal swipe / shift+wheel — the container is
    // ``overflow-x: hidden`` so the browser would normally ignore
    // these gestures; convert ``deltaX`` (or shift+deltaY) into a
    // direct ``scrollLeft`` change so the user's swipe still feels
    // native.  Passive listener since we don't need to preventDefault
    // (browser already won't scroll because overflow-x is hidden).
    const onWheel = (e: WheelEvent) => {
      const dx = e.shiftKey && e.deltaX === 0 ? e.deltaY : e.deltaX;
      if (dx === 0) return;
      el.scrollLeft += dx;
    };
    el.addEventListener('wheel', onWheel, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    // Also re-measure when the inner table reflows (rows added /
    // density change widens cells / etc.).
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener('scroll', update);
      el.removeEventListener('wheel', onWheel);
      ro.disconnect();
    };
  }, []);

  // Measure pinned column widths directly from the live DOM after
  // every render.  Reading ``columnSizing`` would also work but lags
  // by one ResizeObserver tick on the very first paint, leaving the
  // custom scrollbar momentarily full-width before settling — direct
  // DOM measurement is one synchronous frame and avoids that flash.
  const [pinnedWidths, setPinnedWidths] = useState({ left: 0, right: 0 });
  useLayoutEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const measure = () => {
      // Measure the LEAF header row (marked data-header-row="leaf"),
      // not tr:first-child — when a group bracket row is present it
      // becomes the first row and its colSpan cells would mismeasure.
      const lefts  = el.querySelectorAll<HTMLElement>('thead tr[data-header-row="leaf"] > th[data-pin="left"]');
      const rights = el.querySelectorAll<HTMLElement>('thead tr[data-header-row="leaf"] > th[data-pin="right"]');
      let l = 0, r = 0;
      lefts.forEach(c => { l += c.offsetWidth; });
      rights.forEach(c => { r += c.offsetWidth; });
      setPinnedWidths(prev => (prev.left === l && prev.right === r) ? prev : { left: l, right: r });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  // Re-measure when pinning / order / visibility changes the layout.
  }, [effectivePinning, columnOrder, columnVisibility, columnSizing]);
  const pinnedLeftWidth  = pinnedWidths.left;
  const pinnedRightWidth = pinnedWidths.right;

  // Scrollbar geometry — only shown when content overflows.  The
  // track spans the centre region; thumb width is proportional to
  // the visible-to-total ratio, with a floor so it stays grabbable.
  const needsHScroll = scrollMetrics.scrollWidth > scrollMetrics.clientWidth + 1;
  const trackWidth = Math.max(0, scrollMetrics.clientWidth - pinnedLeftWidth - pinnedRightWidth);
  const maxScrollLeft = Math.max(0, scrollMetrics.scrollWidth - scrollMetrics.clientWidth);
  const visibleRatio = scrollMetrics.scrollWidth > 0
    ? scrollMetrics.clientWidth / scrollMetrics.scrollWidth
    : 1;
  const thumbWidth = Math.max(24, Math.round(trackWidth * visibleRatio));
  const thumbMax = Math.max(0, trackWidth - thumbWidth);
  const scrollRatio = maxScrollLeft > 0 ? scrollMetrics.scrollLeft / maxScrollLeft : 0;
  const thumbLeft = Math.round(thumbMax * scrollRatio);

  // Drag the thumb to scroll horizontally.  ``thumbMax`` maps to
  // ``maxScrollLeft`` linearly, so a pixel of thumb drag = (max /
  // thumbMax) pixels of container scroll.
  const onThumbPointerDown = (e: React.PointerEvent) => {
    if (thumbMax <= 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startScroll = scrollMetrics.scrollLeft;
    const move = (mv: PointerEvent) => {
      const dx = mv.clientX - startX;
      const next = Math.max(0, Math.min(
        maxScrollLeft,
        startScroll + (dx / thumbMax) * maxScrollLeft,
      ));
      const el = scrollContainerRef.current;
      if (el) el.scrollLeft = next;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  // Click on the empty track jumps the centre by one page in that
  // direction.  Standard scrollbar behaviour.
  const onTrackClick = (e: React.MouseEvent) => {
    if (e.target !== e.currentTarget || thumbMax <= 0) return;
    const trackRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const clickRel = e.clientX - trackRect.left;
    const goRight = clickRel > thumbLeft + thumbWidth / 2;
    const page = scrollMetrics.clientWidth - pinnedLeftWidth - pinnedRightWidth;
    const el = scrollContainerRef.current;
    if (!el) return;
    el.scrollLeft = Math.max(0, Math.min(
      maxScrollLeft,
      scrollMetrics.scrollLeft + (goRight ? page : -page),
    ));
  };

  // CSV export — visible columns in current display order, filtered
  // + sorted rows.  Filename uses ``tableId`` + today's local date so
  // re-exports don't overwrite the previous file in the operator's
  // Downloads folder.  Date format is YYYY-MM-DD (sortable).
  // Autosize a column to its widest rendered CONTENT (header label
  // included).  Crucial detail: measure the inner content spans, NOT
  // the cells — a cell reports its current width when the content is
  // smaller (scrollWidth = max(content, box)), which turned repeated
  // Autosize presses into a ratchet that grew the column by the
  // buffer every click.  Inline spans report true content width.
  const autosizeColumn = useCallback((colId: string) => {
    const el = scrollContainerRef.current;
    if (!el) return;
    let max = 0;
    // Header: the label span (inline-flex) inside the drag wrapper.
    // +52 covers the header's gap + 3-dot menu + cell padding.  The
    // label text ellipsizes on narrow columns, so the wrapper's rect
    // is the CLIPPED width — add back the hidden portion (scrollWidth
    // minus rendered width of the ``data-col-label`` span) so
    // autosizing a squeezed column restores the full label.
    const labelSpan = el.querySelector<HTMLElement>(
      `thead th[data-col="${colId}"] > div > span:first-child > span`,
    );
    if (labelSpan) {
      const labelText = labelSpan.querySelector<HTMLElement>('[data-col-label]');
      const hidden = labelText
        ? Math.max(0, labelText.scrollWidth - labelText.getBoundingClientRect().width)
        : 0;
      max = Math.ceil(labelSpan.getBoundingClientRect().width + hidden) + 52;
    }
    // Body: each cell's content wrapper is its LAST child (a pinned
    // cell's tint overlay renders first).  +20 covers cell padding.
    el.querySelectorAll<HTMLElement>(`tbody td[data-col="${colId}"]`).forEach(td => {
      const content = td.lastElementChild as HTMLElement | null;
      if (!content) return;
      max = Math.max(max, Math.ceil(content.getBoundingClientRect().width) + 20);
    });
    if (!max) return;
    const width = Math.min(600, Math.max(60, max));
    // Idempotency guard for columns whose cells render block-level
    // content (progress bars fill the cell, so their measured width
    // tracks the column width): if the fit is within a whisker of
    // the current width, treat it as already-fitted instead of
    // nudging wider on every press.
    const current = effectiveSizing[colId];
    if (current != null && Math.abs(width - current) <= 24) return;
    setUserWidths(prev =>
      Object.keys(prev).length
        ? { ...prev, [colId]: width }
        : { ...columnSizing, [colId]: width },
    );
  }, [columnSizing, effectiveSizing, setUserWidths]);

  // Flatten row-group headers to their leaf rows — synthetic group
  // rows have no ``original`` and would export blank lines.
  const flattenLeaves = (rows: Row<Record<string, unknown>>[]): Record<string, unknown>[] =>
    rows.flatMap(r => r.getIsGrouped()
      ? flattenLeaves(r.subRows)
      : [r.original as Record<string, unknown>]);

  /** Export scope: ``'page'`` = the rows currently rendered (the
   *  active pagination slice); ``'all'`` = every filtered + sorted
   *  row across all pages (tanstack's pre-pagination model).  Both
   *  honour the active filters, sort, column visibility and order. */
  const handleExportCsv = (scope: 'page' | 'all') => {
    if (!tableId) return;
    const visibleColIdsInOrder = table.getVisibleLeafColumns().map(c => c.id);
    const colByKey = new Map(columns.map(c => [c.key, c]));
    const exportCols = visibleColIdsInOrder
      .map(id => colByKey.get(id))
      .filter((c): c is AnyColumn => Boolean(c));
    const sourceRows = scope === 'all'
      ? table.getPrePaginationRowModel().rows
      : table.getRowModel().rows;
    const exportRows = flattenLeaves(sourceRows);
    const today = new Date().toISOString().slice(0, 10);
    const suffix = scope === 'all' ? '-all' : '';
    exportRowsAsCsv(`${tableId}${suffix}-${today}.csv`, exportCols, exportRows);
  };

  // Reset wipes every customization (filters / sort / search / column
  // layout) back to declaration defaults.  Surfaced from the Columns
  // popover as "Reset to defaults" — operators reach for this when
  // they've narrowed too far and want a clean slate.
  const resetAll = () => {
    setColumnFilters([]);
    setSorting([]);
    setGlobalFilter('');
    setColumnVisibility({});
    setColumnOrder([]);
    setColumnPinning({ left: [], right: [] });
    // User group overrides too — back to the column config's groups.
    setGroupOverrides({});
    // Row grouping back to the table's configured default (or off).
    setRowGroupPref(defaultRowGroup ?? null);
    setExpanded({});
    // Column widths back to auto layout.
    setUserWidths({});
  };

  // Manage-columns options — derived from the full column list (NOT
  // the rendered header list) so the popover always lists every
  // column, including hidden ones the operator might want to bring
  // back.  Order follows ``columnOrder`` when set so the popover
  // mirrors the current header sequence.
  const manageOptions = useMemo(() => {
    const byKey = new Map(columns.map(c => [c.key, c]));
    const ids = columnOrder.length
      ? [...columnOrder, ...columns.map(c => c.key).filter(k => !columnOrder.includes(k))]
      : columns.map(c => c.key);
    return ids
      .map(id => byKey.get(id))
      .filter((c): c is AnyColumn => Boolean(c))
      .map(c => ({
        id: c.key,
        // Use the static ``label`` for the popover row even when the
        // column renders a rich header in the table — the popover is
        // a text list, not a checkbox row in disguise.  Falls back to
        // the column key when label is empty (locked structural
        // columns where the header is a checkbox, not a name).
        label: c.label || c.key,
        alwaysVisible: c.locked === true,
      }));
  }, [columns, columnOrder]);

  return (
    <div>
      {/* Segment tab strip — OUTSIDE the card, floating directly on
          the page background (no fill of its own), like physical
          folder tabs poking up from the card below.  ``-mb-px`` +
          ``relative z-10`` let each tab overlap the card's top
          border by exactly one pixel: the ACTIVE tab's opaque
          ``bg-muted`` paints over that border line, opening a seam
          into the card's toolbar surface; inactive tabs are
          transparent, so the card border runs straight under them —
          reading as "closed" folders. */}
      {segments && segments.length > 0 && (
        <div
          role="tablist"
          aria-label="Data segments"
          // ``px-6`` insets the first tab (and its 8px outward
          // fillet) fully clear of the card's rounded corner below
          // (radius ~10px) — tighter insets stack the fillet's curve
          // on top of the card-corner curve and the two read as one
          // lumpy S instead of two clean shapes.
          className="relative z-10 -mb-px flex items-end gap-1 px-6"
        >
          {segments.map((seg, i) => {
            const active = seg.key === activeSegment?.key;
            const prevActive = i > 0 && segments[i - 1].key === activeSegment?.key;
            return (
              <Fragment key={seg.key}>
                {/* Hairline separator between two INACTIVE neighbours
                    only — it disappears next to the active tab so the
                    folder silhouette stays clean (browser-tab rule).
                    Dormant with 2 tabs; earns its keep at 3+. */}
                {i > 0 && !active && !prevActive && (
                  <span aria-hidden className="self-center w-px h-4 bg-border" />
                )}
                <SegmentTab
                  label={seg.label}
                  count={segmentCounts[seg.key] ?? 0}
                  showCount={seg.showCount !== false}
                  active={active}
                  onClick={() => setSegmentPref(seg.key)}
                />
              </Fragment>
            );
          })}
        </div>
      )}
      {/* One card holds everything — toolbar, table, pagination — so
          the chrome (Search / Export / Columns / pagination) sits
          INSIDE the bordered surface alongside the rows, matching
          the reference design.  Previously the toolbar and footer
          lived outside the card so only the table body had a card
          edge around it.  ``overflow-hidden`` clips the table rows
          against the card's rounded corners. */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Toolbar shares the ``bg-muted`` surface used by the table
          header row + pinned cells, so all the "chrome" surfaces
          (toolbar / header / pinned cells / footer) read as one
          group and the body cells become the visually-focal "data"
          surface (bg-card).  Same hierarchy in both themes — the
          tint difference is more visible in dark (card 0.275 vs
          muted 0.24) than light (card 1.0 vs muted 0.97), but
          consistent visual grouping either way.  Skipped entirely
          when ``enableToolbar={false}`` so bare display tables
          (billing summaries, form-embedded parts lists) render just
          the table body inside the card. */}
      {enableToolbar && (
      <div className="flex flex-wrap items-center justify-between p-3 gap-3 bg-muted border-b border-border">
        {/* LEFT: page-supplied headerToolbar (filter chips, etc.).
            The bulk-action bar + the active-filter chips share ONE
            strip BELOW this row (they swap), matching the reference. */}
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
          {headerToolbar}
        </div>
        {/* RIGHT: Search input, then a uniform icon cluster —
            Filter · Sort · Columns · Export — then density.  Icons
            share size ``icon`` (32px) so the row reads as one calm
            strip; active state is a corner count badge + tooltip. */}
        <div className="flex items-center gap-2">
          {hasSearch ? (
            <div className="relative max-w-xs flex-shrink-0">
              <Search
                size={14}
                aria-hidden
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
              />
              <Input
                placeholder={searchPlaceholder ?? 'Search…'}
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                className="pl-8"
              />
            </div>
          ) : null}
          {tableId && (
            <>
              <Tip label="Active filters">
              <Button
                ref={filterBtnRef}
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setFilterMenuOpen(o => !o)}
                aria-label="Active filters"
                className={cn(
                  'relative',
                  columnFilters.length > 0 && 'text-primary border-primary/30 bg-primary/10',
                )}
              >
                <FilterIcon />
                {columnFilters.length > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 min-w-4 h-4 px-1 rounded-full bg-primary text-primary-foreground text-3xs font-semibold inline-flex items-center justify-center">
                    {columnFilters.length}
                  </span>
                )}
              </Button>
              </Tip>
              <Tip label="Active sort">
              <Button
                ref={sortBtnRef}
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setSortMenuOpen(o => !o)}
                aria-label="Active sort"
                className={cn(
                  'relative',
                  sorting.length > 0 && 'text-primary border-primary/30 bg-primary/10',
                )}
              >
                <ArrowUpDown />
                {sorting.length > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 min-w-4 h-4 px-1 rounded-full bg-primary text-primary-foreground text-3xs font-semibold inline-flex items-center justify-center">
                    {sorting.length}
                  </span>
                )}
              </Button>
              </Tip>
              <Tip label="Show / hide columns">
              <Button
                ref={manageAnchorRef}
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setManageOpen((o) => !o)}
                aria-label="Manage columns"
                className={cn(
                  'relative',
                  hiddenCount > 0 && 'text-primary border-primary/30 bg-primary/10',
                )}
              >
                <Columns3 />
                {hiddenCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 min-w-4 h-4 px-1 rounded-full bg-primary text-primary-foreground text-3xs font-semibold inline-flex items-center justify-center">
                    {hiddenCount}
                  </span>
                )}
              </Button>
              </Tip>
              {/* Export scope picker — "this page" vs "everything
                  that matches the current filters" (all pages). */}
              <MenuPrimitive.Root>
                <MenuPrimitive.Trigger
                  render={(props) => (
                    <Tip label="Export to CSV">
                    <Button
                      {...props}
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label="Export to CSV"
                    >
                      <Download />
                    </Button>
                    </Tip>
                  )}
                />
                <MenuPrimitive.Portal>
                  <MenuPrimitive.Positioner align="end" sideOffset={4} className="z-50 outline-none">
                    <MenuPrimitive.Popup className="min-w-56 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1 outline-none">
                      {(() => {
                        const pageCount = flattenLeaves(table.getRowModel().rows).length;
                        const allCount = table.getFilteredRowModel().rows.length;
                        return (
                          <>
                            <MenuPrimitive.Item
                              onClick={() => handleExportCsv('page')}
                              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none data-[highlighted]:bg-accent"
                            >
                              <Download size={14} className="text-muted-foreground" />
                              <span className="flex-1 text-foreground text-left">Current page</span>
                              <span className="text-2xs text-muted-foreground tabular-nums">
                                {pageCount.toLocaleString()} rows
                              </span>
                            </MenuPrimitive.Item>
                            <MenuPrimitive.Item
                              onClick={() => handleExportCsv('all')}
                              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none data-[highlighted]:bg-accent"
                            >
                              <Download size={14} className="text-muted-foreground" />
                              <span className="flex-1 text-foreground text-left">All rows</span>
                              <span className="text-2xs text-muted-foreground tabular-nums">
                                {allCount.toLocaleString()} rows
                              </span>
                            </MenuPrimitive.Item>
                          </>
                        );
                      })()}
                    </MenuPrimitive.Popup>
                  </MenuPrimitive.Positioner>
                </MenuPrimitive.Portal>
              </MenuPrimitive.Root>
            </>
          )}
          {/* Density — one cycling control instead of a 3-way
              segmented group: click steps Compact → Default → Roomy.
              A single ``size="icon"`` slot keeps the toolbar cluster
              uniform; the glyph (row count) + tooltip communicate the
              current state and what the next click does. */}
          {(() => {
            const next = DENSITY_CYCLE[
              (DENSITY_CYCLE.indexOf(density) + 1) % DENSITY_CYCLE.length
            ];
            const Icon = DENSITY_ICONS[density];
            return (
              <Tip label={`Density: ${DENSITY_LABELS[density]} (click for ${DENSITY_LABELS[next]})`}>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setDensity(next)}
                aria-label={`Row density: ${DENSITY_LABELS[density]} — click for ${DENSITY_LABELS[next]}`}
              >
                <Icon />
              </Button>
              </Tip>
            );
          })()}
        </div>
      </div>
      )}

      {/* ONE strip below the toolbar, above the table — it SWAPS:
          the bulk-action bar when 1+ rows are selected, otherwise the
          active filter/sort/search chips (null when none).  Same
          location for both, matching the reference; a chrome-free table
          with nothing active/selected shows nothing. */}
      {selectedRowIds.size > 0 && (bulkSelection || enableToolbar) ? (
        <div className="px-3 py-1.5 border-b border-border">
          {selectionBarContent}
        </div>
      ) : filterChipRow}

      <div className="relative">
      <div
        ref={scrollContainerRef}
        // ``overflow-x: hidden`` + ``overflow-y: auto``: the native
        // horizontal scrollbar never exists (and therefore never paints
        // a reserved track at the bottom of the container the way
        // ``overflow-x: auto`` does even with ``::-webkit-scrollbar
        // { height: 0 }``).  Horizontal scrolling is driven entirely
        // by our custom scrollbar (drag) plus the wheel handler below
        // (trackpad swipe).  Vertical scrollbar is unaffected, so
        // stickyHeader tables (Scorecards) keep their native bar.
        //
        // Border + rounded corners now live on the outer card
        // wrapper, so this inner scroll container is plain — it just
        // owns the scrolling behaviour.  When the custom horizontal
        // scrollbar is shown it paints as an ABSOLUTE overlay in the
        // bottom ~12px of this container — reserve that band with
        // padding so the thumb never sits on top of the last row
        // (most visible in compact density where rows are short).
        className={cn(
          'overflow-y-auto overflow-x-hidden',
          needsHScroll && 'pb-3',
        )}
        style={stickyHeader ? { maxHeight: stickyHeader } : undefined}
      >
        {/* Raw <table> rather than the ui/table.tsx ``<Table>`` primitive
            because that primitive wraps the table in its own
            ``<div className="overflow-x-auto">`` — which becomes a
            SECOND scroll container nested inside ours, defeats our
            custom scrollbar, and leaves the native bar visible at
            the bottom of that inner wrapper.  ``min-w-full`` keeps
            the table at least as wide as the container (no awkward
            whitespace on the right) but lets it grow beyond when
            content needs more space, which is exactly what triggers
            our horizontal scroll. */}
        <table
          // Type size follows density — compact drops the whole table
          // to text-xs so the tighter padding actually buys more rows
          // per screen instead of just cramping the same-size text.
          className={cn('min-w-full caption-bottom', DENSITY_TEXT[density])}
          // Once the operator has resized any column, widths become
          // authoritative: fixed layout + explicit total width (sum
          // of column sizes) so a drag actually changes the column
          // instead of the browser re-distributing space.
          style={hasUserWidths ? {
            tableLayout: 'fixed',
            width: table.getTotalSize(),
            minWidth: '100%',
          } : undefined}
        >
          <TableHeader
            className={stickyHeader ? 'sticky top-0 z-10 bg-card' : undefined}
          >
            {/* Group bracket row — one spanning cell per contiguous
                run of same-``group`` columns ("Location" over Street /
                City / State), empty cells over ungrouped runs.  Only
                rendered when at least one visible column declares a
                group, so ungrouped tables pay zero extra height.
                Labelled brackets are DRAGGABLE — dropping one onto
                another run moves the whole group (all member columns
                travel together). */}
            {hasGroupRow && (
              <DndContext
                sensors={sensors}
                onDragStart={(e: DragStartEvent) =>
                  setGroupDrag({ activeId: String(e.active.id), overId: null })}
                onDragOver={(e: DragOverEvent) =>
                  setGroupDrag(prev => ({
                    ...prev,
                    overId: e.over ? String(e.over.id) : null,
                  }))}
                onDragCancel={() => setGroupDrag({ activeId: null, overId: null })}
                onDragEnd={handleGroupDragEnd}
              >
                <SortableContext
                  items={groupRuns.map(r => `grp:${r.firstId}`)}
                  strategy={noShiftStrategy}
                >
                  <TableRow className="bg-muted hover:bg-muted">
                    {groupRuns.map((run) => {
                      // Insertion indicator: a primary bar on the edge
                      // of the hovered run — right edge when the group
                      // is moving rightward (it will land AFTER the
                      // target), left edge when moving leftward.
                      const runId = `grp:${run.firstId}`;
                      let dropSide: 'left' | 'right' | null = null;
                      if (
                        groupDrag.activeId
                        && groupDrag.overId === runId
                        && groupDrag.activeId !== runId
                      ) {
                        const ai = groupRuns.findIndex(r => `grp:${r.firstId}` === groupDrag.activeId);
                        const oi = groupRuns.findIndex(r => `grp:${r.firstId}` === runId);
                        dropSide = ai < oi ? 'right' : 'left';
                      }
                      return (
                        <GroupHeaderCell
                          key={run.firstId}
                          run={run}
                          dropSide={dropSide}
                        />
                      );
                    })}
                  </TableRow>
                </SortableContext>
                {/* Cursor-following chip naming what's being dragged —
                    "LOCATION · 3 columns" — so the operator knows the
                    whole group travels together.  Portaled to <body>
                    (a <div> can't legally nest inside <thead>). */}
                {createPortal(
                  <DragOverlay>
                    {(() => {
                      const activeRun = groupDrag.activeId
                        ? groupRuns.find(r => `grp:${r.firstId}` === groupDrag.activeId)
                        : null;
                      return activeRun?.label ? (
                        <div className="px-2.5 py-1.5 rounded-md bg-popover text-popover-foreground border border-border shadow-lg text-2xs font-medium uppercase tracking-wide whitespace-nowrap cursor-grabbing">
                          {activeRun.label} · {activeRun.span} column{activeRun.span > 1 ? 's' : ''}
                        </div>
                      ) : null;
                    })()}
                  </DragOverlay>,
                  document.body,
                )}
              </DndContext>
            )}
            {visibleHeaderGroups.map((headerGroup) => (
              <DndContext
                key={headerGroup.id}
                sensors={sensors}
                onDragStart={(e: DragStartEvent) => setLeafDragId(String(e.active.id))}
                onDragCancel={() => setLeafDragId(null)}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={visibleColIds}
                  strategy={horizontalListSortingStrategy}
                >
                  {/* ``bg-muted`` (not ``bg-card``) so the header row
                      sits on a slightly off-canvas surface and reads
                      clearly distinct from body cells in both themes.
                      ``bg-card`` would equal the table surface =
                      identical to body cells = no visual hierarchy.
                      ``data-header-row="leaf"`` marks this as the row
                      the pinned-width ResizeObserver measures (the
                      group bracket row above must NOT be measured —
                      its colSpan cells would double-count widths). */}
                  <TableRow data-header-row="leaf" className="bg-muted hover:bg-muted">
                    {headerGroup.headers.map((header, hIdx) => (
                      <ColumnHeaderCell
                        key={header.id}
                        header={header}
                        stickyHeader={!!stickyHeader}
                        colConfig={columns.find(c => c.key === header.column.id)}
                        uniques={uniquesByCol[header.column.id] ?? { options: [], counts: {} }}
                        rangeBounds={rangesByCol[header.column.id]}
                        dateBounds={dateBoundsByCol[header.column.id]}
                        tableId={tableId}
                        onOpenManage={() => setManageOpen(true)}
                        onMeasureWidth={reportColumnWidth}
                        // Leading content (e.g. bulk-select master
                        // checkbox) goes on the FIRST visible header
                        // — when the operator pins / reorders, the
                        // checkbox moves with the new first column.
                        leadingContent={hIdx === 0
                          ? (bulkSelection ? renderSelectAll() : firstColumnLeading?.header())
                          : undefined}
                        groupNames={groupNames}
                        currentGroup={effectiveGroupByKey.get(header.column.id) ?? null}
                        onAssignGroup={(name) => assignGroup(header.column.id, name)}
                        onNewGroup={() => {
                          // window.prompt keeps v1 dependency-free; a
                          // styled inline input can replace it later
                          // without touching the plumbing.
                          const name = window.prompt('New group name')?.trim();
                          if (name) assignGroup(header.column.id, name);
                        }}
                        onUngroup={() => ungroupColumn(header.column.id)}
                        rowGrouped={rowGroupBy === header.column.id}
                        onRowGroup={() => toggleRowGroup(header.column.id)}
                        fixedWidths={hasUserWidths}
                        onAutosize={() => autosizeColumn(header.column.id)}
                        densityClass={DENSITY_HEADER[density]}
                      />
                    ))}
                  </TableRow>
                </SortableContext>
                {/* Cursor-following chip naming the dragged column —
                    gives the operator a clear "I'm holding Odometer"
                    signal on top of the sibling-shift preview. */}
                {createPortal(
                  <DragOverlay>
                    {leafDragId ? (
                      <div className="px-2.5 py-1.5 rounded-md bg-popover text-popover-foreground border border-border shadow-lg text-xs font-medium whitespace-nowrap cursor-grabbing">
                        {columns.find(c => c.key === leafDragId)?.label || leafDragId}
                      </div>
                    ) : null}
                  </DragOverlay>,
                  document.body,
                )}
              </DndContext>
            ))}
          </TableHeader>
          <TableBody>
            {rowCount === 0 ? (
              <TableRow>
                <TableCell colSpan={table.getVisibleLeafColumns().length} className="py-8 text-center text-muted-foreground">
                  No data
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row, rowIdx) => {
                // ── Row-group header row ─────────────────────
                //
                // When row grouping is active, tanstack interleaves
                // synthetic group rows (``getIsGrouped``) with the
                // leaf rows.  A group row renders as ONE full-width
                // cell: expand chevron + optional group checkbox
                // (firstColumnLeading.groupHeader) + custom summary
                // (rowGroupHeader) or the default "<value> (N)".
                if (row.getIsGrouped()) {
                  const value = rowGroupBy ? row.getValue(rowGroupBy) : undefined;
                  const leafOriginals = row.subRows.map(
                    r => r.original as Record<string, unknown>,
                  );
                  return (
                    <TableRow
                      key={row.id}
                      onClick={() => row.toggleExpanded()}
                      className="cursor-pointer bg-muted/40 hover:bg-muted/60"
                    >
                      <TableCell
                        colSpan={table.getVisibleLeafColumns().length}
                        className={DENSITY_GROUP_ROW[density]}
                      >
                        {/* Sticky-left inner wrapper so the group
                            label stays visible while the table is
                            scrolled horizontally (the cell itself
                            spans the full, possibly very wide,
                            table). */}
                        <span className="sticky left-2 inline-flex w-fit items-center gap-2">
                          <ChevronRight
                            size={14}
                            aria-hidden="true"
                            className={cn(
                              'text-muted-foreground transition-transform',
                              row.getIsExpanded() && 'rotate-90',
                            )}
                          />
                          {bulkSelection ? (
                            <span onClick={(e) => e.stopPropagation()}>
                              {renderGroupBox(row.subRows
                                .filter(r => !isRowSelectable || isRowSelectable(r.original as Record<string, unknown>))
                                .map(r => r.id))}
                            </span>
                          ) : firstColumnLeading?.groupHeader && (
                            <span onClick={(e) => e.stopPropagation()}>
                              {firstColumnLeading.groupHeader(value, leafOriginals)}
                            </span>
                          )}
                          {rowGroupHeader ? (
                            rowGroupHeader(value, leafOriginals)
                          ) : (
                            <>
                              <span className="font-medium text-foreground">
                                {String(value ?? '—')}
                              </span>
                              <span className="text-xs text-muted-foreground">
                                ({row.subRows.length})
                              </span>
                            </>
                          )}
                        </span>
                      </TableCell>
                    </TableRow>
                  );
                }
                const isSelected = selectedRowIds.has(row.id);
                // Zebra striping — alternate rows get a subtle tint
                // so the operator can track horizontally across many
                // columns without losing their line.  Parity is on
                // VISUAL index (post-filter, post-sort), so it stays
                // consistent regardless of underlying data order.
                const isZebra = rowIdx % 2 === 1;
                return (
                  <TableRow
                    key={row.id}
                    onClick={(e) => handleRowClick(e, row.id, row.original)}
                    data-state={isSelected ? 'selected' : undefined}
                    style={isSelected
                      // 3px inset shadow on the left edge — paints the
                      // primary-coloured accent stripe without using
                      // ``border-l`` (which would shift the row by 3px
                      // because ``<tr>`` borders interact with
                      // ``border-collapse`` differently than divs).
                      ? { boxShadow: 'inset 3px 0 0 0 var(--primary)' }
                      : undefined}
                    className={cn(
                      onRowClick ? 'cursor-pointer' : '',
                      isZebra && !isSelected && 'bg-muted/30',
                      // Primary-tinted background on selected rows
                      // wins over zebra so the multi-row selection
                      // stays visible across the table.
                      isSelected && 'bg-primary/10 hover:bg-primary/15',
                    )}
                  >
                    {row.getVisibleCells().map((cell, cIdx) => (
                      <PinnedBodyCell
                        key={cell.id}
                        cell={cell}
                        padding={padding}
                        selected={isSelected}
                        zebra={isZebra}
                        // Indent the first cell of leaf rows under an
                        // expanded group so children read as nested.
                        indent={cIdx === 0 && !!rowGroupBy}
                        // Same first-visible-column rule — the per-row
                        // checkbox is rendered into whichever cell is
                        // currently leftmost.
                        leadingContent={
                          cIdx !== 0
                            ? undefined
                            : bulkSelection
                              ? (isRowSelectable && !isRowSelectable(row.original)
                                  ? undefined
                                  : renderRowBox(row.id, row.original))
                              : firstColumnLeading?.cell
                                ? firstColumnLeading.cell(row.original)
                                : undefined
                        }
                      />
                    ))}
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </table>
      </div>
      {/* Custom horizontal scrollbar — anchored to the bottom of the
          table container, spanning ONLY the centre (non-pinned)
          region.  Mirrors AG Grid's "scrollbar over the scrollable
          area" pattern: pinned columns visually don't have a
          scrollbar because they don't scroll.  Only renders when
          content overflows; hidden otherwise. */}
      {needsHScroll && trackWidth > 0 && (
        <div
          className="absolute bottom-1 h-2"
          style={{ left: pinnedLeftWidth, right: pinnedRightWidth }}
        >
          <div
            className="relative h-full bg-muted/40 rounded-full cursor-pointer"
            onClick={onTrackClick}
          >
            <div
              className="absolute top-0 bottom-0 bg-muted-foreground/50 hover:bg-muted-foreground/70 rounded-full cursor-grab active:cursor-grabbing"
              style={{
                width: thumbWidth,
                transform: `translateX(${thumbLeft}px)`,
              }}
              onPointerDown={onThumbPointerDown}
            />
          </div>
        </div>
      )}
      </div>
      {/* Pagination footer — skipped when ``enablePagination={false}``
          (short lists where paginating 5-20 rows adds noise).  The
          border-t lives on the div itself so it disappears with the
          section, leaving the table body flush with the card bottom. */}
      {enablePagination && (
      <div className="flex flex-wrap items-center justify-between p-3 gap-3 bg-muted border-t border-border">
        {/* Filter-status hint on the left — only when active, so the
            footer stays quiet on the default view.  Pagination
            cluster on the right mirrors the reference layout
            (Show per page · range · prev/next). */}
        <p className="text-xs text-muted-foreground">
          {columnFilters.length > 0 || globalFilter
            ? `${columnFilters.length} filter${columnFilters.length === 1 ? '' : 's'} active`
            : ' '}
        </p>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <label className="inline-flex items-center gap-2">
            <span>{t('common.rows_per_page')}</span>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => setPageSize(Number(v))}
            >
              {/* Default size.  (The small variants used to cap their
                  radius at 10px and ignore the Pill corners theme —
                  that cap has since been removed from the ui
                  primitives, so any size now tracks ``--radius``.) */}
              <SelectTrigger aria-label={t('common.rows_per_page')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((s) => (
                  <SelectItem key={s} value={String(s)}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {(() => {
            const totalFiltered = table.getFilteredRowModel().rows.length;
            const start = totalFiltered === 0 ? 0 : pageIndex * pageSize + 1;
            const end   = Math.min(totalFiltered, (pageIndex + 1) * pageSize);
            return (
              <span className="tabular-nums">
                {start}-{end} {t('common.of')} {totalFiltered.toLocaleString()}
              </span>
            );
          })()}
          <div className="inline-flex items-center gap-1">
            {/* ``size="icon"`` (not ``icon-xs``) so the buttons
                respect the Pill / Rounded / Sharp Corners theme —
                the small icon variants hardcode a radius cap that
                silently ignores the theme picker. */}
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              aria-label={t('common.previous')}
            >
              <ChevronLeft />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              aria-label={t('common.next')}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      </div>
      )}
      </div>{/* end card wrapper */}
      {/* Active-filters popover — one row per narrowing filter with a
          per-item clear, plus clear-all.  The mirror image of the
          Columns popover, so "what's limiting my view?" always has a
          single place to look. */}
      {tableId && enableToolbar && (
        <ToolbarPopover
          open={filterMenuOpen}
          onOpenChange={setFilterMenuOpen}
          anchor={filterBtnRef.current}
          icon={<FilterIcon size={14} className="text-muted-foreground" />}
          title="Active filters"
        >
          {columnFilters.length === 0 ? (
            <p className="px-3 py-3 text-2xs text-muted-foreground italic">
              No active filters — open a column's ⋮ menu and pick
              Filter… to narrow the list.
            </p>
          ) : (
            <>
              <div className="max-h-72 overflow-y-auto py-1">
                {columnFilters.map((f) => {
                  const col = columns.find(c => c.key === f.id);
                  return (
                    <div key={f.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                      <span className="shrink-0 font-medium text-foreground">
                        {col?.label || f.id}
                      </span>
                      <Tip label={describeFilter(f.id, f.value)}>
                      <span className="flex-1 truncate text-muted-foreground">
                        {describeFilter(f.id, f.value)}
                      </span>
                      </Tip>
                      <button
                        type="button"
                        onClick={() =>
                          setColumnFilters(prev => prev.filter(x => x.id !== f.id))}
                        aria-label={`Clear ${col?.label || f.id} filter`}
                        className="shrink-0 p-0.5 text-muted-foreground hover:text-foreground"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => { setColumnFilters([]); setFilterMenuOpen(false); }}
                  className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1"
                >
                  <X size={12} aria-hidden="true" />
                  Clear all filters
                </button>
              </div>
            </>
          )}
        </ToolbarPopover>
      )}
      {/* Active-sort popover — same pattern for the sort state. */}
      {tableId && enableToolbar && (
        <ToolbarPopover
          open={sortMenuOpen}
          onOpenChange={setSortMenuOpen}
          anchor={sortBtnRef.current}
          icon={<ArrowUpDown size={14} className="text-muted-foreground" />}
          title="Active sort"
        >
          {sorting.length === 0 ? (
            <p className="px-3 py-3 text-2xs text-muted-foreground italic">
              No active sort — open a column's ⋮ menu and pick Sort.
            </p>
          ) : (
            <>
              <div className="py-1">
                {sorting.map((s) => {
                  const col = columns.find(c => c.key === s.id);
                  return (
                    <div key={s.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                      <span className="shrink-0 font-medium text-foreground">
                        {col?.label || s.id}
                      </span>
                      <span className="flex-1 inline-flex items-center gap-1 text-muted-foreground">
                        {s.desc
                          ? <><ChevronDown size={12} aria-hidden="true" /> Descending</>
                          : <><ChevronUp size={12} aria-hidden="true" /> Ascending</>}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setSorting(prev => prev.filter(x => x.id !== s.id))}
                        aria-label={`Clear ${col?.label || s.id} sort`}
                        className="shrink-0 p-0.5 text-muted-foreground hover:text-foreground"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => { setSorting([]); setSortMenuOpen(false); }}
                  className="w-full px-3 py-2 text-2xs text-muted-foreground hover:text-foreground hover:bg-accent inline-flex items-center justify-center gap-1"
                >
                  <X size={12} aria-hidden="true" />
                  Clear sort
                </button>
              </div>
            </>
          )}
        </ToolbarPopover>
      )}
      {tableId && enableToolbar && (
        <ManageColumnsMenu
          options={manageOptions}
          visibility={effectiveVisibility}
          onToggle={(id) => {
            // Toggle based on the CURRENT effective visibility, not
            // just the persisted map — a defaultHidden column starts
            // hidden but has no persisted entry; without this the
            // first click would silently no-op (undefined → false).
            const currentlyVisible = effectiveVisibility[id] !== false;
            setColumnVisibility((prev) => ({
              ...prev,
              [id]: !currentlyVisible,
            }));
          }}
          onReset={() => {
            resetAll();
            setManageOpen(false);
          }}
          open={manageOpen}
          onOpenChange={setManageOpen}
          anchor={manageAnchorRef.current}
        />
      )}
      {/* The selection action bar is the TOP strip above the table
          (see near the toolbar) — no floating bottom bar. */}
    </div>
  );
}

// ── Toolbar popover shell ───────────────────────────────────
//
// Shared skeleton for the toolbar's Filter / Sort popovers — same
// visual shell as ManageColumnsMenu (header strip + content), anchored
// to the toolbar button that opened it.
function ToolbarPopover({
  open, onOpenChange, anchor, icon, title, children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anchor: HTMLElement | null;
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor ?? undefined}
          align="end"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup className="w-64 bg-popover text-popover-foreground border border-border rounded-md shadow-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              {icon}
              <span className="text-xs font-medium text-foreground">{title}</span>
            </div>
            {children}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

// ── Sticky-pinning helper ───────────────────────────────────
//
// Returns the inline style needed to anchor a pinned column (or
// nothing for a non-pinned column).  ``getStart('left')`` and
// ``getAfter('right')`` are tanstack helpers that sum widths of
// all preceding/following pinned columns so the second left-pin
// sits at ``left: 200`` (or whatever) and so on.  z-index nudged
// above the sticky header (10) when both are stacked, otherwise
// the body row would scroll over the pinned cell.
//
// The LAST left-pinned column and FIRST right-pinned column get a
// soft drop-shadow that fades away from the freeze edge so the
// pinned cluster visually "floats" above the scrolling centre group.
// Without this cue the boundary reads as just a 1px line which is
// easy to miss when scrolling horizontally.
function pinnedStyle(
  column: {
    getIsPinned: () => false | 'left' | 'right';
    getStart: (pos: 'left') => number;
    getAfter: (pos: 'right') => number;
    getIsLastColumn: (pos?: 'left' | 'right' | 'center') => boolean;
    getIsFirstColumn: (pos?: 'left' | 'right' | 'center') => boolean;
  },
  stickyHeader: boolean,
): React.CSSProperties {
  const side = column.getIsPinned();
  if (side === false) return {};
  const base: React.CSSProperties = {
    position: 'sticky',
    zIndex: stickyHeader ? 11 : 2,
  };
  if (side === 'left') {
    return {
      ...base,
      left: column.getStart('left'),
      // Boundary shadow driven by the ``--pin-shadow-right`` design
      // token — opacity / blur are tuned per theme (light: 20%, dark:
      // 35%) so the freeze edge reads on either surface without
      // looking heavy.  Hardcoded ``rgb(0 0 0 / 0.18)`` worked on
      // dark but vanished on white; the token fixes that without
      // forcing every consumer to think about theme.
      boxShadow: column.getIsLastColumn('left')
        ? 'var(--pin-shadow-right)'
        : undefined,
    };
  }
  return {
    ...base,
    right: column.getAfter('right'),
    boxShadow: column.getIsFirstColumn('right')
      ? 'var(--pin-shadow-left)'
      : undefined,
  };
}

// ── Per-column header cell ──────────────────────────────────
//
// Lifted into its own component so it can use the ``useSortable`` hook
// (one per draggable item).  Owns the column's label / filter trigger
// / sort chevron / 3-dot menu cluster.
interface ColumnHeaderCellProps {
  header: Header<Record<string, unknown>, unknown>;
  stickyHeader: boolean;
  /** Source column config — we look up ``filterMode``/``filterRange``
   *  here since tanstack's column meta doesn't expose them.  Kept as
   *  an optional parallel prop so DataGrid can pass the matched
   *  entry for this header. */
  colConfig?: AnyColumn;
  uniques: {
    options: Array<{ value: string; label: string }>;
    counts: Record<string, number>;
  };
  rangeBounds?: { min: number; max: number; step: number; unit: string };
  dateBounds?: { min: string; max: string };
  tableId?: string;
  onOpenManage: () => void;
  /** Called on mount + every resize with this column's rendered
   *  width in pixels.  Parent feeds this into tanstack's
   *  ``columnSizing`` so ``column.getStart('left')`` returns the
   *  correct cumulative offset for pinned columns past the first. */
  onMeasureWidth: (id: string, width: number) => void;
  /** Optional node prepended INSIDE this header cell's label area
   *  (e.g. bulk-select master checkbox).  Set only on the first
   *  visible column. */
  leadingContent?: React.ReactNode;
  /** Column grouping — existing group names + this column's effective
   *  group + the assign / create / remove handlers, passed through to
   *  the 3-dot menu's Group submenu. */
  groupNames: string[];
  currentGroup: string | null;
  onAssignGroup: (name: string) => void;
  onNewGroup: () => void;
  onUngroup: () => void;
  /** Row grouping — is this column the active row-group key, and the
   *  toggle handler. */
  rowGrouped: boolean;
  onRowGroup: () => void;
  /** True once the operator has manually sized any column — the
   *  table is in fixed-layout mode and each header applies its
   *  explicit width. */
  fixedWidths: boolean;
  /** Density-driven header height class (``h-8``/``h-10``/``h-12``) —
   *  overrides TableHead's base ``h-10`` via twMerge so the header
   *  compresses / expands with the body rows. */
  densityClass: string;
  /** Fit this column's width to its widest rendered cell.  Reached
   *  via the 3-dot menu and by double-clicking the resize handle. */
  onAutosize: () => void;
}

function ColumnHeaderCell({
  header, stickyHeader, colConfig, uniques, rangeBounds, dateBounds, tableId,
  onOpenManage, onMeasureWidth, leadingContent,
  groupNames, currentGroup, onAssignGroup, onNewGroup, onUngroup,
  rowGrouped, onRowGroup, fixedWidths, onAutosize, densityClass,
}: ColumnHeaderCellProps) {
  const canSort = header.column.getCanSort();
  const sortedRaw = header.column.getIsSorted();
  const canFilter = header.column.getCanFilter();
  const pinned = header.column.getIsPinned();   // false | 'left' | 'right'
  const isLastVisible = header.column.getIsLastColumn();
  // Boundary detectors — used to draw EXACTLY ONE divider between the
  // pinned cluster and the centre group (with a soft shadow on the
  // boundary cell), rather than a separator between every pair of
  // adjacent columns.  Internal pinned columns get no separator.
  const isLastLeftPinned   = pinned === 'left'  && header.column.getIsLastColumn('left');
  const isFirstRightPinned = pinned === 'right' && header.column.getIsFirstColumn('right');
  // Last centred column when right-pinned columns exist after it —
  // the next column is then the first right-pinned, which carries the
  // boundary border itself, so we skip the separator here to avoid
  // a double-line.
  const isLastCenterBeforeRightPin = !pinned
    && header.column.getIsLastColumn('center')
    && !isLastVisible;
  // Structural / locked columns (bulk-select checkbox etc.) suppress
  // the 3-dot menu and drag handle — they're not user-manipulable.
  // tanstack-table's ``enableHiding``/``enablePinning`` already say
  // false for these (set in tableColumns above); locked also gates
  // the in-cell UI affordances.
  const isLocked = header.column.getCanHide() === false
    && header.column.getCanPin() === false;
  // Controlled-open state for the filter popover — opened ONLY from
  // the 3-dot menu's "Filter…" item.  The column header itself is
  // just text + sort chevron; filter no longer has an inline trigger.
  const [filterOpen, setFilterOpen] = useState(false);
  // Captured for the filter popover to anchor against — opens just
  // below the header cell when triggered from the 3-dot menu.
  const headerCellRef = useRef<HTMLTableCellElement | null>(null);
  // Pinned + locked columns are NOT draggable.  Pinned drag would
  // break sticky anchoring; locked columns are structural and never
  // move regardless of operator input.
  const dragEnabled = !!tableId && pinned === false && !isLocked;

  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: header.column.id, disabled: !dragEnabled });

  const pinStyle = pinnedStyle(header.column, stickyHeader);
  // ``CSS.Translate.toString`` emits ONLY translate3d(x, y, 0).
  // Using ``CSS.Transform.toString`` here would also include
  // scaleX/scaleY derived from the ratio of source-to-target column
  // widths — which makes the dragged label visually grow/shrink as
  // it passes over columns of different widths.  Translate-only keeps
  // the dragged column its natural width; siblings reflow under it
  // via the ``transition``, which is the standard column-drag UX.
  const dragStyle: React.CSSProperties = {
    transform: pinned ? undefined : CSS.Translate.toString(transform),
    transition: pinned ? undefined : transition,
    opacity: isDragging ? 0.5 : 1,
    // Explicit width once the table is in fixed-layout (user-sized)
    // mode — ``getSize`` reads the effective sizing map so a live
    // resize drag updates the width per mousemove.
    width: fixedWidths ? header.getSize() : undefined,
    ...pinStyle,
  };

  const labelNode = flexRender(
    header.column.columnDef.header, header.getContext(),
  );
  const headerText = typeof header.column.columnDef.header === 'string'
    ? header.column.columnDef.header as string
    : header.column.id;

  // Sort indicator — visible ONLY when a sort is active.  Sort is
  // controlled exclusively through the 3-dot menu (Sort ascending /
  // descending / Clear sort), so the column header doesn't carry a
  // standalone click target for it.  When unsorted, no glyph is
  // rendered — keeps the header visually quiet on every column that
  // isn't currently driving the order.
  const sortIndicator =
    sortedRaw === 'asc'  ? <ChevronUp size={12} aria-hidden="true" /> :
    sortedRaw === 'desc' ? <ChevronDown size={12} aria-hidden="true" /> :
    null;

  // Filter-value shape depends on ``colConfig.filterMode``:
  //   select     → ``string[]``
  //   range      → ``[number|null, number|null]``
  //   date-range → ``[string|null, string|null]`` (YYYY-MM-DD)
  // All stored in the same tanstack slot; we key off mode to read.
  const isRangeFilter     = colConfig?.filterMode === 'range';
  const isDateRangeFilter = colConfig?.filterMode === 'date-range';
  const rawFilter = canFilter ? header.column.getFilterValue() : undefined;
  const filterValueArr: string[] = canFilter && !isRangeFilter && !isDateRangeFilter
    ? (rawFilter as string[] | undefined) ?? []
    : [];
  const rangeFilter = isRangeFilter
    ? (rawFilter as [number | null, number | null] | undefined) ?? [null, null]
    : [null, null] as [number | null, number | null];
  const dateRangeFilter = isDateRangeFilter
    ? (rawFilter as [string | null, string | null] | undefined) ?? [null, null]
    : [null, null] as [string | null, string | null];
  const isFiltered =
    isRangeFilter ? (rangeFilter[0] != null || rangeFilter[1] != null) :
    isDateRangeFilter ? (dateRangeFilter[0] != null || dateRangeFilter[1] != null) :
    filterValueArr.length > 0;
  // ``filterActive`` count for the 3-dot menu badge: chosen-options
  // count in select mode, bounds-set count in range / date-range
  // (0 / 1 / 2).
  const filterActive =
    isRangeFilter ? ((rangeFilter[0] != null ? 1 : 0) + (rangeFilter[1] != null ? 1 : 0)) :
    isDateRangeFilter ? ((dateRangeFilter[0] != null ? 1 : 0) + (dateRangeFilter[1] != null ? 1 : 0)) :
    filterValueArr.length;

  // Label area = drag handle.  Just text + an optional sort arrow
  // (only when this column is the active sort).  No inline filter
  // trigger.  When a filter is active we tint the text primary so
  // operators still see at a glance which columns are narrowing the
  // rows; the count + popover are reached via the 3-dot menu's
  // "Filter…" item.
  const labelContent = (
    <span
      className={cn(
        // ``min-w-0`` lets the label shrink inside the flex chain so
        // the inner ``truncate`` span can ellipsize on narrow columns
        // instead of overflowing under the 3-dot menu / into the
        // neighbouring column.
        'inline-flex items-center gap-1 select-none min-w-0',
        isFiltered && 'text-primary',
        sortedRaw && 'text-foreground',
        dragEnabled && 'cursor-grab active:cursor-grabbing',
      )}
      title={isFiltered
        ? isRangeFilter
          ? `${headerText}: ${rangeFilter[0] ?? '−∞'} – ${rangeFilter[1] ?? '+∞'}`
          : isDateRangeFilter
            ? `${headerText}: ${dateRangeFilter[0] ?? '−∞'} – ${dateRangeFilter[1] ?? '+∞'}`
            : `${headerText}: ${filterValueArr.join(', ')}`
        : undefined}
    >
      {/* Leading content (bulk-select master checkbox etc.) sits
          OUTSIDE the drag-handle visual cue but inside the label
          span so the spacing reads as part of the header cluster. */}
      {leadingContent && (
        <span className="inline-flex items-center mr-1 shrink-0">{leadingContent}</span>
      )}
      {/* The label is the ONLY part allowed to give up width — it
          ellipsizes; checkbox / sort chevron keep their full size.
          ``data-col-label`` marks it for autosize, which reads its
          ``scrollWidth`` (full text even while ellipsized). */}
      <span className="truncate" data-col-label>{labelNode}</span>
      {sortIndicator && <span className="shrink-0 inline-flex">{sortIndicator}</span>}
    </span>
  );

  // Compose dnd-kit's ref with our own — both need to attach to the
  // same DOM node (dnd-kit needs the draggable, we need the anchor
  // for the filter popover).  ``useCallback`` keeps the composed ref
  // stable across renders; without it React would invoke the callback
  // ref every render with null → node, churning dnd-kit's internal
  // tracking and silently breaking drag activation.
  const setRefs = useCallback((el: HTMLTableCellElement | null) => {
    setNodeRef(el);
    headerCellRef.current = el;
  }, [setNodeRef]);

  // Measure the rendered width of this header cell and feed it back
  // to tanstack's ``columnSizing`` state via the parent's
  // ``onMeasureWidth`` callback.  Without this, ``getStart('left')``
  // / ``getAfter('right')`` use ``getSize()``'s 150px default for
  // every column, which made every pinned column past the first
  // render at the wrong sticky offset (off by ~120px).  ResizeObserver
  // re-fires on font load, density change, viewport resize, etc., so
  // the offsets stay accurate as the table reflows.
  const columnId = header.column.id;
  useEffect(() => {
    const el = headerCellRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const width = Math.round(entry.contentRect.width);
        if (width > 0) onMeasureWidth(columnId, width);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [columnId, onMeasureWidth]);

  // Compose the cluster — plain label, sort chevron, 3-dot menu.
  // Filter is reachable only via the 3-dot menu's "Filter…" item;
  // the popover renders below the header cell when opened.
  return (
    <TableHead
      ref={setRefs}
      style={dragStyle}
      data-pin={pinned || undefined}
      data-col={header.column.id}
      className={cn(
        'text-muted-foreground font-medium group relative',
        densityClass,
        // Opaque ``bg-muted`` on pinned + sticky-header cells so they
        // aren't see-through against scrolled body content, AND so
        // the header reads as a distinct surface from body cells
        // (matches the TableRow tint above).
        (stickyHeader || pinned) && 'bg-muted',
        // Vertical separator between columns:
        //   • centre columns get a separator unless they're the last
        //     visible column OR the last centre column before a right-
        //     pinned group (the boundary border lives on the pinned
        //     side, not duplicated here).
        //   • pinned-left columns ONLY draw a border on the LAST one
        //     in the left cluster (the freeze boundary) — internal
        //     ones stay borderless so the cluster reads as one block.
        //   • pinned-right columns mirror the same rule via border-l.
        !pinned && !isLastVisible && !isLastCenterBeforeRightPin && 'border-r border-border/60',
        isLastLeftPinned   && 'border-r border-border/60',
        isFirstRightPinned && 'border-l border-border/60',
      )}
    >
      <div className="flex items-center gap-1">
        {/* Drag-attachment surface — fills the available space so the
            operator has a wide grab target instead of just the label
            text width.  Clicking does nothing; mouse-down + 5px of
            movement starts a column reorder when ``tableId`` is set
            (and the column isn't pinned).  ``touch-none`` disables the
            browser's default touch gestures on this element so
            PointerSensor's drag activation works on touch / stylus
            devices (without it the browser eats the pointerdown to
            scroll). */}
        <span
          {...(dragEnabled ? attributes : {})}
          {...(dragEnabled ? listeners : {})}
          className={cn(
            'flex-1 inline-flex items-center min-w-0',
            dragEnabled && 'touch-none',
          )}
        >
          {labelContent}
        </span>

        {/* 3-dot menu — opt-in via ``tableId``; suppressed for locked
            (structural) columns since none of the menu actions —
            sort, filter, pin, hide — apply to a checkbox column. */}
        {tableId && !isLocked && (
          // ``shrink-0`` — the 3-dot trigger never gives up width; on
          // a squeezed column the LABEL ellipsizes and the menu stays
          // whole instead of overlapping the text.
          <span className="ml-auto shrink-0">
            <ColumnHeaderMenu
              columnLabel={headerText}
              canSort={canSort}
              sorted={sortedRaw === 'asc' || sortedRaw === 'desc' ? sortedRaw : false}
              onSortAsc={() => header.column.toggleSorting(false)}
              onSortDesc={() => header.column.toggleSorting(true)}
              onClearSort={() => header.column.clearSorting()}
              onHide={() => header.column.toggleVisibility(false)}
              onManage={onOpenManage}
              pinned={pinned === 'left' || pinned === 'right' ? pinned : false}
              onPinLeft={() => header.column.pin('left')}
              onPinRight={() => header.column.pin('right')}
              onUnpin={() => header.column.pin(false)}
              canFilter={canFilter}
              filterActive={filterActive}
              onFilter={() => setFilterOpen(true)}
              groupNames={groupNames}
              currentGroup={currentGroup}
              onAssignGroup={onAssignGroup}
              onNewGroup={onNewGroup}
              onUngroup={onUngroup}
              rowGrouped={rowGrouped}
              onRowGroup={onRowGroup}
              onAutosize={onAutosize}
            />
          </span>
        )}
      </div>
      {/* Resize handle — thin grab strip on the header's right edge.
          Drag to resize; double-click to autosize to content.  Only
          on toolkit tables (tableId) since widths persist per-user. */}
      {tableId && header.column.getCanResize() && (
        // Two-layer handle: an 8px INVISIBLE hit area straddling the
        // column boundary (easy to grab), containing a centered
        // hairline that's only visible while hovering the header —
        // and thickens to a 2px primary line on direct hover / during
        // the drag.  A solid full-width bar here read as a blue block
        // welded to the neighbouring column.
        <span
          role="separator"
          aria-orientation="vertical"
          aria-label={`Resize ${headerText} column`}
          onMouseDown={(e) => { e.stopPropagation(); header.getResizeHandler()(e); }}
          onTouchStart={(e) => { e.stopPropagation(); header.getResizeHandler()(e); }}
          onDoubleClick={(e) => { e.stopPropagation(); onAutosize(); }}
          onClick={(e) => e.stopPropagation()}
          className="group/resize absolute -right-1 top-0 z-10 flex h-full w-2 cursor-col-resize justify-center select-none touch-none"
        >
          <span
            aria-hidden="true"
            className={cn(
              'h-full w-px bg-border/60 opacity-0 transition-none',
              'group-hover:opacity-100',
              'group-hover/resize:w-0.5 group-hover/resize:bg-primary group-hover/resize:opacity-100',
              header.column.getIsResizing() && 'w-0.5 bg-primary opacity-100',
            )}
          />
        </span>
      )}
      {canFilter && colConfig?.filterMode === 'range' && rangeBounds ? (
        <ColumnFilterMenu
          mode="range"
          label={headerText}
          bounds={rangeBounds}
          value={rangeFilter}
          onChange={(next) =>
            header.column.setFilterValue(
              next[0] == null && next[1] == null ? undefined : next,
            )
          }
          open={filterOpen}
          onOpenChange={setFilterOpen}
          anchor={headerCellRef.current}
        />
      ) : canFilter && colConfig?.filterMode === 'date-range' ? (
        <ColumnFilterMenu
          mode="date-range"
          label={headerText}
          bounds={dateBounds ?? { min: '', max: '' }}
          value={dateRangeFilter}
          onChange={(next) =>
            header.column.setFilterValue(
              next[0] == null && next[1] == null ? undefined : next,
            )
          }
          open={filterOpen}
          onOpenChange={setFilterOpen}
          anchor={headerCellRef.current}
        />
      ) : canFilter ? (
        <ColumnFilterMenu
          label={headerText}
          options={uniques.options}
          counts={uniques.counts}
          value={filterValueArr}
          onChange={(next) =>
            header.column.setFilterValue(next.length ? next : undefined)
          }
          open={filterOpen}
          onOpenChange={setFilterOpen}
          anchor={headerCellRef.current}
        />
      ) : null}
    </TableHead>
  );
}

// ── Group bracket cell ──────────────────────────────────────
//
// One cell per contiguous run in the bracket row.  Labelled runs are
// draggable (grab the "── LOCATION ──" band to move the whole group);
// unlabelled runs only act as drop targets so a group can land
// between / around ungrouped columns.
function GroupHeaderCell({
  run, dropSide,
}: {
  run: GroupRun;
  /** Insertion-indicator edge when this run is the current drop
   *  target of a group drag; null otherwise. */
  dropSide: 'left' | 'right' | null;
}) {
  const {
    attributes, listeners, setNodeRef, isDragging,
  } = useSortable({
    id: `grp:${run.firstId}`,
    // Unlabelled runs: not draggable themselves, still droppable so
    // groups can be dropped next to them.
    disabled: { draggable: !run.label, droppable: false },
  });
  // No transform on the cell itself — the DragOverlay chip is the
  // moving visual, the source cell just dims.  The insertion bar is
  // an inset box-shadow (2px primary) so it doesn't shift layout.
  const style: React.CSSProperties = {
    ...run.sticky,
    opacity: isDragging ? 0.4 : 1,
    boxShadow:
      dropSide === 'left'  ? 'inset 3px 0 0 0 var(--primary)' :
      dropSide === 'right' ? 'inset -3px 0 0 0 var(--primary)' :
      undefined,
  };
  return (
    <th
      ref={setNodeRef}
      colSpan={run.span}
      style={style}
      className={cn(
        'bg-muted px-2 pt-2 pb-1 align-bottom',
        // Stronger underline beneath the bracket so the group reads
        // as a distinct band above its member columns; light side
        // borders bracket the edges.
        run.label && 'border-b-2 border-border border-x border-border/40',
      )}
    >
      {run.label && (
        // ── LOCATION ── : centered small-caps label with flanking
        // hairlines; the whole band is the drag handle.
        <span
          {...attributes}
          {...listeners}
          className="flex items-center gap-2 touch-none cursor-grab active:cursor-grabbing select-none"
        >
          <span aria-hidden="true" className="flex-1 border-t border-border/60" />
          <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground whitespace-nowrap">
            {run.label}
          </span>
          <span aria-hidden="true" className="flex-1 border-t border-border/60" />
        </span>
      )}
    </th>
  );
}

// ── Body cell, pin-aware ────────────────────────────────────
//
// Mirrors the header's sticky positioning + bg + separator so that
// pinned columns scroll independently of the centre group.  The
// body cell needs the same ``left``/``right`` offsets as its header
// — tanstack's ``column.getStart('left')`` returns the cumulative
// width to the START of the column, which is exactly what
// ``position: sticky`` wants.
function PinnedBodyCell({
  cell, padding, selected, zebra, leadingContent, indent,
}: {
  cell: Cell<Record<string, unknown>, unknown>;
  padding: string;
  selected: boolean;
  zebra: boolean;
  leadingContent?: React.ReactNode;
  /** Nudge content right — used on the first cell of leaf rows when
   *  row grouping is active so children read as nested under their
   *  group header. */
  indent?: boolean;
}) {
  const pinned = cell.column.getIsPinned();
  const pinStyle = pinnedStyle(cell.column, false);
  // Same single-boundary rule as the header — only the LAST left-
  // pinned cell carries the right border, only the FIRST right-pinned
  // cell carries the left border; internal pinned cells stay
  // borderless so the cluster reads as one block.
  const isLastLeftPinned   = pinned === 'left'  && cell.column.getIsLastColumn('left');
  const isFirstRightPinned = pinned === 'right' && cell.column.getIsFirstColumn('right');
  // Pinned cells need a FULLY OPAQUE base so the scrolled centre
  // group doesn't bleed through them when scrolling horizontally —
  // ``bg-muted/30`` (zebra) and ``bg-primary/10`` (selected) are both
  // semi-transparent, so applying them directly leaks centre-cell
  // text under the pinned column.  Solution: ``bg-muted`` is the
  // opaque base (matches the header tint, distinct from body cells),
  // and zebra / selected tints are layered ON TOP via an absolute
  // overlay span (which reads as the same colour as the non-pinned
  // row cells, but with a real backstop underneath).
  const overlayClass =
    selected ? 'bg-primary/10' :
    zebra    ? 'bg-muted/30'    :
               null;
  const needsOverlay = pinned && overlayClass;
  return (
    <TableCell
      style={pinStyle}
      data-col={cell.column.id}
      className={cn(
        padding,
        indent && 'pl-8',
        // In fixed-layout (user-sized) mode cell content must not
        // stretch the column — clip with ellipsis instead.
        'overflow-hidden text-ellipsis',
        pinned && 'bg-muted relative',
        isLastLeftPinned   && 'border-r border-border/60',
        isFirstRightPinned && 'border-l border-border/60',
      )}
    >
      {needsOverlay && (
        <span
          aria-hidden="true"
          className={cn('absolute inset-0 pointer-events-none', overlayClass)}
        />
      )}
      <span className={cn(
        pinned && 'relative',
        // When the parent injects leading content (bulk-select
        // checkbox), lay it out side-by-side with the cell's render
        // output so the row reads as `[☐ 233]`.
        leadingContent && 'inline-flex items-center gap-2',
      )}>
        {leadingContent}
        {flexRender(cell.column.columnDef.cell, cell.getContext())}
      </span>
    </TableCell>
  );
}
