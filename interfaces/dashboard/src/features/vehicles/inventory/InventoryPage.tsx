/**
 * Fleet-wide Inventory — every tracked item across every truck.
 *
 * Answers the cross-vehicle questions the per-truck card can't:
 * "where is fuel card •••7213?" (search by identifier), "show me every
 * missing item" (status filter), "what's riding in spare?".  Row click
 * jumps to the truck's detail page, where the per-truck card owns all
 * actions — this page is a read/locate surface, not a second editor.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Boxes, Plus } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import DataGrid from '../../../components/datagrid';
import { Button } from '../../../components/ui/button';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { AddItemDialog, ItemDialog } from './ItemDialog';
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
          <Icon className="text-muted-foreground shrink-0 size-3.5" />
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
  {
    key: 'unit_number',
    label: 'Vehicle',
    sortable: true,
    // The row itself opens the item dialog (manage in place); this cell
    // is the jump to the truck's page.
    render: (v, row) => {
      const r = row as unknown as FleetItem;
      const qs = r.company_code ? `?company=${encodeURIComponent(r.company_code)}` : '';
      return (
        <Link
          to={`/vehicles/${encodeURIComponent(String(v ?? ''))}${qs}`}
          onClick={(e) => e.stopPropagation()}
          className="text-primary hover:underline"
        >
          {String(v ?? '')}
        </Link>
      );
    },
  },
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
      <span className={`px-2 py-0.5 rounded-md text-xs border ${statusClasses(String(v ?? ''))}`}>
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
  const { has } = useViewPermissions();
  const canManage = has('can_manage_vehicles');
  const [addOpen, setAddOpen] = useState(false);
  const [selected, setSelected] = useState<FleetItem | null>(null);
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
        description="Every tracked item across the fleet — search by serial or card number, filter missing/damaged. Click a row to open its truck."
        actions={canManage ? (
          <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
            <Plus /> Add item
          </Button>
        ) : undefined}
      />
      {canManage && addOpen && (
        <AddItemDialog
          categories={data?.categories ?? []}
          onClose={() => setAddOpen(false)}
        />
      )}
      {selected && (
        <ItemDialog
          vehicleName={selected.unit_number}
          company={selected.company_code || undefined}
          item={selected}
          statuses={data?.statuses ?? []}
          canManage={canManage}
          onClose={() => setSelected(null)}
        />
      )}
      {isLoading ? (
        <CardSkeleton />
      ) : error ? (
        <ErrorState title="Could not load the fleet inventory" />
      ) : (
        <DataGrid
          tableId="vehicle-inventory-fleet"
          columns={COLUMNS}
          data={rows}
          // Personal scope tabs: filter e.g. Category = Camera, the "+"
          // new-tab button → a saved "Cameras" tab (per-user, isolated).
          savedTabs
          searchKey={['label', 'identifier', 'unit_number']}
          // Row click manages the item right here — the same dialog the
          // truck card opens (verify / status / transfer / remove /
          // history); the Vehicle cell link jumps to the truck instead.
          onRowClick={(row) => setSelected(row as unknown as FleetItem)}
        />
      )}
    </div>
  );
}
