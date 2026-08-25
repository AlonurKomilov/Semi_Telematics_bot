/**
 * The Permissions ROW MODEL — every grantable row, typed and grouped.
 *
 * Single source both lenses render from: the matrix (Permissions.tsx)
 * and the per-role verb grid (verbGrid.ts + RoleLens.tsx).  The
 * drift-guard tests pin this file: permMatrix.test.ts (kinds, nesting,
 * family order) and tests/test_permission_surface.py (every FeatureSet
 * field has exactly one home — it parses THIS file's key patterns).
 */

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
//   cross_feature — a do-verb that spans features, owned by none (the
//                 config pair).  NOT called "capability": that word is
//                 already the four hubs' in docs/FEATURES.md.
// Services (Alerts / AI / Reports) have NO kind because they have no rows:
// always-on, nothing to grant — see the System Services panel below.
export type RowKind = 'feature' | 'subfeature' | 'component' | 'action' | 'cross_feature';
export interface RowMeta {
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
export interface ScopedFlag extends RowMeta { allKey: string; vehicleKey: string; label: string; scoped: true; description?: string; indented?: boolean }
export interface SimpleFlag extends RowMeta { key: string; label: string; scoped?: false; description?: string; indented?: boolean }
export interface FeatureHeader { header: string; description?: string }
export type PermFlag = ScopedFlag | SimpleFlag | FeatureHeader;
export interface PermGroup { title: string; flags: PermFlag[] }

export const isHeader = (f: PermFlag): f is FeatureHeader => 'header' in f;
export const isScoped = (f: PermFlag): f is ScopedFlag => (f as ScopedFlag).scoped === true;

// ── Per-role data-scope default ───────────────────────────────────
// This matrix only grants/revokes FEATURES.  "Whose data" (All /
// Company / Vehicle) is configured per-user in Team Management, not
// here.  When a scoped (*) feature is ticked we grant it at the role's
// intrinsic default: drivers are vehicle-scoped (their assigned vehicle
// only); every other role sees all, then narrowed per-user by the
// Company / Vehicle assignment in Team Management.  Returns the
// [allKey, vehicleKey] pair to write.
export const DEFAULT_SCOPED_FLAGS = (role: string): [boolean, boolean] =>
  role === 'driver' ? [false, true] : [true, true];

// Owner escape-hatch permissions — locked-on in the Owner column so an
// owner can never revoke their own way back from a misconfiguration.
// Mirrors OWNER_PROTECTED_PERMS in capabilities/iam/permissions.py (the
// backend enforces it regardless of the UI).
export const OWNER_PROTECTED = new Set([
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
// Exported for the drift-guard tests and the verb-grid derivation.
export const PERM_GROUPS: PermGroup[] = [
  {
    // Administration — governing the ACCOUNT itself.  Not "System": that
    // word had drifted into "miscellaneous account-wide", and the three
    // things that made it a category (Alerts, AI, Reports) are services
    // now.  Every row here is a FEATURE with its own home — a folder and
    // router under features/settings/ or capabilities/, and its own
    // featureCatalog entry.  The Settings page groups several of them in
    // the NAV; grouping was never ownership (docs/FEATURES.md).
    title: 'Administration',
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
      { key: 'can_manage_integrations', kind: 'feature', label: 'Integrations', description: 'Connecting and syncing Samsara / Datatruck. The provider-precedence SETTING moved to Config — account-wide.' },
      { key: 'can_manage_storage',      kind: 'feature', label: 'Storage', description: 'Connecting Drive, retrying syncs, clearing orphans. Choosing the backend and the disk quota are settings — Config — account-wide.' },

      { key: 'can_manage_account',     kind: 'feature', label: 'General settings', description: 'The Settings page itself — timezone, bot + forum routing; also rides: department modules' },
      { key: 'can_manage_users',       kind: 'feature', label: 'Team Management', description: "Members, roles, data scope — also opens the Audit Log, which lists only the features this role can already see" },
      { key: 'can_invite',             kind: 'feature', label: 'Send Invites', description: 'Invite new members — the invite carries the role the sender picks' },
      { key: 'can_manage_companies',   kind: 'feature', label: 'Manage Companies', description: 'Sub-companies in the account — codes, names, per-company data scope' },
      { key: 'can_manage_work_hours',  kind: 'feature', label: 'Working Hours', description: 'Shift schedules — they also drive the alert do-not-disturb window' },
      { header: 'Configuration', description: 'cross-feature settings — one flag each, covering every feature they touch' },
      // The config FAMILY (docs/architecture/config.md).  Under its OWN
      // header, not Settings': these span features, so calling them
      // Settings components would misplace them (and the role lens
      // already bands them apart — the two lenses must agree).
      { key: 'can_manage_config_role', kind: 'cross_feature', label: 'Config — own role', indented: true, description: 'Save team-default page layouts for their OWN role (the page gear’s "Team default" block). General settings holders can set any role’s.' },
      { key: 'can_manage_config_all', kind: 'cross_feature', label: 'Config — account-wide', indented: true, description: 'A feature’s SHARED settings, one truth for everyone: scorecard rules + pillar caps, KPI grade thresholds, storage backend + disk quota, provider precedence — and every future feature setting. Config is its own action: a feature’s Manage no longer carries its settings.' },
    ],
  },
  {
    // Shared — features several departments use.
    title: 'Shared',
    flags: [
      { allKey: 'can_location_map', vehicleKey: 'can_location_vehicle', kind: 'feature', label: 'Live Map', scoped: true },
      // The row is the OBJECT, the column supplies the verb: POI layers
      // are a flag-gated part of Live Map's surface (features/location/
      // pois.py — a file, not a home of its own), manage-only.
      { key: 'can_manage_poi_layers', kind: 'component', label: 'POI Layers', indented: true, description: 'Custom map overlays — the Live Map grant shows them, this one edits them' },
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
      // COMPENSATION, deliberately its own flag: can_kpi is shared
      // analytics (grades, viewable widely); payout amounts are money.
      { key: 'can_manage_driver_docs', kind: 'feature', writeLevel: true, label: 'Drivers', description: 'Manage — driver list + document management' },
      // A sub-feature, not a component: features/drivers/onboarding/ has
      // its own home AND its own hub contribution (the stale-approval
      // alert).  Deliberately separate from Driver roster so hiring is
      // granted on purpose — Fleet administers drivers but does not hire.
      { key: 'can_onboard_drivers',    kind: 'subfeature', label: 'Onboarding', indented: true, description: 'Finish the hire a recruiter approved — creates the driver’s invite' },
      { key: 'can_manage_drivers',     kind: 'component', label: 'Driver roster', indented: true, description: 'Invite drivers, assign trucks, link Samsara/TMS, activate / deactivate' },
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
      { key: 'can_truck_anatomy', kind: 'feature', label: 'Truck Anatomy', description: '3D learning model of the rig (System → Assembly → Part). Ships dark — no role has it, the owner included, until it\u2019s granted right here.' },
      { key: 'can_service_tasks', kind: 'feature', writeLevel: true, label: 'Service Tasks', description: 'Manage — the shared task list maintenance and work orders both pick from (reads stay open to anyone who can create those records)' },
      { allKey: 'can_inspections_all', vehicleKey: 'can_inspections_vehicle', kind: 'feature', label: 'PTI Inspections', scoped: true },
    ],
  },
  {
    title: 'Dispatch',
    flags: [
      { allKey: 'can_route_all', vehicleKey: 'can_route_vehicle', kind: 'feature', label: 'Routes', scoped: true },
      // One scoped row like every other feature (owner decision 2026-07-29;
      // the Risk Summary precedent): one tick grants view all + own
      // (view-own = assigned as DRIVER, the Driver panel's toggle).
      // The old own/all MANAGE split is GONE (same decision): any Manage
      // holder edits any load, and the per-load accountability trail —
      // actor + old→new diffs, the HISTORY block in the load dialog —
      // replaced the wall.  can_loads_manage_all no longer exists.
      { allKey: 'can_loads_all', vehicleKey: 'can_loads_own', kind: 'feature', label: 'Loads', scoped: true, description: 'See loads — every load in the account (drivers: their own, via the Driver panel)' },
      { key: 'can_manage_loads', kind: 'action', label: 'Manage', indented: true, description: 'Add / edit / remove ANY load — every change is recorded in the load’s history (who, what, old → new)' },
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
      { key: 'can_carrier_directory', kind: 'feature', label: 'Carrier Directory', description: 'Reference directory of the external carriers we recruit for (pre-qual, presentation, process notes)' },
      { key: 'can_manage_carrier_directory', kind: 'action', label: 'Manage', indented: true, description: 'Add, edit & delete carriers — without this the directory is read-only' },
    ],
  },
  {
    title: 'Accounting',
    flags: [
      { header: 'Costs', description: 'fuel spend + cost-per-mile components' },
      { key: 'can_fuel_cost',     kind: 'component', label: 'Fuel Costs', indented: true },
      { key: 'can_cost_per_mile', kind: 'component', label: 'Cost per Mile', indented: true },
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
export interface Block { parent: PermFlag; children: Block[] }
// Keys collapse-state and React keys by the row's PRIMARY flag key, not
// its label — bare-verb labels ("Manage") repeat across families.
export const blockKey = (f: PermFlag): string =>
  isHeader(f) ? f.header : isScoped(f) ? f.allKey : f.key;
export function toBlocks(flags: PermFlag[]): Block[] {
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
export const GROUP_BLOCKS = PERM_GROUPS.map((g) => ({ title: g.title, blocks: toBlocks(g.flags) }));

// primaryKey → the label of the row it hangs under, so a bare-verb child
// ("Manage") is never ambiguous where rows are listed OUT of tree context
// (cell tooltips, the confirm dialog's change list).
export const PARENT_LABEL: Record<string, string> = {};
{
  const walk = (bs: Block[], parentLabel?: string) => bs.forEach((b) => {
    if (!isHeader(b.parent) && parentLabel) PARENT_LABEL[blockKey(b.parent)] = parentLabel;
    walk(b.children, isHeader(b.parent) ? b.parent.header : b.parent.label);
  });
  GROUP_BLOCKS.forEach((g) => walk(g.blocks));
}
export const contextLabel = (f: SimpleFlag | ScopedFlag): string => {
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
export const DRIVER_TRUCK: ScopedFlag[] = [
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
export const DRIVER_RECORDS: SimpleFlag[] = [
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
export const DRIVER_PANEL_FLAGS: PermFlag[] = [...DRIVER_TRUCK, ...DRIVER_RECORDS];
// Static flag list for the change diff — PERM_GROUPS never changes at runtime.
export const ALL_MATRIX_FLAGS: PermFlag[] = PERM_GROUPS.flatMap((g) => g.flags);

// Department band → account module.  The Modules page folded into this
// matrix: the on/off switch lives ON the department header, so "is the
// department even on" and "what can each role do" are one screen.
// System/Shared bands have no switch (core + account are always on).
export const GROUP_MODULE: Record<string, string> = {
  Fleet: 'fleet', Dispatch: 'dispatch', Safety: 'safety', HR: 'hr', Accounting: 'accounting',
};
export interface ModulesData { enabled: string[]; all: string[] }
// Parents that have sub-rows — collapsed by default (only features show).
// Recursive: a sub-feature with its own children is collapsible too.
export const collectCollapsible = (bs: Block[]): string[] => bs.flatMap((b) => [
  ...(b.children.length > 0 ? [blockKey(b.parent)] : []),
  ...collectCollapsible(b.children),
]);
export const COLLAPSIBLE_KEYS: string[] = GROUP_BLOCKS.flatMap((g) => collectCollapsible(g.blocks));
