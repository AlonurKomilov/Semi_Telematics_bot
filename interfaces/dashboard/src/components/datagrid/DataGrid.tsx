import { Fragment, useState, useMemo, useEffect, useRef, useCallback, useLayoutEffect, startTransition, type Dispatch, type SetStateAction } from 'react';
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
  CornerUpRight, ListTree, Plus, Pencil, Trash2, Star, Table2, EyeOff,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
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
import { Input } from '../ui/input';
import {
  TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../ui/table';
import { cn } from '../../lib/utils';
import type { AnyColumn, AggFn } from '../../types';
import { AGG_FN_LABELS } from '../../types';
import {
  computeAggregate, formatAggDefault, toAggNumber, toAggTimestamp, offeredAggFns,
} from './aggregation';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import ColumnFilterMenu from './ColumnFilterMenu';
import ColumnHeaderMenu from './ColumnHeaderMenu';
import ManageColumnsMenu from './ManageColumnsMenu';
import {
  type SavedTab, rowPassesColFilter, tabMatch,
} from './tabs/savedTabs';
import { rowMatchesSearch as matchesSearch, searchProvenance } from './search';
import SavedTabDialog from './tabs/SavedTabDialog';
import { TAB_ICONS } from './tabs/tabIcons';
import { toneClasses, type Tone } from '../../lib/status';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../ui/select';
import { Button } from '../ui/button';
import { ContextMenu, type MenuAction } from '../ui/context-menu';
import { Tip } from '../tooltip';
import { toast } from 'sonner';
import {
  exportRowsAsCsv, buildTsv, writeToClipboard, buildCsvFromRows, downloadCsv,
} from '../../lib/csv';
import { usePreference, useTablePreference, useSyncLoaded } from '../../preferences';
import PivotView from './pivot/PivotView';
import PivotPanel from './pivot/PivotPanel';
import { prunePivotModel, pivot, pivotToCsvRows, type PivotModel } from './pivot/pivot';
import {
  useOverflow, ScrollbarH, ScrollbarV, HIDE_NATIVE_SCROLLBAR, useFittedHeight,
  useScrollRegion,
} from '../scrolling';
import { derivePivotDimensions } from './pivot/derived';

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
// Synthetic id for the dedicated bulk-select column.  Double-underscore
// so it can never collide with a real data key.  It's a genuine (locked,
// force-pinned-left, non-resizable) tanstack column rather than a
// checkbox riding inside the first data cell — the select box gets its
// own narrow column, and tanstack computes its sticky offset for free.
const SELECT_COL_ID = '__select__';

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
  /** Prompt before running; receives the selection count.  Return an
   *  EMPTY string to skip the prompt at that count — gate on scope so
   *  routine work isn't interrupted and only a large, probably-accidental
   *  selection is questioned.  Omit entirely for actions that never
   *  confirm. */
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
  /** Per-row RIGHT-CLICK actions.  Return the ``MenuAction[]`` for a given
   *  row (the domain object) and the grid wraps that row in a context menu
   *  — Open / Edit / Delete etc.  Return ``[]`` to give a row no menu.
   *  Additive: left-click / inline buttons are untouched.  Pages gate the
   *  actions by permission themselves (return fewer items when read-only). */
  rowActions?: (row: Record<string, unknown>) => MenuAction[];
  /** Enable PIVOT mode — a toolbar toggle that swaps the record list for
   *  a cross-tab report (rows x columns x aggregated values).
   *
   *  Requires ``tableId`` (the model persists per table) and a
   *  CLIENT-COMPLETE dataset: pivot aggregates the rows the grid holds,
   *  so on a server-paged grid it would summarise one page and present
   *  it as the whole truth.  Mark dimensions with ``pivotable`` and
   *  measures with ``aggregable`` on the column config. */
  pivot?: boolean;
  searchKey?: string | string[];
  /** Fixed max-height for the table body (any CSS length, e.g. ``"65vh"``).
   *  Prefer ``fillHeight`` — this is the hand-tuned version, kept for a
   *  grid that must be shorter than the space available to it. */
  stickyHeader?: string;
  /** The grid owns a VIEWPORT instead of growing to fit its rows: the
   *  body scrolls inside the card with a sticky header, while the
   *  toolbar, the horizontal scrollbar and the pagination footer stay
   *  put at the card's edges.
   *
   *  Without it, "rows per page: 250" makes the card 250 rows TALL and
   *  hands the scrolling to the page — which pushes four controls out
   *  of reach at once: the column headers scroll away (leaving unlabelled
   *  columns), the custom horizontal scrollbar rides the bottom of the
   *  table thousands of pixels down, the bulk-action bar sits above the
   *  rows it acts on, and pagination lands past the last row.
   *
   *  No measurement and no magic height — the app shell is already
   *  ``h-screen overflow-hidden`` with ONE scroll region, so this is
   *  pure flexbox. **The parent must be a flex column with a definite
   *  height**: give the page root ``h-full flex flex-col min-h-0`` and
   *  this grid becomes the child that takes the remainder. Used on a
   *  page that isn't laid out that way, the grid keeps its natural
   *  height and nothing breaks. */
  /** @deprecated NO-OP — the grid measures its own room now (see
   *  ``useFittedHeight``).  Kept so the pages that passed it don't
   *  break; delete the prop from a page whenever you touch it. */
  fillHeight?: boolean;
  /** Opt OUT of the measured viewport.  For a table whose surface is
   *  what the reader scrolls — a table inside a chat message, where an
   *  internally-scrolling grid would trap the conversation. */
  autoFit?: boolean;
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
  /** Aggregation the table starts with — a ``{ columnKey: fn }`` map
   *  rendered as a footer total row (until the operator changes it via
   *  the column ⋮ menu; their choice persists per-user and wins).
   *  Only affects columns marked ``aggregable``.  Reset-to-defaults
   *  returns to this. */
  defaultAggregation?: Record<string, AggFn>;
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
  /** What the body says when there are no rows AND no filter/search is
   *  active — i.e. the dataset itself is empty. Filtered-to-nothing and
   *  search-miss render their own copy plus a clear action, so this is
   *  only ever the genuine starting state. Defaults to "No data". */
  emptyMessage?: React.ReactNode;
  segments?: DataGridSegment[];
  /** Enable user-managed saved tabs — personal tabs an operator
   *  builds from the current filters (a "+ New tab" affordance beside
   *  the tabs).  Each tab applies as an ISOLATED scope, exactly like a
   *  built-in segment, and persists per-user (``table.<id>.views``).
   *  This is the SECOND kind of tab: unlike ``segments`` (code-defined,
   *  account-wide) these are user-defined and per-user — see the
   *  two-kinds note atop savedTabs.ts.  Requires ``tableId``.  Saved
   *  tabs sit AFTER any built-in ``segments``; on a grid with no
   *  segments an implicit "All" tab leads.  A tab saved while on a
   *  built-in segment (Active) COMPOSES with it, so it scopes within
   *  that lifecycle slice, not across all of them. */
  savedTabs?: boolean;

  // ── Controlled view-state (opt-in) ───────────────────────────────
  //
  // Every prop below is OPTIONAL and defaults to the grid owning the
  // state itself, exactly as before.  They exist for one case: a page
  // whose data is larger than one fetch, where filtering has to happen
  // on the SERVER.  A grid only ever sees the rows it was handed, so a
  // client-side filter over a capped page silently disagrees with the
  // real total — it narrows 2,000 loaded rows and reports that as the
  // answer for 4,000.  Handing the page control lets the filter UI stay
  // here (one surface, column menus, saved tabs) while the actual
  // narrowing happens where the whole set lives.

  /** Controlled column filters.  Supply with ``onColumnFiltersChange``;
   *  omit both to let the grid keep its own.  Don't switch between
   *  controlled and uncontrolled after mount. */
  columnFilters?: ColumnFiltersState;
  /** Called with the NEXT filter state whenever the grid would change it
   *  (column menu, a removed chip, "clear all"). */
  onColumnFiltersChange?: (next: ColumnFiltersState) => void;
  /** Filtering already happened upstream, so don't filter the rows
   *  again here.  Controlled filters alone do NOT imply this: a page may
   *  control them just to mirror them into the URL while the grid still
   *  does the work. */
  manualFiltering?: boolean;
  /** Controlled sort state.  Supply with ``onSortingChange``. */
  sorting?: SortingState;
  onSortingChange?: (next: SortingState) => void;
  /** The rows arrived already sorted (the page put the order in its
   *  query).  Like ``manualFiltering``: being controlled alone does not
   *  imply it — a page may mirror sort into the URL and still want the
   *  grid to do the work.  With this set, sorting a slice is CORRECT, so
   *  the partial-data guard stops gating it. */
  manualSorting?: boolean;
  /** Controlled pagination — the page fetches one page at a time. */
  pageIndex?: number;
  pageSize?: number;
  onPaginationChange?: (next: { pageIndex: number; pageSize: number }) => void;
  /** Total pages behind the grid; required with ``manualPagination`` so
   *  the pager can count pages it has never seen. */
  pageCount?: number;
  manualPagination?: boolean;
  /** Export EVERY row behind the grid, not the ones it holds.
   *
   *  Without it, "All rows" writes what the grid has — which on a
   *  server-paginated grid is one page, in a file the operator will read
   *  as the whole result.  A page that can fetch the full set from its
   *  own source provides this and owns the download; the menu then says
   *  how many rows that really is. */
  onExportAllRows?: () => void | Promise<void>;
  /** The TRUE number of rows behind this grid, when the page hands it
   *  only a slice (a server-capped page of a larger result set).
   *
   *  Without it the grid assumes the rows it holds ARE the result, and
   *  every whole-set operation quietly answers for the whole from a
   *  part: sorting orders 2,000 of 11,200 and calls it sorted, grouping
   *  groups a fragment, "export all rows" writes a file named -all
   *  containing 18% of the data.  Told the truth, the grid disables
   *  those with a reason instead — and says "loaded" where it means it.
   *  Omit on any grid that holds its whole dataset. */
  totalRows?: number;
  /** Controlled search text.  Supply with ``onGlobalFilterChange``; omit
   *  both to let the grid keep its own.  Under ``manualFiltering`` the
   *  grid stops applying it to rows — the page searched already. */
  globalFilter?: string;
  onGlobalFilterChange?: (next: string) => void;

  /** Controlled segment/tab selection (the key of the active tab).
   *  Don't switch a grid between controlled and uncontrolled after
   *  mount — the internal fallback state goes stale while controlled. */
  segmentKey?: string;
  /** Called with the key of the tab the operator picked.  For a SAVED
   *  tab the second argument carries what that tab actually selects, so
   *  a server-filtered page can put it in the query — the key alone is
   *  an opaque id and the tab would appear to do nothing. */
  onSegmentChange?: (
    key: string,
    tab?: {
      filters: ColumnFiltersState;
      search: string;
      /** The built-in segment the tab was SAVED under, if any.  A tab is
       *  a scope WITHIN a lifecycle slice ("my critical faults, among the
       *  un-acknowledged"), so a server-filtered page has to restore that
       *  slice as well as the filters or the tab widens silently. */
      baseSegment?: string;
      /** The tab's captured ORDER.  Handed over with everything else so a
       *  controlled page can apply the whole tab in ONE write — otherwise
       *  the sort arrives via a separate effect and the tab costs two
       *  queries and two history entries. */
      sort?: SortingState;
    },
  ) => void;
  /** Authoritative per-segment counts, keyed by segment key.  Without
   *  this the badge counts LOADED rows, which on a server-filtered grid
   *  prints a confidently wrong number in the most prominent place on
   *  the page.  Keys left out fall back to the local tally. */
  segmentCounts?: Record<string, number>;
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
// NOTE: the ``.views`` / ``.defaultView`` suffixes are the ORIGINAL
// stored key names, deliberately preserved through the view→tab rename
// — renaming them would orphan every user's saved tabs.  Don't "fix".
const tabsKey      = (id: string | undefined) =>
  id ? `table.${id}.views` : '';
const defaultTabKey = (id: string | undefined) =>
  id ? `table.${id}.defaultView` : '';
// Saved-tab segment keys are prefixed so a saved tab is never confused
// with a code-defined segment.  ``__all__`` is the implicit "everything"
// tab shown when a grid has no built-in segments.
/** A saved tab's segment key is this + the tab id.  EXPORTED because a
 *  page that controls its segment has to recognise one coming back
 *  through ``onSegmentChange`` and hand the same key in as
 *  ``segmentKey`` — spelling it by hand on both sides of that round
 *  trip is a silent break waiting for someone to change the prefix. */
export const TAB_PREFIX = 'tab:';
const ALL_KEY = '__all__';
const NO_TABS: SavedTab[] = [];
const aggregationKey = (id: string | undefined) =>
  id ? `table.${id}.aggregation` : '';
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
  /** Personal-tab customization carried through to the strip render:
   *  ``tone`` colours the count badge, ``iconKey`` is a leading icon. */
  tone?: Tone;
  iconKey?: string;
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
  label, count, showCount, active, onClick, dot, iconKey, countTone, manageable,
}: {
  label: string;
  count: number;
  showCount: boolean;
  active: boolean;
  onClick: () => void;
  /** Personal tab: advertises the Shift+F10 / Menu-key path to its
   *  right-click management menu for keyboard + AT users. */
  manageable?: boolean;
  /** Small accent dot before the label — marks a personal saved tab.
   *  Superseded by ``iconKey`` when the tab has a chosen icon. */
  dot?: boolean;
  /** Optional leading lucide icon (personal-tab customization); when set
   *  it renders in place of the dot, BEFORE the label. */
  iconKey?: string;
  /** Optional tone colouring the COUNT badge only (not the whole tab). */
  countTone?: Tone;
}) {
  const LeadIcon = iconKey ? TAB_ICONS[iconKey] : undefined;
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
      aria-keyshortcuts={manageable ? 'Shift+F10' : undefined}
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
        {LeadIcon ? (
          // The tab's chosen icon leads the label (neutral-coloured — only
          // the count badge takes a tone).  Takes the dot's place.
          <LeadIcon size={14} className={cn('shrink-0', active ? 'text-foreground' : 'text-muted-foreground')} />
        ) : dot ? (
          <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
        ) : null}
        {label}
        {showCount && (
          <span
            className={cn(
              'tabular-nums text-2xs px-1.5 py-0.5 rounded-full',
              // A chosen tone colours the number only; otherwise the count
              // follows the active/inactive default.
              countTone
                ? toneClasses(countTone)
                : active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
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

export default function DataGrid({
  columns, data: sourceData, onRowClick, rowActions, searchKey, stickyHeader, autoFit = true, searchPlaceholder,
  pivot: pivotEnabled = false,
  headerToolbar, tableId, firstColumnLeading, rowGroupHeader, defaultRowGroup,
  defaultAggregation,
  enableToolbar = true, enablePagination = true, segments, emptyMessage,
  savedTabs: savedTabsEnabled = false,
  bulkSelection = false, onBulkSelectionChange, bulkActions, bulkRowLabel,
  isRowSelectable, selectedIds: controlledSelectedIds, onSelectedIdsChange,
  columnFilters: controlledColumnFilters, onColumnFiltersChange,
  globalFilter: controlledGlobalFilter, onGlobalFilterChange,
  totalRows, onExportAllRows,
  sorting: controlledSorting, onSortingChange, manualSorting = false,
  pageIndex: controlledPageIndex, pageSize: controlledPageSize,
  onPaginationChange, pageCount: controlledPageCount, manualPagination = false,
  manualFiltering = false,
  segmentKey: controlledSegmentKey, onSegmentChange, segmentCounts: serverSegmentCounts,
}: DataGridProps) {
  const { t } = useTranslation();
  // Account timezone — used to format ``aggType: 'date'`` aggregates
  // (earliest / latest) in the footer + group rows per the dashboard's
  // date SSOT.  DataGrid only ever renders inside the authed dashboard,
  // so useTimezone → useAuth is always inside a provider here.
  const timeZone = useTimezone();

  // A grid holding only a SLICE can't answer for the whole set — pivot,
  // and the export scope label, both key off this.
  const holdsPartialData = totalRows !== undefined && totalRows > sourceData.length;

  // ── Pivot ──────────────────────────────────────────────────────────
  // The model persists per table; ``enabled`` lives inside it so turning
  // pivot off keeps the configuration for next time.  The PANEL's
  // open/closed state is session-only — it's a configuration surface, not
  // a preference.
  const { value: pivotPref, setValue: setPivotPref } =
    useTablePreference(pivotEnabled ? tableId : undefined, 'pivot');
  // Date columns become Year / Quarter / Month dimensions automatically —
  // a raw timestamp is a useless bucket (one column per row).  Synthetic:
  // these exist only for the pivot pickers, never in the grid's columns.
  const pivotColumns = useMemo(
    () => (pivotEnabled ? derivePivotDimensions(columns, timeZone) : columns),
    [pivotEnabled, columns, timeZone],
  );
  const [pivotPanelOpen, setPivotPanelOpen] = useState(false);
  // Reported up by PivotView so the count can live in the card's own
  // footer band rather than inside the matrix column.
  const [pivotRowCount, setPivotRowCount] = useState(0);
  // Reported so the footer can name what was pruned.  Hiding columns
  // without saying how many would be the grid answering for data it
  // decided not to show.
  const [pivotHiddenCols, setPivotHiddenCols] = useState(0);
  // Device-scoped, NOT per-table: how you like the panel/report split is
  // a habit about this screen, not a property of one grid.
  const { value: pivotPanelWidth, setValue: setPivotPanelWidth } =
    usePreference('pivot.panelWidth');
  const pivotModel = useMemo<PivotModel>(() => {
    const stored = pivotPref?.model;
    // SPREAD, never a hand-written field list.  This rebuild is half of
    // the persistence path (``prunePivotModel`` is the other half), and
    // listing fields by hand meant every model field had to be added in
    // two places or it was silently dropped between them — the stored
    // value went in, an undefined came out, and the prune's ``?? false``
    // turned that into a default.  It killed ``sort`` once, then
    // ``hideEmptyColumns`` / ``pinRowLabels`` / ``pinTotals`` together:
    // three checkboxes that wrote themselves correctly and did nothing.
    // TypeScript cannot catch it (every such field is optional), so the
    // fix is structural — spreading carries fields this line has never
    // heard of, including the next one somebody adds.
    // ``DataGrid.pivotModel.test.tsx`` goes red if this regresses.
    const base: PivotModel = stored
      ? {
          ...stored,
          rows: stored.rows ?? [], columns: stored.columns ?? [],
          values: stored.values ?? [],
        }
      : { rows: [], columns: [], values: [], sort: null, disabled: [] };
    // A saved model can name columns this grid no longer has.
    return prunePivotModel(base, pivotColumns);
  }, [pivotPref, pivotColumns]);
  // A pivot is an aggregate presented as an answer, and it aggregates
  // the rows the grid HOLDS.  Over a slice it would summarise 2,000 of
  // 11,200 and print totals that look authoritative — the same defect as
  // a sorted fragment, but harder to spot because a cross-tab shows no
  // rows to count.  So it stays off entirely while the data is partial;
  // the preference survives, and it returns once the view is narrowed.
  const pivotOn = pivotEnabled && !!pivotPref?.enabled && !holdsPartialData;
  // Read inside setPivotModel, whose identity must stay stable (it is
  // handed to PivotView/PivotPanel on every render).
  const pivotPrefRef = useRef(pivotPref);
  pivotPrefRef.current = pivotPref;
  // Configuring does NOT activate.  This used to force ``enabled: true``,
  // so ticking any field flipped the master switch with no announcement —
  // which was defensible when a click on the toolbar button was the only
  // way in, and is plainly wrong now that a switch sits at the top of the
  // panel saying it owns that decision.  Setting a field while pivot is
  // off now just builds the report you'll turn on when you're ready.
  const setPivotModel = useCallback((model: PivotModel) => {
    // A model change rebuilds the whole cross-tab, which on a wide report
    // is a long synchronous render.  As a TRANSITION it stops blocking
    // input, so the checkbox you clicked responds immediately instead of
    // appearing stuck until the matrix has finished.
    startTransition(() => {
      setPivotPref({ enabled: !!pivotPrefRef.current?.enabled, model });
    });
  }, [setPivotPref]);
  const setPivotEnabled = useCallback((next: boolean) => {
    // A STARTER model on first enable: flipping the switch should
    // produce a report, not an empty state the operator must then
    // configure three times.  First pivotable dimension x first
    // aggregable measure is the obvious summary (Loads -> Customer x
    // Rate), and it lands next to the pickers that shaped it, so it
    // reads as a suggestion to refine rather than a decision made for
    // them.
    let model = pivotModel;
    if (next && model.rows.length === 0 && model.values.length === 0) {
      const firstDim = pivotColumns.find((c) => c.pivotable);
      const firstMeasure = pivotColumns.find((c) => c.aggregable);
      if (firstDim && firstMeasure) {
        model = {
          rows: [firstDim.key],
          columns: [],
          values: [{ key: firstMeasure.key, aggFn: offeredAggFns(firstMeasure)[0] ?? 'sum' }],
        };
      }
    }
    setPivotPref({ enabled: next, model });
  }, [pivotModel, pivotColumns, setPivotPref]);

  const [ownSorting, setOwnSorting] = useState<SortingState>([]);
  const sortingControlled = controlledSorting !== undefined;
  const sorting = controlledSorting ?? ownSorting;
  const sortingRef = useRef(sorting);
  sortingRef.current = sorting;
  const onSortingChangeRef = useRef(onSortingChange);
  onSortingChangeRef.current = onSortingChange;
  const setSorting = useCallback<Dispatch<SetStateAction<SortingState>>>(
    (updater) => {
      const next = typeof updater === 'function'
        ? (updater as (prev: SortingState) => SortingState)(sortingRef.current)
        : updater;
      sortingRef.current = next;
      if (!sortingControlled) setOwnSorting(next);
      onSortingChangeRef.current?.(next);
    },
    [sortingControlled],
  );
  // True when the page told us the real total and we hold less than it.
  // (``holdsPartialData`` is declared above — pivot's guard needs it.)
  // ...but an operation that runs UPSTREAM is correct on a slice.  Sorting
  // 25 server-ordered rows of 11,200 is honest; sorting 25 rows locally
  // and calling it sorted is not.  Only the latter gets gated.
  const gateClientSideOps = holdsPartialData && !manualSorting;
  // Says WHY, and names BOTH ways out.  "Narrow the view first" alone was
  // the wrong advice on page 1 of 160: getting under one page by filtering
  // is a big ask, and raising Rows per page is the other route — omitting
  // it left the operator with a dead end that looked like their fault.
  const gateReason = holdsPartialData
    ? `This works over the rows the table holds — ${sourceData.length.toLocaleString()} of `
      + `${totalRows!.toLocaleString()}. Narrow the view, or raise Rows per page, to cover them all.`
    : undefined;
  // Search — grid-owned unless the page supplies it.  Same dual-mode
  // shape as the filters; on a server-filtered grid the page drives this
  // into its query, so the box searches the whole set rather than the
  // page's slice of it.
  const [ownGlobalFilter, setOwnGlobalFilter] = useState('');
  const globalFilterControlled = controlledGlobalFilter !== undefined;
  const globalFilter = controlledGlobalFilter ?? ownGlobalFilter;
  const globalFilterRef = useRef(globalFilter);
  globalFilterRef.current = globalFilter;
  const setGlobalFilter = useCallback<Dispatch<SetStateAction<string>>>(
    (updater) => {
      const next = typeof updater === 'function'
        ? (updater as (prev: string) => string)(globalFilterRef.current)
        : updater;
      globalFilterRef.current = next;
      if (!globalFilterControlled) setOwnGlobalFilter(next);
      onGlobalFilterChangeRef.current?.(next);
    },
    [globalFilterControlled],
  );
  // Callback refs so the setters below keep a STABLE identity: they feed
  // tanstack's table options and several dependency arrays, and a page
  // passing an inline arrow would otherwise re-create them every render.
  const onColumnFiltersChangeRef = useRef(onColumnFiltersChange);
  onColumnFiltersChangeRef.current = onColumnFiltersChange;
  const onSegmentChangeRef = useRef(onSegmentChange);
  onSegmentChangeRef.current = onSegmentChange;
  const onGlobalFilterChangeRef = useRef(onGlobalFilterChange);
  onGlobalFilterChangeRef.current = onGlobalFilterChange;
  const onPaginationChangeRef = useRef(onPaginationChange);
  onPaginationChangeRef.current = onPaginationChange;
  // Read inside memoised column definitions, so flipping the flag can't
  // require rebuilding every column def.
  const manualFilteringRef = useRef(manualFiltering);
  manualFilteringRef.current = manualFiltering;
  // Filled below, once the persisted tabs have loaded — read at call
  // time by setSegmentPref, so declaration order doesn't matter.
  const savedTabListRef = useRef<SavedTab[]>([]);

  // Column filters — grid-owned unless the page supplies them.  The
  // setter keeps a ``Dispatch<SetStateAction>`` shape in both modes so
  // every existing call site (chip removal, "clear all", the column
  // menus) is untouched, including the ones that pass an updater fn.
  const [ownColumnFilters, setOwnColumnFilters] = useState<ColumnFiltersState>([]);
  const columnFiltersControlled = controlledColumnFilters !== undefined;
  const columnFilters = controlledColumnFilters ?? ownColumnFilters;
  // Read through a ref inside the setter: a controlled parent may batch
  // several updates before re-rendering us, and a functional updater must
  // see the latest value rather than the one captured at render.
  const columnFiltersRef = useRef(columnFilters);
  columnFiltersRef.current = columnFilters;
  const setColumnFilters = useCallback<Dispatch<SetStateAction<ColumnFiltersState>>>(
    (updater) => {
      const next = typeof updater === 'function'
        ? (updater as (prev: ColumnFiltersState) => ColumnFiltersState)(columnFiltersRef.current)
        : updater;
      // Advance the ref NOW so two calls in one synchronous batch chain
      // off each other, the way a plain useState updater would.
      columnFiltersRef.current = next;
      if (!columnFiltersControlled) setOwnColumnFilters(next);
      onColumnFiltersChangeRef.current?.(next);
    },
    [columnFiltersControlled],
  );
  // Density is a personal reading preference — deliberately GLOBAL
  // across every table (one key, no tableId namespace) and synced
  // server-side so it follows the operator across devices like the
  // rest of the layout preferences.  Guarded against corrupt stored
  // values so a bad payload can't strand the table without padding.
  const {
    value: densityPref,
    setValue: setDensity,
  } = usePreference('table.density');
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
  // ``searchKeys`` lives up here (ahead of the table) because a saved
  // tab's scope predicate matches on the SAME global-search keys the
  // live grid uses.
  const searchKeys = useMemo(() => {
    if (!searchKey) return [];
    return Array.isArray(searchKey) ? searchKey : [searchKey];
  }, [searchKey]);
  const hasSearch = searchKeys.length > 0;

  // Does this row match the search box?  One rule, defined once in
  // ``savedTabs`` (pure + tested) and used by the live filter, by a
  // saved tab's captured search, and by the filter-option counts.
  const rowMatchesSearch = useCallback((
    row: Record<string, unknown>, needle: string,
  ): boolean => matchesSearch(row, searchKeys, needle, columns),
  [columns, searchKeys]);

  // Personal saved tabs — persisted per-user (no-op store when the
  // feature or tableId is off).  Each becomes a SEGMENT whose ``match``
  // is the tab's captured filters, so it flows through the identical
  // scoping (``sourceData.filter(match)``), counting, and tab rendering
  // as a code-defined segment — isolated, no cross-tab leak, for free.
  const {
    value: savedTabList,
    setValue: setSavedTabList,
  } = useTablePreference(savedTabsEnabled ? tableId : undefined, 'views', NO_TABS);
  // One-time coach-mark: after an operator makes their FIRST personal tab,
  // teach right-click management (there's no ⋮ button).  Global per-user
  // flag so it fires once across every grid, not once per table.
  const { value: tabCoachSeen, setValue: setTabCoachSeen } =
    usePreference('datagrid.savedTabCoachSeen');
  const tabSegments = useMemo<DataGridSegment[]>(() => {
    if (!savedTabsEnabled) return [];
    return savedTabList.map(v => {
      const own = tabMatch(v, columns, searchKeys);
      // Compose with the segment the tab was captured under, if it still
      // exists — so the tab stays inside that lifecycle scope (a stale
      // baseSegment simply drops to the tab's own filters).
      const base = v.baseSegment
        ? (segments ?? []).find(s => s.key === v.baseSegment)?.match
        : undefined;
      return {
        key: TAB_PREFIX + v.id,
        label: v.name,
        // Under ``manualFiltering`` the tab's criteria go to the SERVER
        // (they ride along on onSegmentChange) — applying them here too
        // would narrow an already-narrowed page and report that as the
        // answer, the exact defect this whole feature exists to prevent.
        match: manualFiltering
          ? undefined
          : (base ? (row) => base(row) && own(row) : own),
        tone: v.tone,
        iconKey: v.icon,
      };
    });
  }, [savedTabsEnabled, savedTabList, columns, searchKeys, segments, manualFiltering]);
  savedTabListRef.current = savedTabList;
  const effectiveSegments = useMemo<DataGridSegment[]>(() => {
    const builtIn = segments ?? [];
    if (!savedTabsEnabled) return builtIn;
    // No built-in segments → an implicit "All" tab leads so the operator
    // can always leave a tab and see the full set again.
    // "All rows", not "All": pages commonly carry their OWN status tab
    // labelled "All" (Loads does), and two tabs one row apart reading
    // the same word label different things — one scopes the QUERY, this
    // one scopes the saved-tab strip, and with a status filter active
    // this one covers a filtered set.
    const base = builtIn.length ? builtIn : [{ key: ALL_KEY, label: 'All rows' }];
    return [...base, ...tabSegments];
  }, [segments, savedTabsEnabled, tabSegments]);

  // Active tab — grid-owned unless the page supplies it.  Same
  // dual-mode shape as the filters above; line 741's functional update
  // (a deleted tab falling back) relies on the updater form working.
  const [ownSegmentPref, setOwnSegmentPref] = useState<string>(effectiveSegments[0]?.key ?? '');
  const segmentControlled = controlledSegmentKey !== undefined;
  const segmentPref = controlledSegmentKey ?? ownSegmentPref;
  const segmentPrefRef = useRef(segmentPref);
  segmentPrefRef.current = segmentPref;
  const setSegmentPref = useCallback<Dispatch<SetStateAction<string>>>(
    (updater) => {
      const next = typeof updater === 'function'
        ? (updater as (prev: string) => string)(segmentPrefRef.current)
        : updater;
      segmentPrefRef.current = next;
      if (!segmentControlled) setOwnSegmentPref(next);
      // A saved tab IS a filter set.  A controlled page needs its
      // criteria, not just its id, or it can't put them in the query —
      // the key alone is opaque and the tab would do nothing.
      const tab = next.startsWith(TAB_PREFIX)
        ? savedTabListRef.current.find(v => TAB_PREFIX + v.id === next)
        : undefined;
      onSegmentChangeRef.current?.(
        next,
        tab
          ? {
            filters: tab.filters,
            search: tab.search ?? '',
            baseSegment: tab.baseSegment,
            sort: tab.sort,
          }
          : undefined,
      );
    },
    [segmentControlled],
  );
  const activeSegment = useMemo(() => {
    if (!effectiveSegments.length) return null;
    // Selected key may reference a tab that no longer exists (a tab was
    // deleted, config changed) — fall back to the first.
    return effectiveSegments.find(s => s.key === segmentPref) ?? effectiveSegments[0];
  }, [effectiveSegments, segmentPref]);

  // A tab can be the DEFAULT (opens on load).  Stored as the tab
  // id per-user; applied once, after the tabs have loaded.
  const {
    value: defaultTab,
    setValue: setDefaultTab,
  } = useTablePreference(savedTabsEnabled ? tableId : undefined, 'defaultView');
  // Both saved-tab prefs are 'synced', so their authoritative values only
  // exist once the account's bulk read has landed.  One store-wide signal
  // replaces the two per-key `hydrated` flags this used to await.
  const prefsLoaded = useSyncLoaded();
  const appliedDefault = useRef(false);
  useEffect(() => {
    if (appliedDefault.current) return;
    // Wait for the account's copy to land — on a fresh device the pref is
    // '' until the bulk read resolves; deciding "no default" before then
    // would permanently skip applying it.  (Resolves immediately when
    // syncing is off, and even if the read FAILS, so this can't hang.)
    if (!prefsLoaded) return;
    // A CONTROLLED grid's tab is the page's to choose.  Auto-applying a
    // stored default here would call onSegmentChange on mount and
    // silently discard the value the page passed in — a controlled prop
    // that the child overrules isn't controlled.
    if (segmentControlled) { appliedDefault.current = true; return; }
    if (!defaultTab) { appliedDefault.current = true; return; }
    const key = TAB_PREFIX + defaultTab;
    if (effectiveSegments.some(s => s.key === key)) {
      setSegmentPref(key);
    }
    // Whether or not the (possibly-deleted) default tab resolved, the
    // one-shot is spent once the preferences have loaded.
    appliedDefault.current = true;
  }, [prefsLoaded, defaultTab, effectiveSegments, segmentControlled]);

  // Applying a tab's captured SORT when it becomes the active tab (click
  // or default-on-load).  Keyed only on the active TAB id, so it fires
  // when you SWITCH tabs — not while you re-sort within one — and never
  // for a built-in tab.  A tab with no captured sort leaves sort as-is.
  const activeTabId = activeSegment?.key.startsWith(TAB_PREFIX)
    ? activeSegment.key.slice(TAB_PREFIX.length) : null;
  useEffect(() => {
    if (!activeTabId) return;
    // A page that controls BOTH the sort and the segment receives the
    // tab's sort in onSegmentChange and applies it with the rest of the
    // tab.  Restoring it here as well would issue a second query and a
    // second history entry for one click.
    if (sortingControlled && segmentControlled) return;
    const v = savedTabList.find(x => x.id === activeTabId);
    if (v?.sort) setSorting(v.sort);
    // A tab saved before pivot existed carries none — leave the current
    // report alone rather than silently switching it off.
    if (pivotEnabled && v?.pivot) {
      setPivotPref({ enabled: v.pivot.enabled, model: v.pivot.model });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId]);
  const segmentCounts = useMemo(() => {
    if (!effectiveSegments.length) return {};
    const counts: Record<string, number> = {};
    for (const seg of effectiveSegments) {
      // A server-supplied count WINS.  On a grid whose rows are a capped
      // page of a larger set, tallying loaded rows would print a number
      // that looks authoritative and isn't — and a tab badge is the most
      // prominent number on the page.
      const fromServer = serverSegmentCounts?.[seg.key];
      if (fromServer !== undefined) { counts[seg.key] = fromServer; continue; }
      counts[seg.key] = seg.match
        ? sourceData.filter(seg.match).length
        : sourceData.length;
    }
    return counts;
  }, [effectiveSegments, sourceData, serverSegmentCounts]);
  const data = useMemo(() => {
    if (!activeSegment?.match) return sourceData;
    return sourceData.filter(activeSegment.match);
  }, [sourceData, activeSegment]);

  // ── saved-tab CRUD ───────────────────────────────────────────────
  // The New / Edit dialog owns the name + filter picking; these just
  // persist what it returns.  ``tabDialog`` = null (closed), 'new', or
  // the tab being edited.
  const [tabDialog, setTabDialog] = useState<SavedTab | 'new' | null>(null);
  const commitTab = useCallback((
    name: string, filters: ColumnFiltersState, search: string,
    tone?: Tone, icon?: string,
  ) => {
    // The live ``sorting`` belongs to the ACTIVE tab.  Grouping + column
    // layout stay the grid's global per-user settings; a tab doesn't
    // touch them.
    const liveSort = sorting.length ? sorting : undefined;
    if (tabDialog === 'new') {
      const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
      // Compose with the built-in segment we're inside (Active/Archive) —
      // not the implicit "All" tab or another saved tab.
      const cur = segmentPref;
      const baseSegment = cur && cur !== ALL_KEY && !cur.startsWith(TAB_PREFIX)
        ? cur : undefined;
      setSavedTabList(prev => [...prev, {
        id, name, filters, search: search || undefined, sort: liveSort, baseSegment,
        pivot: pivotEnabled ? { enabled: pivotOn, model: pivotModel } : undefined,
        tone, icon,
      }]);
      setSegmentPref(TAB_PREFIX + id);
      // First personal tab ever → teach the (icon-less) right-click menu.
      if (!tabCoachSeen) {
        toast('Tip: right-click a tab to rename, recolor, or delete it.', { duration: 6000 });
        setTabCoachSeen(true);
      }
    } else if (tabDialog) {
      const editId = tabDialog.id;
      // Only re-capture the live sort when editing the tab you're
      // actually ON — otherwise ``sorting`` is some OTHER tab's sort and
      // would stomp this tab's saved one.  Keep its own sort otherwise.
      const sort = editId === activeTabId ? liveSort : tabDialog.sort;
      // Same rule as sort: only RE-capture the live pivot when editing the
      // tab you're actually on — otherwise you'd stamp this tab with some
      // other tab's report.
      const pivotSnap = !pivotEnabled
        ? tabDialog.pivot
        : editId === activeTabId
          ? { enabled: pivotOn, model: pivotModel }
          : tabDialog.pivot;
      setSavedTabList(prev => prev.map(v => (
        v.id === editId ? { ...v, name, filters, search: search || undefined, sort, tone, icon, pivot: pivotSnap } : v
      )));
    }
  }, [tabDialog, segmentPref, setSavedTabList, sorting, activeTabId, tabCoachSeen,
      setTabCoachSeen, pivotEnabled, pivotOn, pivotModel]);
  const deleteTab = useCallback((id: string) => {
    // Capture the tab + its position BEFORE removal so Undo can restore it
    // in place — a saved tab took effort (name, filters, colour, icon), so
    // a mis-click shouldn't wipe it with no recourse.
    const idx = savedTabList.findIndex(v => v.id === id);
    const removed = idx >= 0 ? savedTabList[idx] : undefined;
    const wasDefault = defaultTab === id;
    setSavedTabList(prev => prev.filter(v => v.id !== id));
    setSegmentPref(prev => (prev === TAB_PREFIX + id ? (segments?.[0]?.key ?? ALL_KEY) : prev));
    setDefaultTab(prev => (prev === id ? '' : prev));   // don't leave a dangling default
    if (!removed) return;
    toast(`Deleted "${removed.name}"`, {
      action: {
        label: 'Undo',
        onClick: () => {
          setSavedTabList(prev => {
            if (prev.some(v => v.id === removed.id)) return prev;   // already restored
            const next = prev.slice();
            next.splice(Math.min(idx, next.length), 0, removed);
            return next;
          });
          if (wasDefault) setDefaultTab(removed.id);
        },
      },
    });
  }, [savedTabList, defaultTab, setSavedTabList, segments, setDefaultTab]);
  // Reorder personal tabs (right-click → Move left / right) — swap with
  // the neighbour in the saved list.
  const moveTab = useCallback((id: string, dir: -1 | 1) => {
    setSavedTabList(prev => {
      const i = prev.findIndex(v => v.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = prev.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }, [setSavedTabList]);
  // Duplicate a tab — a fresh id + "… copy" name, same scope/sort/style,
  // inserted right after the original (a starting point to tweak).
  const duplicateTab = useCallback((id: string) => {
    setSavedTabList(prev => {
      const i = prev.findIndex(v => v.id === id);
      if (i < 0) return prev;
      const newId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
      const copy: SavedTab = { ...prev[i], id: newId, name: `${prev[i].name} copy` };
      const next = prev.slice();
      next.splice(i + 1, 0, copy);
      return next;
    });
  }, [setSavedTabList]);

  // A personal tab's management actions, declared ONCE as data and fed to
  // the right-click ContextMenu on the tab (see components/ui/context-menu).
  // Edit / Set-default / Move / Delete — the same set the ⋮ menu used to
  // hold, now reached by right-clicking the tab itself.
  const buildTabActions = useCallback((tabId: string): MenuAction[] => {
    const idx = savedTabList.findIndex(v => v.id === tabId);
    const isDefault = defaultTab === tabId;
    return [
      {
        key: 'edit',
        label: 'Edit tab',
        icon: <Pencil size={14} className="text-muted-foreground" />,
        onSelect: () => {
          const v = savedTabList.find(x => x.id === tabId);
          if (v) setTabDialog(v);
        },
      },
      {
        key: 'default',
        label: isDefault ? 'Default tab · clear' : 'Set as default tab',
        icon: <Star size={14} className={isDefault ? 'text-primary fill-current' : 'text-muted-foreground'} />,
        onSelect: () => setDefaultTab(isDefault ? '' : tabId),
      },
      {
        key: 'left',
        label: 'Move left',
        icon: <ChevronLeft size={14} className="text-muted-foreground" />,
        disabled: idx <= 0,
        onSelect: () => moveTab(tabId, -1),
      },
      {
        key: 'right',
        label: 'Move right',
        icon: <ChevronRight size={14} className="text-muted-foreground" />,
        disabled: idx >= savedTabList.length - 1,
        onSelect: () => moveTab(tabId, 1),
      },
      {
        key: 'duplicate',
        label: 'Duplicate tab',
        icon: <Copy size={14} className="text-muted-foreground" />,
        onSelect: () => duplicateTab(tabId),
      },
      {
        key: 'delete',
        label: 'Delete tab',
        icon: <Trash2 size={14} />,
        danger: true,
        separatorBefore: true,
        onSelect: () => deleteTab(tabId),
      },
    ];
  }, [savedTabList, defaultTab, setTabDialog, setDefaultTab, moveTab, duplicateTab, deleteTab]);

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
  } = useTablePreference(tableId, 'visibility');
  // Effective visibility = column-level ``defaultHidden`` overlaid by
  // the operator's persisted choices.  Persisted always wins where
  // set, so unhiding a defaultHidden column sticks; Reset clears
  // persisted and the defaults reappear.  All downstream reads
  // (tanstack state, manage popover, ColumnHeader toggle logic) use
  // this rather than the raw persisted map.
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
  } = useTablePreference(tableId, 'order');
  const {
    value: columnPinning,
    setValue: setColumnPinning,
  } = useTablePreference(tableId, 'pinning', { left: [], right: [] });
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
  } = useTablePreference(tableId, 'colWidths');
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
  } = useTablePreference(tableId, 'groups');
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
  } = useTablePreference(tableId, 'rowGroup', defaultRowGroup ?? null);
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

  // ── Aggregation ──────────────────────────────────────────────
  //
  // A ``{ columnKey: fn }`` map → a footer total row.  Persists per-user
  // like the row-group choice; the operator sets it from each column's
  // ⋮ menu.  The EFFECTIVE model drops any entry whose column no longer
  // exists or isn't ``aggregable`` (a page could revoke aggregability
  // after a stale pref was saved), so a bad pref can never render a
  // total on a column that shouldn't have one.
  const {
    value: aggregationPref,
    setValue: setAggregationPref,
  } = useTablePreference(tableId, 'aggregation', defaultAggregation ?? {});
  const aggregationModel = useMemo<Record<string, AggFn>>(() => {
    const out: Record<string, AggFn> = {};
    for (const [key, fn] of Object.entries(aggregationPref)) {
      const col = columns.find(c => c.key === key);
      // The pref is a raw JSON blob with no server-side schema check —
      // keep an entry only if the column still offers that function.
      // This drops a stale / hand-edited / future-renamed name (so the
      // label lookup can never ``undefined.toLowerCase()`` and crash the
      // header) AND a now-invalid pairing like ``sum`` on a date column.
      if (col?.aggregable && offeredAggFns(col).includes(fn)) out[key] = fn;
    }
    return out;
  }, [aggregationPref, columns]);
  const setColumnAgg = useCallback((key: string, fn: AggFn | null) => {
    setAggregationPref(prev => {
      const next = { ...prev };
      if (fn === null) delete next[key];
      else next[key] = fn;
      return next;
    });
  }, [setAggregationPref]);

  // ── Pagination ───────────────────────────────────────────────
  //
  // Page size persists per-user (lives in user_preferences via
  // useUserPreference) so the operator's choice follows them across
  // sessions / devices.  Page index is in-memory only — it resets
  // whenever the filter / search / sort changes so a narrowed view
  // doesn't start halfway through.
  // Declared before the size/index merges below, which both branch on it.
  const paginationControlled = controlledPageIndex !== undefined;
  const {
    value: preferredPageSize,
    setValue: setPreferredPageSize,
  } = useTablePreference(tableId, 'pageSize', DEFAULT_PAGE_SIZE);
  // Merged exactly like pageIndex.  Reading the stored preference while
  // the PAGE fetched a different size makes the footer describe rows that
  // aren't there ("1-100 of 3,984" over 25 rows) — and worse, the
  // pagination handler forwards the stale size on every Next click, so
  // merely paging would silently rewrite the operator's page size.
  const pageSize = controlledPageSize ?? preferredPageSize;
  const setPageSize = useCallback((next: number) => {
    // A controlled size is the page's to persist (it owns the URL);
    // storing it here too would fight that owner on the next mount.
    if (!paginationControlled) setPreferredPageSize(next);
  }, [paginationControlled, setPreferredPageSize]);
  // Read inside the page-index setter, whose identity must stay stable.
  const pageSizeRef = useRef(pageSize);
  pageSizeRef.current = pageSize;
  const [ownPageIndex, setOwnPageIndex] = useState(0);
  const pageIndex = controlledPageIndex ?? ownPageIndex;
  const setPageIndex = useCallback((next: number) => {
    if (!paginationControlled) setOwnPageIndex(next);
    onPaginationChangeRef.current?.({ pageIndex: next, pageSize: pageSizeRef.current });
  }, [paginationControlled]);
  useEffect(() => {
    // Narrowing the view invalidates the page number — page 7 of a
    // 3-page result is nowhere.  A CONTROLLED page is the owner's to
    // reset; they hold the filters that caused it.
    if (!paginationControlled) setOwnPageIndex(0);
  }, [columnFilters, globalFilter, sorting, rowGroupBy, paginationControlled]);

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
    () => {
      const dataCols = columns.map((col) => {
        const def: ColumnDef<Record<string, unknown>> = {
          id: col.key,
          accessorKey: col.key,
          // ``minSize`` ONLY — a FLOOR, never a width.
          //
          // This also set ``size``, which is the column's WIDTH, not its
          // minimum.  Once an operator has resized any column the table
          // switches to fixed layout and applies ``getSize()`` per
          // column, so setting ``size`` silently RE-WIDTHED their stored
          // layout — narrowing DEL date from the 150 default to 116 and
          // squeezing content into truncation.  ``minSize`` alone does
          // what was intended: ``getSize()`` clamps to it, so a column
          // that was too narrow is raised to the floor and one that was
          // already wider is left alone.
          ...(col.minWidth ? { minSize: col.minWidth } : {}),
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
          // NOTE: this is the tanstack-Row form of the same logic that
          // ``rowPassesColFilter`` (savedTabs.ts) applies to raw rows —
          // keep the two in sync so a saved tab scopes exactly like the
          // live filter it was captured from.  (They match today because
          // every column uses ``accessorKey``, so ``row.getValue(key)``
          // equals ``row.original[key]``.)
          def.filterFn = (row, _colId, filterValue) => {
            // ``manualFiltering``: the rows arrived already filtered, so
            // applying the predicate again would double-filter them.
            // Neutralised HERE rather than by withholding the state (the
            // menus read the state back) or via tanstack's own
            // ``manualFiltering`` option (that short-circuits the entire
            // filtered row model, and GLOBAL SEARCH lives in it too — the
            // search box would silently stop working).
            if (manualFilteringRef.current) return true;
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
      });
      if (!bulkSelection) return dataCols;
      // Prepend the dedicated bulk-select column.  Its checkbox is
      // injected via ``leadingContent`` in the render loops (which close
      // over live selection state) — the def itself is a stable, empty
      // shell so this memo never rebuilds on selection change.  Locked +
      // non-hideable/pinnable/resizable so it behaves like a structural
      // column: force-pinned leftmost, no 3-dot menu, no drag/resize.
      const selectCol: ColumnDef<Record<string, unknown>> = {
        id: SELECT_COL_ID,
        header: '',
        cell: () => null,
        enableSorting: false,
        enableColumnFilter: false,
        enableHiding: false,
        enablePinning: false,
        enableResizing: false,
        size: 44,
        minSize: 44,
        maxSize: 44,
      };
      return [selectCol, ...dataCols];
    },
    [columns, bulkSelection],
  );

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
    // The bulk-select column pins leftmost of ALL — before any locked
    // data column and before the operator's own left pins.
    const forcedLeft = bulkSelection
      ? [SELECT_COL_ID, ...lockedLeftIds]
      : lockedLeftIds;
    if (forcedLeft.length === 0) return columnPinning;
    const userLeft = (columnPinning.left ?? []).filter(id => !forcedLeft.includes(id));
    return {
      left: [...forcedLeft, ...userLeft],
      right: columnPinning.right ?? [],
    };
  }, [columnPinning, lockedLeftIds, bulkSelection]);

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
      // Withheld under manualFiltering: the page already searched
      // server-side, so re-applying it here would narrow the result a
      // second time by the same needle against a narrower column set.
      globalFilter: (hasSearch && !manualFiltering) ? globalFilter : undefined,
      // Always the REAL filters, even under ``manualFiltering``.  The
      // menus, the header tint and the 3-dot badge all read the value
      // back through ``column.getFilterValue()``, which reads THIS — so
      // withholding it would leave an active filter showing a chip on the
      // toolbar and an empty, unticked menu when you opened the column.
      // Not filtering is handled in the filterFn instead (see below).
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
          left: (next.left ?? []).filter(
            id => id !== SELECT_COL_ID && !lockedLeftIds.includes(id)),
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
      if (paginationControlled || next.pageSize !== pageSize) {
        onPaginationChangeRef.current?.(next);
      }
      if (!paginationControlled) setOwnPageIndex(next.pageIndex);
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
    // Under manualPagination the rows ARRIVED as one page, so slicing
    // them again would show the first N of an already-N-row page.
    manualPagination,
    ...(manualPagination && controlledPageCount !== undefined
      ? { pageCount: controlledPageCount } : {}),
    ...(enablePagination && !manualPagination
      ? { getPaginationRowModel: getPaginationRowModel() } : {}),
    globalFilterFn: hasSearch
      ? (row, _colId, filterValue) =>
          rowMatchesSearch(row.original, String(filterValue).toLowerCase())
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
        // Icon AND label, not icon alone.  The bar only appears once rows
        // are selected, so its buttons are the reason the operator
        // selected them — and three anonymous glyphs made the primary
        // action ("Acknowledge", on a safety queue) discoverable only by
        // hovering, which a touch user cannot do at all.  The grid's own
        // grammar rule says the most important action should be the most
        // prominent; a wordless glyph is the opposite.
        const inner = (
          <span className="inline-flex items-center gap-1.5 px-1">
            {Icon && <Icon />}
            <span className="text-xs font-medium">{action.label}</span>
          </span>
        );
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
                      size="xs"
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
              size="xs"
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
    // An EMPTY message means "no confirmation at this count", so a
    // consumer can gate the prompt on scope: routine triage of a few rows
    // stays friction-free while an accidental select-all gets stopped.
    // A modal on every action trains people to dismiss modals, which is
    // worse protection than none.
    const confirmMsg = action.confirm?.(rows.length);
    if (confirmMsg && !window.confirm(confirmMsg)) return;
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
  // Dev-only: catch the misconfiguration at wiring time rather than as a
  // field report.  Deriving options from loaded rows is fine until the
  // rows are a server-filtered slice — then picking a value unloads every
  // other value and the menu strands the operator on their own choice.
  if (import.meta.env.DEV && manualFiltering) {
    for (const col of columns) {
      if (col.filterable && col.filterMode !== 'range'
          && col.filterMode !== 'date-range' && !col.filterOptions) {
        console.warn(
          `[DataGrid] column "${col.key}" is filterable on a manualFiltering `
          + 'grid but declares no filterOptions — its menu will collapse to '
          + 'whatever the current server filter left loaded.',
        );
      }
    }
  }

  const uniquesByCol = useMemo(() => {
    const out: Record<string, {
      options: Array<{ value: string; label: string }>;
      counts: Record<string, number>;
    }> = {};
    const filterByKey = new Map(columnFilters.map(f => [f.id, f.value]));
    const colByKey = new Map(columns.map(c => [c.key, c]));
    // Withheld under ``manualFiltering`` for the same reason the row
    // filter withholds it: the rows arrived searched server-side, so
    // matching the needle again here would count a narrower set than
    // the one on screen.
    const needle = (hasSearch && !manualFiltering)
      ? globalFilter.trim().toLowerCase() : '';
    // Hoisted OUT of the per-column loop: whether a row matches the
    // search does not depend on which column's options we're deriving,
    // and the matcher itself now walks every column — so leaving it
    // inside re-ran a rows x columns pass once per filterable column.
    const searchedRows = needle
      ? data.filter(row => rowMatchesSearch(row, needle))
      : data;
    for (const col of columns) {
      if (!col.filterable || col.filterMode === 'range' || col.filterMode === 'date-range') continue;
      // A DECLARED option list short-circuits the derivation.  The
      // derivation below reads the loaded rows, which is right only when
      // the grid holds the whole set — otherwise choosing a value
      // unloads every other value and the menu strands the operator on
      // their own selection.  No counts: we weren't given the rows to
      // count, and a wrong count is worse than none.
      if (col.filterOptions) {
        out[col.key] = { options: col.filterOptions, counts: {} };
        continue;
      }
      // Rows surviving every filter EXCEPT this column's own.
      const contextRows = searchedRows.filter(row => {
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
  }, [columns, data, columnFilters, globalFilter, hasSearch, manualFiltering, rowMatchesSearch]);

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
  // ── Active filters / sort / search as removable chips ─────────────
  //
  // A row of pills below the toolbar (auto-shown only when something is
  // active) — the always-visible, one-click-remove counterpart to the
  // Filter/Sort popovers: "what's limiting my view?" is now readable at
  // a glance, and each ✕ clears just that constraint.  Adding filters
  // still happens in the column ⋮ menus.
  const trimmedGlobal = hasSearch ? globalFilter.trim() : '';
  // "Clear all" counts only the data CONSTRAINTS (filter/sort/search);
  // grouping + hidden are view-state chips with their own single ✕.
  const chipCount = columnFilters.length + sorting.length + (trimmedGlobal ? 1 : 0);
  const groupedCol = rowGroupBy ? columns.find(c => c.key === rowGroupBy) : undefined;
  // A group-header label should read like the CELLS, not the raw code:
  // group by Type and the header must say "Oil Change", not "oil".  Every
  // leaf in a group shares the grouped value, so a representative leaf
  // drives the column's own display — its ``render`` (the rich cell:
  // icon + label) first, then ``filterLabel`` (the value→label text used
  // by the filter dropdown), then the raw value as a last resort.
  const renderGroupValue = useCallback(
    (value: unknown, leaf: Record<string, unknown> | undefined): React.ReactNode => {
      if (value == null || value === '') return '—';
      if (groupedCol?.render && leaf) return groupedCol.render(value, leaf);
      if (groupedCol?.filterLabel && leaf) return groupedCol.filterLabel(leaf);
      return String(value);
    },
    [groupedCol],
  );
  // Hidden columns get NO chip (the manage-columns badge already shows
  // the count) — only the data constraints + row grouping do.
  const hasAnyChip = chipCount > 0 || !!rowGroupBy;
  const chipCls =
    'inline-flex items-center gap-1 pl-2 pr-0.5 py-0.5 rounded-md border border-border '
    + 'bg-background text-xs text-foreground';
  const chipX = 'ml-0.5 p-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted';
  // The chips flow INLINE on the toolbar line, after the bulk-action
  // bar (or headerToolbar) — no wrapper strip; each chip is a flex item
  // in the toolbar's left slot.
  const filterChips = !hasAnyChip ? null : (
    <>
      {trimmedGlobal && (
        <span className={chipCls}>
          <Search size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
          <span className="truncate max-w-64">“{trimmedGlobal}”</span>
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
              <span className="truncate max-w-72">
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
      {/* The row-list sort does NOT order a pivot — the report sorts by a
          measure, from its own header.  The chip used to keep claiming
          "Sorted by Customer ↑" over a matrix that ignored it entirely.
          It stays visible (the sort is real, and comes back the moment
          you leave pivot) but reads as inactive and says why. */}
      {sorting.map((s) => {
        const col = columns.find(c => c.key === s.id);
        return (
          <span key={`s-${s.id}`} className={cn(chipCls, pivotOn && 'opacity-60')}>
            <ArrowUpDown size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
            <Tip label={pivotOn
              ? `Not applied while pivoting — a pivot orders by a measure, from its own column header. Returns when you turn Pivot off.`
              : `Sorted by ${col?.label || s.id} · ${s.desc ? 'descending' : 'ascending'}`}>
              <span className="truncate max-w-72">
                Sorted by <span className="font-medium">{col?.label || s.id}</span>
                {s.desc ? ' ↓' : ' ↑'}
                {pivotOn && <span className="ml-1 text-muted-foreground">· not applied</span>}
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
      {rowGroupBy && (
        <span className={chipCls}>
          <ListTree size={12} className="text-muted-foreground shrink-0" aria-hidden="true" />
          <span className="truncate max-w-72">
            Grouped by <span className="font-medium">{groupedCol?.label || rowGroupBy}</span>
          </span>
          <button type="button" onClick={() => setRowGroupPref(null)}
            aria-label="Ungroup rows" className={chipX}>
            <X size={12} />
          </button>
        </span>
      )}
      {/* No "N hidden" chip — the column-manager button on the toolbar
          right already carries the hidden-count badge, so a chip would
          just duplicate it.  Row grouping keeps its chip because nothing
          else on the toolbar surfaces that state. */}
      {chipCount >= 2 && (
        <button type="button"
          onClick={() => { setColumnFilters([]); setSorting([]); setGlobalFilter(''); }}
          className="ml-0.5 px-1.5 py-0.5 text-2xs text-muted-foreground hover:text-foreground">
          Clear all
        </button>
      )}
    </>
  );

  // ── "Why is this row here?" — search hits in hidden columns ───────
  //
  // Searching hidden columns is the right call (see ``search.ts``), but
  // on a 76-column directory it hands back rows whose every visible cell
  // reads "—".  That reads as a broken search, and the only recovery is
  // to guess which of 76 columns to reveal.  So name the column that
  // matched, and offer to reveal it.
  //
  // Scoped to the rows ON THIS PAGE: the cost is bounded by the page
  // size rather than the dataset, and the note explains the rows the
  // operator is looking at instead of making a claim about rows they
  // can't see.  Skipped under ``manualFiltering`` — there the SERVER
  // matched, so we don't know what it matched on and would be guessing.
  const pageRows = table.getRowModel().rows;
  // Keyed on row IDENTITY, not on the array: tanstack returns a fresh
  // array every render, which would defeat the memo and re-run a
  // 250-row x 76-column scan on every unrelated state change.  ``data``
  // rides along because ids survive a refetch that changes contents.
  const pageRowKey = pageRows.map(r => r.id).join('\u0000');
  const searchNote = useMemo(() => {
    if (!trimmedGlobal || manualFiltering) return null;
    const rows = pageRows.filter(r => !r.getIsGrouped()).map(r => r.original);
    const p = searchProvenance(
      rows, columns, (key) => effectiveVisibility[key] !== false,
      trimmedGlobal.toLowerCase(),
    );
    if (p.unexplained === 0) return null;
    // Rows a column can actually account for — the count the "Show"
    // button is promising to fix.  The rest matched a ``searchKey`` row
    // field with no column, which revealing nothing can help.
    const byColumn = p.unexplained - p.fieldOnly;
    return { ...p, byColumn };
    // ``pageRowKey`` stands in for ``pageRows`` (see above); listing the
    // array itself would bust the memo on every render and make it
    // useless.  The disable must sit on the line ABOVE the deps — on the
    // first line of a multi-line comment it disables the COMMENT.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trimmedGlobal, manualFiltering, pageRowKey, data, columns, effectiveVisibility]);

  // "Show column" is only half an action: on a 76-column grid the column
  // lands at its ordinal position among the visible ones, which can be
  // far off the right edge — so the click would complete with nothing
  // visibly different, which reads as a dead button.  The effect that
  // scrolls it into view lives further down, where ``scrollEl`` exists.
  const [revealedCol, setRevealedCol] = useState<string | null>(null);

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
    // Drop the synthetic select column — it lives in ``tableColumns``
    // (hence in ``groupRuns``) but NOT in the ``columns`` prop that
    // ``base`` is rebuilt from.  Leaving it in would make ``newVisible``
    // one longer than the real slots, baking ``__select__`` into a real
    // column's position and shifting a real key off the persisted order.
    const newVisible = arrayMove(groupRuns, fromIdx, toIdx)
      .flatMap(r => r.memberIds)
      .filter(id => id !== SELECT_COL_ID);
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

  // The footer's narrowing hint, shared by both modes.  It fired on
  // ``globalFilter`` but counted only ``columnFilters``, so a view
  // narrowed by the SEARCH BOX and nothing else announced "0 filters
  // active" — a count contradicting the very condition that printed it.
  // Search IS a narrowing; it just isn't a column filter.
  const narrowings = columnFilters.length + (globalFilter ? 1 : 0);
  const narrowingHint = narrowings > 0
    ? `${narrowings} filter${narrowings === 1 ? '' : 's'} active`
    // NON-BREAKING space, as before: it holds the footer's line height so
    // the bar doesn't change height when the hint clears.
    : '\u00a0';

  // The rows the pivot summarises.  MEMOISED deliberately: built inline
  // in the JSX this was a fresh array on every render, so PivotView's
  // own ``useMemo`` never hit and the entire cross-tab — 500 source rows
  // into ~22,000 cells — was rebuilt for any unrelated state change
  // (a menu opening, a hover, the row-count report coming back).
  const filteredRowModel = table.getFilteredRowModel();
  const pivotSourceRows = useMemo(
    () => filteredRowModel.rows
      .filter((r) => !r.getIsGrouped())
      .map((r) => r.original as Record<string, unknown>),
    [filteredRowModel],
  );


  // ── Custom horizontal scrollbar ─────────────────────────────
  //
  // Native scrollbar spans the whole container including under the
  // pinned columns, which is misleading: pinned columns DON'T scroll.
  // The container uses ``overflow-x: hidden`` so the native bar is
  // gone entirely; we drive horizontal scrolling via this custom
  // scrollbar (drag) and a wheel handler (trackpad).

  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  // The table branch UNMOUNTS whenever pivot is switched on, and mounts
  // a BRAND NEW element when it's switched off again.  Every observer
  // below was set up in an effect keyed on things that don't change
  // across that swap, so after one pivot round-trip they were all still
  // watching a detached node: scroll metrics froze, and with them the
  // custom horizontal and vertical scrollbars simply stopped rendering
  // — the grid could no longer be scrolled sideways at all.
  //
  // Publishing the node through a callback ref makes every measurement
  // effect key on the ELEMENT, so they re-attach whenever it is
  // replaced, for any reason — not just this one.
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const setScrollNode = useCallback((el: HTMLDivElement | null) => {
    scrollContainerRef.current = el;
    setScrollEl(el);
  }, []);

  // Land the reveal in the viewport (see ``revealedCol`` above).  An
  // EFFECT rather than a rAF after the click, because the visibility
  // write goes through a persisted preference and the <th> does not
  // exist yet when the handler returns.  Keyed on ``effectiveVisibility``
  // so it re-runs the moment the column actually renders; a reveal that
  // never lands simply leaves the key set and does nothing.
  useEffect(() => {
    if (!revealedCol || !scrollEl) return;
    // Matched via dataset rather than an attribute selector: a column
    // key is page-supplied and can carry anything (Carrier Directory's
    // are ``f:pay:solo_rate``), so it has no business inside a selector
    // string.
    const th = [...scrollEl.querySelectorAll<HTMLElement>('thead th[data-col]')]
      .find(el => el.dataset.col === revealedCol);
    if (!th) return;
    // ``inline: 'nearest'`` moves the least that works and respects the
    // region's scroll-padding, so the column stops clear of the frozen
    // ones instead of under them.  ``block: 'nearest'`` keeps the reveal
    // from also scrolling the rows.
    th.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    setRevealedCol(null);
  }, [revealedCol, scrollEl, effectiveVisibility]);

  const [theadEl, setTheadEl] = useState<HTMLTableSectionElement | null>(null);
  const setTheadNode = useCallback((el: HTMLTableSectionElement | null) => {
    theadRef.current = el;
    setTheadEl(el);
  }, []);

  // Scroll position is only meaningful relative to the list you were
  // reading — so when the list changes IDENTITY (page, sort, filter,
  // search, tab), go back to the top.  Staying put means clicking
  // "next page" lands you in the middle of the new page, and a sticky
  // header means nothing on screen changes shape to tell you that
  // happened.  Harmless when the page scrolls instead of the body (the
  // container is then at scrollTop 0 anyway) — no fillHeight gate, so
  // the behaviour can't diverge between the two modes.
  // ``pageSize`` and ``grouping`` are in here for the same reason as the
  // rest, and were missing: switching 25 -> 250 rows replaces every row
  // below the fold, and grouping re-orders the whole list. Both left the
  // reader parked at an offset that now pointed at different data.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (el && el.scrollTop !== 0) el.scrollTop = 0;
  }, [pageIndex, pageSize, sorting, columnFilters, globalFilter, segmentPref, grouping]);

  // Does the BODY scroll (rather than the page)?  True either way the
  // grid gets its own viewport — a hand-set ``stickyHeader`` height or
  // ``fillHeight`` taking the remaining space.  Everything downstream
  // (sticky thead, its opaque background, the raised z-index that keeps
  // pinned header cells above scrolled body cells, whose scrollbar is
  // drawn) depends on the SCROLLING, not on which prop asked for it.
  // ── The grid finds its own viewport ──────────────────────────────
  //
  // This USED to be the ``fillHeight`` prop, and it only worked when the
  // page ALSO wrapped it in ``flex h-full flex-col min-h-0`` — a
  // contract 3 of 40 grid surfaces had actually paid, because CSS can't
  // be inverted and every new page had to remember it again.  Now the
  // grid measures the room left below itself and clamps to that, so no
  // page has to know and a change here reaches every grid at once.
  //
  // ``null`` means "not enough room, grow naturally and let the page
  // scroll" — which is what pages that stack charts and KPI cards above
  // their table get, correctly, without asking for it.
  const [cardEl, setCardEl] = useState<HTMLDivElement | null>(null);
  const fitted = useFittedHeight(cardEl, autoFit);
  const fills = fitted !== null;
  const bodyScrolls = !!stickyHeader || fills;

  // The sticky header lives INSIDE the scroll container, so a native
  // vertical scrollbar runs the container's full height — up alongside
  // the column labels and their ⋮ menus, which reads as the rows
  // scrolling "into" the header.  We hide the native bar when the body
  // scrolls and draw our own starting BELOW the header (the same
  // treatment the horizontal bar already gets), which is where MUI's
  // sits: its headers are a separate element outside the scroller.
  // Measured rather than assumed — header height changes with density,
  // wrapped labels and the aggregation micro-label.
  const theadRef = useRef<HTMLTableSectionElement | null>(null);
  const [headerHeight, setHeaderHeight] = useState(0);
  useEffect(() => {
    const el = theadEl;
    if (!el) return;
    const measure = () => setHeaderHeight(h => {
      const next = el.offsetHeight;
      return h === next ? h : next;
    });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [theadEl, bodyScrolls, density]);

  // Booleans, NOT live metrics.  Subscribing the whole grid to scroll
  // position re-rendered every row on every frame; only the bars need
  // that, and they subscribe themselves.
  const overflow = useOverflow(scrollEl);


  // NO wheel bridge here any more.  It existed to put back a gesture
  // ``overflow-x: hidden`` had switched off; with ``auto`` the browser
  // applies ``deltaX`` itself, so keeping it would apply the delta a
  // SECOND time — the exact 2x-scroll bug that shipped when both
  // scrollbars installed it.

  // Measure pinned column widths directly from the live DOM after
  // every render.  Reading ``columnSizing`` would also work but lags
  // by one ResizeObserver tick on the very first paint, leaving the
  // custom scrollbar momentarily full-width before settling — direct
  // DOM measurement is one synchronous frame and avoids that flash.
  const [pinnedWidths, setPinnedWidths] = useState({ left: 0, right: 0 });

  // The scroll REGION contract (components/scrolling).  It owns overflow
  // on both axes, focusability + the landmark name, overscroll
  // containment, and the scroll-padding that keeps the browser's
  // scroll-into-view clear of the sticky header and the frozen columns.
  //
  // ⚠️ ``allowScrollChaining`` when the grid does NOT own a viewport.
  // Without ``fillHeight`` or ``stickyHeader`` this container has no
  // height cap, so it never scrolls vertically — it is a scroll container
  // with zero scroll range sitting inside the page's own scroller.
  // Containing the overscroll of a box that cannot scroll can only ever
  // swallow a wheel the page should have got.  Containment is for panes
  // that genuinely scroll; this states that rather than relying on how a
  // given browser latches a gesture to a zero-range box.
  const region = useScrollRegion({
    label: 'Table rows',
    // x is ``auto``, NOT ``hidden``.  ``hidden`` left the browser with no
    // horizontal scrolling mechanism at all — no touch pan, no keyboard,
    // no autoscroll — so a wide table was reachable only by dragging an
    // 8px painted thumb.  It was chosen because ``overflow-x: auto``
    // reserves a scrollbar track at the container's bottom "even with
    // ::-webkit-scrollbar { height: 0 }" — true of ``height: 0``, but
    // HIDE_NATIVE_SCROLLBAR uses ``display: none`` + ``scrollbar-width:
    // none``, which removes the bar outright.  The VERTICAL axis has run
    // exactly that combination all along; this is the same treatment, one
    // axis later.
    axis: { y: 'auto', x: 'auto' },
    allowScrollChaining: !bodyScrolls,
    stickyTop: bodyScrolls ? headerHeight : undefined,
    pinnedLeft: pinnedWidths.left,
    pinnedRight: pinnedWidths.right,
  });

  useLayoutEffect(() => {
    const el = scrollEl;
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
  }, [scrollEl, effectivePinning, columnOrder, columnVisibility, columnSizing]);
  const pinnedLeftWidth  = pinnedWidths.left;
  const pinnedRightWidth = pinnedWidths.right;

  // Scrollbar geometry — only shown when content overflows.  The
  // track spans the centre region; thumb width is proportional to
  // the visible-to-total ratio, with a floor so it stays grabbable.
  const needsHScroll = overflow.x;

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
    const today0 = new Date().toISOString().slice(0, 10);
    // In pivot mode the flat record list is NOT what's on screen —
    // exporting it would hand the operator a different artifact from the
    // one they configured and are reading.  Export the matrix instead.
    // Scope is ignored here on purpose: a pivot already summarises every
    // filtered row, so "this page" has no meaning.
    if (pivotOn) {
      const rowsForPivot = table.getPrePaginationRowModel().rows
        .filter((r) => !r.getIsGrouped())
        .map((r) => r.original as Record<string, unknown>);
      const grid = pivotToCsvRows(pivot(rowsForPivot, pivotModel, pivotColumns));
      if (grid.length === 0) return;      // nothing configured yet
      downloadCsv(`${tableId}-pivot-${today0}.csv`, buildCsvFromRows(grid));
      return;
    }
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
    const suffix = scope === 'all' ? (holdsPartialData ? '-loaded' : '-all') : '';
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
    // Aggregation back to the table's configured default (or none).
    setAggregationPref(defaultAggregation ?? {});
    // Column widths back to auto layout.
    setUserWidths({});
  };

  // Reduce ONE column over a set of rows and format the result — shared
  // by the grand-total footer (whole filtered set) and each group row
  // (that group's leaves), so a group total and the footer total always
  // agree on how a column is summed + formatted.
  const renderAggCell = useCallback((
    key: string, fn: AggFn, originals: Record<string, unknown>[],
  ): React.ReactNode => {
    const col = columns.find(c => c.key === key);
    if (!col) return null;
    const isDate = col.aggType === 'date';
    // sum / avg are meaningless on dates — never compute them (the menu
    // doesn't offer them either, this is defence for a stale pref).
    if (isDate && (fn === 'sum' || fn === 'avg')) return null;

    let value: number | null;
    // A date column is CALENDAR-DAY (``YYYY-MM-DD``, no instant/tz) vs an
    // INSTANT (a full timestamp).  ``new Date('2026-07-20')`` parses to
    // UTC midnight, so a calendar-day value MUST also be formatted in UTC
    // or a viewer west of UTC sees the previous day.  An instant is a
    // real point in time → format its day in the account tz.  Decide by
    // the raw values' shape (a column is homogeneous in practice).
    let dateOnly = false;
    if (fn === 'count') {
      value = originals.length;
    } else if (isDate) {
      const raws = originals.map(o =>
        (col.aggValue ? col.aggValue(o) : o[key]) as
          number | string | Date | null | undefined);
      dateOnly = raws.every(r =>
        r == null || r === ''
        || (typeof r === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(r)));
      const ts = raws.map(toAggTimestamp)
        .filter((n): n is number => Number.isFinite(n));
      value = computeAggregate(fn, ts, originals.length);
    } else {
      const nums = originals
        // ``toAggNumber`` maps missing (null/undefined/'') → NaN so it's
        // excluded here, not folded in as 0 (see aggregation.ts).
        .map(o => toAggNumber(col.aggValue ? col.aggValue(o) : o[key]))
        .filter((n): n is number => Number.isFinite(n));
      value = computeAggregate(fn, nums, originals.length);
    }

    if (value == null) return '—';
    if (col.aggFormat) return col.aggFormat(value, fn);
    // Date min/max → render the DAY (a column needing time precision
    // passes its own ``aggFormat``).  ``count`` stays a number.
    if (isDate && fn !== 'count') {
      return formatDay(value, { timeZone: dateOnly ? 'UTC' : timeZone });
    }
    return formatAggDefault(value, fn);
  }, [columns, timeZone]);

  // Footer totals — one value per aggregated column, reduced over the
  // FILTERED leaf rows (all pages, so the total reflects the whole
  // narrowed set, not just the current page).  Recomputes only when the
  // model or the filtered inputs change.  ``{}`` → no footer row.
  const footerAgg = useMemo<Record<string, React.ReactNode>>(() => {
    if (Object.keys(aggregationModel).length === 0) return {};
    const originals = table.getFilteredRowModel().rows
      .filter(r => !r.getIsGrouped())
      .map(r => r.original as Record<string, unknown>);
    const out: Record<string, React.ReactNode> = {};
    for (const [key, fn] of Object.entries(aggregationModel)) {
      out[key] = renderAggCell(key, fn, originals);
    }
    return out;
    // ``data`` + filter/search state are the inputs that reshape the
    // filtered row set; ``table`` is stable.
  }, [aggregationModel, renderAggCell, table, data, columnFilters, globalFilter]);

  // Group rows show per-group aggregates aligned under their columns
  // (MUI's grouped-aggregation look) ONLY when a model is active AND the
  // page owns neither a custom ``rowGroupHeader`` (owns the whole group
  // row as one summary cell — e.g. Alerts) NOR a custom
  // ``firstColumnLeading.groupHeader`` (custom leading content the
  // per-column path has no slot for).  Either → keep the classic
  // full-width colSpan group row, which renders both.
  const groupAggActive = Object.keys(aggregationModel).length > 0
    && !rowGroupHeader
    && !firstColumnLeading?.groupHeader;

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
        // Buckets the Manage-columns popover. Grids whose columns come
        // from a large template set this so 70+ entries stay findable.
        group: c.group,
      }))
      // Locked columns are structural (a row chevron, a checkbox) and are
      // alwaysVisible anyway, so listing them is noise the operator can't
      // act on — and one with no label renders under its raw key, leaking
      // an internal id like "_chevron" into a user-facing menu.
      .filter(c => !c.alwaysVisible);
  }, [columns, columnOrder]);

  return (
    // ``fillHeight``: this grid is a flex child that takes whatever
    // vertical space the page has left, and passes that constraint down
    // to the scroll container.  ``min-h-0`` at every level is what
    // actually lets it SHRINK — a flex item defaults to
    // ``min-height: auto`` (never smaller than its content), which on a
    // 250-row table means "never smaller than 250 rows" and the whole
    // mechanism silently does nothing.
    <div className={fills ? 'flex flex-col min-h-0' : undefined}>
      {/* Segment tab strip — OUTSIDE the card, floating directly on
          the page background (no fill of its own), like physical
          folder tabs poking up from the card below.  ``-mb-px`` +
          ``relative z-10`` let each tab overlap the card's top
          border by exactly one pixel: the ACTIVE tab's opaque
          ``bg-muted`` paints over that border line, opening a seam
          into the card's toolbar surface; inactive tabs are
          transparent, so the card border runs straight under them —
          reading as "closed" folders. */}
      {effectiveSegments.length > 0 && (
        <div
          role="tablist"
          aria-label="Data segments"
          // ``px-6`` insets the first tab (and its 8px outward
          // fillet) fully clear of the card's rounded corner below
          // (radius ~10px) — tighter insets stack the fillet's curve
          // on top of the card-corner curve and the two read as one
          // lumpy S instead of two clean shapes.
          // ``overflow-x-auto`` because a fixed row of tabs CLIPS on a
          // narrow screen, and a clipped tab is not merely ugly — it is
          // unreachable.  On a 400px phone the Vehicles strip cut off at
          // "Stopped" and "No telemetry" could not be selected at all.
          // ``shrink-0`` on the tabs stops flex from squeezing them into
          // illegible slivers instead of overflowing.
          className="relative z-10 -mb-px flex items-end gap-1 px-6 overflow-x-auto [&>*]:shrink-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {effectiveSegments.map((seg, i) => {
            // A CONTROLLED key that names nothing — a saved tab deleted
            // while its id sat in the URL, or a link pasted from someone
            // whose tabs differ — highlights NOTHING rather than falling
            // back to the first tab.  The rows are still right (the page's
            // own filters drive them), so the only question is what the
            // strip claims: "one of these" would be a specific wrong
            // answer, where no highlight correctly says "not one of these".
            const highlightKey = segmentControlled ? segmentPref : activeSegment?.key;
            const active = seg.key === highlightKey;
            const prev = i > 0 ? effectiveSegments[i - 1] : undefined;
            const prevActive = prev?.key === highlightKey;
            // ``isTab`` = the ONE discriminator between the two tab kinds:
            // a personal SAVED TAB (TAB_PREFIX key) vs a built-in segment
            // (Active/Archive, no prefix).  Only saved tabs get the accent
            // dot + ⋮ management menu; built-ins render plain.
            const isTab = seg.key.startsWith(TAB_PREFIX);
            const prevIsTab = prev?.key.startsWith(TAB_PREFIX);
            const tabId = isTab ? seg.key.slice(TAB_PREFIX.length) : '';
            return (
              <Fragment key={seg.key}>
                {/* A firmer divider marks the boundary between the
                    code-defined segments and the operator's personal
                    tabs; a hairline otherwise separates two INACTIVE
                    neighbours (it vanishes next to the active tab so the
                    folder silhouette stays clean). */}
                {isTab && !prevIsTab && i > 0 ? (
                  <span aria-hidden className="self-center w-px h-5 bg-border mx-1.5 mb-1" />
                ) : i > 0 && !active && !prevActive && (
                  <span aria-hidden className="self-center w-px h-4 bg-border" />
                )}
                {isTab ? (
                  // Personal tab: RIGHT-CLICK the tab to manage it (Edit /
                  // Set-default / Move / Delete) — the actions live in
                  // ``buildTabActions`` and render through the shared
                  // ContextMenu.  The blue dot + the "Right-click…" hint are
                  // the discoverability cues that replaced the old ⋮ button.
                  <ContextMenu items={buildTabActions(tabId)} className="items-end">
                    {/* Tip's render-composition needs a DOM node to merge
                        onto; SegmentTab keeps its own internal ref, so the
                        hover hint rides this wrapping span. */}
                    <Tip label="Right-click to manage (rename · color · delete)">
                      <span className="inline-flex items-end">
                        <SegmentTab
                          dot
                          manageable
                          iconKey={seg.iconKey}
                          countTone={seg.tone}
                          label={seg.label}
                          count={segmentCounts[seg.key] ?? 0}
                          showCount={seg.showCount !== false}
                          active={active}
                          onClick={() => setSegmentPref(seg.key)}
                        />
                      </span>
                    </Tip>
                  </ContextMenu>
                ) : (
                  <SegmentTab
                    label={seg.label}
                    count={segmentCounts[seg.key] ?? 0}
                    showCount={seg.showCount !== false}
                    active={active}
                    onClick={() => setSegmentPref(seg.key)}
                  />
                )}
              </Fragment>
            );
          })}
          {/* New-tab "+" — an icon-only affordance after the last tab,
              like a browser's new-tab button.  Opens the build-a-tab
              dialog.  Only when the feature is on. */}
          {savedTabsEnabled && (
            <Tip label="New tab">
              <button
                type="button"
                onClick={() => setTabDialog('new')}
                aria-label="New tab"
                className="self-center mb-1 ml-1 inline-flex items-center justify-center size-7 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
              >
                <Plus size={16} />
              </button>
            </Tip>
          )}
        </div>
      )}
      {/* One card holds everything — toolbar, table, pagination — so
          the chrome (Search / Export / Columns / pagination) sits
          INSIDE the bordered surface alongside the rows, matching
          the reference design.  Previously the toolbar and footer
          lived outside the card so only the table body had a card
          edge around it.  ``overflow-hidden`` clips the table rows
          against the card's rounded corners. */}
      <div
        ref={setCardEl}
        className={cn(
          'rounded-lg border border-border bg-card overflow-hidden',
          // The column that pins toolbar + footer to its edges and gives
          // the table body everything between them.
          fills && 'flex flex-col',
          // A clamped card must not truncate on paper — print has no
          // viewport to fit, and a scrolled-away row simply vanishes.
          'print:max-h-none',
        )}
        // max-height, never height: a three-row table stays three rows
        // tall instead of becoming a tall box with empty space under it.
        style={fitted !== null ? { maxHeight: fitted } : undefined}
      >
      {/* The fields panel is a PEER COLUMN of the whole grid — toolbar,
          body and footer all sit to its left — rather than a box inside
          the body region.  Nested in the body it started below the
          toolbar and stopped above the footer, so it read as something
          the grid contained; MUI stands it alongside, which is what it
          actually is: a second surface, not part of the table. */}
      <div className={cn('flex items-stretch', fills && 'flex-1 min-h-0')}>
      <div className={cn('flex-1 min-w-0 flex flex-col', fills && 'min-h-0')}>
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
        {/* LEFT, all on THIS toolbar line (Search + the filter/sort/
            columns/export icons stay on the right):
              [bulk-action bar when selected, else headerToolbar]
              followed by the active filter/sort/search CHIPS.
            So selecting rows shows the actions, and the chips sit right
            after them on the same bar. */}
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
          {selectedRowIds.size > 0 ? selectionBarContent : headerToolbar}
          {filterChips}
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
              {/* No toolbar Filter / Sort buttons.  They were pure
                  redundancy: the popovers only VIEWED / removed / cleared
                  active filters + sorts — exactly what the left-side chips
                  now do — and never actually ADDED one (they punted to the
                  column ⋮ menu, which remains the real add path).  A
                  dead-end funnel that tells you to go elsewhere is worse
                  than no funnel; the chips are the honest active-state UI. */}
              {/* Plain icon — NO hidden-count badge.  Hiding a column is a
                  deliberate act the operator already knows they did; a
                  persistent number here read as an unresolved "notification"
                  to clear.  Filter/Sort keep their badges (those ARE active
                  view constraints); "columns hidden" is just layout. */}
              {/* ONE control, matching MUI: the toolbar icon opens the
                  panel, and the switch inside it pivots the grid.  It
                  used to pivot on the spot, which replaced the row list
                  before the operator had said what they wanted
                  summarised — and it needed a second "Fields" button to
                  reach the pickers afterwards.  The button still paints
                  ACTIVE while pivoted, so a closed panel never hides
                  the fact that you're looking at a report. */}
              {pivotEnabled && (
                <Tip label={
                  holdsPartialData
                    ? `A pivot would summarise the ${sourceData.length.toLocaleString()} rows loaded, not all ${totalRows!.toLocaleString()}. Narrow the view, or raise Rows per page.`
                    : (pivotOn ? 'Pivot fields — currently pivoted' : 'Summarise as a pivot table')
                }>
                  {/* Disabled-with-reason rather than hidden: a control
                      that vanishes teaches nothing, and the operator
                      can act on "narrow the view first". */}
                  <Button
                    type="button"
                    variant={pivotOn ? 'default' : 'outline'}
                    size="icon"
                    onClick={() => setPivotPanelOpen((o) => !o)}
                    // ``aria-pressed`` tracks what this button DOES —
                    // open the panel.  The FILL tracks something else
                    // (whether the grid is pivoted), so that state is said
                    // in the NAME rather than left to contradict it.
                    aria-pressed={pivotPanelOpen}
                    // Named "Pivot", matching the panel's own heading —
                    // renaming it "Pivot fields" would have added a FOURTH
                    // name for one surface, which the audit also flagged.
                    aria-label={pivotOn ? 'Pivot — on' : 'Pivot'}
                    disabled={holdsPartialData}
                    className="size-8"
                  >
                    <Table2 size={16} />
                  </Button>
                </Tip>
              )}
              {/* Column machinery is SUPERSEDED in pivot mode — the
                  columns are synthesized from the model — but the button
                  STAYS, disabled with the reason.  It used to vanish,
                  which re-flowed the whole toolbar the instant you
                  pivoted: every icon to its right jumped left, so the
                  control under your cursor was no longer the one you
                  were aiming at.  A toolbar that changes shape on a
                  mode switch costs more than a greyed button explains.
                  The stored layout prefs are untouched and return on
                  exit. */}
              <Tip label={pivotOn
                ? 'Columns come from the pivot fields while pivoting — turn Pivot off to manage them'
                : 'Show / hide columns'}>
              <Button
                ref={manageAnchorRef}
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setManageOpen((o) => !o)}
                disabled={pivotOn}
                aria-label="Manage columns"
              >
                <Columns3 />
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
                        // A pivot already summarises EVERY filtered row, so
                        // "current page" has no meaning here — offering it
                        // would imply a distinction that doesn't exist.
                        if (pivotOn) {
                          return (
                            <MenuPrimitive.Item
                              // NOT onExportAllRows: that fetches raw rows
                              // from the page's source, and this item
                              // exports the PIVOT — a different artefact.
                              onClick={() => handleExportCsv('all')}
                              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none data-[highlighted]:bg-accent"
                            >
                              <Download size={14} className="text-muted-foreground" />
                              <span className="flex-1 text-foreground text-left">
                                Pivot table
                              </span>
                              <span className="text-2xs text-muted-foreground tabular-nums">
                                {allCount.toLocaleString()} rows
                              </span>
                            </MenuPrimitive.Item>
                          );
                        }
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
                            {/* "All rows" means all the grid HAS.  On a
                                slice that isn't all the rows there are, so
                                it says "loaded" and shows both numbers —
                                exporting what you're looking at is useful,
                                exporting 18% of the data into a file named
                                "-all" is not. */}
                            <MenuPrimitive.Item
                              onClick={() => (onExportAllRows
                                ? void onExportAllRows()
                                : handleExportCsv('all'))}
                              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer outline-none data-[highlighted]:bg-accent"
                            >
                              <Download size={14} className="text-muted-foreground" />
                              <span className="flex-1 text-foreground text-left">
                                {onExportAllRows || !holdsPartialData
                                  ? 'All rows'
                                  : 'All loaded rows'}
                              </span>
                              <span className="text-2xs text-muted-foreground tabular-nums">
                                {onExportAllRows
                                  // The page fetches the whole set, so the
                                  // honest number is the real total.
                                  ? `${(totalRows ?? allCount).toLocaleString()} rows`
                                  : holdsPartialData
                                    ? `${allCount.toLocaleString()} of ${totalRows!.toLocaleString()}`
                                    : `${allCount.toLocaleString()} rows`}
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

      {/* The panel is a SIBLING of whichever body is showing, not a
          child of the pivot branch — it has to be reachable while the
          grid is still a row list, because that is where you switch
          pivoting ON.  The two bodies then swap inside the left column
          without the panel unmounting. */}
      <div className={cn('flex flex-col', fills && 'flex-1 min-h-0')}>
      {/* Search hits the operator cannot see.  The live region is
          mounted whenever the row list is, and only its CONTENTS are
          conditional — a region that appears at the same moment as its
          text is frequently not announced at all, so a screen-reader
          user would get the unexplained rows and none of the
          explanation.  Empty, it costs no height. */}
      {!pivotOn && (
      <div aria-live="polite">
        {searchNote && (() => {
          // Two names, then a count: the point is "which column do I
          // reveal", and a five-name list stops being readable.
          const named = searchNote.sources.slice(0, 2).map(s => s.label).join(', ');
          const more = searchNote.sources.length - 2;
          const many = searchNote.sources.length > 1;
          // "on this page" only when there IS another page — otherwise
          // it is a caveat about a distinction that doesn't exist.
          const paged = enablePagination && table.getPageCount() > 1;
          return (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5 border-b border-border bg-card text-2xs text-muted-foreground">
              <EyeOff size={12} aria-hidden className="shrink-0" />
              {searchNote.byColumn > 0 ? (
                <>
                  <span>
                    {searchNote.byColumn === 1 ? '1 row' : `${searchNote.byColumn} rows`}
                    {paged ? ' on this page match ' : ' match '}
                    &ldquo;{trimmedGlobal}&rdquo;
                    {many ? ' only in hidden columns: ' : ' only in a hidden column: '}
                    <span className="font-medium text-foreground">{named}</span>
                    {more > 0 && ` +${more} more`}
                  </span>
                  <Button
                    variant="link" size="xs" className="px-0"
                    onClick={() => {
                      // The layout is the operator's own work and this
                      // write is PERSISTED per-user across devices — a
                      // one-click link must not permanently edit a
                      // curated view with no way back.  Same Undo shape
                      // as deleting a saved tab.
                      const before = columnVisibility;
                      setColumnVisibility((prev) => {
                        const next = { ...prev };
                        for (const src of searchNote.sources) next[src.key] = true;
                        return next;
                      });
                      setRevealedCol(searchNote.sources[0].key);
                      toast(
                        many
                          ? `Showing ${searchNote.sources.length} columns`
                          : `Showing “${searchNote.sources[0].label}”`,
                        { action: { label: 'Undo', onClick: () => setColumnVisibility(before) } },
                      );
                    }}
                  >
                    {many ? 'Show columns' : 'Show column'}
                  </Button>
                </>
              ) : (
                // Nothing to reveal: these rows matched a ``searchKey``
                // row field that is not a column anywhere.  Say so
                // rather than offering a button that would do nothing.
                <span>
                  {searchNote.unexplained === 1 ? '1 row' : `${searchNote.unexplained} rows`}
                  {paged ? ' on this page match ' : ' match '}
                  &ldquo;{trimmedGlobal}&rdquo; in data that isn&rsquo;t shown as a column
                  {/* No column to reveal — but if the page opens a row,
                      that IS the recovery, so don't leave a dead end. */}
                  {onRowClick ? '. Open the row to see it.' : '.'}
                </span>
              )}
            </div>
          );
        })()}
      </div>
      )}
      {pivotOn ? (
        // PIVOT MODE — a report, not a record list.  Fed the SAME
        // post-segment/filter/search rows the footer aggregation reduces,
        // so the pivot's numbers can never disagree with the grid's.
        // Under fillHeight the card has a DEFINITE height and clips
        // (``overflow-hidden``), and PivotView caps nothing vertically —
        // so the matrix needs its own vertical scroller here or a tall
        // report is silently cut off with no way to reach the rest.
        // Horizontal scrolling stays PivotView's (its sticky row-label
        // column depends on being inside that scroller).
        <div className={cn(fills && 'flex-1 min-h-0')}>
            <PivotView
              fill={fills}
              onRowCount={setPivotRowCount}
              onHiddenColumns={setPivotHiddenCols}
              rows={pivotSourceRows}
              model={pivotModel}
              columns={pivotColumns}
              padding={padding}
              onModelChange={setPivotModel}
              onOpenPanel={() => setPivotPanelOpen(true)}
            />
        </div>
      ) : (
      /* ``min-h-[16rem]`` rather than ``min-h-0`` is the floor: the body
         grows to fill, but a cramped viewport (phone, or a page with a
         tall header above the grid) stops it collapsing to a slit — the
         page's own scroll region takes over instead.  A COLUMN, because
         under fillHeight the horizontal scrollbar stops being an overlay
         and becomes the row below the body (see below). */
      <div className={cn('relative group/grid', fills && 'flex flex-1 flex-col min-h-[16rem]')}>
      {/* The scroll REGION contract comes from components/scrolling: it
          owns the overflow on both axes, focusability + the landmark
          name, overscroll containment, and the scroll-padding that keeps
          the browser's scroll-into-view out from under the sticky header
          and the frozen columns (WCAG 2.1.1 + 2.4.11).  What stays HERE
          is what is genuinely the grid's own business — the band the
          overlay scrollbar occupies, the flex sizing, and hiding the
          native bar we repaint ourselves. */}
      <div
        ref={setScrollNode}
        {...region.props}
        // ``overflow-x: hidden`` (via the region's axis) means the native
        // horizontal bar never exists, and therefore never paints a
        // reserved track at the container's bottom the way
        // ``overflow-x: auto`` does even with ``::-webkit-scrollbar
        // { height: 0 }``.  Horizontal scrolling is driven entirely by
        // our painted bar (drag) plus the wheel bridge (trackpad swipe).
        //
        // When that bar is shown it paints as an ABSOLUTE overlay in the
        // bottom ~12px of this container — reserve that band with padding
        // so the thumb never sits on top of the last row (most visible in
        // compact density where rows are short).  Under fillHeight the
        // bar moves into normal flow BELOW this box, so there is nothing
        // to reserve.
        className={cn(
          needsHScroll && !fills && 'pb-3',
          fills && 'flex-1 min-h-0',
          // Hide the NATIVE bars when we draw our own — a vertical one
          // would run the container's full height, up beside the sticky
          // column labels, and a horizontal one would span the pinned
          // columns as though they scrolled.  Scrolling itself is
          // untouched; only the painting moves.
          // ``|| needsHScroll`` because x is ``auto`` now: a grid that
          // scrolls sideways but not vertically would otherwise show a
          // real native bar under the rows.
          (bodyScrolls || needsHScroll) && HIDE_NATIVE_SCROLLBAR,
        )}
        // The region's style goes LAST.  Spreading ``region.props`` and
        // then setting ``style`` would replace it wholesale — deleting
        // the overflow, the containment and the scroll-padding — which is
        // precisely the clobbering the contract moved into inline style
        // to prevent.  The wrapper component merges in this same order.
        style={{
          ...(stickyHeader ? { maxHeight: stickyHeader } : null),
          ...region.props.style,
        }}
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
          className={cn(
            'min-w-full caption-bottom', DENSITY_TEXT[density],
            // A definite height so the slack-absorbing row below can
            // resolve its percentage; on overflow a table's height acts
            // as a minimum, so this never truncates.
            fills && 'h-full',
          )}
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
            ref={setTheadNode}
            className={bodyScrolls ? 'sticky top-0 z-10 bg-card' : undefined}
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
                        gateSort={gateClientSideOps}
                        gateGroup={holdsPartialData}
                        gateReason={gateReason}
                        key={header.id}
                        header={header}
                        stickyHeader={bodyScrolls}
                        colConfig={columns.find(c => c.key === header.column.id)}
                        uniques={uniquesByCol[header.column.id] ?? { options: [], counts: {} }}
                        rangeBounds={rangesByCol[header.column.id]}
                        dateBounds={dateBoundsByCol[header.column.id]}
                        tableId={tableId}
                        onOpenManage={() => setManageOpen(true)}
                        onMeasureWidth={reportColumnWidth}
                        // The bulk-select master checkbox renders into the
                        // dedicated select column (always hIdx 0 when on).
                        // ``firstColumnLeading`` (expand toggle, row-number)
                        // attaches to the first DATA column, which sits one
                        // slot right when the select column is present.
                        leadingContent={
                          bulkSelection && hIdx === 0
                            ? renderSelectAll()
                            : hIdx === (bulkSelection ? 1 : 0)
                              ? firstColumnLeading?.header()
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
                        aggCurrent={aggregationModel[header.column.id] ?? null}
                        aggFns={offeredAggFns(columns.find(c => c.key === header.column.id))}
                        onSetAgg={(fn) => setColumnAgg(header.column.id, fn)}
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
                {/* ``py-8`` reads as centred when the card hugs its
                    rows, but under fillHeight the card is viewport-tall
                    and the message strands itself at the top of a large
                    blank area.  Grow the cell to the body's height so
                    the text sits in the middle of the space it's
                    actually explaining. */}
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className={cn(
                    'text-center text-muted-foreground',
                    fills ? 'h-64 align-middle' : 'py-8',
                  )}
                >
                  {/* Three different situations used to share one system
                      word. "No data" is true of an empty dataset, a
                      filtered-to-nothing view and a search miss — but only
                      the first is the operator's starting point; the other
                      two have an obvious next step, and saying nothing
                      about it strands them. */}
                  {(globalFilter || columnFilters.length > 0) ? (
                    <span className="inline-flex flex-col items-center gap-2">
                      <span>
                        {globalFilter
                          ? <>No match for &ldquo;{globalFilter}&rdquo;</>
                          : 'Nothing matches the current filters'}
                      </span>
                      <Button size="xs" variant="ghost" onClick={() => {
                        setGlobalFilter('');
                        setColumnFilters([]);
                      }}>
                        Clear {globalFilter ? 'search' : 'filters'}
                      </Button>
                    </span>
                  ) : (emptyMessage ?? 'No data')}
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
                  // Per-column group aggregates (aligned under their
                  // columns).  One real <td> per visible column — the
                  // select column carries the group checkbox, the first
                  // DATA column carries the chevron + value + count, and
                  // each aggregated column shows this group's total.  Uses
                  // ``pinnedStyle`` so pinned + select columns stay
                  // aligned; a solid ``bg-muted`` keeps them opaque over
                  // horizontally-scrolled content (like the footer).
                  if (groupAggActive) {
                    const cols = table.getVisibleLeafColumns();
                    const firstDataIdx = Math.max(
                      0, cols.findIndex(c => c.id !== SELECT_COL_ID));
                    return (
                      <TableRow
                        key={row.id}
                        onClick={() => row.toggleExpanded()}
                        className="cursor-pointer"
                      >
                        {cols.map((col, i) => {
                          const aggFn = aggregationModel[col.id];
                          const isLabelCell = i === firstDataIdx;
                          // Keep the group identity (chevron + value +
                          // count) pinned at the left edge while scrolling
                          // right to the aggregate columns — the old
                          // colSpan row did this with a sticky-left inner
                          // span; the per-column label lives in a real
                          // cell, so pin THAT cell (just after the pinned
                          // cluster) when its column isn't already
                          // operator-pinned.  Otherwise the label scrolls
                          // away and you lose track of which group's
                          // totals you're reading.
                          const labelSticky = isLabelCell && !col.getIsPinned();
                          const cellStyle: React.CSSProperties = labelSticky
                            ? {
                                position: 'sticky',
                                left: pinnedLeftWidth,
                                zIndex: 3,
                                width: hasUserWidths ? col.getSize() : undefined,
                              }
                            : {
                                ...pinnedStyle(col, false),
                                width: hasUserWidths ? col.getSize() : undefined,
                              };
                          return (
                            <td
                              key={col.id}
                              style={cellStyle}
                              className={cn(DENSITY_GROUP_ROW[density], 'bg-muted')}
                            >
                              {col.id === SELECT_COL_ID ? (
                                bulkSelection && (
                                  <span onClick={(e) => e.stopPropagation()}>
                                    {renderGroupBox(row.subRows
                                      .filter(r => !isRowSelectable || isRowSelectable(r.original as Record<string, unknown>))
                                      .map(r => r.id))}
                                  </span>
                                )
                              ) : i === firstDataIdx ? (
                                <span className="inline-flex items-center gap-2 min-w-0">
                                  <ChevronRight
                                    size={14}
                                    aria-hidden="true"
                                    className={cn(
                                      'shrink-0 text-muted-foreground transition-transform',
                                      row.getIsExpanded() && 'rotate-90',
                                    )}
                                  />
                                  <span className="font-medium text-foreground truncate">
                                    {renderGroupValue(value, leafOriginals[0])}
                                  </span>
                                  <span className="shrink-0 text-xs text-muted-foreground">
                                    ({row.subRows.length})
                                  </span>
                                </span>
                              ) : aggFn ? (
                                <span className="font-semibold text-primary tabular-nums whitespace-nowrap">
                                  {renderAggCell(col.id, aggFn, leafOriginals)}
                                </span>
                              ) : null}
                            </td>
                          );
                        })}
                      </TableRow>
                    );
                  }
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
                                {renderGroupValue(value, leafOriginals[0])}
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
                const rowMenu = rowActions ? rowActions(row.original) : [];
                const cells = row.getVisibleCells().map((cell, cIdx) => (
                  <PinnedBodyCell
                    key={cell.id}
                    cell={cell}
                    padding={padding}
                    selected={isSelected}
                    zebra={isZebra}
                    // Indent the first DATA cell of leaf rows under an
                    // expanded group so children read as nested (the
                    // select column, when present, keeps its fixed
                    // width — indenting it would just shift checkboxes).
                    indent={cIdx === (bulkSelection ? 1 : 0) && !!rowGroupBy}
                    // The per-row checkbox renders into the dedicated
                    // select column (cIdx 0 when on); firstColumnLeading
                    // rides the first DATA column, one slot right.
                    leadingContent={
                      bulkSelection && cIdx === 0
                        ? (isRowSelectable && !isRowSelectable(row.original)
                            ? undefined
                            : renderRowBox(row.id, row.original))
                        : cIdx === (bulkSelection ? 1 : 0)
                          ? firstColumnLeading?.cell?.(row.original)
                          : undefined
                    }
                  />
                ));
                const rowProps: React.ComponentPropsWithoutRef<'tr'> & { 'data-state'?: 'selected' } = {
                  onClick: (e) => handleRowClick(e, row.id, row.original),
                  'data-state': isSelected ? 'selected' : undefined,
                  // 3px inset shadow on the left edge — paints the
                  // primary-coloured accent stripe without using
                  // ``border-l`` (which would shift the row by 3px because
                  // ``<tr>`` borders interact with ``border-collapse``
                  // differently than divs).
                  style: isSelected ? { boxShadow: 'inset 3px 0 0 0 var(--primary)' } : undefined,
                  className: cn(
                    // A clickable row needs a hover the eye actually
                    // catches.  The base ``hover:bg-muted/50`` is only a
                    // 20% step up from the ``bg-muted/30`` zebra, so on
                    // alternate rows the primary navigation of the page
                    // had almost no affordance.  ``cn`` is tailwind-merge,
                    // so this later utility deterministically wins.
                    onRowClick ? 'cursor-pointer hover:bg-muted' : '',
                    isZebra && !isSelected && 'bg-muted/30',
                    // Primary-tinted background on selected rows wins over
                    // zebra so the multi-row selection stays visible.
                    isSelected && 'bg-primary/10 hover:bg-primary/15',
                  ),
                };
                // Right-click menu wraps the ROW: the trigger MERGES onto
                // the <tr> (render prop) — a <span> can't legally wrap a
                // table row.  No actions → a plain row, zero overhead.
                return rowMenu.length > 0 ? (
                  <ContextMenu key={row.id} items={rowMenu} render={<TableRow {...rowProps} />}>
                    {cells}
                  </ContextMenu>
                ) : (
                  <TableRow key={row.id} {...rowProps}>
                    {cells}
                  </TableRow>
                );
              })
            )}
            {/* Slack absorber — the same problem the pivot matrix had:
                ``sticky bottom-0`` on the totals row only pins it once
                the rows OVERFLOW, so on a short page the total hugged
                the last row and floated mid-card with blank space
                beneath.  A ``height: 100%`` row takes the surplus and
                collapses when the rows do overflow. */}
            {fills && rowCount > 0 && (
              <TableRow aria-hidden className="h-full hover:bg-transparent border-0">
                <td colSpan={table.getVisibleLeafColumns().length} />
              </TableRow>
            )}
          </TableBody>
          {/* Aggregation footer — one total row, rendered only when a
              model is active.  Cells reuse ``pinnedStyle`` so they stay
              column-aligned under pinned + select columns during
              horizontal scroll; a solid ``bg-muted`` on every cell keeps
              them opaque over scrolled content.  Gate on a VISIBLE
              aggregated column (not the raw key count) so hiding the
              last aggregated column doesn't leave an empty totals bar
              with its divider and nothing in it.

              ANCHORED when the body scrolls, by the same trick as the
              header (sticky on the section element, not per-cell — so
              the pinned cells' own z-indexes stay relative to it and
              nothing needs re-layering).  Otherwise a column header
              advertising "sum" points at a number 250 rows below it:
              the label is a promise the reader can't collect.  When the
              PAGE scrolls there's nothing to stick to, so it stays in
              normal flow exactly as before. */}
          {table.getVisibleLeafColumns().some(c => c.id in footerAgg) && (
            <tfoot className={bodyScrolls ? 'sticky bottom-0 z-10' : undefined}>
              <tr className="border-t-2 border-border">
                {table.getVisibleLeafColumns().map((col) => (
                  <td
                    key={col.id}
                    style={{
                      ...pinnedStyle(col, false),
                      width: hasUserWidths ? col.getSize() : undefined,
                    }}
                    className={cn(
                      padding,
                      'bg-muted font-semibold text-primary tabular-nums whitespace-nowrap',
                    )}
                  >
                    {footerAgg[col.id] ?? null}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {/* Scrollbars — one shared implementation for the record list and
          the pivot matrix (./scrollbars.tsx), which is where the reasons
          live: a bar over only the scrollable region so pinned columns
          don't appear to scroll, and a vertical bar that starts below
          the sticky header instead of running up beside the column ⋮
          menus.  ``flow`` under fillHeight puts the horizontal bar in
          normal flow below the body rather than overlaying the row at
          the viewport's edge. */}
      <ScrollbarH
        el={scrollEl}
        insetLeft={pinnedLeftWidth}
        insetRight={pinnedRightWidth}
        flow={fills}
      />
      {/* ``headerHeight > 0`` is not an optimisation — it is the whole
          contract.  The bar is offset below the sticky header so it
          never runs up beside the column labels and their ⋮ menus (the
          reason it is custom at all), and that offset IS the measured
          height.  Rendering before the measurement lands would put a
          full-height bar against the header for a frame, which is the
          one thing it exists to avoid. */}
      {bodyScrolls && headerHeight > 0 && (
        <ScrollbarV el={scrollEl} insetTop={headerHeight} />
      )}
      </div>
      )}
      </div>
      {/* Pagination footer — skipped when ``enablePagination={false}``
          (short lists where paginating 5-20 rows adds noise).  The
          border-t lives on the div itself so it disappears with the
          section, leaving the table body flush with the card bottom. */}
      {/* PIVOT's footer — the SAME shell as the pagination bar below, in
          the same slot, so the card's bottom edge doesn't change shape
          when you switch modes.  Paging is meaningless on a cross-tab,
          so the right-hand cluster is the report's row count instead;
          the left-hand filter hint is as true here as it is there. */}
      {pivotOn && (
      <div className="flex flex-wrap items-center justify-between p-3 gap-3 bg-muted border-t border-border">
        <p className="text-xs text-muted-foreground">
          {narrowingHint}
        </p>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {pivotHiddenCols > 0 && (
            <span className="tabular-nums">
              {pivotHiddenCols.toLocaleString()} empty column{pivotHiddenCols === 1 ? '' : 's'} hidden
            </span>
          )}
          {/* Asked for, nothing to do.  Silence here read as a broken
              checkbox: you tick it, the matrix doesn't change, and nothing
              says the request was even understood. */}
          {pivotModel.hideEmptyColumns && pivotHiddenCols === 0 && (
            <span>no empty columns to hide</span>
          )}
          <span className="tabular-nums">
            Total rows: {pivotRowCount.toLocaleString()}
          </span>
        </div>
      </div>
      )}
      {enablePagination && !pivotOn && (
      <div className="flex flex-wrap items-center justify-between p-3 gap-3 bg-muted border-t border-border">
        {/* Filter-status hint on the left — only when active, so the
            footer stays quiet on the default view.  Pagination
            cluster on the right mirrors the reference layout
            (Show per page · range · prev/next). */}
        <p className="text-xs text-muted-foreground">
          {narrowingHint}
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
            // Server-paged: the grid holds one page, so its own row count
            // would read "1-25 of 25" on every page.  The true total is
            // the one the page fetched with.
            const totalFiltered = manualPagination && totalRows !== undefined
              ? totalRows
              : table.getFilteredRowModel().rows.length;
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
      </div>{/* end grid column */}
      {pivotEnabled && pivotPanelOpen && (
        <PivotPanel
          columns={pivotColumns}
          model={pivotModel}
          onChange={setPivotModel}
          onClose={() => setPivotPanelOpen(false)}
          enabled={pivotOn}
          onEnabledChange={setPivotEnabled}
          width={pivotPanelWidth}
          onWidthChange={setPivotPanelWidth}
          fill={fills}
        />
      )}
      </div>{/* end grid + panel row */}
      </div>{/* end card wrapper */}
      {/* Active-filters popover — one row per narrowing filter with a
          per-item clear, plus clear-all.  The mirror image of the
          Columns popover, so "what's limiting my view?" always has a
          single place to look. */}
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
      {savedTabsEnabled && tabDialog && (
        <SavedTabDialog
          open
          onOpenChange={(o) => { if (!o) setTabDialog(null); }}
          columns={columns}
          data={sourceData}
          title={tabDialog === 'new' ? 'New tab' : 'Edit tab'}
          saveLabel={tabDialog === 'new' ? 'Save tab' : 'Save changes'}
          initialName={tabDialog === 'new' ? '' : tabDialog.name}
          initialFilters={tabDialog === 'new' ? columnFilters : tabDialog.filters}
          initialSearch={tabDialog === 'new' ? (hasSearch ? globalFilter : '') : (tabDialog.search ?? '')}
          initialTone={tabDialog === 'new' ? undefined : tabDialog.tone}
          initialIcon={tabDialog === 'new' ? undefined : tabDialog.icon}
          capturedSort={(() => {
            // Mirror EXACTLY what commitTab will persist, so the note
            // never disagrees: live sort for a new tab or the active
            // tab; otherwise the tab's own saved sort.
            const useLive = tabDialog === 'new' || tabDialog.id === activeTabId;
            const s = useLive ? sorting : (tabDialog.sort ?? []);
            if (!s.length) return undefined;
            const c = columns.find(col => col.key === s[0].id);
            return `sorted by ${c?.label ?? s[0].id} ${s[0].desc ? '↓' : '↑'}`
              + (s.length > 1 ? ` +${s.length - 1}` : '');
          })()}
          onSave={commitTab}
        />
      )}
      {/* The selection action bar is the TOP strip above the table
          (see near the toolbar) — no floating bottom bar. */}
    </div>
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
  /** Sorting happens locally on a slice — it would order a fragment. */
  gateSort?: boolean;
  /** Row-grouping is always local, so a slice groups a fragment. */
  gateGroup?: boolean;
  /** Full reason + both remedies, for the gated items' tooltip. */
  gateReason?: string;
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
  /** Aggregation — the column's active footer function (null = none),
   *  the functions offered, and the setter.  ``aggregable`` gates
   *  whether the ⋮ menu shows the Aggregate submenu at all. */
  aggCurrent: AggFn | null;
  aggFns: readonly AggFn[];
  onSetAgg: (fn: AggFn | null) => void;
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
  rowGrouped, onRowGroup, aggCurrent, aggFns, onSetAgg,
  fixedWidths, onAutosize, densityClass, gateSort, gateGroup, gateReason,
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
  // Never let the operator hide the LAST visible data column — a table
  // with zero columns paints blank and takes its own 3-dot menu with it,
  // stranding the user (no way back to "Manage columns…").  When one
  // hideable column is left, its "Hide column" item disables and steers
  // to the column manager instead.  Locked/structural columns (the
  // bulk-select checkbox) don't count as data columns here.
  const canHideThisColumn = header.getContext().table
    .getVisibleLeafColumns()
    .filter((c) => c.getCanHide()).length > 1;
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
  // The active filter's VALUE, on hover.  Was a native ``title=`` — the
  // design system bans it (unthemed, and a touch user can never see it),
  // and this is the one place a header says WHAT it's filtered to.
  const filterSummary = isFiltered
    ? isRangeFilter
      ? `${headerText}: ${rangeFilter[0] ?? '−∞'} – ${rangeFilter[1] ?? '+∞'}`
      : isDateRangeFilter
        ? `${headerText}: ${dateRangeFilter[0] ?? '−∞'} – ${dateRangeFilter[1] ?? '+∞'}`
        : `${headerText}: ${filterValueArr.join(', ')}`
    : '';

  const labelInner = (
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
          ``scrollWidth`` (full text even while ellipsized).  When the
          column is aggregated, the function name sits as a muted
          micro-label directly beneath (MUI's "Gross / sum" pattern). */}
      {aggCurrent ? (
        <span className="inline-flex flex-col min-w-0 leading-tight">
          <span className="truncate" data-col-label>{labelNode}</span>
          <span className="text-3xs font-normal text-muted-foreground normal-case">
            {AGG_FN_LABELS[aggCurrent].toLowerCase()}
          </span>
        </span>
      ) : (
        <span className="truncate" data-col-label>{labelNode}</span>
      )}
      {sortIndicator && <span className="shrink-0 inline-flex">{sortIndicator}</span>}
    </span>
  );

  const labelContent = filterSummary
    ? <Tip label={filterSummary}>{labelInner}</Tip>
    : labelInner;

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
              gateSort={gateSort}
              gateGroup={gateGroup}
              gateReason={gateReason}
              sorted={sortedRaw === 'asc' || sortedRaw === 'desc' ? sortedRaw : false}
              onSortAsc={() => header.column.toggleSorting(false)}
              onSortDesc={() => header.column.toggleSorting(true)}
              onClearSort={() => header.column.clearSorting()}
              onHide={() => header.column.toggleVisibility(false)}
              canHide={canHideThisColumn}
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
              aggregable={colConfig?.aggregable === true}
              aggFns={aggFns}
              aggCurrent={aggCurrent}
              onSetAgg={onSetAgg}
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
          className={cn(
            'group/resize absolute top-0 z-10 flex h-full w-2 cursor-col-resize select-none touch-none',
            // Between two columns the strip STRADDLES the boundary
            // (-right-1) with its hairline centred, so the line lands
            // exactly on the edge and both sides are easy to grab.
            // The last column has no neighbour, so straddling hangs 4px
            // of grab-strip past the table — and the hairline reads as a
            // stray vertical line floating after the final column
            // rather than as that column's edge.  Same line, same
            // place, nothing outside.
            isLastVisible ? 'right-0 justify-end' : '-right-1 justify-center',
          )}
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
