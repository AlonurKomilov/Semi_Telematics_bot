/**
 * Feature Catalog — the single source of truth for "what features exist
 * and where they belong."
 *
 * This is the keystone of the module/permission model (Option C).  Today
 * the sidebar is hand-curated in six `shells/nav/*Nav.ts` files, which
 * duplicate shared features and drift.  This catalog replaces that: every
 * feature is declared ONCE here, tagged with three things, and the
 * sidebar / permission page / module page are all *derived* from it.
 *
 * Each feature declares:
 *   • module(s) — which department capability surfaces it.  A feature
 *     appears in a user's sidebar only if AT LEAST ONE of its modules is
 *     enabled for the account.  `core` is always-on (can't be disabled).
 *   • tier — `system` (always present for anyone with the permission),
 *     `shared` (a cross-department feature several roles use), or
 *     `role` (a single department's own tool).  Drives grouping + headers.
 *   • permission — the `can_*` flag(s) gating real access.  Null = open
 *     to everyone.  This is the SAME flag the API enforces, so the nav
 *     never shows something a click would 403 on.
 *
 * The generated sidebar = catalog
 *   .filter(f => f.modules.some(m => account.enabledModules.has(m)))
 *   .filter(f => f.permission == null || userHasAny(f.permission))
 *   grouped by tier/module, with §-style headers.
 *
 * ⚠️ The `modules` arrays on SHARED features (Geofences, Parking,
 * Scorecards, Drivers, Fuel Costs…) are the main tuning knob: they decide
 * what, say, a Fleet-only company sees.  They're laid out explicitly below
 * so they're easy to review/adjust — see the inline notes.
 */
import {
  LayoutDashboard, Bot, Bell, FileText, BookOpen,
  Map as MapIcon, Truck, MapPin, Wrench, Receipt, ClipboardCheck, ParkingSquare,
  Route, Trophy, AlertTriangle, Camera, Package, Gauge,
  IdCard, GraduationCap, Link, UserPlus,
  Fuel, DollarSign, CreditCard,
  Users, Building2, Shield, Cloud, ClipboardList, Settings as SettingsIcon,
  Plug, Store, Cog,
  type LucideIcon, Boxes } from 'lucide-react';

export type Module =
  | 'core'        // always on — universal features every account gets
  | 'fleet'       // vehicle ops: maintenance, work orders, inspections
  | 'dispatch'    // routing: routes, parking, geofences
  | 'safety'      // scorecards, safety events, cameras, coaching
  | 'hr'          // drivers, coaching, onboarding, working hours
  | 'accounting'  // costs, subscription
  | 'account';    // owner/admin account configuration (always on for owner)

// tier = how widely a feature is used:
//   system = everyone (Overview, Alerts, Reports…)
//   shared = several departments (Live Map, Vehicles, Drivers…)
//   role   = one department's own tool (Maintenance, Routes, Driver Pay…)
// NB: distinct from the `modules` field (WHICH department) — tier is the
// breadth, modules is the owner.
export type Tier = 'system' | 'shared' | 'role';

// Sidebar section a feature lives in (display grouping for the generated
// nav).  `main` = pinned top (no header); `tail` = pinned bottom.
export type NavGroupKey =
  | 'main' | 'operations' | 'monitoring' | 'reports'
  | 'people' | 'costs' | 'account' | 'settings' | 'governance' | 'tail';

export interface CatalogFeature {
  /** Stable id (also the i18n suffix `nav.<id>`). */
  id: string;
  labelKey: string;
  path: string;
  icon: LucideIcon;
  /** Surfaced when ANY of these account modules is enabled. */
  modules: Module[];
  tier: Tier;
  /** can_* flag(s) gating access — ANY-of.  Null = open to all. */
  permission: string | string[] | null;
  /** Which sidebar section the generated nav places it in. */
  navGroup: NavGroupKey;
  /** True for catalog entries that have a route + permission but should
   *  NOT appear as their own sidebar item (e.g. Invites is a tab inside
   *  Team Management). */
  navHidden?: boolean;
  /** Nest this entry under another catalog entry in the sidebar
   *  (Settings-style indented child).  The parent renders a chevron;
   *  if the parent isn't visible for the persona, the child falls back
   *  to a flat entry so a grant never becomes unreachable. */
  parentId?: string;
}

// Reusable permission groups (kept in sync with capabilities/iam/permissions.py).
const P_LOCATION = ['can_location_map', 'can_location_vehicle'];
const P_VEHICLE = ['can_vehicle_all', 'can_vehicle_vehicle'];
const P_ALERTS = ['can_alerts_all', 'can_alerts_vehicle'];
const P_REPORTS = ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'];

export const FEATURE_CATALOG: CatalogFeature[] = [
  // ── CORE (always on) ──────────────────────────────────────────────
  { id: 'overview',       labelKey: 'nav.overview',       path: '/',          icon: LayoutDashboard, modules: ['core'], tier: 'system', permission: null, navGroup: 'main' },
  // Route-only: the assistant launches from the topbar icon (beside the
  // avatar) + ⌘J, not a sidebar row — navHidden keeps the route guard
  // and can_ai_chat wiring while dropping the nav entry.
  { id: 'ai_assistant',   labelKey: 'nav.ai_assistant',   path: '/ai/chat',   icon: Bot,             modules: ['core'], tier: 'system', permission: ['can_ai_chat'], navGroup: 'main', navHidden: true },
  // Alerts launches from the topbar bell (a monitoring SERVICE beside
  // the AI icon), not a sidebar row — navHidden keeps the /alerts route
  // guard + P_ALERTS wiring while dropping the nav entry.
  { id: 'alerts',         labelKey: 'nav.alerts',         path: '/alerts',    icon: Bell,            modules: ['core'], tier: 'system', permission: P_ALERTS, navGroup: 'monitoring', navHidden: true },
  { id: 'reports',        labelKey: 'nav.reports',        path: '/reports',   icon: FileText,        modules: ['core'], tier: 'system', permission: P_REPORTS, navGroup: 'reports' },
  // KPI — the account-wide performance analytics surface (dispatcher grades
  // first; fleet/safety/driver sections later).  Shared-tier, delegatable via
  // can_kpi.  Tagged `account` (like Billing) so it lives in the Owner/Admin
  // persona's own sidebar — it's management oversight, not a department tool;
  // dispatch/accounting surface it for department leads granted the flag.
  { id: 'kpi',            labelKey: 'nav.kpi',            path: '/kpi',       icon: Gauge,           modules: ['account', 'dispatch', 'accounting'], tier: 'shared', permission: ['can_kpi'], navGroup: 'reports' },
  { id: 'knowledge_base', labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen,        modules: ['core'], tier: 'system', permission: null, navGroup: 'tail' },
  // Universal operational views — every working persona needs to find a
  // truck, so these live in core (always available) rather than a module.
  { id: 'live_map', labelKey: 'nav.live_map', path: '/live-map', icon: MapIcon, modules: ['core'], tier: 'shared', permission: P_LOCATION, navGroup: 'operations' },
  { id: 'vehicles', labelKey: 'nav.vehicles', path: '/vehicles', icon: Truck, modules: ['core'], tier: 'shared', permission: P_VEHICLE, navGroup: 'operations' },
  // Inventory — the Vehicle feature's account-wide component page (what
  // lives in every truck; search by serial, filter missing/damaged).
  // Sits directly under Vehicles in the sidebar — a destination, unlike
  // the per-truck card (which is a detail-page section).
  { id: 'vehicle_inventory', labelKey: 'nav.vehicle_inventory', path: '/vehicles/inventory', icon: Boxes, modules: ['fleet', 'account'], tier: 'shared', permission: ['can_vehicle_all', 'can_vehicle_vehicle'], navGroup: 'operations', parentId: 'vehicles' },

  // ── FLEET (vehicle ops) ───────────────────────────────────────────
  { id: 'maintenance', labelKey: 'nav.maintenance', path: '/maintenance', icon: Wrench,         modules: ['fleet'], tier: 'role', permission: ['can_maintenance_all', 'can_maintenance_vehicle'], navGroup: 'operations' },
  { id: 'work_orders', labelKey: 'nav.work_orders', path: '/work-orders', icon: Receipt,        modules: ['fleet'], tier: 'role', permission: ['can_work_orders_all', 'can_work_orders_vehicle'], navGroup: 'operations' },
  { id: 'vendors', labelKey: 'nav.vendors', path: '/vendors', icon: Store,             modules: ['fleet'], tier: 'role', permission: 'can_work_orders_all', navGroup: 'operations' },
  // Parts — master data + per-part analytics (recurrence, price per
  // vendor).  Graduated from a Work Orders component; feature-owned gate.
  { id: 'parts', labelKey: 'nav.parts', path: '/parts', icon: Cog,                      modules: ['fleet'], tier: 'role', permission: 'can_parts', navGroup: 'operations' },
  { id: 'service-tasks', labelKey: 'nav.service_tasks', path: '/service-tasks', icon: ClipboardList, modules: ['fleet'], tier: 'role', permission: 'can_service_tasks', navGroup: 'operations' },
  { id: 'inspections', labelKey: 'nav.inspections', path: '/inspections', icon: ClipboardCheck, modules: ['fleet'], tier: 'role', permission: ['can_inspections_all', 'can_inspections_vehicle'], navGroup: 'operations' },
  // Geofences: zones serve both fleet (sites/yards) and dispatch
  // (routing boundaries) — surfaced when EITHER department is on.
  { id: 'geofences', labelKey: 'nav.geofences', path: '/geofences', icon: MapPin, modules: ['fleet', 'dispatch'], tier: 'shared', permission: ['can_geofence_all', 'can_geofence_vehicle'], navGroup: 'operations' },

  // ── DISPATCH (routing) ────────────────────────────────────────────
  { id: 'routes',  labelKey: 'nav.routes',  path: '/routes',  icon: Route,         modules: ['dispatch'], tier: 'role', permission: ['can_route_all', 'can_route_vehicle'], navGroup: 'operations' },
  // Loads — the canonical load/shipment list.  Dispatch owns entry; owners,
  // fleet and drivers read it (drivers see their own — scoped server-side),
  // so it's shared-tier like Vehicles.
  { id: 'loads',   labelKey: 'nav.loads',   path: '/loads',   icon: Package,       modules: ['dispatch'], tier: 'shared', permission: ['can_loads_all', 'can_loads_own'], navGroup: 'operations' },
  // Parking is a single-purpose feature (UNSAFE-parking events only — no
  // zones or general parking management), so it's role-tier like Safety
  // Events; the module list still lets dispatch + fleet surface it.
  { id: 'parking', labelKey: 'nav.parking', path: '/parking', icon: ParkingSquare, modules: ['dispatch', 'fleet', 'safety'], tier: 'role', permission: ['can_parking_all', 'can_parking_vehicle'], navGroup: 'operations' },

  // ── SAFETY (behaviour + incidents) ────────────────────────────────
  { id: 'events', labelKey: 'nav.events', path: '/safety-events', icon: AlertTriangle, modules: ['safety', 'hr'], tier: 'role', permission: ['can_events_all', 'can_events_vehicle'], navGroup: 'monitoring' },
  { id: 'cameras',       labelKey: 'nav.cameras',       path: '/cameras',       icon: Camera,        modules: ['safety', 'fleet'], tier: 'role', permission: ['can_cameras'], navGroup: 'monitoring' },
  // Scorecards are a Safety/coaching capability — surfaced only when the
  // Safety or HR department is on.  A Fleet-only or Dispatch-only company
  // won't see them (they can enable Safety to get them).
  { id: 'scorecards', labelKey: 'nav.scorecards', path: '/scorecards', icon: Trophy, modules: ['safety', 'hr'], tier: 'shared', permission: ['can_scorecard_all', 'can_scorecard_vehicle'], navGroup: 'monitoring' },

  // ── HR (people) ───────────────────────────────────────────────────
  { id: 'coaching', labelKey: 'nav.coaching', path: '/coaching', icon: GraduationCap, modules: ['hr', 'safety'], tier: 'role', permission: ['can_coaching_admin'], navGroup: 'people' },
  // Drivers (documents/compliance) — HR owns it; fleet + safety read it.
  { id: 'drivers', labelKey: 'nav.drivers', path: '/workforce/drivers', icon: IdCard, modules: ['hr', 'fleet', 'safety'], tier: 'shared', permission: ['can_manage_driver_docs'], navGroup: 'people' },
  // Recruiting intake — recruiter sends an apply.* link, prospects submit
  // FMCSA applications here; HR/recruiter triage + convert-to-driver.
  { id: 'applications', labelKey: 'nav.applications', path: '/workforce/applications', icon: UserPlus, modules: ['hr'], tier: 'role', permission: ['can_manage_applications'], navGroup: 'people' },
  // Carrier Knowledge Base — recruiter reference directory of external
  // carriers.  Read for recruiters; managers edit (page-level gate).
  { id: 'carrier_directory', labelKey: 'nav.carrier_directory', path: '/workforce/carrier-directory', icon: Building2, modules: ['hr'], tier: 'role', permission: ['can_carrier_directory'], navGroup: 'people' },
  // Working Hours has been folded into Team Management → Working
  // Hours tab.  Sidebar entry removed so operators don't see two
  // doors to the same config (Team Management nav entry covers it).
  // The route /work-hours still resolves via a Navigate
  // redirect in router.tsx for any legacy bookmark.

  // ── ACCOUNTING (money) ────────────────────────────────────────────
  { id: 'driver_pay',    labelKey: 'nav.driver_pay',    path: '/driver-pay',   icon: CreditCard, modules: ['accounting'], tier: 'role', permission: ['can_driver_pay_admin'], navGroup: 'costs' },
  // Costs — one accounting-leaning feature with two components (Fuel
  // Costs + Cost per Mile), so both are role-tier; the module lists let
  // dispatch (fuel) and fleet (CPM) surface their half.
  { id: 'fuel_costs',    labelKey: 'nav.fuel_costs',    path: '/costs/fuel', icon: Fuel,       modules: ['accounting', 'dispatch'], tier: 'role', permission: ['can_fuel_cost'], navGroup: 'costs' },
  { id: 'cost_per_mile', labelKey: 'nav.cost_per_mile', path: '/costs/cpm',  icon: DollarSign, modules: ['accounting', 'fleet'], tier: 'role', permission: ['can_cost_per_mile'], navGroup: 'costs' },

  // ── ACCOUNT ADMIN (always on for owner/admin) ─────────────────────
  // Invites is reached via the Team Management page tab now (navHidden),
  // but stays in the catalog so HR's onboarding entry + the route resolve.
  { id: 'invites',          labelKey: 'nav.invites',          path: '/invites',         icon: Link,          modules: ['hr', 'account'], tier: 'role', permission: ['can_invite'], navGroup: 'people', navHidden: true },
  { id: 'team_management',  labelKey: 'nav.team_management',  path: '/team',           icon: Users,         modules: ['account'], tier: 'system', permission: ['can_manage_users'], navGroup: 'settings' },
  // Billing — accounting owns the relationship; the owner always has it.
  { id: 'billing',          labelKey: 'nav.billing',          path: '/billing',         icon: CreditCard,    modules: ['accounting', 'account'], tier: 'role', permission: ['can_manage_billing'], navGroup: 'account' },
  { id: 'companies',        labelKey: 'nav.companies',        path: '/companies',       icon: Building2,     modules: ['account'], tier: 'system', permission: ['can_manage_companies'], navGroup: 'settings' },
  { id: 'integrations',     labelKey: 'nav.integrations',     path: '/integrations',    icon: Plug,          modules: ['account'], tier: 'system', permission: ['can_manage_integrations'], navGroup: 'account' },
  { id: 'storage',          labelKey: 'nav.storage',          path: '/storage',         icon: Cloud,         modules: ['account'], tier: 'system', permission: ['can_manage_storage'], navGroup: 'account' },
  { id: 'settings',         labelKey: 'nav.settings',         path: '/settings',        icon: SettingsIcon,  modules: ['account'], tier: 'system', permission: ['can_manage_account', 'can_manage_role_bot'], navGroup: 'settings' },
  { id: 'role_permissions', labelKey: 'nav.role_permissions', path: '/permissions',     icon: Shield,        modules: ['account'], tier: 'system', permission: ['can_manage_permissions'], navGroup: 'account' },
  // Scorecard Rules is the Scorecards (Shared) feature's CONFIG
  // component — reached via the gear icon on /scorecards (navHidden
  // keeps the route + deep links alive without a second sidebar entry).
  // Tier + modules MIRROR the parent scorecards (Shared, safety/hr) so
  // it appears/masks in lockstep with the feature it configures.
  { id: 'scorecard_rules',  labelKey: 'nav.scorecard_rules',  path: '/scorecard-rules', icon: Trophy,        modules: ['safety', 'hr'], tier: 'shared', permission: ['can_manage_scorecard_rules'], navGroup: 'monitoring', navHidden: true },
  { id: 'audit_log',        labelKey: 'nav.audit_log',        path: '/audit',           icon: ClipboardList, modules: ['account'], tier: 'system', permission: ['can_manage_users'], navGroup: 'settings' },
];

/** Display metadata for the 5 toggleable department modules (Core +
 *  Account are always on and aren't shown as toggles). */
export const TOGGLEABLE_MODULES: { id: Module; label: string; icon: LucideIcon; blurb: string }[] = [
  { id: 'fleet',      label: 'Fleet',      icon: Wrench,       blurb: 'Maintenance, work orders, inspections, geofences' },
  { id: 'dispatch',   label: 'Dispatch',   icon: Route,        blurb: 'Routes, parking, geofences' },
  { id: 'safety',     label: 'Safety',     icon: AlertTriangle, blurb: 'Scorecards, safety events, cameras' },
  { id: 'hr',         label: 'HR',         icon: IdCard,       blurb: 'Drivers, coaching, onboarding, working hours' },
  { id: 'accounting', label: 'Accounting', icon: DollarSign,   blurb: 'Fuel costs, cost per mile, subscription' },
];

export const ALWAYS_ON_MODULES: Module[] = ['core', 'account'];

/** Path → catalog feature, for sidebar module-filtering. */
export const CATALOG_BY_PATH: Map<string, CatalogFeature> = new Map(
  FEATURE_CATALOG.map((f) => [f.path, f]),
);

/**
 * Is a feature (by path) visible given the account's enabled modules?
 * Always-on modules (core/account) show regardless; a multi-module
 * feature shows if ANY of its modules is enabled.  Unknown paths and an
 * undefined `enabled` (pre-rollout / older API) both default to visible
 * so we never hide something by accident.
 */
export function isPathModuleEnabled(path: string, enabled: string[] | undefined): boolean {
  const feat = CATALOG_BY_PATH.get(path);
  if (!feat) return true;
  if (feat.modules.some((m) => (ALWAYS_ON_MODULES as string[]).includes(m))) return true;
  if (!enabled) return true;
  return feat.modules.some((m) => enabled.includes(m));
}
