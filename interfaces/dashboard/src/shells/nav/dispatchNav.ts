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
  Map, Route,
  AlertTriangle, ParkingSquare, MapPin, IdCard, FileText, Mail,
  Camera,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const dispatchNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Synthetic "Dispatching" top group — a dispatcher's high-frequency
    // operational tools, pulled from across the canonical groups so the
    // map and alerts are one click away from any page.
    titleKey: 'nav.dispatching_group',
    items: [
      { labelKey: 'nav.live_map',  path: '/fleet/map',       icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.routes',    path: '/fleet/routes',    icon: Route,         permission: ['can_route_all', 'can_route_own'] },
      { labelKey: 'nav.alerts',    path: '/safety/alerts',   icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.geofences', path: '/fleet/geofences', icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { labelKey: 'nav.parking',   path: '/fleet/parking',   icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.vehicles',  path: '/fleet/vehicles',  icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.drivers', path: '/workforce/drivers', icon: IdCard, permission: ['can_manage_driver_docs'] },
    ],
  },
  {
    // Safety items dispatchers occasionally need (e.g. reviewing an
    // event's footage before reassigning a route).
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.safety_events', path: '/safety/events',  icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.cameras',       path: '/safety/cameras', icon: Camera,        permission: ['can_faults'] },
    ],
  },
  {
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports',       path: '/reports',               icon: FileText, permission: null },
      { labelKey: 'nav.subscriptions', path: '/reports/subscriptions', icon: Mail,     permission: null },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
  // NOTE: Maintenance/Work Orders/Cost Reports/Risk Summary intentionally
  // omitted — those are Fleet/Admin/Safety concerns.  Dispatchers do
  // route work; they don't schedule oil changes or review monthly cost
  // breakdowns.  Same omission rationale for account_admin group.
];
