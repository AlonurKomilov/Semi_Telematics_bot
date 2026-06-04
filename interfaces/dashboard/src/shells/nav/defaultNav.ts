/**
 * Default sidebar navigation tree — used by DefaultShell (Owner / Admin).
 *
 * This file is the SSOT for "what's in the sidebar" for the default
 * shell.  Per-role shells (FleetShell, DispatchShell, …) export their
 * own nav configs from sibling files (fleetNav.ts, etc.).
 *
 * **Strict role binding** — Owner/Admin's default sidebar contains
 * ONLY items that are Owner/Admin's own work: executive visibility
 * (Live Map + Vehicles + their scoped Alerts), Reports (persona-
 * filtered tabs), Workforce admin (staffing the account), and
 * Account admin (configuring the account).  Operational items
 * (Routes/Geofences/Parking/Maintenance/Work Orders/Inspections/
 * Driver Scorecards/Safety Events/Cameras/Coaching/Drivers/Payroll/
 * Fuel Costs/Cost per Mile) are NOT in the default sidebar — Owner
 * accesses them by switching view via the persona selector
 * (RoleViewContext.switchView → fleet/dispatch/safety/hr/accounting.
 * 4truck.us).  See docs/architecture/PERSONA.md for the model.
 *
 * Exceptions kept as executive concerns:
 *   • Risk Summary — owner-level risk oversight signal
 *   • Cost Reports — owner-level quarterly economics review
 */
import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, BookOpen,
  Map, Trophy, Users, Shield, Building2,
  Link, ClipboardList, Clock, CreditCard,
  TrendingUp, Cloud, Settings as SettingsIcon,
  type LucideIcon,
} from 'lucide-react';

// NavItem labels are i18n keys looked up at render time with t().
// titleKey is null when the group has no header (top + tail groups).
export interface NavItem {
  labelKey: string;
  path: string;
  icon: LucideIcon;
  permission: string | string[] | null;
}

export interface NavGroup {
  titleKey: string | null;
  items: NavItem[];
}

export const defaultNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Executive visibility — Owner needs asset-level "where are my
    // trucks and what do I have" without doing the operational work.
    // Routes / Geofences / Maintenance / Work Orders / Inspections /
    // Parking moved to their owning persona's sidebar (Dispatch,
    // Fleet); Owner switches view via the persona selector.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map', path: '/live-map', icon: Map,   permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles', path: '/vehicles', icon: Truck, permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Owner inbox — the persona-scoped Alerts count (Phase 3-D strict
    // binding) shows system + reescalate alerts only.  Operational
    // triage (driver scorecards / safety events / cameras) lives on
    // Safety's sidebar; Owner switches view to drill in.
    titleKey: null,
    items: [
      { labelKey: 'nav.alerts', path: '/alerts', icon: Bell, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    // Reports — collapsed to a single entry pointing at the Reports
    // module shell.  Inside the module, ReportsLayout.tsx renders a
    // cross-page sub-nav with Reports / Risk Summary / Cost Reports /
    // Scheduled Reports.  Per-tab visibility is gated there by the
    // user's flags so the sub-nav stays in lock-step with what the
    // sidebar used to show as four separate items.  Pre-2026-06 each
    // sub-page had its own sidebar entry; collapsed here so the
    // sidebar doesn't drift from the in-module tab list.
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
    ],
  },
  {
    // Workforce admin — staffing the account is Owner/Admin's work.
    // Drivers / Coaching / Payroll moved to their owning persona's
    // sidebar (HR / Safety / Accounting); Owner switches view to
    // those personas to manage day-to-day workforce ops.
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.team_management', path: '/admin/users',      icon: Users, permission: ['can_manage_users'] },
      { labelKey: 'nav.invites',         path: '/admin/invites',    icon: Link,  permission: ['can_invite'] },
      { labelKey: 'nav.working_hours',   path: '/admin/work-hours', icon: Clock, permission: ['can_manage_account'] },
    ],
  },
  {
    // Account admin — Owner/Admin's core configuration work.
    titleKey: 'nav.admin_group',
    items: [
      { labelKey: 'nav.companies',        path: '/admin/companies',       icon: Building2,     permission: ['can_manage_companies'] },
      { labelKey: 'nav.role_permissions', path: '/admin/permissions',     icon: Shield,        permission: ['can_manage_account'] },
      { labelKey: 'nav.scorecard_rules',  path: '/admin/scorecard-rules', icon: Trophy,        permission: ['can_manage_account'] },
      { labelKey: 'nav.billing',          path: '/admin/billing',         icon: CreditCard,    permission: ['can_manage_billing'] },
      // Attachment Storage — account-level setting (where the fleet's
      // bytes land: platform disk vs the user's connected Google Drive).
      // Lives in the sidebar, not the avatar menu, because the choice
      // affects the whole account, not just the logged-in user.
      { labelKey: 'nav.storage',          path: '/admin/storage',         icon: Cloud,         permission: ['can_manage_account'] },
      { labelKey: 'nav.audit_log',        path: '/admin/audit',           icon: ClipboardList, permission: ['can_manage_users'] },
      // Account-wide settings (timezone, Telegram bot, working hours,
      // feature flags).  Personal preferences (display name, language,
      // own timezone override, quiet hours) live in /profile via the
      // AvatarMenu.
      { labelKey: 'nav.settings',         path: '/admin/settings',        icon: SettingsIcon,  permission: ['can_manage_account'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
];
