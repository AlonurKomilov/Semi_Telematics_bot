import { useState, useMemo, useEffect, useRef } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
  type ColumnOrderState,
  type Header,
} from '@tanstack/react-table';
import {
  ChevronUp, ChevronDown, ChevronsUpDown, Rows3, Rows2, Rows4,
  Search, Filter, X, Columns3,
} from 'lucide-react';
import {
  DndContext, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, horizontalListSortingStrategy, arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Input } from './ui/input';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';
import { cn } from '../lib/utils';
import type { AnyColumn } from '../types';
import ColumnFilterMenu from './ColumnFilterMenu';
import ColumnHeaderMenu from './ColumnHeaderMenu';
import ManageColumnsMenu from './ManageColumnsMenu';

type Density = 'compact' | 'default' | 'roomy';
const DENSITY_KEY = '4truck.table.density';

const DENSITY_PADDING: Record<Density, string> = {
  compact: 'py-1.5',
  default: 'py-3',
  roomy: 'py-4',
};

interface DataTableProps {
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
}

// ── localStorage helpers ──────────────────────────────────────
//
// Keys are namespaced per-table so two tables on the same page (rare
// today, but coming as we add splits) don't clobber each other.
const visibilityKey = (id: string) => `4truck.table.${id}.visibility`;
const orderKey      = (id: string) => `4truck.table.${id}.order`;

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch { return fallback; }
}

function saveJSON(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

export default function DataTable({
  columns, data, onRowClick, searchKey, stickyHeader, searchPlaceholder,
  headerToolbar, tableId,
}: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [density, setDensity] = useState<Density>(() => {
    try {
      const v = localStorage.getItem(DENSITY_KEY);
      if (v === 'compact' || v === 'default' || v === 'roomy') return v;
    } catch { /* ignore */ }
    return 'default';
  });
  useEffect(() => {
    try { localStorage.setItem(DENSITY_KEY, density); } catch { /* ignore */ }
  }, [density]);

  // ── Visibility + order state (Tier-1 features) ─────────────
  //
  // Both are persisted per ``tableId``.  Without ``tableId`` the
  // state is in-memory only — and since the 3-dot menu / drag
  // handles are gated behind ``tableId``, those tables effectively
  // get the legacy fixed-layout behaviour for free.
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    () => tableId ? loadJSON<VisibilityState>(visibilityKey(tableId), {}) : {},
  );
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(
    () => tableId ? loadJSON<ColumnOrderState>(orderKey(tableId), []) : [],
  );

  // Reconcile stored ids with the current column config.  Stale ids
  // (column renamed / removed) are dropped; new ids appended in
  // declaration order so a freshly-added column appears at the end
  // rather than being silently absent.
  useEffect(() => {
    if (!tableId) return;
    const allIds = columns.map(c => c.key);
    setColumnOrder((prev) => {
      if (prev.length === 0) return allIds;
      const validPrev = prev.filter(id => allIds.includes(id));
      const missing   = allIds.filter(id => !validPrev.includes(id));
      const next = [...validPrev, ...missing];
      // Avoid an extra render when nothing changed.
      const same = next.length === prev.length && next.every((id, i) => id === prev[i]);
      return same ? prev : next;
    });
  }, [columns, tableId]);

  useEffect(() => {
    if (!tableId) return;
    saveJSON(visibilityKey(tableId), columnVisibility);
  }, [tableId, columnVisibility]);

  useEffect(() => {
    if (!tableId) return;
    saveJSON(orderKey(tableId), columnOrder);
  }, [tableId, columnOrder]);

  const tableColumns = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      columns.map((col) => {
        const def: ColumnDef<Record<string, unknown>> = {
          id: col.key,
          accessorKey: col.key,
          header: col.label,
          enableSorting: col.sortable !== false,
          enableColumnFilter: col.filterable === true,
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
          def.filterFn = (row, _colId, filterValue) => {
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

  const table = useReactTable({
    data,
    columns: tableColumns,
    state: {
      sorting,
      globalFilter: hasSearch ? globalFilter : undefined,
      columnFilters,
      columnVisibility,
      columnOrder,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
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

  const uniquesByCol = useMemo(() => {
    const out: Record<string, {
      options: Array<{ value: string; label: string }>;
      counts: Record<string, number>;
    }> = {};
    for (const col of columns) {
      if (!col.filterable) continue;
      const counts: Record<string, number> = {};
      const labelByValue: Record<string, string> = {};
      for (const row of data) {
        const v = col.filterValue
          ? col.filterValue(row)
          : String((row as Record<string, unknown>)[col.key] ?? '');
        const lab = col.filterLabel ? col.filterLabel(row) : v;
        counts[v] = (counts[v] ?? 0) + 1;
        if (!(v in labelByValue)) labelByValue[v] = lab;
      }
      out[col.key] = {
        options: Object.keys(counts).map(v => ({ value: v, label: labelByValue[v] })),
        counts,
      };
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
  const hiddenCount = useMemo(
    () => columns.filter(c => columnVisibility[c.key] === false).length,
    [columns, columnVisibility],
  );

  const visibleHeaderGroups = table.getHeaderGroups();
  // Sortable context needs the list of column ids currently rendered
  // (after visibility filter) so drag swaps reorder the right slice.
  const visibleColIds = useMemo(
    () => visibleHeaderGroups[0]?.headers.map(h => h.column.id) ?? [],
    [visibleHeaderGroups],
  );

  const sensors = useSensors(
    // 5px activation distance — short label clicks still open the
    // filter popover; only an actual drag triggers reorder.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setColumnOrder((prev) => {
      const base = prev.length ? prev : columns.map(c => c.key);
      const oldIdx = base.indexOf(active.id as string);
      const newIdx = base.indexOf(over.id as string);
      if (oldIdx === -1 || newIdx === -1) return prev;
      return arrayMove(base, oldIdx, newIdx);
    });
  };

  const padding = DENSITY_PADDING[density];

  // Resetting visibility/order: empty objects let tanstack-table fall
  // back to "all visible, declaration order".  We persist the empty
  // shape too so the next reload also defaults.
  const resetLayout = () => {
    setColumnVisibility({});
    setColumnOrder([]);
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
      .map(c => ({ id: c.key, label: c.label }));
  }, [columns, columnOrder]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between mb-3 gap-3">
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
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
          {headerToolbar}
          {columnFilters.length > 0 && (
            <button
              type="button"
              onClick={() => setColumnFilters([])}
              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-primary border border-primary/30 bg-primary/10 hover:bg-primary/15 rounded-md"
            >
              <X size={12} aria-hidden="true" />
              Clear filters
              <span className="text-2xs text-primary/70">
                ({columnFilters.length})
              </span>
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {tableId && (
            <button
              ref={manageAnchorRef}
              type="button"
              onClick={() => setManageOpen((o) => !o)}
              aria-label="Manage columns"
              title="Show / hide columns"
              className={cn(
                'inline-flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs border border-border text-muted-foreground hover:text-foreground hover:bg-accent',
                hiddenCount > 0 && 'text-primary border-primary/30 bg-primary/10',
              )}
            >
              <Columns3 size={14} />
              <span>Columns</span>
              {hiddenCount > 0 && (
                <span className="text-2xs bg-primary/15 text-primary px-1 rounded">
                  {hiddenCount} hidden
                </span>
              )}
            </button>
          )}
          <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md" role="group" aria-label="Row density">
            {([
              { v: 'compact', icon: Rows4, label: 'Compact' },
              { v: 'default', icon: Rows3, label: 'Default' },
              { v: 'roomy', icon: Rows2, label: 'Roomy' },
            ] as const).map(({ v, icon: Icon, label }) => (
              <button
                key={v}
                onClick={() => setDensity(v)}
                aria-label={label}
                aria-pressed={density === v}
                title={label}
                className={`p-1.5 rounded ${
                  density === v
                    ? 'bg-card text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>
      </div>
      <div
        className="overflow-auto rounded-lg border border-border"
        style={stickyHeader ? { maxHeight: stickyHeader } : undefined}
      >
        <Table>
          <TableHeader
            className={stickyHeader ? 'sticky top-0 z-10 bg-card' : undefined}
          >
            {visibleHeaderGroups.map((headerGroup) => (
              <DndContext
                key={headerGroup.id}
                sensors={sensors}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={visibleColIds}
                  strategy={horizontalListSortingStrategy}
                >
                  <TableRow className="bg-card hover:bg-card">
                    {headerGroup.headers.map((header) => (
                      <ColumnHeaderCell
                        key={header.id}
                        header={header}
                        stickyHeader={!!stickyHeader}
                        uniques={uniquesByCol[header.column.id] ?? { options: [], counts: {} }}
                        tableId={tableId}
                        onOpenManage={() => setManageOpen(true)}
                      />
                    ))}
                  </TableRow>
                </SortableContext>
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
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  onClick={() => onRowClick?.(row.original)}
                  className={onRowClick ? 'cursor-pointer' : ''}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className={padding}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-baseline justify-between mt-2 gap-3">
        <p className="text-xs text-muted-foreground">
          {columnFilters.length > 0 || globalFilter
            ? `${rowCount} of ${data.length} ${data.length === 1 ? 'row' : 'rows'} · ${columnFilters.length} filter${columnFilters.length === 1 ? '' : 's'} active`
            : `${rowCount} ${rowCount === 1 ? 'row' : 'rows'}`}
        </p>
      </div>
      {tableId && (
        <ManageColumnsMenu
          options={manageOptions}
          visibility={columnVisibility}
          onToggle={(id) =>
            setColumnVisibility((prev) => ({
              ...prev,
              [id]: prev[id] === false ? true : false,
            }))
          }
          onReset={() => {
            resetLayout();
            setManageOpen(false);
          }}
          open={manageOpen}
          onOpenChange={setManageOpen}
          anchor={manageAnchorRef.current}
        />
      )}
    </div>
  );
}

// ── Per-column header cell ──────────────────────────────────
//
// Lifted into its own component so it can use the ``useSortable`` hook
// (one per draggable item).  Owns the column's label / filter trigger
// / sort chevron / 3-dot menu cluster.
interface ColumnHeaderCellProps {
  header: Header<Record<string, unknown>, unknown>;
  stickyHeader: boolean;
  uniques: {
    options: Array<{ value: string; label: string }>;
    counts: Record<string, number>;
  };
  tableId?: string;
  onOpenManage: () => void;
}

function ColumnHeaderCell({
  header, stickyHeader, uniques, tableId, onOpenManage,
}: ColumnHeaderCellProps) {
  const canSort = header.column.getCanSort();
  const sortedRaw = header.column.getIsSorted();
  const canFilter = header.column.getCanFilter();
  const dragEnabled = !!tableId;

  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: header.column.id, disabled: !dragEnabled });

  const dragStyle: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const labelNode = flexRender(
    header.column.columnDef.header, header.getContext(),
  );
  const headerText = typeof header.column.columnDef.header === 'string'
    ? header.column.columnDef.header as string
    : header.column.id;

  const sortChevron = canSort && (
    sortedRaw === 'asc'  ? <ChevronUp size={14} /> :
    sortedRaw === 'desc' ? <ChevronDown size={14} /> :
    <ChevronsUpDown size={14} className="opacity-30" />
  );

  const filterValueArr = canFilter
    ? (header.column.getFilterValue() as string[] | undefined) ?? []
    : [];
  const isFiltered = filterValueArr.length > 0;

  // The label is the drag handle when ``tableId`` is set, otherwise
  // a plain inline span.  Filter-popover wraps the label so the
  // click still opens the filter list.
  const labelContent = (
    <span
      className={cn(
        'inline-flex items-center gap-1 select-none',
        canFilter && 'hover:text-foreground',
        canFilter && isFiltered && 'text-primary',
        dragEnabled && 'cursor-grab active:cursor-grabbing',
      )}
      title={canFilter
        ? (isFiltered
            ? `${headerText}: ${filterValueArr.join(', ')}`
            : `Filter by ${headerText}`)
        : undefined}
    >
      {labelNode}
      {canFilter && (
        <Filter
          size={12}
          aria-hidden="true"
          className={cn(isFiltered ? 'opacity-100' : 'opacity-30')}
        />
      )}
      {isFiltered && (
        <span className="text-2xs bg-primary/15 text-primary px-1 rounded">
          {filterValueArr.length}
        </span>
      )}
    </span>
  );

  // Compose the cluster — label (with optional filter wrapper), sort
  // chevron, 3-dot menu.  Layout: label left, chevron + 3-dot right.
  return (
    <TableHead
      ref={setNodeRef}
      style={dragStyle}
      className={cn(
        'text-muted-foreground font-medium group',
        stickyHeader && 'bg-card',
      )}
    >
      <div className="flex items-center gap-1">
        {/* Drag attachment surface — label + filter trigger. */}
        <span
          {...(dragEnabled ? attributes : {})}
          {...(dragEnabled ? listeners : {})}
          className="inline-flex items-center min-w-0"
        >
          {canFilter ? (
            <ColumnFilterMenu
              label={headerText}
              options={uniques.options}
              counts={uniques.counts}
              value={filterValueArr}
              onChange={(next) =>
                header.column.setFilterValue(next.length ? next : undefined)
              }
            >
              {labelContent}
            </ColumnFilterMenu>
          ) : (
            labelContent
          )}
        </span>

        {/* Sort quick-toggle — kept as a one-tap action.  The 3-dot
            menu also has explicit asc/desc/clear for screen-reader
            users and operators who prefer named actions. */}
        {canSort && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              header.column.toggleSorting();
            }}
            aria-label={`Sort by ${headerText}`}
            className="p-0.5 text-muted-foreground hover:text-foreground"
          >
            {sortChevron}
          </button>
        )}

        {/* 3-dot menu — opt-in.  Tables without ``tableId`` keep the
            legacy fixed-layout behaviour (no menu, no drag, no
            visibility editor). */}
        {tableId && (
          <span className="ml-auto">
            <ColumnHeaderMenu
              columnLabel={headerText}
              canSort={canSort}
              sorted={sortedRaw === 'asc' || sortedRaw === 'desc' ? sortedRaw : false}
              onSortAsc={() => header.column.toggleSorting(false)}
              onSortDesc={() => header.column.toggleSorting(true)}
              onClearSort={() => header.column.clearSorting()}
              onHide={() => header.column.toggleVisibility(false)}
              onManage={onOpenManage}
            />
          </span>
        )}
      </div>
    </TableHead>
  );
}
