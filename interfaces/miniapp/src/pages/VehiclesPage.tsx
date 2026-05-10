/**
 * VehiclesPage — driver hero card (1 vehicle) or searchable, sortable fleet
 * list (multi-vehicle operators).
 *
 * Improvements over v1:
 *  - VK Icon24 icons everywhere (no emoji)
 *  - 2×2 stat grid with fuel/DEF gauge bars
 *  - Separate company + timestamp rows; engine-state pill
 *  - Primary/secondary action button hierarchy
 *  - Multi-list: custom VehicleRow with color strip + stat pills
 *  - "Stopped Xh ago" subtitle from real signal time
 *  - Search bar with icon + clear button
 *  - Sort control (status / name / faults)
 *  - Maintenance tasks use shared BottomSheet
 *  - Fault count color scales: orange ≥1, red ≥3
 */

import { useCallback, useEffect, useState } from 'react';
import { Placeholder } from '@telegram-apps/telegram-ui';
import {
  Icon24TruckOutline,
  Icon24SpeedometerMiddleOutline,
  Icon24DropsOutline,
  Icon24WaterDropOutline,
  Icon24WarningTriangleOutline,
  Icon24LockOutline,
  Icon24GlobeOutline,
  Icon24LocationMapOutline,
  Icon24LocationOutline,
  Icon24DocumentListOutline,
  Icon24Search,
  Icon24Cancel,
  Icon24SortOutline,
  Icon24GearOutline,
  Icon24ServicesOutline,
  Icon24ArrowRightOutline,
} from '@vkontakte/icons';
import { apiJSON, classifyError, type ClassifiedError } from '../api/client';
import type { Vehicle } from '../types';
import { VehicleCardSkeleton, ListRowsSkeleton } from '../components/Skeleton';
import { RelativeTime } from '../components/RelativeTime';
import { VehicleDetailSheet } from '../components/VehicleDetailSheet';
import { BottomSheet } from '../components/BottomSheet';
import { usePullToRefresh } from '../hooks/usePullToRefresh';
import { haptics } from '../hooks/useTelegram';
import { useUnitSystem } from '../hooks/useUnitSystem';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { fmtSpeed, speedUnit } from '../utils/units';

// Re-import the shared MaintenanceTask shape.  Keeping the import below
// the local Props on purpose so existing render code reads in declaration
// order without a hop to the types module.
import type { MaintenanceTask } from '../types';

interface Props {
  active: boolean;
  onGoToMap: () => void;
  timezone?: string;
}

type SortOrder = 'status' | 'name' | 'faults';

const STATUS_RANK: Record<string, number> = { moving: 0, idle: 1, stopped: 2 };

function vehicleStatus(v: Vehicle): 'moving' | 'idle' | 'stopped' {
  if (v.status) return v.status;
  if (v.engine_state === 'On') return (v.speed_mph ?? 0) > 2 ? 'moving' : 'idle';
  return 'stopped';
}

function statusLabel(s: 'moving' | 'idle' | 'stopped'): string {
  return s === 'moving' ? 'Moving' : s === 'idle' ? 'Idle' : 'Stopped';
}

/** Returns orange at ≥1 fault, red at ≥3 faults. */
function faultColor(count: number): string | undefined {
  if (count >= 3) return 'var(--st-red)';
  if (count >= 1) return 'var(--st-orange)';
  return undefined;
}

/** Thin gauge bar for fuel / DEF tiles. */
function GaugeBar({ pct, warn = false, critical = false }: { pct: number; warn?: boolean; critical?: boolean }) {
  const fill = critical ? 'var(--st-red)' : warn ? 'var(--st-orange)' : 'var(--st-blue, #2990ff)';
  return (
    <div className="gauge-bar">
      <div className="gauge-bar__fill" style={{ width: `${Math.min(Math.max(pct, 0), 100)}%`, background: fill }} />
    </div>
  );
}

export function VehiclesPage({ active, onGoToMap, timezone }: Props) {
  const units = useUnitSystem();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ClassifiedError | null>(null);
  const [search, setSearch] = useState('');
  const [sortOrder, setSortOrder] = useState<SortOrder>('status');
  const [detailName, setDetailName] = useState<string | null>(null);
  const [maintenanceTasks, setMaintenanceTasks] = useState<MaintenanceTask[]>([]);
  const [maintSheetOpen, setMaintSheetOpen] = useState(false);

  const debouncedSearch = useDebouncedValue(search, 200);

  const load = useCallback(async () => {
    setError(null);
    try {
      // page_size=200 is the backend max — match the map endpoint, which
      // returns the full fleet.  Without this the list silently caps at
      // the route default of 50 even on a 85-vehicle fleet.  The mini
      // app does its own client-side search/sort/filter so we need the
      // full list anyway.
      const data = await apiJSON<{ vehicles: Vehicle[] }>('/api/vehicles/?page_size=200');
      setVehicles(data.vehicles ?? []);
      setUpdatedAt(new Date().toISOString());
    } catch (e) {
      setError(classifyError(e));
    }
  }, []);

  useEffect(() => {
    apiJSON<{ tasks: MaintenanceTask[] }>('/api/maintenance/tasks?status=pending&page_size=10')
      .then(d => setMaintenanceTasks(d.tasks ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => { setLoading(true); load().finally(() => setLoading(false)); }, [load]);
  useEffect(() => { if (active) load(); }, [active, load]);

  const ptr = usePullToRefresh<HTMLDivElement>({ onRefresh: load });

  if (loading) return <div><VehicleCardSkeleton /><ListRowsSkeleton count={2} /></div>;

  if (error) {
    const ErrIcon =
      error.kind === 'auth'    ? Icon24LockOutline
      : error.kind === 'network' ? Icon24GlobeOutline
      : Icon24WarningTriangleOutline;
    const header =
      error.kind === 'auth'    ? 'Session expired'
      : error.kind === 'server'  ? 'Server error'
      : error.kind === 'network' ? 'No connection'
      : 'Failed to load';
    return (
      <div className="centered">
        <Placeholder
          header={header}
          description={error.message}
          action={error.kind === 'auth' ? null : (
            <button className="retry-btn" onClick={() => { setLoading(true); load().finally(() => setLoading(false)); }}>Retry</button>
          )}
        >
          <ErrIcon width={48} height={48} aria-label={header} style={{ opacity: 0.4 }} />
        </Placeholder>
      </div>
    );
  }

  if (vehicles.length === 0) {
    return (
      <div className="centered">
        <Placeholder header="No vehicle assigned" description="No vehicle has been assigned to you yet — ask your dispatcher.">
          <Icon24TruckOutline width={48} height={48} />
        </Placeholder>
      </div>
    );
  }

  // ── Maintenance banner + sheet ────────────────────────────────────────
  const maintBanner = maintenanceTasks.length > 0 ? (
    <>
      <div
        className="maintenance-banner"
        role="button"
        onClick={() => { haptics.selection(); setMaintSheetOpen(true); }}
      >
        <Icon24ServicesOutline width={18} height={18} />
        <span style={{ flex: 1 }}>
          <strong>{maintenanceTasks.length}</strong> pending maintenance task{maintenanceTasks.length !== 1 ? 's' : ''}
        </span>
        <span style={{ color: 'var(--st-blue, #2990ff)', fontSize: 13 }}>View →</span>
      </div>
      <BottomSheet
        open={maintSheetOpen}
        onClose={() => setMaintSheetOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24ServicesOutline width={18} height={18} />
            Pending Tasks
          </span>
        }
      >
        {maintenanceTasks.map(t => {
          // Mileage progress: only show when both due_miles and a captured
          // last_odometer are present.  pct = how far along the truck is
          // toward the threshold; 100% means due now, >100% means overdue.
          const hasMileage = t.due_miles != null && t.last_odometer != null;
          const pct = hasMileage
            ? Math.max(0, Math.min(120, Math.round((t.last_odometer! / t.due_miles!) * 100)))
            : null;
          const remaining = hasMileage ? Math.round(t.due_miles! - t.last_odometer!) : null;
          const overdue = pct !== null && pct >= 100;
          return (
            <div key={t.id} className="maint-row">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="maint-row__title">{t.task_type}</div>
                <div className="maint-row__sub">
                  {t.vehicle_name}
                  {t.due_date && ` · Due ${t.due_date}`}
                  {t.due_miles != null && ` · Due at ${t.due_miles.toLocaleString()} mi`}
                </div>
                {hasMileage && (
                  <div className="odometer-progress" aria-label={`Mileage progress: ${pct}%`}>
                    <div className="odometer-progress__track">
                      <div
                        className={`odometer-progress__fill${overdue ? ' odometer-progress__fill--overdue' : ''}`}
                        style={{ width: `${Math.min(pct!, 100)}%` }}
                      />
                    </div>
                    <div className="odometer-progress__label">
                      {t.last_odometer!.toLocaleString()} / {t.due_miles!.toLocaleString()} mi
                      {overdue
                        ? ` · ${Math.abs(remaining!).toLocaleString()} mi overdue`
                        : ` · ${remaining!.toLocaleString()} mi to go`}
                    </div>
                  </div>
                )}
              </div>
              <span className={`status-badge ${overdue || t.status === 'overdue' ? 'stopped' : 'idle'}`}>
                {overdue || t.status === 'overdue' ? 'overdue' : 'pending'}
              </span>
            </div>
          );
        })}
      </BottomSheet>
    </>
  ) : null;

  // ── Single-vehicle driver hero ─────────────────────────────────────────
  if (vehicles.length === 1) {
    const v = vehicles[0];
    const status = vehicleStatus(v);
    return (
      <div ref={ptr.ref} style={{ overflowY: 'auto', height: '100%' }}>
        <div className={`ptr ${ptr.pulling > 0 || ptr.refreshing ? 'ptr--active' : ''}`}>
          {ptr.refreshing ? '↻ Refreshing…' : ptr.pulling > 0 ? '↓ Pull to refresh' : ''}
        </div>
        {maintBanner}
        <VehicleHero
          v={v}
          status={status}
          updatedAt={updatedAt}
          timezone={timezone}
          units={units}
          onGoToMap={() => { haptics.selection(); onGoToMap(); }}
          onViewDetail={() => { haptics.selection(); setDetailName(v.name); }}
        />
        <VehicleDetailSheet name={detailName} onClose={() => setDetailName(null)} />
      </div>
    );
  }

  // ── Multi-vehicle fleet list ───────────────────────────────────────────
  const q = debouncedSearch.trim().toLowerCase();
  const filtered = (q
    ? vehicles.filter(v =>
        v.name.toLowerCase().includes(q) ||
        (v.company ?? '').toLowerCase().includes(q) ||
        (v.address ?? '').toLowerCase().includes(q),
      )
    : [...vehicles]
  ).sort((a, b) => {
    if (sortOrder === 'name')   return a.name.localeCompare(b.name);
    if (sortOrder === 'faults') return (b.fault_count ?? 0) - (a.fault_count ?? 0);
    // 'status': moving → idle → stopped; alphabetical within group
    const ra = STATUS_RANK[vehicleStatus(a)] ?? 3;
    const rb = STATUS_RANK[vehicleStatus(b)] ?? 3;
    return ra !== rb ? ra - rb : a.name.localeCompare(b.name);
  });

  return (
    <div ref={ptr.ref} style={{ overflowY: 'auto', height: '100%' }}>
      <div className={`ptr ${ptr.pulling > 0 || ptr.refreshing ? 'ptr--active' : ''}`}>
        {ptr.refreshing ? '↻ Refreshing…' : ptr.pulling > 0 ? '↓ Pull to refresh' : ''}
      </div>
      {maintBanner}

      {/* Search bar with icon + clear button */}
      <div className="search-bar">
        <Icon24Search className="search-bar__icon" width={18} height={18} />
        <input
          className="search-bar__input"
          type="search"
          placeholder="Search vehicles…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search vehicles"
        />
        {search && (
          <button className="search-bar__clear" onClick={() => setSearch('')} aria-label="Clear search">
            <Icon24Cancel width={18} height={18} />
          </button>
        )}
      </div>

      {/* Sort control */}
      <div className="sort-bar">
        <Icon24SortOutline width={15} height={15} className="sort-bar__icon" />
        {(['status', 'name', 'faults'] as SortOrder[]).map(s => (
          <button
            key={s}
            className={`sort-bar__btn${sortOrder === s ? ' sort-bar__btn--active' : ''}`}
            onClick={() => setSortOrder(s)}
          >
            {s === 'status' ? 'Status' : s === 'name' ? 'Name' : 'Faults'}
          </button>
        ))}
        <span className="sort-bar__count">{filtered.length} vehicles</span>
      </div>

      {filtered.length === 0 ? (
        <div className="centered">
          <Placeholder header="No matches" description={`No vehicles matching "${search}"`}>🔍</Placeholder>
        </div>
      ) : (
        <div className="vlist">
          {filtered.map(v => (
            <VehicleRow
              key={v.id ?? v.name}
              v={v}
              units={units}
              timezone={timezone}
              onClick={() => { haptics.selection(); setDetailName(v.name); }}
            />
          ))}
        </div>
      )}
      <VehicleDetailSheet name={detailName} onClose={() => setDetailName(null)} />
    </div>
  );
}

// ── Vehicle hero card ──────────────────────────────────────────────────────

interface HeroProps {
  v: Vehicle;
  status: 'moving' | 'idle' | 'stopped';
  updatedAt: string | null;
  timezone?: string;
  units: 'imperial' | 'metric';
  onGoToMap: () => void;
  onViewDetail: () => void;
}

function VehicleHero({ v, status, updatedAt, timezone, units, onGoToMap, onViewDetail }: HeroProps) {
  const speedNum = v.speed_mph != null
    ? (units === 'metric' ? Math.round(v.speed_mph * 1.60934) : Math.round(v.speed_mph))
    : null;
  const fuel = v.fuel_percent != null ? Math.round(v.fuel_percent) : null;
  const def  = v.def_percent  != null ? Math.round(v.def_percent)  : null;
  const signalTime = v.time ?? updatedAt;
  const engineOn = v.engine_state === 'On';
  const faultCount = v.fault_count ?? 0;

  return (
    <div className="vehicle-hero">
      <div className={`vehicle-hero__strip ${status}`} />

      {/* Row 1: vehicle name + status badge */}
      <div className="vehicle-hero__row1">
        <span className="vehicle-hero__name">{v.name}</span>
        <span className={`status-badge ${status}`}>{statusLabel(status)}</span>
      </div>

      {/* Row 2: company (own line, not squashed with timestamp) */}
      {v.company && <div className="vehicle-hero__company">{v.company}</div>}

      {/* Row 3: last updated + engine state pill */}
      <div className="vehicle-hero__meta">
        {signalTime && (
          <span className="vehicle-hero__updated">
            updated <RelativeTime iso={signalTime} timezone={timezone} />
          </span>
        )}
        <span className={`vehicle-hero__engine${engineOn ? ' vehicle-hero__engine--on' : ''}`}>
          <Icon24GearOutline width={13} height={13} />
          {engineOn ? 'Engine on' : 'Engine off'}
        </span>
      </div>

      {/* 2×2 stat grid: Speed | Faults  /  Fuel (gauge) | DEF (gauge) */}
      <div className="vehicle-hero__stats">
        {/* Speed */}
        <div className="vehicle-hero__stat">
          <Icon24SpeedometerMiddleOutline width={20} height={20} />
          <div className="vehicle-hero__stat-value">{speedNum != null ? speedNum : '—'}</div>
          <div className="vehicle-hero__stat-label">{speedUnit(units)}</div>
        </div>

        {/* Faults — color scales with severity */}
        <div className="vehicle-hero__stat" style={faultCount ? { color: faultColor(faultCount) } : undefined}>
          <Icon24WarningTriangleOutline width={20} height={20} />
          <div className="vehicle-hero__stat-value">{faultCount}</div>
          <div className="vehicle-hero__stat-label">Faults</div>
        </div>

        {/* Fuel with gauge bar */}
        <div
          className="vehicle-hero__stat vehicle-hero__stat--gauge"
          style={fuel != null && fuel < 15 ? { color: 'var(--st-red)' } : fuel != null && fuel < 25 ? { color: 'var(--st-orange)' } : undefined}
        >
          <Icon24DropsOutline width={20} height={20} />
          <div className="vehicle-hero__stat-value">{fuel != null ? `${fuel}%` : '—'}</div>
          <div className="vehicle-hero__stat-label">Fuel</div>
          {fuel != null && <GaugeBar pct={fuel} warn={fuel < 25} critical={fuel < 15} />}
        </div>

        {/* DEF with gauge bar */}
        <div
          className="vehicle-hero__stat vehicle-hero__stat--gauge"
          style={def != null && def < 10 ? { color: 'var(--st-red)' } : def != null && def < 20 ? { color: 'var(--st-orange)' } : undefined}
        >
          <Icon24WaterDropOutline width={20} height={20} />
          <div className="vehicle-hero__stat-value">{def != null ? `${def}%` : '—'}</div>
          <div className="vehicle-hero__stat-label">DEF</div>
          {def != null && <GaugeBar pct={def} warn={def < 20} critical={def < 10} />}
        </div>
      </div>

      {/* Odometer line — warehouse-sourced, refreshed every 60s on the
          backend.  Distinct from `time` (which tracks GPS/state freshness)
          so we surface the odometer's own as-of timestamp. */}
      {v.odometer_miles != null && (
        <div className="vehicle-hero__odometer">
          <Icon24SpeedometerMiddleOutline width={14} height={14} />
          <span className="vehicle-hero__odometer-value">
            {Math.round(v.odometer_miles).toLocaleString()} mi
          </span>
          {v.odometer_time && (
            <span className="vehicle-hero__odometer-time">
              · <RelativeTime iso={v.odometer_time} timezone={timezone} />
            </span>
          )}
        </div>
      )}

      {/* Address — tappable, opens map */}
      {v.address && (
        <div className="vehicle-hero__addr" onClick={onGoToMap} role="button" aria-label="Open in map">
          <Icon24LocationOutline width={16} height={16} style={{ flexShrink: 0 }} />
          <span className="vehicle-hero__addr-text">{v.address}</span>
          <span style={{ color: 'var(--st-blue, #2990ff)', flexShrink: 0 }}>→</span>
        </div>
      )}

      {/* Actions: primary CTA full-width, then secondary row */}
      <div className="vehicle-hero__actions">
        <button className="vehicle-hero__action vehicle-hero__action--primary" onClick={onGoToMap}>
          <Icon24LocationMapOutline width={18} height={18} />
          Open in map
        </button>
        <div className="vehicle-hero__actions-row2">
          <button className="vehicle-hero__action" onClick={onViewDetail}>
            <Icon24DocumentListOutline width={18} height={18} />
            Details
          </button>
          {faultCount > 0 && (
            <button
              className="vehicle-hero__action vehicle-hero__action--warn"
              onClick={() => { haptics.selection(); onViewDetail(); }}
            >
              <Icon24WarningTriangleOutline width={18} height={18} />
              Faults ({faultCount})
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Multi-vehicle list row ──────────────────────────────────────────────────

interface RowProps {
  v: Vehicle;
  units: 'imperial' | 'metric';
  timezone?: string;
  onClick: () => void;
}

function VehicleRow({ v, units, timezone, onClick }: RowProps) {
  const status = vehicleStatus(v);
  const speed  = v.speed_mph   != null ? fmtSpeed(v.speed_mph, units) : null;
  const fuel   = v.fuel_percent != null ? Math.round(v.fuel_percent)   : null;
  const def    = v.def_percent  != null ? Math.round(v.def_percent)    : null;
  const faultCount = v.fault_count ?? 0;

  return (
    <div className="vrow" role="button" onClick={onClick}>
      <div className={`vrow__strip ${status}`} />
      <div className="vrow__body">
        {/* Top: name + badge */}
        <div className="vrow__top">
          <span className="vrow__name">{v.name}</span>
          <span className={`status-badge ${status}`}>{statusLabel(status)}</span>
        </div>

        {/* Stat pills */}
        <div className="vrow__pills">
          {speed && (
            <span className="vrow__pill">
              <Icon24SpeedometerMiddleOutline width={12} height={12} />
              {speed}
            </span>
          )}
          {fuel != null && (
            <span className={`vrow__pill${fuel < 15 ? ' vrow__pill--warn' : ''}`}>
              <Icon24DropsOutline width={12} height={12} />
              {fuel}%
            </span>
          )}
          {def != null && (
            <span className={`vrow__pill${def < 10 ? ' vrow__pill--warn' : ''}`}>
              <Icon24WaterDropOutline width={12} height={12} />
              {def}% DEF
            </span>
          )}
          {faultCount > 0 && (
            <span className={`vrow__pill${faultCount >= 3 ? ' vrow__pill--critical' : ' vrow__pill--warn'}`}>
              <Icon24WarningTriangleOutline width={12} height={12} />
              {faultCount}
            </span>
          )}
        </div>

        {/* Subtitle: "Stopped 3h ago · address" for stopped/idle; address for moving */}
        {status !== 'moving' && v.time ? (
          <div className="vrow__sub">
            {statusLabel(status)} <RelativeTime iso={v.time} timezone={timezone} />
            {v.address ? ` · ${v.address.slice(0, 40)}` : ''}
          </div>
        ) : v.address ? (
          <div className="vrow__sub">{v.address.slice(0, 60)}</div>
        ) : null}
      </div>
      <Icon24ArrowRightOutline width={18} height={18} className="vrow__chevron" />
    </div>
  );
}
