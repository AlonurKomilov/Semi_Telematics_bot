/**
 * Dispatch persona sidebar — routing-first navigation.
 *
 * A dispatcher's workflow is map-first: where are my routes, which
 * vehicles are running late, which alerts need acknowledgement?  This
 * nav synthesizes a "Dispatching" top group that pulls together the
 * items a dispatcher hits dozens of times a day, regardless of which
 * group they live in under defaultNav.
 *
 * Note: permission filtering still runs on top — items the user can't
 * access don't render.  Several items overlap with the Safety group's
 * canonical home (Alerts in particular).  That's intentional: this
 * file determines order/visibility, the permission flag itself stays
 * the SSOT for access.
 */
import {
  LayoutDashboard, Truck, Bell, Bot, BookOpen,
  Map, Route, Fuel, Trophy,
  ParkingSquare, MapPin, FileText, Mail,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const dispatchNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Synthetic "Dispatching" top group — a dispatcher's high-frequency
    // operational tools, pulled from across the canonical groups so the
    // map and alerts are one click away from any page.  Maintenance
    // intentionally omitted under strict role binding — that's Fleet's
    // workspace; a dispatcher needing maintenance context switches
    // view to Fleet via the persona selector.
    titleKey: 'nav.dispatching_group',
    items: [
      { labelKey: 'nav.live_map',    path: '/live-map',    icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.routes',      path: '/routes',      icon: Route,         permission: ['can_route_all', 'can_route_own'] },
      { labelKey: 'nav.alerts',      path: '/alerts',      icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.geofences',   path: '/geofences',   icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { labelKey: 'nav.parking',     path: '/parking',     icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.vehicles',    path: '/vehicles',    icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Driver behaviour context for dispatchers — scorecards help pick
    // which driver to assign to a sensitive load.  Cameras + workforce
    // entries were dropped: Dispatch's role defaults don't grant
    // can_faults or can_manage_driver_docs, so those entries appeared
    // in the sidebar but every click 403'd.  If dispatch needs the
    // camera view they can switch persona via View-Dashboard-As.
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.driver_scorecards', path: '/driver-scorecards', icon: Trophy, permission: ['can_scorecard_all', 'can_scorecard_own'] },
    ],
  },
  {
    // Reports group — collapsed to the Reports module shell entry.
    // Fuel Costs stays as a separate sidebar item (it's under
    // /costs/* structurally, not /reports/*) so dispatchers can route
    // around fuel cost without entering the Reports module.
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
      { labelKey: 'nav.fuel_costs', path: '/costs/fuel', icon: Fuel, permission: ['can_fuel_cost'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
  // NOTE: Work Orders / Cost Reports / Risk Summary intentionally
  // omitted — those are Fleet/Admin/Safety concerns.  Dispatchers see
  // Maintenance read-only (vehicle availability) but don't schedule
  // service, and they don't review monthly cost breakdowns or risk
  // composites.  Same omission rationale for account_admin group.
];
