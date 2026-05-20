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
  Wrench, Map, Route, AlertTriangle,
  Camera, ParkingSquare, Fuel, DollarSign, IdCard, Receipt,
  TrendingUp, GraduationCap,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const fleetNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map',    path: '/live-map',       icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles',    path: '/vehicles',  icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
      { labelKey: 'nav.routes',      path: '/routes',    icon: Route,         permission: ['can_route_all', 'can_route_own'] },
      { labelKey: 'nav.geofences',   path: '/geofences', icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { labelKey: 'nav.maintenance', path: '/maintenance',     icon: Wrench,        permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { labelKey: 'nav.work_orders', path: '/work-orders',     icon: Receipt,       permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { labelKey: 'nav.parking',     path: '/parking',   icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports',       path: '/reports',               icon: FileText,   permission: null },
      { labelKey: 'nav.subscriptions', path: '/reports/subscriptions', icon: Mail,       permission: null },
      { labelKey: 'nav.fuel_costs',    path: '/costs/fuel',            icon: Fuel,       permission: ['can_fuel_cost'] },
      { labelKey: 'nav.cost_per_mile', path: '/costs/cpm',             icon: DollarSign, permission: ['can_cost_per_mile'] },
      { labelKey: 'nav.cost_reports',  path: '/cost-reports',          icon: TrendingUp, permission: ['can_maintenance_all'] },
    ],
  },
  {
    // Safety items relevant to fleet managers — they care about events
    // and faults on THEIR trucks, but rarely review scorecards in depth.
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.alerts',        path: '/alerts', icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.safety_events', path: '/safety-events', icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.cameras',       path: '/cameras', icon: Camera,       permission: ['can_faults'] },
    ],
  },
  {
    // Workforce — fleet managers occasionally check driver docs (DOT
    // compliance) but coaching/payroll belong to admin/safety personas.
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.drivers',  path: '/workforce/drivers', icon: IdCard,        permission: ['can_manage_driver_docs'] },
      { labelKey: 'nav.coaching', path: '/coaching',          icon: GraduationCap, permission: ['can_coaching_admin'] },
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
  // gets a Fleet-realistic sidebar.  To customize a specific account's
  // settings, switch back to the Owner/Admin view via the top-left
  // persona selector.
  // ``risk_summary`` is also omitted — it's primarily a Safety persona
  // tool and clutters the Fleet experience.
];
