/**
 * Fleet-wide Inventory — every tracked item across every truck.
 *
 * Answers the cross-vehicle questions the per-truck card can't:
 * "where is fuel card •••7213?" (search by identifier), "show me every
 * missing item" (status filter), "what's riding in spare?".  Row click
 * jumps to the truck's detail page, where the per-truck card owns all
 * actions — this page is a read/locate surface, not a second editor.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Boxes } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import DataGrid from '../../../components/DataGrid';
import { PageHeader, CardSkeleton, ErrorState } from '../../../components/shell';
import { Freshness } from '../../../components/tooltip';
import { statusClasses } from '../../../lib/status';
import type { AnyColumn } from '../../../types';
import { categoryMeta, STATUS_LABELS } from './categories';
import type { InventoryItem } from './useInventory';

interface FleetItem extends InventoryItem {
  unit_number: string;
  company_code: string;
  vehicle_type: string;
}

interface FleetInventoryResponse {
  items: FleetItem[];
  categories: string[];
  statuses: string[];
}

const COLUMNS: AnyColumn[] = [
  {
    key: 'label',
    label: 'Item',
    sortable: true,
    render: (v, row) => {
      const r = row as unknown as FleetItem;
      const { Icon } = categoryMeta(r.category);
      return (
        <span className="inline-flex items-center gap-2">
          <Icon size={14} className="text-muted-foreground shrink-0" />
          <span>{String(v ?? '')}</span>
        </span>
      );
    },
  },
  {
    key: 'category',
    label: 'Category',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as unknown as FleetItem).category ?? ''),
    filterLabel: (row) => categoryMeta((row as unknown as FleetItem).category).label,
    render: (v) => categoryMeta(String(v ?? '')).label,
  },
  {
    key: 'identifier',
    label: 'Identifier',
    render: (v) =>
      v
        ? <span className="font-mono text-xs">{String(v)}</span>
        : <span className="text-muted-foreground text-xs">—</span>,
  },
  { key: 'unit_number', label: 'Truck', sortable: true },
  {
    key: 'company_code',
    label: 'Company',
    sortable: true,
    filterable: true,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground text-xs">—</span>),
  },
  {
    key: 'status',
    label: 'Status',
    sortable: true,
    filterable: true,
    filterValue: (row) => String((row as unknown as FleetItem).status ?? ''),
    filterLabel: (row) => {
      const s = (row as unknown as FleetItem).status;
      return STATUS_LABELS[s] ?? s;
    },
    render: (v) => (
      <span className={`px-2 py-0.5 rounded-full text-xs border ${statusClasses(String(v ?? ''))}`}>
        {STATUS_LABELS[String(v)] ?? String(v ?? '')}
      </span>
    ),
  },
  {
    key: 'last_verified_at',
    label: 'Verified',
    sortable: true,
    render: (v) =>
      v ? (
        <Freshness ts={String(v)}>
          <span className="text-xs text-muted-foreground">verified</span>
        </Freshness>
      ) : (
        <span className="text-xs text-muted-foreground/60">never</span>
      ),
  },
];

export default function InventoryPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery<FleetInventoryResponse>({
    queryKey: ['vehicle-inventory-fleet'],
    queryFn: () => apiJSON('/vehicles/inventory/all'),
    staleTime: 30_000,
  });

  const rows = useMemo(
    () => (data?.items ?? []) as unknown as Record<string, unknown>[],
    [data],
  );

  return (
    <div>
      <PageHeader
        icon={Boxes}
        title="Inventory"
        description="Every tracked item across the fleet — search by serial or card number, filter missing/damaged. Click a row to open its truck, where items are managed."
      />
      {isLoading ? (
        <CardSkeleton />
      ) : error ? (
        <ErrorState title="Could not load the fleet inventory" />
      ) : (
        <DataGrid
          tableId="vehicle-inventory-fleet"
          columns={COLUMNS}
          data={rows}
          searchKey={['label', 'identifier', 'unit_number']}
          onRowClick={(row) => {
            const r = row as unknown as FleetItem;
            const qs = r.company_code ? `?company=${encodeURIComponent(r.company_code)}` : '';
            navigate(`/vehicles/${encodeURIComponent(r.unit_number)}${qs}`);
          }}
        />
      )}
    </div>
  );
}
