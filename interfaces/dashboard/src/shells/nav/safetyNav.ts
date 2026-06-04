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
  IdCard, GraduationCap, Map, Truck,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const safetyNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Inspections intentionally omitted under strict role binding —
    // PTI history is HR/Fleet's compliance + mechanical workflow,
    // not safety's behavioural focus.  Safety operators needing
    // walkaround context for an incident review switch view to HR
    // or Fleet via the persona selector.
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.driver_scorecards', path: '/driver-scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { labelKey: 'nav.safety_events',     path: '/safety-events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.cameras',           path: '/cameras',    icon: Camera,        permission: ['can_faults'] },
      { labelKey: 'nav.alerts',            path: '/alerts',     icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
      { labelKey: 'nav.parking',           path: '/parking',     icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
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
    // Reports — collapsed to a single entry; Risk Summary is the
    // Safety persona's primary report and it surfaces inside the
    // Reports module via the cross-page sub-nav.
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
    ],
  },
  {
    // Fleet items a safety manager needs for context: Live Map to see
    // where a vehicle was during an incident, Vehicles to drill into a
    // specific truck's profile.  Maintenance removed under strict role
    // binding — that's Fleet's workspace.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map', path: '/live-map', icon: Map,   permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles', path: '/vehicles', icon: Truck, permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
  // NOTE: Work Orders / Costs / Maintenance / Inspections intentionally
  // omitted — those are Fleet / HR / Admin concerns.  A safety operator
  // needing cross-role context uses the "View dashboard as…" switcher
  // to jump to that persona's workspace via subdomain.  Same omission
  // rationale for account_admin group (Companies, Billing, etc.).
];
