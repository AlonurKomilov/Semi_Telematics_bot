import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Pencil, Plus, Truck } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import StatusBadge from '../../components/StatusBadge';
import { Freshness, InfoTip, Tip } from '../../components/tooltip';
import { useInventoryAlerts } from './inventory/useInventory';
import { PackageX } from 'lucide-react';
import { toneClasses } from '../../lib/status';
import { Button } from '../../components/ui/button';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  FilterChips,
  useLoadingStage,
} from '../../components/shell';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useRoleView } from '../../context/RoleViewContext';
import type { Vehicle, VehiclesResponse } from '../../types';
import type { AnyColumn } from '../../types';
import UtilizationSummary from './UtilizationSummary';
import VehicleManageDialog from './VehicleManageDialog';

const TYPE_LABEL: Record<string, string> = {
  truck: 'Truck', trailer: 'Trailer', other: 'Other',
};

// Personas that benefit from the 30-day utilization roll-up at the top
// of the Vehicles page.  Drivers + Dispatch don't need it (drivers care
// about their own vehicle only; dispatch lives in the live view).
const UTILIZATION_PERSONAS = new Set(['owner', 'admin', 'fleet', 'accounting']);

type StatusFilter = 'all' | 'moving' | 'idle' | 'stopped' | 'no_telemetry';
const STATUS_OPTIONS: readonly StatusFilter[] = ['all', 'moving', 'idle', 'stopped', 'no_telemetry'] as const;

// Parse a full address into street / city / state parts.  The three
// Location-group columns share this heuristic so they always agree on
// how to interpret a given address.  Heuristic: split on ", "; treat
// a trailing all-digit token as ZIP and drop it; the LAST remaining
// part is the state, the previous is the city, everything before is
// the street.  Falls back gracefully for lines that don't fit.
const parseAddress = (addr: string): { street: string; city: string; state: string } => {
  const a = addr.trim();
  if (!a) return { street: '', city: '', state: '' };
  const parts = a.split(',').map(s => s.trim()).filter(Boolean);
  if (parts.length < 2) return { street: a, city: '', state: '' };
  const last = parts[parts.length - 1];
  const stateIdx = /^\d[\d-]*$/.test(last) ? parts.length - 2 : parts.length - 1;
  const cityIdx = stateIdx - 1;
  const state = parts[stateIdx] ?? '';
  const city = cityIdx >= 0 ? (parts[cityIdx] ?? '') : '';
  const street = cityIdx > 0 ? parts.slice(0, cityIdx).join(', ') : '';
  return { street, city, state };
};

const ALL_COLUMNS: AnyColumn[] = [
  { key: 'name', label: 'Vehicle', sortable: true },
  {
    key: 'vehicle_type',
    label: 'Type',
    sortable: true,
    // Type is enum (truck / trailer / other) — few unique values,
    // ideal filter target.  ``filterValue`` matches on the raw key so
    // "truck" narrows correctly; ``filterLabel`` shows "Truck" in the
    // dropdown instead of the code.
    filterable: true,
    filterValue: (row) => String((row as Vehicle).vehicle_type ?? 'truck'),
    filterLabel: (row) => TYPE_LABEL[String((row as Vehicle).vehicle_type ?? 'truck')] ?? 'Truck',
    render: (v) => TYPE_LABEL[(v as string) || 'truck'] ?? 'Truck',
  },
  {
    key: 'company', label: 'Company', sortable: true,
    // Company codes are enumerated per account (G1, PTG, OSY, CFT,
    // RMR, …) — filter lets operators narrow to a subfleet in one
    // click without typing.
    filterable: true,
  },
  {
    key: 'status',
    label: 'Status',
    sortable: true,
    // Status is moving / idle / stopped — same 3-value pattern as the
    // top-of-page chip row, but reachable per-column too.  Filter
    // matches the raw string; display capitalises it.
    filterable: true,
    filterValue: (row) => String((row as Vehicle).status ?? ''),
    filterLabel: (row) => {
      const s = String((row as Vehicle).status ?? '').toLowerCase().replace(/_/g, ' ');
      return s ? s.charAt(0).toUpperCase() + s.slice(1) : '(none)';
    },
    // Row-level freshness rides the Status badge — `time` is the
    // freshest known reading for the row (_simplify: GPS, else
    // fuel/DEF), so a frozen row announces itself in the list.
    render: (v, row) => (
      <Freshness ts={(row as Vehicle).time ?? null}>
        <StatusBadge status={v as string} />
      </Freshness>
    ),
  },
  // ── Location group: Street | City | State ─────────────────
  // The full address is split into three columns bracketed under one
  // "Location" group header (the ``group`` field).  Each piece owns
  // its own sort + filter, and no text is duplicated across columns.
  // City / State get select-mode filters (few distinct values);
  // Street stays filter-less (unique per row — the global search
  // covers it since ``searchKey`` includes ``address``).
  {
    key: 'address', label: 'Street', sortable: true,
    group: 'Location',
    // Street portion only; the full raw address stays available as a
    // hover tooltip so nothing is lost.
    render: (v) => {
      const full = String(v ?? '');
      const { street } = parseAddress(full);
      return full
        ? <Tip label={full}><span>{street || full}</span></Tip>
        : <span className="text-muted-foreground">—</span>;
    },
  },
  {
    key: '_city', label: 'City', sortable: true,
    group: 'Location',
    filterable: true,
    filterValue: (row) => parseAddress(String((row as Vehicle).address ?? '')).city || '—',
    render: (_v, row) => parseAddress(String((row as Vehicle).address ?? '')).city || <span className="text-muted-foreground">—</span>,
  },
  {
    key: '_state', label: 'State', sortable: true,
    group: 'Location',
    filterable: true,
    filterValue: (row) => parseAddress(String((row as Vehicle).address ?? '')).state || '—',
    render: (_v, row) => parseAddress(String((row as Vehicle).address ?? '')).state || <span className="text-muted-foreground">—</span>,
  },
  // Numeric columns get range-mode filter (Min/Max number inputs).
  // Bounds auto-compute from live data; ``filterRange.unit`` is what
  // shows next to the inputs in the popover.  ``step`` matches the
  // display precision (whole %/h/mi — no half-percents).
  {
    key: 'fuel_percent',
    label: 'Fuel',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, max: 100, step: 1, unit: '%' },
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'def_percent',
    label: 'DEF',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, max: 100, step: 1, unit: '%' },
    render: (v) => v != null ? `${Math.round(v as number)}%` : '—',
  },
  {
    key: 'fault_count',
    label: 'Faults',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, step: 1 },
    // Total active faults fleet-wide (or per group).
    aggregable: true, aggFns: ['sum', 'avg', 'max'],
    render: (v) => (v as number) > 0 ? <span className="text-warn font-medium">{v as number}</span> : '0',
  },
  {
    key: 'odometer_miles',
    label: 'Odometer',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, step: 1000, unit: 'mi' },
    // Odometer is a READING, not a quantity — summing readings is
    // meaningless.  Offer max (highest-mileage truck) + avg (fleet avg).
    aggregable: true, aggFns: ['max', 'avg'],
    aggFormat: (value) => `${Math.round(value).toLocaleString()} mi`,
    render: (v) => v != null
      ? `${Math.round(v as number).toLocaleString()} mi`
      : <span className="text-muted-foreground">—</span>,
  },
  {
    key: 'engine_hours',
    label: 'Engine Hrs',
    sortable: true,
    filterable: true,
    filterMode: 'range',
    filterRange: { min: 0, step: 100, unit: 'h' },
    // Also a meter READING — max / avg, never sum.
    aggregable: true, aggFns: ['max', 'avg'],
    aggFormat: (value) => `${Math.round(value).toLocaleString()} h`,
    render: (v) => v != null
      ? `${Math.round(v as number).toLocaleString()} h`
      : <span className="text-muted-foreground">—</span>,
  },
];

// Universal columns rendered for every persona — the identity + status
// fields a fleet manager, dispatcher, safety, HR, or accounting user
// all need to recognize a truck.  ``_city`` + ``_state`` are included
// so every persona has the option to unhide + filter by them, but
// they start ``defaultHidden`` so they don't crowd the default view.
const UNIVERSAL_COLUMN_KEYS = new Set([
  'name', 'vehicle_type', 'company', 'status', 'address', '_city', '_state',
]);

// Per-persona column visibility.  Mirrors the strict-binding rule from
// the Overview KPI grid: each role's table only includes columns
// relevant to their workspace.  Fleet sees mechanical detail; Dispatch
// sees fuel for low-fuel triage; Safety / HR / Accounting get just the
// universals because they don't action vehicle ops from this list.
//
// Owner / Admin get the full superset — they're the cross-cutting
// executive view; if they want a persona-tuned view they switch via
// "View dashboard as…" → subdomain navigation → persona's view loads.
const PERSONA_EXTRA_COLUMNS: Record<string, ReadonlyArray<string>> = {
  owner:      ['fuel_percent', 'def_percent', 'fault_count', 'odometer_miles', 'engine_hours'],
  admin:      ['fuel_percent', 'def_percent', 'fault_count', 'odometer_miles', 'engine_hours'],
  fleet:      ['def_percent', 'fault_count', 'odometer_miles', 'engine_hours'],
  dispatcher: ['fuel_percent'],
  safety:     [],
  hr:         [],
  accounting: ['odometer_miles', 'engine_hours'],  // utilisation for CPM
  driver:     [],
};

export default function Vehicles() {
  const { t } = useTranslation();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const navigate = useNavigate();
  const { persona } = useShellConfig();
  // Gate on the ACTIVE VIEW's permission (viewHas), not the logged-in
  // user's own (has).  Otherwise an Owner previewing the Fleet persona
  // keeps Owner powers and still sees Add/Edit even when Manage
  // Vehicles is unchecked for Fleet — a misleading preview.  viewHas
  // resolves to the previewed persona's permission for an Owner/Admin
  // who's switched view, and to the real user's own permission
  // otherwise (so an actual Fleet user is gated correctly too).  The
  // backend still enforces can_manage_vehicles on every write.
  const { viewHas } = useRoleView();
  const canManage = viewHas('can_manage_vehicles');

  // null = closed; {vehicle:null} = create; {vehicle:row} = edit.
  const [dialog, setDialog] = useState<{ vehicle: Vehicle | null } | null>(null);

  // Fleet-list inventory badge — vehicle registry id → attention count.
  // Shown ONLY when something needs attention (missing/damaged/…): zero
  // noise when every truck's inventory is healthy.
  const { data: invAlerts } = useInventoryAlerts(true);
  // Memoized: a fresh `{}` fallback each render would change the columns
  // memo's deps every time, rebuilding the whole column set per render.
  const invByVehicle = useMemo(() => invAlerts?.by_vehicle ?? {}, [invAlerts]);

  const columns = useMemo(() => {
    const extras = PERSONA_EXTRA_COLUMNS[persona] ?? PERSONA_EXTRA_COLUMNS.owner ?? [];
    const allowed = new Set<string>([
      ...UNIVERSAL_COLUMN_KEYS,
      ...extras,
    ]);
    const cols = ALL_COLUMNS.filter((c) => allowed.has(c.key)).map((c) => {
      if (c.key !== 'name') return c;
      return {
        ...c,
        render: (v: unknown, row: Record<string, unknown>) => {
          const r = row as unknown as Vehicle;
          const inv = r.registry_id != null ? invByVehicle[String(r.registry_id)] : undefined;
          return (
            <span className="inline-flex items-center gap-1.5">
              <span>{String(v ?? '')}</span>
              {inv && inv.attention > 0 && (
                <Tip label={`Inventory: ${inv.attention} item${inv.attention === 1 ? '' : 's'} need attention`}>
                  <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-2xs border ${toneClasses('danger')}`}>
                    <PackageX size={12} />
                    {inv.attention}
                  </span>
                </Tip>
              )}
            </span>
          );
        },
      } as AnyColumn;
    });
    if (!canManage) return cols;
    // Edit affordance — only for operators who can manage vehicles, and
    // only on rows that exist in the registry (registry_id present).
    return [
      ...cols,
      {
        key: '_edit',
        label: '',
        render: (_v: unknown, row: Record<string, unknown>) => {
          const r = row as unknown as Vehicle;
          if (r.registry_id == null) return null;
          return (
            <Tip label="Edit this vehicle">
            <Button
              type="button" variant="ghost" size="xs"
              onClick={(e) => { e.stopPropagation(); setDialog({ vehicle: r }); }}
            >
              <Pencil size={12} />
            </Button>
            </Tip>
          );
        },
      } as AnyColumn,
    ];
  }, [persona, canManage, invByVehicle]);

  const {
    data,
    isLoading,
    isFetching,
    error: queryError,
    refetch,
    dataUpdatedAt,
  } = useQuery<VehiclesResponse>({
    queryKey: ['vehicles', statusFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (statusFilter !== 'all') params.set('status', statusFilter);
      // 500 matches the route's le= cap.  The whole registry (trucks +
      // trailers) rides one page; beyond 500 vehicles this fetch must
      // walk total_pages instead (useFleetList already shows the shape).
      params.set('page_size', '500');
      return apiJSON<VehiclesResponse>(`/vehicles?${params}`);
    },
    placeholderData: (prev) => prev,
  });

  const vehicles: Vehicle[] = data?.vehicles ?? [];
  const totalCount =
    (data as unknown as { count?: number } | undefined)?.count ?? vehicles.length;
  const error =
    queryError instanceof Error ? queryError.message : queryError ? String(queryError) : '';

  const counts: Record<string, number> = { moving: 0, idle: 0, stopped: 0, no_telemetry: 0 };
  vehicles.forEach((v) => {
    if (v.status && counts[v.status] !== undefined) counts[v.status]++;
  });
  // Registry-only rows (trailers, manual trucks) exist only once the
  // registry overlay is live — hide the chip entirely for fleets where
  // everything reports, instead of a permanent "No Telemetry 0".
  const statusOptions = counts.no_telemetry > 0 || statusFilter === 'no_telemetry'
    ? STATUS_OPTIONS
    : STATUS_OPTIONS.filter((s) => s !== 'no_telemetry');

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
          <div className="flex items-center gap-2">
            {canManage && (
              <Button
                type="button" variant="outline" size="sm"
                onClick={() => setDialog({ vehicle: null })}
              >
                <Plus size={14} />
                Add vehicle
              </Button>
            )}
            <LastUpdated
              fetchedAt={dataUpdatedAt}
              isFetching={isFetching}
              onRefresh={refetch}
            />
          </div>
        }
      />

      {UTILIZATION_PERSONAS.has(persona) && <UtilizationSummary />}

      <div className="mb-4 flex items-center gap-2">
        <FilterChips
          options={statusOptions}
          value={statusFilter}
          onChange={setStatusFilter}
          labelFor={(s) => s.replace(/_/g, ' ')}
          countFor={(s) =>
            s === 'all' ? totalCount : counts[s] ?? 0
          }
        />
        {statusOptions.includes('no_telemetry') && (
          // Learn-once explainer: +86 "no telemetry" rows appearing for
          // the first time must read as "the whole registry now shows",
          // not as a telematics outage.
          <InfoTip
            size={14}
            label="Vehicles without a telematics device — trailers and manually added trucks. They're listed so the fleet count is complete; motion status applies only to reporting vehicles."
          />
        )}
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
        <DataGrid
          // Opts into the full column-controls layer: 3-dot menu on
          // every column header (Sort / Filter / Pin / Hide), Manage
          // Columns popover, drag-to-reorder, Export CSV button, and
          // per-user layout persistence via useUserPreference.  Match
          // the toolkit Maintenance Tasks has — this table is bigger
          // (typically 50-100+ trucks) and benefits even more from
          // sort/pin/hide.
          tableId="vehicles"
          columns={columns}
          data={vehicles as unknown as Record<string, unknown>[]}
          // Multi-field search — an operator typing "PTG" or "moving"
          // or a street name should all narrow the list, not just the
          // vehicle number.
          searchKey={['name', 'company', 'status', 'address']}
          onRowClick={(row) => {
            // Route is mounted at root (`vehicles/:name`), not under
            // `/fleet/`; the persona context (fleet./dispatch./safety.)
            // is carried by the subdomain so the URL path stays neutral.
            //
            // The ``?company=`` query param disambiguates cross-company
            // vehicle-name collisions.  Two trucks named "103" in
            // different companies under the same account are legal;
            // without the qualifier the detail page would arbitrarily
            // render whichever row landed first in the API response.
            const name = String(row.name ?? '');
            const company = String(
              (row as { _org?: unknown; company?: unknown })._org
              ?? (row as { _org?: unknown; company?: unknown }).company
              ?? '',
            ).trim();
            const qs = company
              ? `?company=${encodeURIComponent(company)}`
              : '';
            navigate(`/vehicles/${encodeURIComponent(name)}${qs}`);
          }}
        />
      )}

      {dialog && (
        <VehicleManageDialog
          open
          vehicle={dialog.vehicle}
          existingVehicles={vehicles}
          onClose={() => setDialog(null)}
          onSaved={() => refetch()}
        />
      )}
    </div>
  );
}
