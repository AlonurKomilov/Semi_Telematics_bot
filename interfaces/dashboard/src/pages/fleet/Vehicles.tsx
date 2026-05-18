import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Truck } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  FilterChips,
  useLoadingStage,
} from '../../components/shell';
import type { Vehicle, VehiclesResponse } from '../../types';
import type { AnyColumn } from '../../types';

type StatusFilter = 'all' | 'moving' | 'idle' | 'stopped';
const STATUS_OPTIONS: readonly StatusFilter[] = ['all', 'moving', 'idle', 'stopped'] as const;

const columns: AnyColumn[] = [
  { key: 'name', label: 'Vehicle' },
  { key: 'company', label: 'Company' },
  {
    key: 'status',
    label: 'Status',
    render: (v) => <StatusBadge status={v as string} />,
  },
  { key: 'address', label: 'Location' },
  {
    key: 'fuel_percent',
    label: 'Fuel',
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'def_percent',
    label: 'DEF',
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'fault_count',
    label: 'Faults',
    render: (v) => (v as number) > 0 ? <span className="text-orange-600 dark:text-orange-400 font-medium">{v as number}</span> : '0',
  },
  {
    key: 'odometer_miles',
    label: 'Odometer',
    render: (v) => v != null
      ? `${Math.round(v as number).toLocaleString()} mi`
      : <span className="text-muted-foreground">—</span>,
  },
  {
    key: 'engine_hours',
    label: 'Engine Hrs',
    render: (v) => v != null
      ? `${Math.round(v as number).toLocaleString()} h`
      : <span className="text-muted-foreground">—</span>,
  },
];

export default function Vehicles() {
  const { t } = useTranslation();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const navigate = useNavigate();

  const {
    data,
    isLoading,
    isFetching,
    error: queryError,
    refetch,
    dataUpdatedAt,
  } = useQuery<VehiclesResponse>({
    queryKey: ['fleet-vehicles', statusFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (statusFilter !== 'all') params.set('status', statusFilter);
      params.set('page_size', '200');
      return apiJSON<VehiclesResponse>(`/vehicles?${params}`);
    },
    placeholderData: (prev) => prev,
  });

  const vehicles: Vehicle[] = data?.vehicles ?? [];
  const totalCount =
    (data as unknown as { count?: number } | undefined)?.count ?? vehicles.length;
  const error =
    queryError instanceof Error ? queryError.message : queryError ? String(queryError) : '';

  const counts: Record<string, number> = { moving: 0, idle: 0, stopped: 0 };
  vehicles.forEach((v) => {
    if (v.status && counts[v.status] !== undefined) counts[v.status]++;
  });

  // Same warehouse-first pattern as the rest of the fleet — when the
  // warehouse is cold the list falls back to live Samsara, which on a
  // 100-truck fleet can take 15-30s.  useLoadingStage drives the
  // progressive feedback (Loading… → Still loading… → Retry).
  const stage = useLoadingStage(isLoading && vehicles.length === 0);

  return (
    <div>
      <PageHeader
        icon={Truck}
        title={t('vehicles.page_title')}
        description={t('vehicles.page_description')}
        actions={
          <LastUpdated
            fetchedAt={dataUpdatedAt}
            isFetching={isFetching}
            onRefresh={refetch}
          />
        }
      />

      <div className="mb-4">
        <FilterChips
          options={STATUS_OPTIONS}
          value={statusFilter}
          onChange={setStatusFilter}
          countFor={(s) =>
            s === 'all' ? totalCount : counts[s] ?? 0
          }
        />
      </div>

      {stage === 'timeout' && vehicles.length === 0 ? (
        <ErrorState
          title={t('common.loading_takes_long')}
          message={t('scorecards.loading_too_long_message')}
          onRetry={() => refetch()}
        />
      ) : error && vehicles.length === 0 ? (
        <ErrorState
          title={t('vehicles.load_failed')}
          message={error}
          onRetry={() => refetch()}
        />
      ) : isLoading && vehicles.length === 0 ? (
        <TableSkeleton
          rows={8}
          cols={7}
          message={stage === 'slow' ? t('scorecards.loading_slow') : t('common.loading')}
        />
      ) : vehicles.length === 0 ? (
        <EmptyState
          icon={Truck}
          title={t('vehicles.no_matches')}
          description={
            statusFilter === 'all'
              ? t('common.no_data')
              : t('vehicles.no_matches')
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={vehicles as unknown as Record<string, unknown>[]}
          searchKey="name"
          onRowClick={(row) =>
            navigate(`/fleet/vehicle/${encodeURIComponent(row.name as string)}`)
          }
        />
      )}
    </div>
  );
}
