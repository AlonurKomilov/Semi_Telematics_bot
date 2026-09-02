/**
 * Drill-down KPI grid — Tier 4 of the Overview.
 *
 * Persona-agnostic by contract — receives the resolved ``kpiPriority``
 * array via sectionProps.  The page wrapper (``Overview.tsx``)
 * computes the priority from ``personaConfig.resolveKpiPriority(persona)``;
 * this section just renders the KPIs in the order it's handed.
 *
 * Each KPI is permission-gated independently via the ``has`` prop;
 * ``showWhen`` further hides KPIs whose underlying number is zero /
 * missing so the grid doesn't pad with empty cards.
 */
import {
  Bell,
  Fuel,
  Wrench,
  ClipboardList,
  ParkingCircle,
  Sparkles,
  Truck,
  Route,
  type LucideIcon,
} from 'lucide-react';
import { KpiCard } from '../../../components/shell';
import type { KpiKey } from '../personaConfig';
import type { OverviewSectionProps } from './_shared/types';

interface KpiDef {
  key: KpiKey;
  label: string;
  value: number | string;
  tone?: 'default' | 'positive' | 'warning' | 'critical' | 'info';
  icon: LucideIcon;
  hint?: string;
  href?: string;
  permission?: (has: (flag: string) => boolean) => boolean;
  showWhen?: () => boolean;
}

export default function OverviewKpiGrid({
  stats,
  navigate,
  has,
  kpiPriority,
}: OverviewSectionProps) {
  const unsafeParking =
    (stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0);

  const allKpis: KpiDef[] = [
    {
      key: 'pendingAlerts',
      // "Open", not "Pending": ``pending_alerts`` is the wire key, and the
      // board calls this the open queue.  One object, one noun.
      label: 'Open alerts',
      value: stats.pending_alerts ?? 0,
      tone: (stats.pending_alerts ?? 0) > 0 ? 'info' : 'default',
      icon: Bell,
      // The scope is the whole point of this hint.  This number counts
      // only the alert types the ACTIVE VIEW handles, while the board
      // lists every type — so the two legitimately differ, and without
      // saying so they just look like one of them is wrong.
      hint: 'Awaiting acknowledgement, in this view',
      href: '/alerts',
      permission: (h) => h('can_view_vehicles'),
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
      href: '/vehicles',
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
      permission: (h) => h('can_manage_maintenance'),
      showWhen: () => (stats.maintenance_due ?? 0) > 0,
    },
    {
      key: 'unsafeParking',
      label: 'Unsafe parking',
      value: unsafeParking,
      tone: 'critical',
      icon: ParkingCircle,
      hint: 'Drivers parked outside safe zones',
      href: '/parking',
      permission: (h) => h('can_view_vehicles'),
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
      href: '/vehicles',
      permission: (h) => h('can_vehicle_all'),
    },
    {
      key: 'routesLink',
      label: 'Routes',
      value: 'Replay',
      tone: 'info',
      icon: Route,
      hint: 'Trip history per truck',
      href: '/routes',
      permission: (h) => h('can_view_routes'),
    },
  ];

  const visible = kpiPriority
    .map((k) => allKpis.find((c) => c.key === k))
    .filter((c): c is KpiDef => !!c)
    .filter((c) => (c.permission ? c.permission(has) : true))
    .filter((c) => (c.showWhen ? c.showWhen() : true));

  if (visible.length === 0) return null;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
      {visible.map((k) => (
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
  );
}
