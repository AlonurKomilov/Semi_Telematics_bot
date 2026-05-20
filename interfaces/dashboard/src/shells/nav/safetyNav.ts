/**
 * Safety persona sidebar — scorecard + events-first navigation.
 *
 * A safety manager's day is driven by scorecards (which drivers are
 * trending down), events (harsh braking, speeding), cameras (incident
 * review), and coaching follow-ups.  This nav puts the Safety group
 * first and surfaces Coaching prominently in Workforce.  Operational
 * fleet management (Vehicles/Routes/Maintenance) is dropped since a
 * safety manager typically doesn't manage trucks day-to-day.
 *
 * Note: permission filtering still runs on top — items the user can't
 * access don't render.  This config controls ORDER and VISIBILITY only.
 */
import {
  LayoutDashboard, Bell, Bot, FileText, Mail, BookOpen,
  Trophy, AlertTriangle, Camera, ParkingSquare,
  IdCard, GraduationCap, Map,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const safetyNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.driver_scorecards', path: '/safety/scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { labelKey: 'nav.safety_events',     path: '/safety/events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.cameras',           path: '/safety/cameras',    icon: Camera,        permission: ['can_faults'] },
      { labelKey: 'nav.alerts',            path: '/safety/alerts',     icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.parking',           path: '/fleet/parking',     icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    // Workforce — safety personas drive coaching workflows and review
    // driver documents/qualifications.
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.coaching', path: '/coaching',          icon: GraduationCap, permission: ['can_coaching_admin'] },
      { labelKey: 'nav.drivers',  path: '/workforce/drivers', icon: IdCard,        permission: ['can_manage_driver_docs'] },
    ],
  },
  {
    // Reports — safety-leaning report items (Risk Summary belongs here
    // primarily, fuel/cost reports are de-emphasized for this persona).
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.risk_summary',  path: '/reports/risk-summary',  icon: FileText, permission: ['can_risk_report_all', 'can_risk_report_own'] },
      { labelKey: 'nav.reports',       path: '/reports',               icon: FileText, permission: null },
      { labelKey: 'nav.subscriptions', path: '/reports/subscriptions', icon: Mail,     permission: null },
    ],
  },
  {
    // Fleet items kept lightweight — a safety manager occasionally
    // needs the Live Map to see where a vehicle was during an incident.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map', path: '/fleet/map', icon: Map, permission: ['can_location_map', 'can_location_own'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
  // NOTE: Maintenance/Work Orders/Costs intentionally omitted — those
  // are Fleet/Admin concerns and don't belong in a safety manager's
  // daily flow.  Same for account_admin group (Companies, Billing,
  // etc.).
];
