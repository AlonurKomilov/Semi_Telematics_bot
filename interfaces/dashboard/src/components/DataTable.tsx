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
import { ChevronUp, ChevronDown, ChevronsUpDown, Rows3, Rows2, Rows4 } from 'lucide-react';
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
  searchKey?: string;
  /**
 * when set, the table body scrolls within a fixed-height
   * container and the header stays pinned to the top.  Useful for
   * long lists (scorecards, vehicles) where the user otherwise loses
   * the column labels half-way down.  Pass a CSS length string \u2014 e.g.
   * ``"60vh"`` or ``"480px"``.  Omit for the legacy non-sticky layout.
   */
  stickyHeader?: string;
  searchPlaceholder?: string;
}

export default function DataTable({
  columns, data, onRowClick, searchKey, stickyHeader, searchPlaceholder,
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
      columns.map((col) => ({
        id: col.key,
        accessorKey: col.key,
        header: col.label,
        enableSorting: col.sortable !== false,
        cell: ({ getValue, row }) =>
          col.render
            ? col.render(getValue(), row.original)
            : (getValue() as React.ReactNode) ?? '—',
      })),
    [columns],
  );

  const table = useReactTable({
    data,
    columns: tableColumns,
    state: {
      sorting,
      globalFilter: searchKey ? globalFilter : undefined,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: searchKey
      ? (row, _colId, filterValue) => {
          const val = row.original[searchKey];
          return val
            ? String(val).toLowerCase().includes(String(filterValue).toLowerCase())
            : false;
        }
      : undefined,
  });

  const rowCount = table.getRowModel().rows.length;

  const padding = DENSITY_PADDING[density];

  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-3">
        {searchKey ? (
          <Input
            placeholder={searchPlaceholder ?? 'Search...'}
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            className="max-w-xs"
          />
        ) : <span />}
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
              <Icon size={13} />
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
        {rowCount} row{rowCount !== 1 && 's'}
      </p>
    </div>
  );
}


