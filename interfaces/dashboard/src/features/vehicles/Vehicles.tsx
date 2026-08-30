import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Pencil, Plus, Truck } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid, { type DataGridSegment } from '../../components/datagrid';
import { vehicleRowMenu } from './contextMenu';
import StatusBadge from '../../components/StatusBadge';
import { Freshness, InfoTip, Tip } from '../../components/tooltip';
import { useInventoryAlerts } from './inventory/useInventory';
import { PackageX } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  LastUpdated,
  useLoadingStage,
} from '../../components/shell';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useRoleView } from '../../context/RoleViewContext';
import type { Vehicle, VehiclesResponse } from '../../types';
import type { AnyColumn } from '../../types';
import UtilizationSummary from './UtilizationSummary';
import Mileage from './Mileage';
import VehicleManageDialog from './VehicleManageDialog';
import VehiclesConfigPanel from './config/VehiclesConfigPanel';
import { FeatureConfigGear } from '../_lib/FeatureConfigGear';
import DeviceEventsCard from './DeviceEventsCard';
import { Badge } from '@/components/ui/badge';

const TYPE_LABEL: Record<string, string> = {
  truck: 'Truck', trailer: 'Trailer', other: 'Other',
};

// The wire says `manual`; a person reading the grid gets "Local" —
// somebody on 4truck added this truck by hand.  Provider names are
// simply themselves.
const SOURCE_VALUE_LABEL: Record<string, string> = {
  samsara: 'Samsara', datatruck: 'Datatruck', manual: 'Local',
};

// Personas that benefit from the 30-day utilization roll-up at the top
// of the Vehicles page.  Drivers + Dispatch don't need it (drivers care
// about their own vehicle only; dispatch lives in the live view).
const UTILIZATION_PERSONAS = new Set(['owner', 'admin', 'fleet', 'accounting']);

// Lifecycle tabs, DECLARED — DataGrid owns the strip, the counts, the
// scoping and the reset.  This page used to own all four: a useState, a
// forEach to tally, a visibility rule, and a FilterChips render — plus a
// ``?status=`` round-trip per click.  That last one made the tallies
// LIE: the counts were computed from the returned rows, so selecting
// "Moving" reported Idle 0 / Stopped 0, and the No-telemetry tab (which
// hides itself at zero) vanished until you went back to All.
//
// The server built the whole list and filtered it in Python either way,
// so dropping the parameter costs nothing and buys exact counts, one
// cache entry instead of five, and no refetch per tab.
const STATUS_SEGMENTS: DataGridSegment[] = [
  // `!r.archived` on every live tab, including All: a retired truck
  // belongs on exactly one tab, its own.  Without this the vehicle count
  // in the hero would quietly grow each time someone archived a truck.
  { key: 'all', label: 'All', match: (r) => !r.archived },
  { key: 'moving', label: 'Moving', match: (r) => r.status === 'moving' },
  { key: 'idle', label: 'Idle', match: (r) => r.status === 'idle' },
  { key: 'stopped', label: 'Stopped', match: (r) => r.status === 'stopped' },
  { key: 'no_telemetry', label: 'No telemetry', match: (r) => r.status === 'no_telemetry' },
];
// Registry-only rows (trailers, manual trucks) exist only once the
// registry overlay is live.  Drop the tab entirely for fleets where
// everything reports, rather than showing a permanent "No telemetry 0"
// that reads as an outage.  Swapping the ARRAY is the house pattern for
// this (ServiceTasks does the same) — no DataGrid prop needed.
const REPORTING_ONLY = STATUS_SEGMENTS.filter((s) => s.key !== 'no_telemetry');

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
    // Where the truck CAME FROM, and who has contributed since.
    // Filter = the creator (that answers "show me what Datatruck
    // added"); the cell renders every contributor, because one truck
    // is routinely created by one integration and enriched by another
    // — the single value lied about exactly this for weeks.
    // `manual` reads as "Local": a person on 4truck added it.
    key: 'source', label: 'Source', sortable: true,
    filterable: true,
    filterValue: (row) => String((row as Vehicle).source ?? ''),
    filterLabel: (row) =>
      SOURCE_VALUE_LABEL[String((row as Vehicle).source ?? '')]
      ?? ((row as Vehicle).source || '(unknown)'),
    render: (_v, row) => {
      const r = row as Vehicle;
      const list = (r.sources?.length ? r.sources : [r.source])
        .filter((x): x is string => Boolean(x))
        .map((x) => SOURCE_VALUE_LABEL[x] ?? x);
      if (!list.length) return <span className="text-muted-foreground">—</span>;
      const [creator, ...enrichers] = list;
      // Creator leads and reads a shade stronger; the order is a FACT
      // (created-by, then enriched-by), stated in the tooltip so
      // nobody mistakes it for fill-priority — that is per-field and
      // lives in the Vehicles config gear.
      return (
        <Tip
          label={
            enrichers.length
              ? `Created by ${creator}, enriched by ${enrichers.join(', ')}. `
                + 'Which source wins each field is set in Vehicles → Config.'
              : `Created by ${creator}.`
          }
        >
          <span>
            <span className="text-foreground">{creator}</span>
            {enrichers.length > 0 && (
              <span className="text-muted-foreground">
                {' · '}{enrichers.join(' · ')}
              </span>
            )}
          </span>
        </Tip>
      );
    },
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
    // Total active faults account-wide (or per group).
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
  // Where the truck came from (creator · enrichers).  Universal so any
  // persona can unhide + filter it, but defaultHidden below for the
  // non-roster personas — the same _city/_state treatment.
  'source',
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
  owner:      ['fuel_percent', 'def_percent', 'fault_count', 'odometer_miles', 'engine_hours', 'source'],
  admin:      ['fuel_percent', 'def_percent', 'fault_count', 'odometer_miles', 'engine_hours', 'source'],
  fleet:      ['def_percent', 'fault_count', 'odometer_miles', 'engine_hours', 'source'],
  dispatcher: ['fuel_percent'],
  safety:     [],
  hr:         [],
  accounting: ['odometer_miles', 'engine_hours'],  // utilisation for CPM
  driver:     [],
};

export default function Vehicles() {
  // Page tabs (same pattern as Vendors/Parts/Service Tasks): the
  // registry list and the period-mileage report are different VIEWS of
  // the feature, not filters on one grid — so they're page tabs, not
  // DataGrid tabs.
  const [pageTab, setPageTab] = useState<'vehicles' | 'mileage'>('vehicles');
  const { t } = useTranslation();
  const navigate = useNavigate();
  // The detail-page path for a vehicle row.  Shared by the row click and
  // the right-click menu so both route identically.
  //
  // Route is mounted at root (`vehicles/:name`), not under `/fleet/`; the
  // persona context (fleet./dispatch./safety.) is carried by the subdomain
  // so the URL path stays neutral.  The ``?company=`` query param
  // disambiguates cross-company vehicle-name collisions — two trucks named
  // "103" in different companies under one account are legal; without the
  // qualifier the detail page renders whichever row landed first.
  const vehiclePath = (row: Record<string, unknown>): string => {
    const name = String(row.name ?? '');
    const company = String(
      (row as { _org?: unknown; company?: unknown })._org
      ?? (row as { _org?: unknown; company?: unknown }).company
      ?? '',
    ).trim();
    const qs = company ? `?company=${encodeURIComponent(company)}` : '';
    return `/vehicles/${encodeURIComponent(name)}${qs}`;
  };
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
      // Source is universal (anyone may unhide + filter it) but starts
      // hidden for personas that did not opt in via extras — the same
      // crowd-control _city/_state get, decided per persona because
      // defaultHidden is a column property, not a persona one.
      if (c.key === 'source' && !extras.includes('source')) {
        return { ...c, defaultHidden: true };
      }
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
                  <Badge tone="danger" className="gap-0.5">
                    <PackageX className="size-3" />
                    {inv.attention}
                  </Badge>
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
              <Pencil />
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
    queryKey: ['vehicles'],
    queryFn: () => {
      // No ``status`` param: the tabs scope the loaded rows, so the
      // whole registry is fetched once and every tab count is exact.
      const params = new URLSearchParams();
      // 500 matches the route's le= cap.  The whole registry (trucks +
      // trailers) rides one page; beyond 500 vehicles this fetch must
      // walk total_pages instead (useFleetList already shows the shape).
      params.set('page_size', '500');
      return apiJSON<VehiclesResponse>(`/vehicles?${params}`);
    },
    placeholderData: (prev) => prev,
  });

  // Trucks that have been retired.  A SECOND query, because an
  // archived truck has no live telematics row — the ingest stops
  // writing them and archiving deletes the last one — so there is
  // nothing for the main list's live merge to overlay.  Only fetched
  // for someone who can act on them.
  const { data: archivedData, refetch: refetchArchived } =
    useQuery<{ vehicles: Vehicle[] }>({
      queryKey: ['vehicles', 'archived'],
      queryFn: () => apiJSON<{ vehicles: Vehicle[] }>(
        '/vehicles/registry/archived'),
      enabled: canManage,
      staleTime: 60_000,
    });

  // Memoised, not `data?.vehicles ?? []`: that literal is a NEW array
  // every render, so the merge below would rebuild — and re-render the
  // grid — on any state change at all, on a list of up to 500 trucks.
  const live: Vehicle[] = useMemo(() => data?.vehicles ?? [], [data]);
  const archived: Vehicle[] = useMemo(
    () => (archivedData?.vehicles ?? []).map((v) => ({
      ...v,
      archived: true,
      // `archived`, not `inactive`: inactive is this app's colour for a
      // thing that stopped working, and a retired truck is a decision
      // someone made.  See lib/status.ts.
      status: 'archived',
    })),
    [archivedData],
  );
  // One grid, one source of truth for slicing it.  The Archived tab is
  // a segment like every other, so it inherits search, sort, columns
  // and the row menu instead of becoming a second table that drifts.
  const vehicles: Vehicle[] = useMemo(
    () => [...live, ...archived], [live, archived],
  );
  const error =
    queryError instanceof Error ? queryError.message : queryError ? String(queryError) : '';

  const hasNoTelemetry = live.some((v) => v.status === 'no_telemetry');
  const segments = useMemo(() => {
    const base = hasNoTelemetry ? STATUS_SEGMENTS : REPORTING_ONLY;
    // Only when there is something to show.  A permanent "Archived 0"
    // is a tab that teaches nothing and costs a click's worth of
    // attention on every visit.
    return archived.length
      ? [...base, { key: 'archived', label: 'Archived',
                    match: (r: Record<string, unknown>) => !!r.archived }]
      : base;
  }, [hasNoTelemetry, archived.length]);

  // Same warehouse-first pattern as the rest of the fleet — when the
  // warehouse is cold the list falls back to live Samsara, which on a
  // 100-truck fleet can take 15-30s.  useLoadingStage drives the
  // progressive feedback (Loading… → Still loading… → Retry).
  const stage = useLoadingStage(isLoading && vehicles.length === 0);

  const handleRestore = async (v: Vehicle) => {
    if (v.registry_id == null) return;
    try {
      await apiJSON(`/vehicles/registry/${v.registry_id}/restore`,
                    { method: 'POST' });
      // Both lists: the truck leaves one and joins the other.
      await Promise.all([refetch(), refetchArchived()]);
      toast.success(`${v.name} restored`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Restore failed');
    }
  };

  return (
    <div>
      <PageHeader
        icon={Truck}
        title={t('vehicles.page_title')}
        description={t('vehicles.page_description')}
        actions={
          <div className="flex items-center gap-2">
            {/* The roster's own config: field precedence + auto-pilot.
                Here and not on Integrations, because the settings are
                vehicle_field_precedence / source_lifecycle:vehicle
                (/vehicles/config — the URL-follows-the-domain-noun
                rule), and because per-feature gears are the shape that
                scales: driver and load source policies go to THEIR
                pages.  The gear self-gates on can_manage_config_all. */}
            <FeatureConfigGear feature="Vehicles" size="xl">
              <VehiclesConfigPanel />
            </FeatureConfigGear>
            {canManage && (
              <Button
                type="button" variant="outline" size="sm"
                onClick={() => setDialog({ vehicle: null })}
              >
                <Plus />
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

      {/* Identity-watch notices: rendered only for vehicle admins and
          only while an event is open — zero pixels otherwise. */}
      <DeviceEventsCard
        canManage={canManage}
        vehicles={vehicles}
        onResolved={() => refetch()}
      />

      <div role="tablist" aria-label="Vehicle views" className="flex gap-1 mb-4 border-b border-border">
        {([
          { key: 'vehicles' as const, label: 'Vehicles' },
          { key: 'mileage' as const, label: 'Mileage' },
        ]).map(({ key, label }) => {
          const sel = pageTab === key;
          return (
            <button
              key={key}
              role="tab"
              aria-selected={sel}
              onClick={() => setPageTab(key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                sel
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {pageTab === 'mileage' ? <Mileage /> : (<>

      {UTILIZATION_PERSONAS.has(persona) && <UtilizationSummary />}

      {/* The chip row is gone — the tabs are DataGrid's now.  The
          explainer stays: "+86 no-telemetry rows" appearing for the
          first time must read as "the whole registry now shows", not as
          a telematics outage.  It follows its subject onto the header,
          beside the title, since the tab it explained lives inside the
          card. */}

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
          description={t('common.no_data')}
        />
      ) : (
        <DataGrid
          // A failed REFETCH leaves these rows on screen, so the
          // ``length === 0`` branch above never fires and the operator
          // reads a stale table as current.  The band says so without
          // taking the rows or the controls away.
          error={error || undefined}
          onRetry={() => refetch()}
          // Opts into the full column-controls layer: 3-dot menu on
          // every column header (Sort / Filter / Pin / Hide), Manage
          // Columns popover, drag-to-reorder, Export CSV button, and
          // per-user layout persistence via useUserPreference.  Match
          // the toolkit Maintenance Tasks has — this table is bigger
          // (typically 50-100+ trucks) and benefits even more from
          // sort/pin/hide.
          tableId="vehicles"
          segments={segments}
          // The explainer follows its subject.  It used to sit beside the
          // chip row; the tabs now live inside the card, so it rides the
          // toolbar's left slot — the nearest thing to them that a page
          // can address.  "+86 no-telemetry rows" appearing for the first
          // time must read as "the whole registry now shows", not as a
          // telematics outage.
          headerToolbar={hasNoTelemetry ? (
            <InfoTip
              size={14}
              label="Vehicles without a telematics device — trailers and manually added trucks. They're listed so the fleet count is complete; motion status applies only to reporting vehicles."
            />
          ) : undefined}
          columns={columns}
          data={vehicles as unknown as Record<string, unknown>[]}
          // Multi-field search — an operator typing "PTG" or "moving"
          // or a street name should all narrow the list, not just the
          // vehicle number.
          searchKey={['name', 'company', 'status', 'address']}
          onRowClick={(row) => navigate(vehiclePath(row))}
          // Right-click → Open / Open in new tab / Edit (managers,
          // registry rows).  Action list lives in ./contextMenu.
          rowActions={(row) => vehicleRowMenu(row as unknown as Vehicle, {
            path: vehiclePath(row),
            navigate,
            canManage,
            openEdit: (v) => setDialog({ vehicle: v }),
            restore: handleRestore,
          })}
        />
      )}

      </>)}

      {dialog && (
        <VehicleManageDialog
          open
          vehicle={dialog.vehicle}
          existingVehicles={vehicles}
          onClose={() => setDialog(null)}
          // Restore reached from inside the dialog moves a truck
          // BETWEEN the two lists, so both refetch — an add touches
          // only the live one, and the extra request rides an action a
          // person takes by hand.
          onSaved={() => { void refetch(); void refetchArchived(); }}
        />
      )}
    </div>
  );
}
