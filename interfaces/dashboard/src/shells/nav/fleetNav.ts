/**
 * Fleet persona sidebar — operations-first navigation.
 *
 * A fleet manager's day revolves around vehicle health, maintenance,
 * and where the trucks are physically.  This nav puts the Fleet group
 * at the top (which is also where defaultNav has it), then trims away
 * the account-administration noise (Companies / Billing / Storage /
 * Role Permissions / Audit / Settings) since fleet managers don't
 * typically manage account-level config.  Items they DO need elsewhere
 * (Reports, Driver Documents, Safety Events for their trucks) stay in
 * their natural groups but lower in the sidebar.
 *
 * Note: permission filtering still runs on top of this list — items the
 * user can't access don't render.  This config controls ORDER and
 * VISIBILITY (i.e. which groups appear at all), not access.
 */
import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Wrench, Map,
  Camera, ParkingSquare, DollarSign, IdCard, Receipt,
  TrendingUp, Trophy, ClipboardCheck,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const fleetNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Routes intentionally omitted under strict role binding — that's
    // Dispatch's workspace.  A fleet manager who needs route context
    // (e.g. "where did this truck go last week to investigate fuel
    // burn") switches view to Dispatch via the persona selector.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map',    path: '/live-map',    icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles',    path: '/vehicles',    icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
      { labelKey: 'nav.geofences',   path: '/geofences',   icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { labelKey: 'nav.maintenance', path: '/maintenance', icon: Wrench,        permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { labelKey: 'nav.work_orders', path: '/work-orders', icon: Receipt,       permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { labelKey: 'nav.inspections', path: '/inspections', icon: ClipboardCheck, permission: ['can_inspections_all'] },
      { labelKey: 'nav.parking',     path: '/parking',     icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    // Safety_events intentionally omitted — that's Safety's primary.
    // Fleet keeps Cameras (mechanical/damage review) and Driver
    // Scorecards (driver behaviour affects fleet wear), plus the
    // universal Alerts inbox.
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.alerts',            path: '/alerts',            icon: Bell,   permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.cameras',           path: '/cameras',           icon: Camera, permission: ['can_faults'] },
      { labelKey: 'nav.driver_scorecards', path: '/driver-scorecards', icon: Trophy, permission: ['can_scorecard_all', 'can_scorecard_own'] },
    ],
  },
  {
    // Reports group — collapsed to a single entry pointing at the
    // Reports module shell.  Cost per Mile is the only outlier (it
    // lives at /costs/cpm structurally, not under /reports/*) so it
    // stays as its own sidebar item.  Per-tab visibility inside the
    // Reports module is gated by flags in ReportsLayout.tsx.
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
      { labelKey: 'nav.cost_per_mile', path: '/costs/cpm', icon: DollarSign, permission: ['can_cost_per_mile'] },
    ],
  },
  {
    // Workforce — fleet managers check driver docs (DOT compliance).
    // Coaching intentionally omitted under strict role binding —
    // that's Safety's workspace.
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.drivers', path: '/workforce/drivers', icon: IdCard, permission: ['can_manage_driver_docs'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
  // NOTE: account_admin group (Companies/Billing/Role Permissions/etc.)
  // is intentionally omitted.  A Fleet user is unlikely to have those
  // permissions in the first place; an Owner/Admin previewing as Fleet
  // gets a Fleet-realistic sidebar.  Cross-role navigation (Routes,
  // Safety Events, Coaching, Fuel Costs) happens via the persona
  // selector → subdomain switch, not via cross-pollinated sidebars.
];
