/**
 * HR persona sidebar — people-management navigation.
 *
 * An HR manager's day is driven by driver compliance (CDL / medical
 * expiry, document tracking), coaching follow-ups, and onboarding.
 * Their navigation puts the Workforce group at the top, surfaces
 * Safety items as coaching context, and trims away vehicle-ops,
 * finance, and account-config noise.  Read-only Fleet (Live Map +
 * Vehicles) stays in a separate group as context — HR doesn't action
 * fleet ops but needs to see WHICH vehicle a driver is on for
 * compliance / incident review.
 *
 * Permission filtering still runs on top — see capabilities/iam/
 * permissions.py for the HR FeatureSet that determines which items
 * actually render.  This config controls ORDER and VISIBILITY; the
 * permission flag is the SSOT for access.
 */
import {
  LayoutDashboard, Truck, Bell, Bot, BookOpen, Map,
  AlertTriangle, IdCard, FileText, Mail, Link,
  Trophy, GraduationCap,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const hrNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Workforce-first — HR's primary working set.  Inspections
    // intentionally omitted under strict role binding — that's the
    // Fleet (mechanical) / Safety (compliance) workspace.  HR
    // operators needing PTI history for an incident review switch
    // view to Fleet via the persona selector.
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.drivers',  path: '/workforce/drivers', icon: IdCard,        permission: ['can_manage_driver_docs'] },
      { labelKey: 'nav.coaching', path: '/coaching',          icon: GraduationCap, permission: ['can_coaching_admin'] },
    ],
  },
  {
    // Safety items as coaching context — driver scorecards drive
    // coaching priorities; safety events become coaching topics; the
    // alerts queue surfaces driver behaviour HR cares about.
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.driver_scorecards', path: '/driver-scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { labelKey: 'nav.safety_events',     path: '/safety-events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.alerts',            path: '/alerts',            icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    // Read-only Fleet — HR sees WHO is on WHICH truck for compliance
    // / incident review.  No Maintenance or Work Orders — those are
    // Fleet's domain.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map', path: '/live-map', icon: Map,   permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles', path: '/vehicles', icon: Truck, permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Reports — collapsed to the Reports module shell entry; the
    // module's cross-page sub-nav surfaces Risk Summary + Scheduled
    // Reports tabs internally for HR.
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
    ],
  },
  {
    titleKey: null,
    items: [
      // Invites are HR's onboarding tool — surface as a standalone
      // item rather than buried in admin/.
      { labelKey: 'nav.invites',        path: '/admin/invites', icon: Link,     permission: ['can_invite'] },
      { labelKey: 'nav.knowledge_base', path: '/knowledge',     icon: BookOpen, permission: null },
    ],
  },
  // NOTE: account_admin group (Companies / Billing / Role Permissions
  // / Storage / Settings) intentionally omitted.  HR isn't an
  // account-config role; broader account admin lives with Owner.
];
