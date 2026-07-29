import { useState, useMemo, Fragment, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield, Check, X, Lock, ChevronRight, ChevronDown, Bell, Bot, FileText, Smartphone, AlertTriangle } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses, toneText } from '../../lib/status';

// Column order mirrors the persona-selector dropdown.  The Driver role is
// deliberately ABSENT: a driver never manages anything and lives only in the
// Telegram mini app, so the staff-matrix columns (Permissions, Storage,
// Manage-*) are meaningless for it.  Driver access is configured in the
// dedicated "Driver — self-service" panel below the matrix instead.
const ROLES = [
  'owner', 'admin', 'fleet', 'dispatcher', 'safety', 'hr', 'accounting', 'recruiter',
] as const;
type RoleId = typeof ROLES[number];

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner', admin: 'Admin', fleet: 'Fleet', dispatcher: 'Dispatch',
  safety: 'Safety', hr: 'HR', accounting: 'Accounting',
  recruiter: 'Recruiter', driver: 'Driver',
};

// ── Permission flag model ─────────────────────────────────────────
// A feature is gated either by a single boolean flag (SimpleFlag) or by
// a scoped pair (ScopedFlag: an "all" key + a "vehicle" key).  In this
// matrix the cell is a single checkbox: checked = grant the feature at
// the role's default scope (vehicle for drivers, all for everyone
// else); unchecked = no access.  Whose data the role actually sees
// (All / Company / Vehicle) is configured per-user in Team Management.
// `indented` rows are sub-components of the feature/header above them
// (e.g. Manage POI Layers under Live Map; the individual reports under
// the Reports header).  A FeatureHeader is a pure label row (no
// checkboxes) used to group sub-permissions that have no parent flag.
// Every tickable row DECLARES what kind of thing it grants — the matrix's
// taxonomy is explicit, not implied by indentation (docs/FEATURES.md
// "Row kinds").  The kinds mirror the product taxonomy 1:1:
//   feature     — a feature's front door (untagged row = view access)
//   subfeature  — own home (folder + hub contributions) under a family
//                 (Health/Faults/Fuel/Efficiency under features/vehicles/)
//   component   — flag-gated part of the parent's surface, no own home
//   action      — a do/write verb on one feature (the "Manage" rows)
//   capability  — spans features (the two Config rows)
// Services (Alerts / AI / Reports) have NO kind because they have no rows:
// always-on, nothing to grant — see the System Services panel below.
type RowKind = 'feature' | 'subfeature' | 'component' | 'action' | 'capability';
interface RowMeta {
  kind: RowKind;
  /** Attach under the row with this PRIMARY key (allKey for scoped rows)
   *  instead of the nearest top-level row — lets a sub-feature own its
   *  own action/component rows (depth 2).  Capabilities never nest. */
  parentKey?: string;
  /** The row's SINGLE flag is write-level (key says manage/admin) though
   *  its label is the feature noun — renders the muted "manage" tag so
   *  an owner can tell granting it is not read-only.  Bare-verb action
   *  rows don't need it: their label already announces the write. */
  writeLevel?: true;
}
interface ScopedFlag extends RowMeta { allKey: string; vehicleKey: string; label: string; scoped: true; description?: string; indented?: boolean }
interface SimpleFlag extends RowMeta { key: string; label: string; scoped?: false; description?: string; indented?: boolean }
interface FeatureHeader { header: string; description?: string }
type PermFlag = ScopedFlag | SimpleFlag | FeatureHeader;
interface PermGroup { title: string; flags: PermFlag[] }

const isHeader = (f: PermFlag): f is FeatureHeader => 'header' in f;
const isScoped = (f: PermFlag): f is ScopedFlag => (f as ScopedFlag).scoped === true;

// ── Per-role data-scope default ───────────────────────────────────
// This matrix only grants/revokes FEATURES.  "Whose data" (All /
// Company / Vehicle) is configured per-user in Team Management, not
// here.  When a scoped (*) feature is ticked we grant it at the role's
// intrinsic default: drivers are vehicle-scoped (their assigned vehicle
// only); every other role sees all, then narrowed per-user by the
// Company / Vehicle assignment in Team Management.  Returns the
// [allKey, vehicleKey] pair to write.
const DEFAULT_SCOPED_FLAGS = (role: string): [boolean, boolean] =>
  role === 'driver' ? [false, true] : [true, true];

// Owner escape-hatch permissions — locked-on in the Owner column so an
// owner can never revoke their own way back from a misconfiguration.
// Mirrors OWNER_PROTECTED_PERMS in capabilities/iam/permissions.py (the
// backend enforces it regardless of the UI).
const OWNER_PROTECTED = new Set([
  'can_manage_account', 'can_manage_users', 'can_manage_billing', 'can_manage_companies', 'can_manage_permissions',
]);

// PERM_GROUPS — admin-facing grouping, mirrors the sidebar sections so an
// admin maps "what I see in the nav" → "where I grant it".  Flag names
// are unchanged (backend enforcement keeps working).
// Groups mirror the catalog taxonomy (tier → department): System, Shared,
// then one block per department — the SAME buckets as the Modules page,
// so the matrix and the modules speak one language.  Sub-permissions are
// `indented` under their parent feature (POI Layers under Live Map, the
// reports under the Reports header, View-Own pairs under their admin row).
// Exported for the drift-guard test (permMatrix.test.ts) — every row's
// kind, nesting and family order are pinned there.  The react-refresh
// warning is accepted: the constant belongs beside the rows it types,
// and the page reloads fine (same trade RoleViewContext makes).
// eslint-disable-next-line react-refresh/only-export-components
export const PERM_GROUPS: PermGroup[] = [
  {
    // System — available to everyone, account-wide.
    title: 'System',
    flags: [
      // Alerts (the inbox) and Reports (the hub) are NOT rows here — both are
      // always-on services EVERY role has.  Disabling a feature only drops
      // that feature's alerts/report-tab out of the surface (Faults / Health /
      // Fuel / Safety Events / Geofences / Maintenance), never the surface
      // itself.  Both are shown read-only in the "System Services" panel below.
      // The individual report TYPES are genuine per-role features and live in
      // their owning department: Risk Summary → Safety, Cost Reports →
      // Accounting (mirrors how the per-vehicle reports moved under Vehicles).
      // Scheduled Reports (the digest subscription) is part of the always-on
      // Reports service — derived, so it has no row.
      // The AI assistant has NO matrix rows either: it's fully always-on, and
      // each of its tools answers only from data the role can already see — so
      // a tool's access IS its feature's access (e.g. the engine-state lookup
      // follows Vehicles, like the fleet-list tools).  Nothing to toggle here.
      // Settings is ONE System-tier feature whose components each carry
      // their own permission — account administration can be held by one
      // role or delegated piecemeal (e.g. HR gets Invites without Users).
      // Components are FLAT siblings (Invites / Working Hours / Audit Log
      // were lifted from under Team Management); the Team Management page
      // still HOSTS some as tabs — UI hosting ≠ taxonomy.
      // Standalone System-tier governance features — their backend lives in
      // capabilities/ (like Alerts↔capabilities/alerting).  NOT Settings
      // components: consumed account-wide, each with its own page.
      { key: 'can_manage_permissions',  kind: 'feature', label: 'Permissions', description: 'This role matrix — the owner always keeps it' },
      { key: 'can_manage_integrations', kind: 'feature', label: 'Integrations', description: 'Telematics connections (Samsara, Datatruck)' },
      { key: 'can_manage_storage',      kind: 'feature', label: 'Storage', description: 'File-storage backend & quota' },
      { header: 'Settings', description: 'account administration — each component has its own permission' },
      // General settings is the Settings FEATURE's own front door; the rows
      // after it are that feature's components (flag-gated parts of its
      // surface, no home of their own — docs/FEATURES.md).
      { key: 'can_manage_account',     kind: 'feature', label: 'General settings', indented: true, description: 'The Settings page itself — timezone, bot + forum routing; also rides: department modules' },
      { key: 'can_manage_users',       kind: 'component', label: 'Team Management', indented: true, description: 'Members, roles, data scope — also gates the Audit Log' },
      { key: 'can_invite',             kind: 'component', label: 'Send Invites', indented: true },
      { key: 'can_manage_companies',   kind: 'component', label: 'Manage Companies', indented: true },
      { key: 'can_manage_work_hours',  kind: 'component', label: 'Working Hours', indented: true },
      // The config FAMILY (docs/architecture/config.md) — capabilities, not
      // Settings components: they span features.  Never nested, never tagged
      // (their labels announce themselves).
      { key: 'can_manage_config_role', kind: 'capability', label: 'Config — own role', indented: true, description: 'Save team-default page layouts for their OWN role (the page gear’s "Team default" block). General settings holders can set any role’s.' },
      { key: 'can_manage_config_all', kind: 'capability', label: 'Config — account-wide', indented: true, description: 'A feature’s SHARED settings, one truth for everyone: scorecard rules + pillar caps, KPI grade thresholds, and every future feature setting.' },
    ],
  },
  {
    // Shared — features several departments use.
    title: 'Shared',
    flags: [
      { allKey: 'can_location_map', vehicleKey: 'can_location_vehicle', kind: 'feature', label: 'Live Map', scoped: true },
      // Keeps its specific verb phrase: it manages POI layers (a sub-thing),
      // not the Live Map feature itself — a bare "Manage" would over-claim.
      { key: 'can_manage_poi_layers', kind: 'action', label: 'Manage POI Layers', indented: true },
      { allKey: 'can_vehicle_all',  vehicleKey: 'can_vehicle_vehicle',  kind: 'feature', label: 'Vehicles', scoped: true },
      { key: 'can_manage_vehicles', kind: 'action', label: 'Manage', indented: true, description: 'Add / edit / remove vehicles in the registry (trucks + trailers, with or without telematics)' },
      // SUB-FEATURES of the Vehicles family: each has its OWN home
      // (features/vehicles/<x>/ with report.py / ai_tool.py / alert.py /
      // scoring_signal.py) and gates the live tab + report + AI tool.
      { key: 'can_health',     kind: 'subfeature', label: 'Health', indented: true, description: 'Engine gauges — battery, oil, coolant, DEF, RPM' },
      { key: 'can_faults',     kind: 'subfeature', label: 'Faults', indented: true, description: 'Active fault codes (DTCs) + the faults report' },
      { key: 'can_fuel',       kind: 'subfeature', label: 'Fuel', indented: true, description: 'Fuel & DEF tank levels + low-fuel alerts' },
      { key: 'can_efficiency', kind: 'subfeature', label: 'Efficiency', indented: true, description: 'MPG, idle vs drive time, harsh-driving utilization' },
      { allKey: 'can_geofence_all', vehicleKey: 'can_geofence_vehicle', kind: 'feature', label: 'Geofences', scoped: true },
      { key: 'can_kpi', kind: 'feature', label: 'KPI & Performance', description: 'Account-wide performance analytics — dispatcher grades first; fleet/safety/driver sections later' },
      { key: 'can_manage_driver_docs', kind: 'feature', writeLevel: true, label: 'Drivers', description: 'Manage — driver list + document management' },
      { key: 'can_manage_drivers',     kind: 'action', label: 'Manage', indented: true, description: 'Roster admin — invite drivers, assign trucks, link Samsara/TMS, activate/deactivate' },
      // NOTE: can_driver_docs_own (a driver viewing their OWN docs) is a
      // driver self-service flag — it lives in the "Driver — self-service"
      // panel, not this staff matrix.  Same for the other view-own flags.
      { allKey: 'can_scorecard_all', vehicleKey: 'can_scorecard_vehicle', kind: 'feature', label: 'Scorecards', scoped: true },
      // Scorecard Rules editing has no row of its own anymore — it folded
      // into "Config — account-wide" (Settings group) with KPI thresholds.
    ],
  },
  {
    title: 'Fleet',
    flags: [
      { allKey: 'can_maintenance_all', vehicleKey: 'can_maintenance_vehicle', kind: 'feature', label: 'Maintenance', scoped: true },
      { allKey: 'can_work_orders_all', vehicleKey: 'can_work_orders_vehicle', kind: 'feature', label: 'Work Orders', scoped: true },
      // Vendors rides can_work_orders_all (same audience, one matrix
      // row governs both); Parts is feature-owned — its list still
      // serves the WO editor's autocomplete for can_work_orders_all.
      { key: 'can_parts', kind: 'feature', label: 'Parts', description: 'Parts catalog + per-part analytics (recurrence, price per vendor)' },
      { key: 'can_service_tasks', kind: 'feature', writeLevel: true, label: 'Service Tasks', description: 'Manage — the shared task list maintenance and work orders both pick from (reads stay open to anyone who can create those records)' },
      { allKey: 'can_inspections_all', vehicleKey: 'can_inspections_vehicle', kind: 'feature', label: 'PTI Inspections', scoped: true },
    ],
  },
  {
    title: 'Dispatch',
    flags: [
      { allKey: 'can_route_all', vehicleKey: 'can_route_vehicle', kind: 'feature', label: 'Routes', scoped: true },
      // Loads does scope as two separate rows (all/own) instead of one
      // scoped pair — a Phase-3 normalization candidate awaiting an owner
      // decision (one tick = both, the Risk Summary precedent).
      { key: 'can_loads_all', kind: 'feature', label: 'Loads (all)', description: 'See every load in the account' },
      { key: 'can_loads_own', kind: 'feature', label: 'Loads (own)', indented: true, description: 'See loads assigned to the user (driver scope)' },
      { key: 'can_manage_loads', kind: 'action', label: 'Manage — own loads', indented: true, description: 'Add / edit / remove loads — own loads only unless "Manage — all loads" is also granted' },
      { key: 'can_loads_manage_all', kind: 'action', label: 'Manage — all loads', indented: true, description: 'Edit / delete ANY dispatcher’s loads (the dispatch-manager grant); without it, a dispatcher manages only their own' },
    ],
  },
  {
    title: 'Safety',
    flags: [
      { allKey: 'can_events_all', vehicleKey: 'can_events_vehicle', kind: 'feature', label: 'Safety Events', scoped: true },
      { key: 'can_cameras', kind: 'feature', label: 'Cameras', description: 'Dashcam footage' },
      { allKey: 'can_parking_all', vehicleKey: 'can_parking_vehicle', kind: 'feature', label: 'Parking', scoped: true, description: 'Unsafe-parking events' },
      // The Risk Summary report tab — a stakeholder/personnel risk deliverable.
      // It's a report TYPE (feature), surfaced inside the always-on Reports
      // hub; it lives here because it's safety-owned data.
      { allKey: 'can_risk_report_all', vehicleKey: 'can_risk_report_own', kind: 'feature', label: 'Risk Summary', scoped: true, description: 'Stakeholder Risk Summary report (in the Reports hub)' },
    ],
  },
  {
    title: 'HR',
    flags: [
      // can_coaching_view_own (a driver viewing their OWN coaching) is driver
      // self-service — it lives in the Driver panel, not here.
      { key: 'can_coaching_admin',    kind: 'feature', writeLevel: true, label: 'Coaching', description: 'Manage — coaching rules, assignments & review' },
    ],
  },
  {
    title: 'Recruiting',
    flags: [
      { key: 'can_manage_applications', kind: 'feature', writeLevel: true, label: 'Applications', description: 'Manage — recruiting links + the driver-application dashboard' },
      // Specific verb kept: hiring is one concrete act, not feature admin.
      { key: 'can_convert_to_driver',  kind: 'action', label: 'Hire Applicant', indented: true, description: 'Convert an approved application into a driver / invite — without full Send-Invites power' },
      { key: 'can_carrier_directory', kind: 'feature', label: 'Carrier Directory', description: 'Reference directory of the external carriers we recruit for (pre-qual, presentation, process notes)' },
      { key: 'can_manage_carrier_directory', kind: 'action', label: 'Manage', indented: true, description: 'Add, edit & delete carriers — without this the directory is read-only' },
    ],
  },
  {
    title: 'Accounting',
    flags: [
      { header: 'Costs', description: 'fuel spend + cost-per-mile components' },
      { key: 'can_fuel_cost',     kind: 'feature', label: 'Fuel Costs', indented: true },
      { key: 'can_cost_per_mile', kind: 'feature', label: 'Cost per Mile', indented: true },
      // The Cost Reports tab — executive maintenance/work-order cost rollups,
      // a report TYPE (feature) surfaced in the always-on Reports hub.  Lives
      // here because it's cost-owned data (deliberately split from Maintenance).
      { key: 'can_cost_reports', kind: 'feature', label: 'Cost Reports', description: 'Executive cost rollups (in the Reports hub)' },
      // billing = the platform charging family; the customer-facing label is
      // "Billing" (the page shows their plan).  Not driver pay (Driver Pay).
      { key: 'can_manage_billing',   kind: 'feature', writeLevel: true, label: 'Billing', description: 'Manage — the account’s plan & payment. Not driver pay (that’s Driver Pay)' },
      // Driver Pay is an Accounting feature (docs/FEATURES.md), beside Costs /
      // Cost Reports / Billing — gated by the Accounting module.
      // can_driver_pay_view_own (a driver viewing their OWN paystubs via the
      // Telegram bot) is driver self-service — it lives in the Driver panel.
      { key: 'can_driver_pay_admin',    kind: 'feature', writeLevel: true, label: 'Driver Pay', description: 'Manage — driver pay runs, statements & bonus rules' },
    ],
  },
];

// A parent + its child rows as a TREE, so the matrix can collapse the
// detail at any depth.  `indented` attaches to the nearest top-level row
// (the historical one-level behaviour); `parentKey` attaches EXPLICITLY —
// including under an indented row, which is how a sub-feature owns its
// own action/component rows (depth 2).
interface Block { parent: PermFlag; children: Block[] }
// Keys collapse-state and React keys by the row's PRIMARY flag key, not
// its label — bare-verb labels ("Manage") repeat across families.
const blockKey = (f: PermFlag): string =>
  isHeader(f) ? f.header : isScoped(f) ? f.allKey : f.key;
function toBlocks(flags: PermFlag[]): Block[] {
  // ORDER MATTERS for parentKey: the lookup map fills as rows are read,
  // so a parentKey must point at a row declared EARLIER in the group —
  // a forward reference silently renders as an extra top-level row.
  // Declare a sub-feature's children directly after it.
  const blocks: Block[] = [];
  const byKey = new Map<string, Block>();
  for (const f of flags) {
    const node: Block = { parent: f, children: [] };
    if (!isHeader(f)) byKey.set(blockKey(f), node);
    const explicitParent = !isHeader(f) && f.parentKey ? byKey.get(f.parentKey) : undefined;
    if (explicitParent) explicitParent.children.push(node);
    else if (!isHeader(f) && f.indented && blocks.length) blocks[blocks.length - 1].children.push(node);
    else blocks.push(node);
  }
  return blocks;
}
const GROUP_BLOCKS = PERM_GROUPS.map((g) => ({ title: g.title, blocks: toBlocks(g.flags) }));

// primaryKey → the label of the row it hangs under, so a bare-verb child
// ("Manage") is never ambiguous where rows are listed OUT of tree context
// (cell tooltips, the confirm dialog's change list).
const PARENT_LABEL: Record<string, string> = {};
{
  const walk = (bs: Block[], parentLabel?: string) => bs.forEach((b) => {
    if (!isHeader(b.parent) && parentLabel) PARENT_LABEL[blockKey(b.parent)] = parentLabel;
    walk(b.children, isHeader(b.parent) ? b.parent.header : b.parent.label);
  });
  GROUP_BLOCKS.forEach((g) => walk(g.blocks));
}
const contextLabel = (f: SimpleFlag | ScopedFlag): string => {
  const parent = PARENT_LABEL[blockKey(f)];
  return parent ? `${parent} · ${f.label}` : f.label;
};

// ── Driver — self-service ─────────────────────────────────────────
// The Driver role is pulled OUT of the staff matrix (see ROLES) into its
// own panel because it is a fundamentally different thing: a driver never
// MANAGES anything, and works only in the Telegram mini app.  This curated
// list is exactly the driver-relevant, STORED flags — no Permissions /
// Storage / Manage-* rows that would be nonsense for a driver.  Every toggle
// writes the `driver` role's stored perms, which the mini app reads verbatim
// via /api/user/me, so a change here lands in every driver's app on next load.
//
// "Own truck" mirrors the mini app's page gates (App.tsx canAccessPage +
// BottomNav): each grant is written at the driver's vehicle scope (their
// assigned truck only).  Alerts and the AI assistant are intentionally NOT
// here: both are DERIVED from the Vehicle grant in
// capabilities/permissions/roles.derive_service_perms — give a driver their
// truck and the alerts inbox + assistant tab come with it.
const DRIVER_TRUCK: ScopedFlag[] = [
  { kind: 'feature', allKey: 'can_location_map',    vehicleKey: 'can_location_vehicle',    label: 'Live Map',            scoped: true, description: 'See their assigned truck on the map' },
  { kind: 'feature', allKey: 'can_vehicle_all',     vehicleKey: 'can_vehicle_vehicle',     label: 'Vehicle & Assistant', scoped: true, description: 'Their truck’s info + the AI assistant tab (also carries the Alerts inbox)' },
  { kind: 'feature', allKey: 'can_maintenance_all', vehicleKey: 'can_maintenance_vehicle', label: 'Maintenance',         scoped: true, description: 'Their truck’s maintenance schedule' },
  { kind: 'feature', allKey: 'can_inspections_all', vehicleKey: 'can_inspections_vehicle', label: 'PTI Inspections',     scoped: true, description: 'Do & review pre-trip inspections on their truck' },
  { kind: 'feature', allKey: 'can_route_all',       vehicleKey: 'can_route_vehicle',       label: 'Routes',              scoped: true, description: 'Their own assigned routes' },
  { kind: 'feature', allKey: 'can_scorecard_all',   vehicleKey: 'can_scorecard_vehicle',   label: 'Scorecard',           scoped: true, description: 'Their own safety scorecard' },
  { kind: 'feature', allKey: 'can_events_all',      vehicleKey: 'can_events_vehicle',      label: 'Safety Events',       scoped: true, description: 'Their own safety events' },
  // Not mini-app pages, but stored driver grants with LIVE driver surfaces
  // (Telegram bot menus, alert relevance, driver API scopes) — they need an
  // edit path here since the staff matrix no longer has a Driver column.
  { kind: 'feature', allKey: 'can_geofence_all',    vehicleKey: 'can_geofence_vehicle',    label: 'Geofences',           scoped: true, description: 'Bot geofence/parking menus + geofence & parking alerts for their truck' },
  { kind: 'feature', allKey: 'can_parking_all',     vehicleKey: 'can_parking_vehicle',     label: 'Parking',             scoped: true, description: 'Their truck’s unsafe-parking events' },
  { kind: 'feature', allKey: 'can_work_orders_all', vehicleKey: 'can_work_orders_vehicle', label: 'Work Orders',         scoped: true, description: 'View their truck’s work orders + upload shop invoices' },
];
// The driver's OWN-record flags (view-own).  Kept out of the staff matrix for
// the same reason — they only ever apply to the driver themself.
const DRIVER_RECORDS: SimpleFlag[] = [
  { kind: 'feature', key: 'can_driver_docs_own',   label: 'Own Documents', description: 'View their own driver documents' },
  { kind: 'feature', key: 'can_driver_pay_view_own',  label: 'Own Paystubs',  description: 'View their own paystubs (Telegram /driver-pay)' },
  { kind: 'feature', key: 'can_coaching_view_own', label: 'Own Coaching',  description: 'View their own coaching notes' },
  { kind: 'feature', key: 'can_loads_own',         label: 'Own Loads',     description: 'Loads assigned to them (dashboard Loads page, own scope)' },
  { kind: 'feature', key: 'can_risk_report_own',   label: 'Own Risk Summary', description: 'Their own Stakeholder Risk Summary report' },
];
// The flags the Driver panel edits.  The `driver` role is diffed against THIS
// list (not PERM_GROUPS) because the view-own records here deliberately have
// no staff-matrix row — so a driver-panel edit still surfaces in the save bar
// + confirm dialog.
const DRIVER_PANEL_FLAGS: PermFlag[] = [...DRIVER_TRUCK, ...DRIVER_RECORDS];
// Static flag list for the change diff — PERM_GROUPS never changes at runtime.
const ALL_MATRIX_FLAGS: PermFlag[] = PERM_GROUPS.flatMap((g) => g.flags);

// Department band → account module.  The Modules page folded into this
// matrix: the on/off switch lives ON the department header, so "is the
// department even on" and "what can each role do" are one screen.
// System/Shared bands have no switch (core + account are always on).
const GROUP_MODULE: Record<string, string> = {
  Fleet: 'fleet', Dispatch: 'dispatch', Safety: 'safety', HR: 'hr', Accounting: 'accounting',
};
interface ModulesData { enabled: string[]; all: string[] }
// Parents that have sub-rows — collapsed by default (only features show).
// Recursive: a sub-feature with its own children is collapsible too.
const collectCollapsible = (bs: Block[]): string[] => bs.flatMap((b) => [
  ...(b.children.length > 0 ? [blockKey(b.parent)] : []),
  ...collectCollapsible(b.children),
]);
const COLLAPSIBLE_KEYS: string[] = GROUP_BLOCKS.flatMap((g) => collectCollapsible(g.blocks));

interface PermsData {
  current: Record<string, Record<string, boolean>>;
  defaults: Record<string, Record<string, boolean>>;
  fields: string[];
  /** role → the extra flags a MANAGER of that role gets (per-user is_manager
   *  tier, code-defined MANAGER_GRANTS).  Marks the senior-tier cells. */
  manager_grants?: Record<string, string[]>;
  /** role → its senior tier (labels + grants).  Drives the two-level
   *  Role→Tier columns.  A role absent here has no tier (single column). */
  tiers?: Record<string, { senior_label: string; base_label: string; grants: string[] }>;
}

// role -> flag -> pending boolean
type Edits = Record<string, Record<string, boolean>>;

interface Change { key: string; roleLabel: string; label: string; from: string; to: string; granted: boolean }

export default function Permissions() {
  const { t } = useTranslation();
  const { refreshPermissions } = useRoleView();
  const { user: authUser, refreshUser } = useAuth();
  const qc = useQueryClient();

  const [edits, setEdits] = useState<Edits>({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  // Sub-permissions collapsed by default — show only the top-level
  // features; expand a feature to reveal/edit its sub-rows.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(COLLAPSIBLE_KEYS));
  const toggleCollapse = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  const allExpanded = collapsed.size === 0;

  const { data, isLoading, error: qErr } = useQuery({
    queryKey: ['perms-roles'],
    queryFn: () => apiJSON<PermsData>('/admin/permissions/roles'),
  });

  // Module switches ride can_manage_account (Account Settings' remit) —
  // matrix editing itself is can_manage_permissions, so the switches
  // render only for holders of the account flag.
  const canManageModules = !!authUser?.permissions?.can_manage_account;
  const { data: modData } = useQuery({
    queryKey: ['account-modules'],
    queryFn: () => apiJSON<ModulesData>('/admin/account/modules'),
    enabled: canManageModules,
  });
  const [moduleEdits, setModuleEdits] = useState<Record<string, boolean>>({});
  const moduleOn = (id: string): boolean =>
    moduleEdits[id] ?? !!modData?.enabled.includes(id);
  const moduleChanges = useMemo(
    () => !modData ? [] : Object.entries(GROUP_MODULE)
      .filter(([, id]) => moduleOn(id) !== modData.enabled.includes(id))
      .map(([title, id]) => ({ title, id, on: moduleOn(id) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modData, moduleEdits],
  );
  const toggleModule = (id: string) => {
    setModuleEdits((prev) => ({ ...prev, [id]: !moduleOn(id) }));
    setSuccess('');
  };

  // Effective value of a single flag for a role (pending edit wins).
  const flagVal = (role: string, key: string): boolean => {
    const e = edits[role]?.[key];
    return e !== undefined ? e : !!data?.current[role]?.[key];
  };
  const curFlagVal = (role: string, key: string): boolean => !!data?.current[role]?.[key];

  // A feature is "granted" if any of its flags is on (scoped = all || own).
  const grantedFor = (role: string, f: PermFlag, valFn: (r: string, k: string) => boolean): boolean =>
    isHeader(f) ? false : isScoped(f) ? valFn(role, f.allKey) || valFn(role, f.vehicleKey) : valFn(role, f.key);

  const isGranted = (role: string, f: PermFlag) => grantedFor(role, f, flagVal);
  // Owner cells for the escape-hatch perms are locked on (never editable).
  const ownerLocked = (role: string, f: PermFlag) =>
    role === 'owner' && !isHeader(f) && !isScoped(f) && OWNER_PROTECTED.has(f.key);

  // Manager tier — "manager" is a per-user is_manager tier on the base role,
  // not a separate role/column.  The matrix MARKS the flags a manager gains
  // (from MANAGER_GRANTS) rather than adding columns.  Read-only annotation:
  // the cell toggle still edits the EMPLOYEE (base-role) grant.

  // ── Two-level Role → Tier columns ────────────────────────────────
  // Each role expands to its tier sub-columns.  Base/single columns are
  // EDITABLE (they hold the role's stored perms).  Senior columns are a
  // READ-ONLY preview (base + tier grants).  Owner is special: Co-owner (base)
  // vs Primary (senior), differing only on the Owner-powers rows below.
  // Each column carries a ``key`` = the STORAGE key it edits: the base role
  // ("admin"), the senior tier ("admin__manager"), or "owner" (both owner
  // columns share one perm set — they differ only on the locked Owner-powers
  // rows).  Every tier is independently editable + stored.
  type TierKind = 'base' | 'senior' | null;
  interface Col { role: RoleId; tier: TierKind; key: string; label: string }
  const tiersMeta = data?.tiers ?? {};
  const roleColumns = (role: RoleId): Col[] => {
    if (role === 'owner') return [
      // Co-owner is a SEPARATE, restrictable owner row (owner__co); Primary is
      // the full, protected owner row (owner).  Independently editable.
      { role, tier: 'base', key: 'owner__co', label: 'Co-owner' },
      { role, tier: 'senior', key: 'owner', label: 'Primary' },
    ];
    const tm = tiersMeta[role];
    if (tm) return [
      { role, tier: 'base', key: role, label: tm.base_label },
      { role, tier: 'senior', key: `${role}__manager`, label: tm.senior_label },
    ];
    return [{ role, tier: null, key: role, label: ROLE_LABELS[role] ?? role }];
  };
  const columns: Col[] = ROLES.flatMap(roleColumns);
  // Distinct storage keys + a display label per key (for the confirm summary).
  // `driver` has no grid column (it's edited in the panel below) but IS a
  // storage key, so include it here so its edits surface in the change diff
  // and the sticky save bar exactly like a matrix column.
  const colKeys = [...new Set([...columns.map((c) => c.key), 'driver'])];
  const keyLabel: Record<string, string> = { driver: 'Driver' };
  for (const c of columns) {
    if (c.role === 'owner') keyLabel[c.key] = `Owner · ${c.label}`;                 // Primary / Co-owner
    else if (c.tier === 'senior') keyLabel[c.key] = `${ROLE_LABELS[c.role] ?? c.role} · ${c.label}`;
    else keyLabel[c.key] = ROLE_LABELS[c.role] ?? c.role;
  }

  // Owner-powers — primary-owner-only ACTIONS surfaced as read-only rows so
  // the Owner Primary|Co-owner columns are truthful (they differ HERE only).
  // Not can_* flags — they map to is_primary_owner gates.
  const OWNER_POWERS = [
    { key: '__manage_owners', label: 'Manage owners', description: 'Add / remove co-owners (password + emailed code)' },
    { key: '__delete_account', label: 'Delete / restore account', description: 'Schedule deletion + cancel in the grace window' },
  ];

  function toggle(role: string, f: PermFlag) {
    if (isHeader(f) || ownerLocked(role, f)) return;
    const granted = isGranted(role, f);
    setEdits((prev) => {
      const roleEdits = { ...(prev[role] ?? {}) };
      if (isScoped(f)) {
        // Grant at the role's default scope; revoke = None (both off).
        // Whose data is narrowed per-user in Team Management.
        const [a, o] = granted ? [false, false] : DEFAULT_SCOPED_FLAGS(role);
        roleEdits[f.allKey] = a; roleEdits[f.vehicleKey] = o;
      } else {
        roleEdits[f.key] = !granted;
      }
      return { ...prev, [role]: roleEdits };
    });
    setSuccess('');
  }

  // Human label of a cell's state — granted / no-access.  Drives both
  // the change diff and the confirm dialog.
  const cellState = (role: string, f: PermFlag, valFn: (r: string, k: string) => boolean): string => {
    if (isHeader(f)) return '';
    return grantedFor(role, f, valFn) ? 'Granted' : 'No access';
  };

  // Human-readable list of pending changes (drives the confirm dialog).
  const changes = useMemo<Change[]>(() => {
    if (!data) return [];
    const out: Change[] = [];
    for (const key of colKeys) {
      // The driver has no grid column — it's diffed against the panel's own
      // flag list (which includes the view-own records that have no matrix row).
      const flags = key === 'driver' ? DRIVER_PANEL_FLAGS : ALL_MATRIX_FLAGS;
      for (const f of flags) {
        if (isHeader(f)) continue;
        const before = cellState(key, f, curFlagVal);
        const after = cellState(key, f, flagVal);
        if (before !== after) out.push({ key, roleLabel: keyLabel[key] ?? key, label: contextLabel(f), from: before, to: after, granted: after !== 'No access' });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, edits]);

  // Only the flags that actually differ, grouped per role, for the API.
  const changedByRole = useMemo(() => {
    const out: Record<string, Record<string, boolean>> = {};
    for (const [role, roleEdits] of Object.entries(edits))
      for (const [k, v] of Object.entries(roleEdits))
        if (curFlagVal(role, k) !== v) (out[role] ??= {})[k] = v;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edits, data]);

  const totalPending = changes.length + moduleChanges.length;

  async function applyChanges() {
    setSaving(true); setError('');
    try {
      for (const [key, perms] of Object.entries(changedByRole)) {
        // Storage key → {role, tier}.  "admin__manager" = senior tier;
        // "owner__co" = the restrictable co-owner tier; else the base role.
        let role = key; let tier: string | undefined;
        if (key === 'owner__co') { role = 'owner'; tier = 'co'; }
        else if (key.endsWith('__manager')) { role = key.slice(0, -'__manager'.length); tier = 'senior'; }
        await apiJSON('/admin/permissions/roles', {
          method: 'PUT',
          body: tier ? { role, permissions: perms, tier } : { role, permissions: perms },
        });
      }
      if (moduleChanges.length && modData) {
        const enabled = modData.all.filter((id) => moduleOn(id));
        await apiJSON('/admin/account/modules', { method: 'PUT', body: { enabled } });
        await qc.invalidateQueries({ queryKey: ['account-modules'] });
      }
      setEdits({});
      setModuleEdits({});
      setConfirmOpen(false);
      setSuccess(`Saved ${totalPending} change${totalPending === 1 ? '' : 's'}.`);
      await qc.invalidateQueries({ queryKey: ['perms-roles'] });
      refreshPermissions();
      try { await refreshUser(); } catch { /* best-effort */ }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  const cellChanged = (role: string, f: PermFlag) => cellState(role, f, flagVal) !== cellState(role, f, curFlagVal);

  // Render one matrix row.  `depth` is the row's tree depth (0 = top-level;
  // 1 = child; 2 = a sub-feature's own child); `collapse` adds an
  // expand/collapse chevron to any parent that has sub-rows.
  const renderRow = (f: PermFlag, depth = 0, collapse?: { isCollapsed: boolean; onToggle: () => void }): ReactNode => {
    // Always reserve the chevron's width on a top-level row so EVERY feature
    // label lines up at the same x — collapsible features show the chevron,
    // simple features (no components) show an empty spacer.  Without this the
    // left edge looks ragged ("why does this one have an arrow and that one
    // doesn't?").
    // Visible chevron (text-foreground, not the faint muted tone) for
    // collapsible rows; an equal-width empty spacer keeps labels aligned on
    // simple rows.  The whole label area below is the click target — the arrow
    // is just the affordance — so users don't have to hit the tiny icon.
    const chevronSlot = collapse ? (
      <span className="w-5 h-5 shrink-0 -ml-0.5 flex items-center justify-center rounded-md border border-border bg-muted text-foreground">
        {collapse.isCollapsed ? <ChevronRight size={14} strokeWidth={2.5} /> : <ChevronDown size={14} strokeWidth={2.5} />}
      </span>
    ) : (
      <span className="w-5 shrink-0 -ml-0.5" aria-hidden />
    );
    const clickableProps = collapse
      ? {
          onClick: collapse.onToggle,
          role: 'button' as const,
          tabIndex: 0,
          onKeyDown: (e: React.KeyboardEvent) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); collapse.onToggle(); }
          },
        }
      : {};

    // A feature/header that OWNS components starts a new visual block, so it
    // gets the top divider.  Its component rows (``indented``) drop the
    // divider and gain a left rail instead — they read as a group hanging
    // off the feature above, not as separate features.
    if (isHeader(f)) {
      return (
        <tr className="border-t border-border hover:bg-muted/20">
          <td colSpan={1 + columns.length} className="px-3 py-2 sticky left-0 bg-card z-10">
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {chevronSlot}
              <span className="text-sm font-medium">{f.header}</span>
              {f.description && <span className="text-2xs text-muted-foreground ml-2">{f.description}</span>}
            </div>
          </td>
        </tr>
      );
    }
    const nested = depth > 0;
    return (
      <tr className={`${nested ? '' : 'border-t border-border'} hover:bg-muted/20`}>
        <td className={`sticky left-0 bg-card z-10 ${nested ? (depth > 1 ? 'pl-8 pr-3' : 'pl-4 pr-3') : 'px-3 py-1.5'}`}>
          {/* Nested row: a continuous left rail (the cell carries no
              vertical padding so adjacent rails touch) makes the rows read as
              a group hanging off the parent above; depth 2 indents further. */}
          <div className={nested ? 'border-l-2 border-border pl-3 py-1.5' : ''}>
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {(!nested || collapse) && chevronSlot}
              <span className={nested ? 'text-muted-foreground' : 'font-medium'}>{f.label}</span>
              {isScoped(f) && <span className="text-2xs text-muted-foreground" title="Scoped feature — checkbox = full access">*</span>}
              {/* The muted kind tag: this row's single flag is WRITE-level
                  though its label is the feature noun (Billing, Coaching…) —
                  without it, granting reads as read-only view access. */}
              {f.writeLevel && (
                <span className="text-3xs uppercase tracking-wide text-muted-foreground border border-border rounded px-1">
                  manage
                </span>
              )}
            </div>
            {f.description && <div className="text-2xs text-muted-foreground/70 mt-0.5">{f.description}</div>}
          </div>
        </td>
        {columns.map((col, ci) => {
          // Every tier column is EDITABLE and edits its OWN storage key
          // (base role / {role}__manager / owner).  Owner escape-hatch flags
          // stay locked-on so an owner can't self-lock-out.
          const on = isGranted(col.key, f);
          const locked = ownerLocked(col.key, f);
          const changed = !locked && cellChanged(col.key, f);
          const leftBorder = col.tier === 'senior' ? 'border-l border-border/40' : 'border-l border-border';
          return (
            <td key={`${col.key}-${ci}`} className={`text-center px-2 py-1.5 ${leftBorder} ${changed ? 'bg-primary/10' : ''}`}>
              <button
                onClick={() => toggle(col.key, f)}
                disabled={locked}
                aria-pressed={on}
                title={locked
                  ? `Owner always keeps "${f.label}" — prevents lockout`
                  : `${keyLabel[col.key]} · ${contextLabel(f)}: ${on ? 'granted' : 'no access'}`}
                className={`inline-flex items-center justify-center w-5 h-5 rounded border transition ${
                  locked
                    ? 'bg-primary/40 border-primary/40 text-primary-foreground cursor-not-allowed'
                    : on
                      ? 'bg-primary border-primary text-primary-foreground'
                      : 'bg-transparent border-border text-transparent hover:border-muted-foreground'
                }`}
              >
                {locked ? <Lock size={12} strokeWidth={2.5} /> : <Check size={14} strokeWidth={3} />}
              </button>
            </td>
          );
        })}
      </tr>
    );
  };

  // One driver-panel row: a single-column toggle (the driver has no tiers and
  // no "whose data" choice — always their own truck), editing the `driver`
  // storage key.  Reuses the matrix's grant/toggle logic so the change flows
  // through the same save bar + confirm dialog.
  const renderDriverRow = (f: ScopedFlag | SimpleFlag): ReactNode => {
    const on = isGranted('driver', f);
    const changed = cellChanged('driver', f);
    const rowKey = isScoped(f) ? f.vehicleKey : f.key;
    return (
      <li key={rowKey} className={`flex items-center justify-between gap-3 rounded px-1.5 py-1 ${changed ? 'bg-primary/10' : ''}`}>
        <div className="min-w-0">
          <div className="text-sm text-foreground">{f.label}</div>
          {f.description && <div className="text-2xs text-muted-foreground/70">{f.description}</div>}
        </div>
        <button
          type="button"
          onClick={() => toggle('driver', f)}
          role="switch"
          aria-checked={on}
          aria-label={f.label}
          title={`${f.label}: ${on ? 'granted' : 'no access'}`}
          className={`relative w-8 h-4 rounded-full transition shrink-0 ${on ? 'bg-primary' : 'bg-muted'}`}
        >
          <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-background shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
        </button>
      </li>
    );
  };

  return (
    <div className="pb-20">
      <PageHeader
        icon={Shield}
        title={t('pages.role_perms_title')}
        description="What each role can DO. Tick a cell, review the summary, then Save — changes apply immediately. (Department on/off switches sit on the section headers; whose data each person sees — All / Company / Vehicle — is per-user in Team Management.)"
      />

      {error && <div className="mb-3"><p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{error}</p></div>}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      {isLoading || !data ? (
        <CardSkeleton />
      ) : qErr ? (
        <p className="text-danger text-sm">{qErr instanceof Error ? qErr.message : 'Failed to load'}</p>
      ) : (
        <>
          <div className="flex justify-end mb-2">
            <button
              type="button"
              onClick={() => setCollapsed(allExpanded ? new Set(COLLAPSIBLE_KEYS) : new Set())}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              {allExpanded ? 'Collapse all' : 'Expand all'}
            </button>
          </div>
          <div className="rounded-lg border border-border overflow-x-auto bg-card">
            <table className="w-full text-sm border-collapse">
              <thead>
                {/* Row 1 — role group headers (tiered roles span their 2 sub-columns) */}
                <tr className="bg-muted/40">
                  <th rowSpan={2} className="text-left font-semibold px-3 py-2 sticky left-0 bg-muted/40 z-10 min-w-[200px] align-bottom">Feature</th>
                  {ROLES.map((r) => {
                    const cols = roleColumns(r);
                    return cols.length === 2 ? (
                      <th key={r} colSpan={2} className="px-2 pt-2 pb-1 text-center font-semibold text-xs whitespace-nowrap border-l border-border">
                        <span className="inline-flex items-center gap-1 justify-center">
                          {ROLE_LABELS[r] ?? r}
                          {tiersMeta[r] && (
                            <span title={`Has a senior tier — a ${tiersMeta[r].senior_label} gains ${tiersMeta[r].grants.length} extra permission(s).`} className="inline-flex">
                              <Shield size={12} className="text-primary/70 shrink-0" aria-label="Has a senior tier" />
                            </span>
                          )}
                        </span>
                      </th>
                    ) : (
                      <th key={r} rowSpan={2} className="px-2 py-2 text-center font-semibold text-xs whitespace-nowrap border-l border-border align-middle">
                        {ROLE_LABELS[r] ?? r}
                      </th>
                    );
                  })}
                </tr>
                {/* Row 2 — tier sub-labels under each tiered role */}
                <tr className="bg-muted/40">
                  {ROLES.map((r) => {
                    const cols = roleColumns(r);
                    if (cols.length !== 2) return null;
                    return cols.map((c, i) => (
                      <th key={`${r}-${c.tier}`} className={`px-2 pb-2 pt-0 text-center text-2xs font-medium text-muted-foreground whitespace-nowrap ${i === 0 ? 'border-l border-border' : ''}`}>
                        {c.label}
                      </th>
                    ));
                  })}
                </tr>
              </thead>
              <tbody>
                {GROUP_BLOCKS.map((group) => {
                  const moduleId = GROUP_MODULE[group.title];
                  const on = moduleId ? moduleOn(moduleId) : true;
                  const flipped = moduleId ? moduleChanges.some((m) => m.id === moduleId) : false;
                  const control = moduleId && modData ? (
                    <span className={`inline-flex items-center gap-1.5 ${flipped ? 'rounded px-1 bg-primary/10' : ''}`}>
                      {canManageModules && (
                        <button
                          type="button"
                          onClick={() => toggleModule(moduleId)}
                          role="switch"
                          aria-checked={on}
                          aria-label={`${group.title} module`}
                          title={`${group.title} department ${on ? 'on' : 'off'} — disabling hides its features from every sidebar`}
                          className={`relative w-8 h-4 rounded-full transition shrink-0 ${on ? 'bg-primary' : 'bg-muted'}`}
                        >
                          <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-background shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
                        </button>
                      )}
                      {!on && (
                        <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('warn')}`}>
                          module off — features hidden from every sidebar
                        </span>
                      )}
                    </span>
                  ) : undefined;
                  return (
                  <FragmentGroup key={group.title} title={group.title} control={control} colSpan={1 + columns.length}>
                    {(function renderBlocks(blocks: Block[], depth: number): ReactNode {
                      return blocks.map((block, bi) => {
                        const pk = blockKey(block.parent);
                        const hasChildren = block.children.length > 0;
                        const isColl = hasChildren && collapsed.has(pk);
                        return (
                          <Fragment key={`${group.title}-${pk}-${bi}`}>
                            {renderRow(block.parent, depth, hasChildren ? { isCollapsed: isColl, onToggle: () => toggleCollapse(pk) } : undefined)}
                            {hasChildren && !isColl && renderBlocks(block.children, depth + 1)}
                          </Fragment>
                        );
                      });
                    })(group.blocks, 0)}
                  </FragmentGroup>
                  );
                })}
                {/* OWNER POWERS — read-only rows.  Only the Owner Primary vs
                    Co-owner columns differ here (Primary yes, Co-owner no);
                    every other role shows a dash.  This is what makes the
                    Owner two-column split truthful. */}
                <tr className="bg-muted/30 border-t border-border">
                  <td colSpan={1 + columns.length} className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Owner powers <span className="normal-case font-normal text-2xs text-muted-foreground/70">— primary-owner only · not editable</span>
                  </td>
                </tr>
                {OWNER_POWERS.map((p) => (
                  <tr key={p.key} className="hover:bg-muted/20">
                    <td className="sticky left-0 bg-card z-10 px-3 py-1.5">
                      <span className="font-medium">{p.label}</span>
                      <div className="text-2xs text-muted-foreground/70 mt-0.5">{p.description}</div>
                    </td>
                    {columns.map((col) => {
                      const isOwner = col.role === 'owner';
                      const primary = isOwner && col.tier === 'senior';
                      return (
                        <td key={`${col.role}-${col.tier}-${p.key}`} className={`text-center px-2 py-1.5 ${col.tier === 'senior' ? 'border-l border-border/40' : 'border-l border-border'}`}>
                          {isOwner ? (
                            <span
                              title={`${col.label}: ${primary ? 'yes' : 'no'} — ${p.label}`}
                              className={`inline-flex items-center justify-center w-5 h-5 rounded border ${primary ? 'bg-primary/60 border-primary/60 text-primary-foreground' : 'border-border/50 text-transparent'}`}
                            >
                              {primary ? <Check size={14} strokeWidth={3} /> : null}
                            </span>
                          ) : (
                            <span className="text-muted-foreground/40 text-xs" title="Owners only">–</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Driver — self-service.  The driver role lives HERE, not in the
              staff matrix: a driver only ever sees their OWN truck + records,
              in the Telegram mini app.  Edits write the `driver` role's stored
              perms, which the mini app reads via /api/user/me — so a change
              reaches every driver's app on next load. */}
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Smartphone size={16} className="text-primary shrink-0" />
              <span className="text-base font-semibold text-foreground">Driver — self-service</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('info')}`}>mobile app</span>
            </div>
            <p className="text-2xs text-muted-foreground mb-3">
              Drivers don&apos;t manage anything and use the Telegram <span className="font-medium text-foreground/80">mini app</span> only, so they sit apart from the matrix above. Tick what a driver sees of their <span className="font-medium text-foreground/80">own truck</span> and <span className="font-medium text-foreground/80">own records</span> — changes reach every driver&apos;s app on next load. The <span className="font-medium text-foreground/80">Alerts</span> inbox and <span className="font-medium text-foreground/80">AI assistant</span> come automatically with the Vehicle grant.
            </p>

            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Own truck</div>
            <ul className="space-y-0.5 mb-4">
              {DRIVER_TRUCK.map((f) => {
                // The Vehicle grant silently carries the Alerts inbox + AI tab
                // (derived, not stored — roles.derive_service_perms).  When it's
                // OFF, name that consequence at the point of the loss so an
                // admin can't strip a driver's inbox + assistant without knowing.
                if (isScoped(f) && f.vehicleKey === 'can_vehicle_vehicle' && !isGranted('driver', f)) {
                  return (
                    <Fragment key="veh-derived">
                      {renderDriverRow(f)}
                      <li className={`flex items-start gap-1.5 pl-1.5 pb-0.5 text-2xs ${toneText('warn')}`}>
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                        <span>Alerts inbox &amp; AI assistant are off too — both ride the Vehicle grant.</span>
                      </li>
                    </Fragment>
                  );
                }
                return renderDriverRow(f);
              })}
            </ul>

            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Own records</div>
            <ul className="space-y-0.5">
              {DRIVER_RECORDS.map(renderDriverRow)}
            </ul>
          </div>

          {/* System Services — always-on infrastructure, NOT owner-toggled.
              Access is derived from each role's feature permissions, so there
              is nothing to tick.  This panel documents the model in-place so
              an admin isn't left wondering where the old Alerts / AI rows went. */}
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">System Services</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('ok')}`}>always on</span>
            </div>
            <p className="text-2xs text-muted-foreground mb-3">
              Infrastructure services — not granted here. Each one follows the role&apos;s feature permissions automatically: disable a feature and its slice stops, but the service itself never stops.
            </p>
            <ul className="space-y-2.5">
              <li className="flex items-start gap-2.5">
                <Bell size={16} className="text-muted-foreground mt-0.5 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-foreground">Alerts</div>
                  <div className="text-2xs text-muted-foreground">Every role has the inbox. It shows the alerts for whichever features the role can see — disable a feature (Faults, Health, Fuel, Safety Events, Geofences, Maintenance) and just those alerts drop out. Scope follows the role&apos;s vehicle access — account-wide or own-vehicle.</div>
                </div>
              </li>
              <li className="flex items-start gap-2.5">
                <Bot size={16} className="text-muted-foreground mt-0.5 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-foreground">AI Assistant</div>
                  <div className="text-2xs text-muted-foreground">Available to every role. Each AI tool answers only from data the role can already see — a tool&apos;s access is just its feature&apos;s access (e.g. the engine-state lookup follows Vehicles), so there&apos;s nothing separate to grant.</div>
                </div>
              </li>
              <li className="flex items-start gap-2.5">
                <FileText size={16} className="text-muted-foreground mt-0.5 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-foreground">Reports</div>
                  <div className="text-2xs text-muted-foreground">The Reports hub and its scheduled-report subscription are open to every role. Which report tabs appear follows the role&apos;s features — the report TYPES (Risk Summary, Cost Reports) stay grantable above, under Safety and Accounting.</div>
                </div>
              </li>
            </ul>
          </div>
        </>
      )}

      {/* Footnote */}
      {!isLoading && data && (
        <>
          <p className="text-2xs text-muted-foreground mt-2">
            Row grammar: an untagged row is what the role can <span className="font-medium text-foreground/80">see</span> · a <span className="font-medium text-foreground/80">Manage</span> row (or the <span className="uppercase tracking-wide text-3xs border border-border rounded px-1">manage</span> tag) is what it can <span className="font-medium text-foreground/80">do</span> · the two <span className="font-medium text-foreground/80">Config</span> rows delegate configuration · Alerts, AI and Reports are always-on services (panel below) — nothing to grant.
          </p>
          <p className="text-2xs text-muted-foreground mt-1">
            <span className="font-semibold">*</span> scoped feature — ticking grants the role access to the feature. <span className="font-medium text-foreground/80">Whose data</span> they see (All / Company / Vehicle) is set per-user in <span className="font-medium text-foreground/80">Team Management</span> (Company Access + Vehicle Assignments).
          </p>
          <p className="text-2xs text-muted-foreground mt-1 inline-flex items-center gap-1 flex-wrap">
            <Shield size={12} className="text-primary/70 shrink-0" />
            <span>
              a <span className="font-medium text-foreground/80">shield</span> on a role header means it has tiers (e.g. Standard / Full admin). Each tier is edited <span className="font-medium text-foreground/80">independently</span> — changing one column never affects the other. A person's tier is set per-user in <span className="font-medium text-foreground/80">Team Management</span>. The <span className="font-medium text-foreground/80">Owner powers</span> rows are primary-owner-only and can't be edited.
            </span>
          </p>
        </>
      )}

      {/* Sticky save bar — appears only when there are pending changes. */}
      {totalPending > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-popover border-t border-border px-4 py-3 flex items-center justify-between gap-4 shadow-lg">
          <span className="text-sm text-muted-foreground">
            {totalPending} pending change{totalPending === 1 ? '' : 's'}
          </span>
          <div className="flex items-center gap-2">
            <button onClick={() => { setEdits({}); setModuleEdits({}); }} className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground">Discard</button>
            <button onClick={() => setConfirmOpen(true)} className="px-4 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition">
              Review &amp; Save
            </button>
          </div>
        </div>
      )}

      {/* Confirmation — every change spelled out before it powers on. */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => !saving && setConfirmOpen(false)}>
          <div className="bg-card rounded-xl border border-border w-full max-w-md max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold">Confirm permission changes</h2>
              <p className="text-sm text-muted-foreground mt-0.5">{totalPending} change{totalPending === 1 ? '' : 's'} will take effect immediately.</p>
            </div>
            <div className="px-5 py-3 overflow-y-auto space-y-3">
              {moduleChanges.length > 0 && (
                <div>
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">Department modules</div>
                  {moduleChanges.map((m) => (
                    <div key={m.id} className="text-sm flex items-center justify-between py-0.5">
                      <span>{m.title}</span>
                      <span className={m.on ? 'text-ok' : 'text-danger'}>{m.on ? 'On' : 'Off — hidden from every sidebar'}</span>
                    </div>
                  ))}
                </div>
              )}
              {[...new Set(changes.map((c) => c.key))].map((key) => (
                <div key={key}>
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">{changes.find((c) => c.key === key)?.roleLabel ?? key}</div>
                  <ul className="space-y-0.5">
                    {changes.filter((c) => c.key === key).map((c) => (
                      <li key={c.label} className="flex items-center gap-2 text-sm">
                        {c.granted
                          ? <Check size={14} className="text-ok shrink-0" />
                          : <X size={14} className="text-danger shrink-0" />}
                        <span>{c.label}</span>
                        <span className="text-2xs text-muted-foreground">{c.from} → {c.to}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <div className="px-5 py-4 border-t border-border flex justify-end gap-2">
              <button onClick={() => setConfirmOpen(false)} disabled={saving} className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground">Cancel</button>
              <button onClick={applyChanges} disabled={saving} className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition disabled:opacity-50">
                {saving ? 'Saving…' : 'Confirm & apply'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// A permission group: a header row spanning all columns + its feature rows.
function FragmentGroup({ title, control, children, colSpan }: { title: string; control?: ReactNode; children: ReactNode; colSpan: number }) {
  return (
    <>
      <tr className="bg-muted/50 border-t-2 border-border">
        <td colSpan={colSpan} className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          <div className="flex items-center gap-2">
            <span>{title}</span>
            {control}
          </div>
        </td>
      </tr>
      {children}
    </>
  );
}
