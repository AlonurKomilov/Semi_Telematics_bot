import { useState, useMemo, useEffect } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { ChevronUp, ChevronDown, ChevronsUpDown, Rows3, Rows2, Rows4, Search } from 'lucide-react';
import { Input } from './ui/input';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';
import { cn } from '../lib/utils';
import type { AnyColumn } from '../types';

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
  /** Field(s) to search.  String matches the legacy single-field
   *  behavior; an array searches across all listed fields with OR
   *  semantics so "turbo" in description still matches a row whose
   *  vehicle_name doesn't contain it. */
  searchKey?: string | string[];
  /**
 * when set, the table body scrolls within a fixed-height
   * container and the header stays pinned to the top.  Useful for
   * long lists (scorecards, vehicles) where the user otherwise loses
   * the column labels half-way down.  Pass a CSS length string \u2014 e.g.
   * ``"60vh"`` or ``"480px"``.  Omit for the legacy non-sticky layout.
   */
  stickyHeader?: string;
  searchPlaceholder?: string;
  /** Rendered next to the search box on the table toolbar row.
   *  Use for inline filter chips so the "narrow this list" controls
   *  live in one place instead of stacking above the table.  Wrap
   *  itself, so the chip strip can flow to a second row on narrow
   *  viewports without colliding with the density toggle on the
   *  right edge. */
  headerToolbar?: React.ReactNode;
}

export default function DataTable({
  columns, data, onRowClick, searchKey, stickyHeader, searchPlaceholder,
  headerToolbar,
}: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
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

  const tableColumns = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      columns.map((col) => {
        const def: ColumnDef<Record<string, unknown>> = {
          id: col.key,
          accessorKey: col.key,
          header: col.label,
          enableSorting: col.sortable !== false,
          cell: ({ getValue, row }) =>
            col.render
              ? col.render(getValue(), row.original)
              : (getValue() as React.ReactNode) ?? '—',
        };
        // Optional custom sort key — used for enum columns like
        // priority where alphabetical (critical < high < low < medium)
        // is wrong and a rank-based comparator (critical > high >
        // medium > low) is what operators expect.
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
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    // OR-across-fields search: a hit in any of ``searchKeys`` keeps
    // the row.  Lowercase-substring match keeps the API forgiving for
    // free-text fields (descriptions, vehicle names with mixed case).
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

  const padding = DENSITY_PADDING[density];

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between mb-3 gap-3">
        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
          {hasSearch ? (
            // Inline search-icon prefix + tighter width visually
            // distinguishes the table-level search from the global
            // ⌘K command palette that lives in the page header.
            <div className="relative max-w-xs flex-shrink-0">
              <Search
                size={14}
                aria-hidden
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
              />
              <Input
                placeholder={searchPlaceholder ?? 'Search...'}
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                className="pl-8"
              />
            </div>
          ) : null}
          {headerToolbar}
        </div>
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
      <div
        className="overflow-auto rounded-lg border border-border"
        style={stickyHeader ? { maxHeight: stickyHeader } : undefined}
      >
        <Table>
          <TableHeader
            className={stickyHeader ? 'sticky top-0 z-10 bg-card' : undefined}
          >
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="bg-card hover:bg-card">
                {headerGroup.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead
                      key={header.id}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                      className={cn(
                        'text-muted-foreground font-medium',
                        canSort && 'cursor-pointer select-none hover:text-foreground',
                        stickyHeader && 'bg-card',
                      )}
                    >
                      <div className="flex items-center gap-1">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          sorted === 'asc'  ? <ChevronUp size={14} /> :
                          sorted === 'desc' ? <ChevronDown size={14} /> :
                          <ChevronsUpDown size={14} className="opacity-30" />
                        )}
                      </div>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {rowCount === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="py-8 text-center text-muted-foreground">
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
      <p className="text-xs text-muted-foreground mt-2">
        {rowCount} {rowCount === 1 ? 'row' : 'rows'}
      </p>
    </div>
  );
}


