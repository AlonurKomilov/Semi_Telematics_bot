/**
 * "Needs action right now" alert strip — Tier 1 of the Overview.
 *
 * Pulls from the dashboard stats and builds a flat list of the things
 * that need acknowledging today.  Each item is gated by BOTH:
 *   • permission  (e.g. ``can_fuel`` for low-fuel trucks), AND
 *   • view-role relevance (does the active view's KPI priority
 *     include this key?).  This keeps a Safety user from seeing
 *     "low-fuel trucks" — that's Dispatch's lane — even though
 *     Safety has ``can_fuel`` via role permissions.
 *
 * The view-role check reuses the same ``kpiPriority`` the KpiGrid
 * already receives via sectionProps.  Source of truth stays in one
 * place (``personaConfig.ROLE_KPI_PRIORITY``).  Sections remain
 * persona-agnostic per Pattern B contract — they receive the resolved
 * priority list and never read activeView themselves.
 *
 * Renders empty (and contributes nothing visually) when nothing's
 * actionable — the AlertStrip component itself returns null in that
 * case.
 */
import { Bell, Fuel, ParkingCircle, ClipboardList, Wrench } from 'lucide-react';
import { AlertStrip, type AlertItem } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewAlertStrip({ stats, has, kpiPriority }: OverviewSectionProps) {
  // View-role relevance: only render items whose KPI key is in the
  // active view's priority list.  Empty set ⇒ no operational signals
  // relevant to this view (accounting / fallback case) ⇒ strip stays
  // empty.
  const relevant = new Set<string>(kpiPriority);

  const alertItems: AlertItem[] = [];

  if (
    (stats.pending_alerts ?? 0) > 0 &&
    relevant.has('pendingAlerts') &&
    has('can_view_vehicles')
  ) {
    alertItems.push({
      label: 'open alerts',
      count: stats.pending_alerts ?? 0,
      href: '/alerts',
      icon: Bell,
      tone: 'info',
    });
  }

  if ((stats.low_fuel ?? 0) > 0 && relevant.has('lowFuel') && has('can_fuel')) {
    alertItems.push({
      label: 'low-fuel trucks',
      count: stats.low_fuel ?? 0,
      href: '/vehicles',
      icon: Fuel,
      tone: 'critical',
    });
  }

  const unsafeParking =
    (stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0);
  if (
    unsafeParking > 0 &&
    relevant.has('unsafeParking') &&
    has('can_view_vehicles')
  ) {
    alertItems.push({
      label: 'parked unsafely',
      count: unsafeParking,
      href: '/parking',
      icon: ParkingCircle,
      tone: 'critical',
    });
  }

  if (
    (stats.maintenance_due ?? 0) > 0 &&
    relevant.has('maintenance') &&
    has('can_manage_maintenance')
  ) {
    alertItems.push({
      label: 'maintenance due',
      count: stats.maintenance_due ?? 0,
      href: '/maintenance',
      icon: ClipboardList,
      tone: 'warning',
    });
  }

  if (
    (stats.faults ?? 0) > 0 &&
    relevant.has('faults') &&
    has('can_faults')
  ) {
    alertItems.push({
      label: 'with active faults',
      count: stats.faults ?? 0,
      href: '/vehicles',
      icon: Wrench,
      tone: 'warning',
    });
  }

  return <AlertStrip items={alertItems} />;
}
