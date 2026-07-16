/**
 * Parts — the per-account parts master data (features/parts).
 *
 * The catalog collects ITSELF: every work-order line and Datatruck
 * sync resolves its part name into the catalog (alias-aware), so this
 * page is a lens over invoice truth, not a data-entry chore.  Rows
 * drill into the part profile (recurrence per vehicle, price per
 * vendor, purchase history); dedup/merge and edits live there too.
 */
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Cog } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import type { CatalogPart, AnyColumn } from '../../types';

function money(v: unknown): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const partColumns: AnyColumn[] = [
  {
    key: 'name', label: 'Part', sortable: true,
    render: (v, row) => {
      const r = row as unknown as CatalogPart;
      return (
        <span className="inline-flex items-center gap-2">
          <span className="font-medium">{String(v)}</span>
          {r.part_number && (
            <span className="font-mono text-2xs text-muted-foreground">{r.part_number}</span>
          )}
        </span>
      );
    },
  },
  {
    key: 'usage_count', label: 'Uses', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
  },
  {
    key: 'total_spent', label: 'Total Spent', sortable: true,
    render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
  },
  {
    key: 'notes', label: 'Notes', sortable: false,
    render: (v) => (v
      ? <span className="text-muted-foreground truncate max-w-sm inline-block align-bottom">{String(v)}</span>
      : <span className="text-muted-foreground">—</span>),
  },
];

export default function Parts() {
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery<{ parts: CatalogPart[] }>({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON<{ parts: CatalogPart[] }>('/parts'),
  });
  const parts = data?.parts ?? [];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        icon={Cog}
        title="Parts"
        description="Every part your invoices mention, deduplicated into one catalog. Open a part to see which trucks keep needing it and what each shop charges."
      />

      {error ? (
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load parts'} />
      ) : isLoading ? (
        <TableSkeleton rows={8} cols={4} />
      ) : parts.length === 0 ? (
        <EmptyState
          icon={Cog}
          title="No parts yet"
          description="Parts appear here automatically as work-order line items are saved — no manual entry needed."
        />
      ) : (
        <DataGrid
          tableId="parts-catalog"
          columns={partColumns}
          data={parts as unknown as Record<string, unknown>[]}
          searchKey={['name', 'part_number', 'notes']}
          searchPlaceholder="Search parts…"
          onRowClick={(row) => navigate(`/parts/${(row as unknown as CatalogPart).id}`)}
        />
      )}
    </div>
  );
}
