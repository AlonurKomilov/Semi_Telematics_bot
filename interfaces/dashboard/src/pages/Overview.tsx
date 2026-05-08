import { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Truck,
  Activity,
  CircleDot,
  CircleSlash,
  Wrench,
  Fuel,
  Bell,
  ParkingCircle,
  ClipboardList,
  Sparkles,
  MessageCircle,
  ShieldAlert,
  Route,
  type LucideIcon,
} from 'lucide-react';
import { apiJSON } from '../api/client';
import { usePermissions } from '../hooks/usePermissions';
import { useAuth } from '../context/AuthContext';
import {
  PageHeader,
  KpiCard,
  KpiSkeleton,
  ErrorState,
  EmptyState,
  LastUpdated,
  Greeting,
  AlertStrip,
  AISummaryCard,
  OnboardingBanner,
  type AlertItem,
} from '../components/shell';
import type { DashboardStats } from '../types';

const FleetStatusChart = lazy(() => import('../components/FleetStatusChart'));

// ── Role config ──────────────────────────────────────────────
//
// The dashboard serves multiple roles, not just "fleet manager". The
// shared (non-driver) overview adapts its title/description and KPI
// ordering by role so each user sees what matters most for their job.

type DashboardRole = 'driver' | 'dispatcher' | 'safety' | 'fleet' | 'admin' | 'owner';

interface RoleOverviewConfig {
  title: string;
  description: string;
  /** Drives KPI ordering — earlier keys render first. */
  priority: ReadonlyArray<KpiKey>;
}

type KpiKey =
  | 'total'
  | 'moving'
  | 'idle'
  | 'stopped'
  | 'pendingAlerts'
  | 'lowFuel'
  | 'faults'
  | 'maintenance'
  | 'unsafeParking'
  | 'aiBriefing'
  | 'vehiclesLink'
  | 'routesLink';

const ROLE_CONFIG: Record<Exclude<DashboardRole, 'driver'>, RoleOverviewConfig> = {
  dispatcher: {
    title: 'Dispatch Overview',
    description:
      'Vehicle movement, active routes, and alerts that need triage. Use this page to see who\'s rolling and where attention is needed.',
    priority: [
      'moving', 'idle', 'stopped', 'total',
      'pendingAlerts', 'unsafeParking',
      'routesLink', 'vehiclesLink', 'aiBriefing',
    ],
  },
  safety: {
    title: 'Safety Overview',
    description:
      'Pending alerts, unsafe events, parking issues, and the trucks that need follow-up. Drill into scorecards or coaching from any card.',
    priority: [
      'pendingAlerts', 'unsafeParking', 'faults',
      'total', 'moving', 'idle', 'stopped',
      'aiBriefing', 'vehiclesLink',
    ],
  },
  fleet: {
    title: 'Fleet Overview',
    description:
      'At-a-glance health of your fleet — alerts that need action, current vehicle status, and quick drill-downs.',
    priority: [
      'total', 'moving', 'idle', 'stopped',
      'faults', 'lowFuel', 'maintenance',
      'pendingAlerts', 'unsafeParking',
      'aiBriefing', 'vehiclesLink', 'routesLink',
    ],
  },
  admin: {
    title: 'Operations Overview',
    description:
      'Account-wide health — vehicle status, alerts, costs, and team activity. Tap any card to drill in.',
    priority: [
      'total', 'pendingAlerts', 'faults',
      'moving', 'idle', 'stopped',
      'lowFuel', 'maintenance', 'unsafeParking',
      'aiBriefing', 'vehiclesLink', 'routesLink',
    ],
  },
  owner: {
    title: 'Operations Overview',
    description:
      'Account-wide health — vehicle status, alerts, costs, and team activity. Tap any card to drill in.',
    priority: [
      'total', 'pendingAlerts', 'faults',
      'moving', 'idle', 'stopped',
      'lowFuel', 'maintenance', 'unsafeParking',
      'aiBriefing', 'vehiclesLink', 'routesLink',
    ],
  },
};

function configForRole(role?: string): RoleOverviewConfig {
  if (!role) return ROLE_CONFIG.admin;
  if (role in ROLE_CONFIG) return ROLE_CONFIG[role as keyof typeof ROLE_CONFIG];
  return ROLE_CONFIG.admin;
}

// ── Driver Overview ──────────────────────────────────────────

function DriverOverview({
  stats,
  navigate,
  greeting,
}: {
  stats: DashboardStats;
  navigate: ReturnType<typeof useNavigate>;
  greeting?: string;
}) {
  const truck = stats.my_vehicle;
  const fuelTone =
    truck?.fuel_pct == null
      ? 'default'
      : truck.fuel_pct < 20
      ? 'critical'
      : truck.fuel_pct < 40
      ? 'warning'
      : 'positive';

  return (
    <div>
      {greeting !== undefined && (
        <Greeting
          name={greeting}
          context={
            stats.truck_num
              ? `Assigned vehicle: ${stats.truck_num}`
              : 'No vehicle assigned yet — ask your admin to link a truck.'
          }
        />
      )}
      <PageHeader
        icon={Truck}
        title="My Dashboard"
        description="Live status, fuel, faults, and quick links to your routes, scorecard, and alerts."
      />

      {!truck ? (
        <EmptyState
          icon={Truck}
          title="No vehicle data available"
          description="Ask your admin to assign a truck to your account so we can show you live status, fuel, and routes."
        />
      ) : (
        <>
          <div className="bg-card border border-border rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide">
                  Assigned vehicle
                </p>
                <h2 className="text-xl font-semibold text-foreground mt-0.5">
                  {truck.name}
                </h2>
              </div>
              <span
                className={`px-3 py-1 rounded-full text-sm font-medium ${
                  truck.status === 'Moving'
                    ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                    : truck.status === 'Idle'
                    ? 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400'
                    : 'bg-destructive/15 text-destructive'
                }`}
              >
                {truck.status}
              </span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Speed</p>
                <p className="text-xl font-bold text-foreground mt-1">
                  {truck.speed_mph} mph
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Fuel level</p>
                <p
                  className={`text-xl font-bold mt-1 ${
                    fuelTone === 'critical'
                      ? 'text-destructive'
                      : fuelTone === 'warning'
                      ? 'text-yellow-700 dark:text-yellow-400'
                      : fuelTone === 'positive'
                      ? 'text-green-600 dark:text-green-400'
                      : 'text-foreground'
                  }`}
                >
                  {truck.fuel_pct != null ? `${truck.fuel_pct}%` : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Active faults</p>
                <p
                  className={`text-xl font-bold mt-1 ${
                    truck.faults > 0
                      ? 'text-orange-600 dark:text-orange-400'
                      : 'text-green-600 dark:text-green-400'
                  }`}
                >
                  {truck.faults}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Company</p>
                <p className="text-xl font-bold text-foreground mt-1">
                  {truck.company || '—'}
                </p>
              </div>
            </div>
            {truck.location && (
              <p className="text-xs text-muted-foreground mt-3">{truck.location}</p>
            )}
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="My alerts"
              value={stats.my_alerts ?? 0}
              tone={(stats.my_alerts ?? 0) > 0 ? 'info' : 'default'}
              icon={Bell}
              hint="Open notifications for your truck"
              onClick={() => navigate('/safety/alerts')}
            />
            <KpiCard
              label="My scorecard"
              value="View"
              tone="info"
              icon={ShieldAlert}
              hint="Safety score & ranking"
              onClick={() => navigate('/safety/scorecards')}
            />
            <KpiCard
              label="My routes"
              value="View"
              tone="info"
              icon={Route}
              hint="Recent and upcoming trips"
              onClick={() => navigate('/fleet/routes')}
            />
            <KpiCard
              label="Ask AI"
              value="Chat"
              tone="info"
              icon={MessageCircle}
              hint="Faults, status, maintenance"
              onClick={() =>
                navigate('/ai/chat', {
                  state: {
                    initialMessage: `How is my assigned truck doing? Give me a status summary.`,
                  },
                })
              }
            />
          </div>
        </>
      )}
    </div>
  );
}

// ── Role-aware Overview (everyone except drivers) ─────────────

interface KpiDef {
  key: KpiKey;
  label: string;
  value?: number | string;
  tone?: 'default' | 'positive' | 'warning' | 'critical' | 'info';
  icon: LucideIcon;
  hint?: string;
  href?: string;
  permission?: (has: (flag: string) => boolean) => boolean;
  /** Hide if the underlying number is zero / missing. */
  showWhen?: () => boolean;
}

function RoleOverview({
  stats,
  navigate,
  has,
  fetchedAt,
  isFetching,
  refetch,
  greeting,
  role,
}: {
  stats: DashboardStats;
  navigate: ReturnType<typeof useNavigate>;
  has: (flag: string) => boolean;
  fetchedAt?: number;
  isFetching: boolean;
  refetch: () => void;
  greeting?: string;
  role?: string;
}) {
  const f = stats.fleet || {};
  const total = f.total ?? 0;
  const moving = f.moving ?? 0;
  const idle = f.idle ?? 0;
  const stopped = f.stopped ?? 0;
  const movingPct = total > 0 ? Math.round((moving / total) * 100) : 0;

  const cfg = configForRole(role);

  // Tier-1 alerts — only the things that need action right now.
  const alertItems: AlertItem[] = [];
  if ((stats.pending_alerts ?? 0) > 0 && (has('can_alerts_all') || has('can_alerts_own'))) {
    alertItems.push({
      label: 'pending alerts',
      count: stats.pending_alerts ?? 0,
      href: '/safety/alerts',
      icon: Bell,
      tone: 'info',
    });
  }
  if ((stats.low_fuel ?? 0) > 0 && has('can_fuel')) {
    alertItems.push({
      label: 'low-fuel trucks',
      count: stats.low_fuel ?? 0,
      href: '/fleet/vehicles',
      icon: Fuel,
      tone: 'critical',
    });
  }
  const unsafeParking = (stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0);
  if (unsafeParking > 0 && (has('can_alerts_all') || has('can_alerts_own'))) {
    alertItems.push({
      label: 'parked unsafely',
      count: unsafeParking,
      href: '/fleet/parking',
      icon: ParkingCircle,
      tone: 'critical',
    });
  }
  if ((stats.maintenance_due ?? 0) > 0 && has('can_maintenance_all')) {
    alertItems.push({
      label: 'maintenance due',
      count: stats.maintenance_due ?? 0,
      href: '/maintenance',
      icon: ClipboardList,
      tone: 'warning',
    });
  }
  if ((stats.faults ?? 0) > 0 && has('can_faults')) {
    alertItems.push({
      label: 'with active faults',
      count: stats.faults ?? 0,
      href: '/fleet/vehicles',
      icon: Wrench,
      tone: 'warning',
    });
  }

  // KPI definitions — declarative so we can sort by role priority.
  const allKpis: KpiDef[] = [
    {
      key: 'total',
      label: 'Total vehicles',
      value: total,
      icon: Truck,
      hint: total > 0 ? `${movingPct}% currently moving` : undefined,
      href: has('can_vehicle_all') ? '/fleet/vehicles' : undefined,
    },
    {
      key: 'moving',
      label: 'Moving',
      value: moving,
      tone: 'positive',
      icon: Activity,
      hint: total > 0 ? `${movingPct}% of total` : undefined,
    },
    {
      key: 'idle',
      label: 'Idle',
      value: idle,
      tone: 'warning',
      icon: CircleDot,
      hint: total > 0 ? `${Math.round((idle / total) * 100)}% of total` : undefined,
    },
    {
      key: 'stopped',
      label: 'Stopped',
      value: stopped,
      tone: 'critical',
      icon: CircleSlash,
      hint: total > 0 ? `${Math.round((stopped / total) * 100)}% of total` : undefined,
    },
    {
      key: 'pendingAlerts',
      label: 'Pending alerts',
      value: stats.pending_alerts ?? 0,
      tone: (stats.pending_alerts ?? 0) > 0 ? 'info' : 'default',
      icon: Bell,
      hint: 'Need acknowledgement',
      href: '/safety/alerts',
      permission: (h) => h('can_alerts_all') || h('can_alerts_own'),
      showWhen: () => stats.pending_alerts !== undefined,
    },
    {
      key: 'lowFuel',
      label: 'Low fuel',
      value: stats.low_fuel ?? 0,
      tone: (stats.low_fuel ?? 0) > 0 ? 'critical' : 'positive',
      icon: Fuel,
      hint: 'Below 20%',
      permission: (h) => h('can_fuel'),
      showWhen: () => stats.low_fuel !== undefined,
    },
    {
      key: 'faults',
      label: 'Vehicles with faults',
      value: stats.faults ?? 0,
      tone: (stats.faults ?? 0) > 0 ? 'warning' : 'positive',
      icon: Wrench,
      hint: 'Open diagnostic codes',
      href: '/fleet/vehicles',
      permission: (h) => h('can_faults'),
      showWhen: () => stats.faults !== undefined,
    },
    {
      key: 'maintenance',
      label: 'Maintenance due',
      value: stats.maintenance_due ?? 0,
      tone: 'warning',
      icon: ClipboardList,
      hint: 'Tasks ready to schedule',
      href: '/maintenance',
      permission: (h) => h('can_maintenance_all'),
      showWhen: () => (stats.maintenance_due ?? 0) > 0,
    },
    {
      key: 'unsafeParking',
      label: 'Unsafe parking',
      value: unsafeParking,
      tone: 'critical',
      icon: ParkingCircle,
      hint: 'Drivers parked outside safe zones',
      href: '/fleet/parking',
      permission: (h) => h('can_alerts_all') || h('can_alerts_own'),
      showWhen: () => unsafeParking > 0,
    },
    {
      key: 'aiBriefing',
      label: 'AI briefing',
      value: 'Generate',
      tone: 'info',
      icon: Sparkles,
      hint: 'Status summary & recommendations',
      href: '/ai/chat?tab=briefing',
      permission: (h) => h('can_faults'),
    },
    {
      key: 'vehiclesLink',
      label: 'Vehicles list',
      value: 'Open',
      tone: 'info',
      icon: Truck,
      hint: 'Filter by status, fuel, or faults',
      href: '/fleet/vehicles',
      permission: (h) => h('can_vehicle_all'),
    },
    {
      key: 'routesLink',
      label: 'Routes',
      value: 'Replay',
      tone: 'info',
      icon: Route,
      hint: 'Trip history per truck',
      href: '/fleet/routes',
      permission: (h) => h('can_route_all') || h('can_route_own'),
    },
  ];

  const visible = cfg.priority
    .map((k) => allKpis.find((c) => c.key === k))
    .filter((c): c is KpiDef => !!c)
    .filter((c) => (c.permission ? c.permission(has) : true))
    .filter((c) => (c.showWhen ? c.showWhen() : true));

  // Split: status-tile group (state) vs link/action group (drill-downs).
  // The four core movement tiles — total/moving/idle/stopped — render
  // first as a 4-up grid, then everything else flows in a 3-up grid.
  const statusKeys: KpiKey[] = ['total', 'moving', 'idle', 'stopped'];
  const statusTiles = visible.filter((k) => statusKeys.includes(k.key));
  const otherTiles  = visible.filter((k) => !statusKeys.includes(k.key));

  return (
    <div>
      {greeting !== undefined && (
        <Greeting
          name={greeting}
          context={
            total > 0
              ? `${total} vehicles · ${movingPct}% currently moving`
              : 'Connect Samsara to start syncing vehicles.'
          }
        />
      )}

      <PageHeader
        icon={Truck}
        title={cfg.title}
        description={cfg.description}
        actions={
          <LastUpdated
            fetchedAt={fetchedAt}
            isFetching={isFetching}
            onRefresh={refetch}
          />
        }
      />

      {/* Tier 1 — needs action */}
      <AlertStrip items={alertItems} />

      {/* Tier 2 — AI narrative */}
      {total > 0 && <AISummaryCard stats={stats} role={role} />}

      {/* Tier 3 — current state (4-up status row) */}
      {statusTiles.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          {statusTiles.map((k) => (
            <KpiCard
              key={k.key}
              label={k.label}
              value={k.value}
              tone={k.tone}
              icon={k.icon}
              hint={k.hint}
              onClick={k.href ? () => navigate(k.href!) : undefined}
            />
          ))}
        </div>
      )}

      {/* Tier 3 — distribution chart */}
      {(moving > 0 || idle > 0 || stopped > 0) && (
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-sm text-foreground font-medium">
                Vehicle status distribution
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {movingPct >= 70
                  ? `Strong utilization today — ${movingPct}% of trucks on the road.`
                  : movingPct < 30
                  ? `Most trucks idle or stopped — only ${movingPct}% moving.`
                  : `${moving} of ${total} trucks moving (${movingPct}%).`}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">{total} vehicles</p>
          </div>
          <Suspense
            fallback={<div className="h-[220px] bg-muted/40 rounded animate-pulse" />}
          >
            <FleetStatusChart moving={moving} idle={idle} stopped={stopped} />
          </Suspense>
        </div>
      )}

      {/* Tier 4 — drill-downs (role-priority ordered) */}
      {otherTiles.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {otherTiles.map((k) => (
            <KpiCard
              key={k.key}
              label={k.label}
              value={k.value}
              tone={k.tone}
              icon={k.icon}
              hint={k.hint}
              onClick={k.href ? () => navigate(k.href!) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────

export default function Overview() {
  const navigate = useNavigate();
  const { has } = usePermissions();
  const { user } = useAuth();
  const role = user?.role;
  const isDriver = role === 'driver';
  const greetingName = user?.display_name || '';

  const {
    data: stats,
    error: queryError,
    isLoading,
    isFetching,
    refetch,
    dataUpdatedAt,
  } = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: () => apiJSON<DashboardStats>('/fleet/overview/stats'),
  });
  const errorMsg =
    queryError instanceof Error ? queryError.message : queryError ? 'Failed to load' : '';

  const headerCfg = isDriver
    ? { title: 'My Dashboard', description: 'Live status, fuel, faults, and quick links.' }
    : configForRole(role);

  if (isLoading && !stats) {
    return (
      <div>
        <Greeting name={greetingName} context="Loading current status…" />
        <PageHeader icon={Truck} title={headerCfg.title} />
        <KpiSkeleton count={4} />
      </div>
    );
  }

  if (errorMsg && !stats) {
    return (
      <div>
        <PageHeader icon={Truck} title={headerCfg.title} />
        <ErrorState
          title="Couldn't load dashboard"
          message={errorMsg}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (!stats) return null;

  if (isDriver) {
    return <DriverOverview stats={stats} navigate={navigate} greeting={greetingName} />;
  }

  // Fresh-tenant onboarding: detect when the account has no vehicles yet.
  const fleetTotal = stats.fleet?.total ?? 0;
  const showOnboarding = role === 'owner' || role === 'admin';

  return (
    <>
      {showOnboarding && (
        <OnboardingBanner
          vehicleCount={fleetTotal}
          userCount={1 /* TODO: wire real count when /admin/users is preloaded */}
        />
      )}
      <RoleOverview
        stats={stats}
        navigate={navigate}
        has={has}
        fetchedAt={dataUpdatedAt}
        isFetching={isFetching}
        refetch={refetch}
        greeting={greetingName}
        role={role}
      />
    </>
  );
}
