import { useState, useMemo, Fragment, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield, Check, X, Lock, ChevronRight, ChevronDown, Bell, Bot, FileText } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses } from '../../lib/status';

// Column order mirrors the persona-selector dropdown.
const ROLES = [
  'owner', 'admin', 'fleet', 'dispatcher', 'safety', 'hr', 'accounting', 'recruiter', 'driver',
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
interface ScopedFlag { allKey: string; vehicleKey: string; label: string; scoped: true; description?: string; indented?: boolean }
interface SimpleFlag { key: string; label: string; scoped?: false; description?: string; indented?: boolean }
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
const PERM_GROUPS: PermGroup[] = [
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
      { key: 'can_manage_permissions',  label: 'Permissions', description: 'This role matrix — the owner always keeps it' },
      { key: 'can_manage_integrations', label: 'Integrations', description: 'Telematics connections (Samsara, Datatruck)' },
      { key: 'can_manage_storage',      label: 'Storage', description: 'File-storage backend & quota' },
      { header: 'Settings', description: 'account administration — each component has its own permission' },
      { key: 'can_manage_account',     label: 'General settings', indented: true, description: 'The Settings page itself — timezone, bot + forum routing; also rides: department modules, Scorecard Rules' },
      { key: 'can_manage_users',       label: 'Team Management', indented: true, description: 'Members, roles, data scope — also gates the Audit Log' },
      { key: 'can_invite',             label: 'Send Invites', indented: true },
      { key: 'can_manage_companies',   label: 'Manage Companies', indented: true },
      { key: 'can_manage_work_hours',  label: 'Working Hours', indented: true },
    ],
  },
  {
    // Shared — features several departments use.
    title: 'Shared',
    flags: [
      { allKey: 'can_location_map', vehicleKey: 'can_location_vehicle', label: 'Live Map', scoped: true },
      { key: 'can_manage_poi_layers', label: 'Manage POI Layers', indented: true },
      { allKey: 'can_vehicle_all',  vehicleKey: 'can_vehicle_vehicle',  label: 'Vehicles', scoped: true },
      { key: 'can_manage_vehicles', label: 'Manage Vehicles', indented: true, description: 'Add / edit / remove vehicles in the registry (trucks + trailers, with or without telematics)' },
      // Per-vehicle component views (each gates the live tab on the Vehicle
      // Info page + its report + AI tool) — mirrors features/vehicles/*.
      { key: 'can_health',     label: 'Health', indented: true, description: 'Engine gauges — battery, oil, coolant, DEF, RPM' },
      { key: 'can_faults',     label: 'Faults', indented: true, description: 'Active fault codes (DTCs) + the faults report' },
      { key: 'can_fuel',       label: 'Fuel', indented: true, description: 'Fuel & DEF tank levels + low-fuel alerts' },
      { key: 'can_efficiency', label: 'Efficiency', indented: true, description: 'MPG, idle vs drive time, harsh-driving utilization' },
      { allKey: 'can_geofence_all', vehicleKey: 'can_geofence_vehicle', label: 'Geofences', scoped: true },
      { key: 'can_manage_driver_docs', label: 'Drivers', description: 'Driver list + document management' },
      { key: 'can_driver_docs_own',    label: 'View Own Documents', indented: true },
      { allKey: 'can_scorecard_all', vehicleKey: 'can_scorecard_vehicle', label: 'Driver Scorecards', scoped: true, description: 'Scorecard Rules (the scoring config) is this feature’s admin component' },
    ],
  },
  {
    title: 'Fleet',
    flags: [
      { allKey: 'can_maintenance_all', vehicleKey: 'can_maintenance_vehicle', label: 'Maintenance', scoped: true },
      { allKey: 'can_work_orders_all', vehicleKey: 'can_work_orders_vehicle', label: 'Work Orders', scoped: true },
      { allKey: 'can_inspections_all', vehicleKey: 'can_inspections_vehicle', label: 'PTI Inspections', scoped: true },
    ],
  },
  {
    title: 'Dispatch',
    flags: [
      { allKey: 'can_route_all', vehicleKey: 'can_route_vehicle', label: 'Routes', scoped: true },
    ],
  },
  {
    title: 'Safety',
    flags: [
      { allKey: 'can_events_all', vehicleKey: 'can_events_vehicle', label: 'Safety Events', scoped: true },
      { key: 'can_cameras', label: 'Cameras', description: 'Dashcam footage' },
      { allKey: 'can_parking_all', vehicleKey: 'can_parking_vehicle', label: 'Parking', scoped: true, description: 'Unsafe-parking events' },
      // The Risk Summary report tab — a stakeholder/personnel risk deliverable.
      // It's a report TYPE (feature), surfaced inside the always-on Reports
      // hub; it lives here because it's safety-owned data.
      { allKey: 'can_risk_report_all', vehicleKey: 'can_risk_report_own', label: 'Risk Summary', scoped: true, description: 'Stakeholder Risk Summary report (in the Reports hub)' },
    ],
  },
  {
    title: 'HR',
    flags: [
      { key: 'can_coaching_admin',    label: 'Coaching' },
      { key: 'can_coaching_view_own', label: 'View Own Coaching', indented: true },
    ],
  },
  {
    title: 'Recruiting',
    flags: [
      { key: 'can_manage_applications', label: 'Applications', description: 'Recruiting links + the driver-application dashboard' },
      { key: 'can_convert_to_driver',  label: 'Hire Applicant', indented: true, description: 'Convert an approved application into a driver / invite — without full Send-Invites power' },
    ],
  },
  {
    title: 'Accounting',
    flags: [
      { header: 'Costs', description: 'fuel spend + cost-per-mile components' },
      { key: 'can_fuel_cost',     label: 'Fuel Costs', indented: true },
      { key: 'can_cost_per_mile', label: 'Cost per Mile', indented: true },
      // The Cost Reports tab — executive maintenance/work-order cost rollups,
      // a report TYPE (feature) surfaced in the always-on Reports hub.  Lives
      // here because it's cost-owned data (deliberately split from Maintenance).
      { key: 'can_cost_reports', label: 'Cost Reports', description: 'Executive cost rollups (in the Reports hub)' },
      { key: 'can_payroll_admin',    label: 'Payroll' },
      { key: 'can_payroll_view_own', label: 'View Own Paystubs', indented: true },
      { key: 'can_manage_billing',   label: 'Billing' },
    ],
  },
];

// A parent feature + its indented sub-permissions, so the matrix can
// collapse the detail (POI Layers under Live Map, the reports under the
// Reports header, View-Own pairs under their admin row).
interface Block { parent: PermFlag; children: PermFlag[] }
const blockKey = (f: PermFlag): string => (isHeader(f) ? f.header : f.label);
function toBlocks(flags: PermFlag[]): Block[] {
  const blocks: Block[] = [];
  for (const f of flags) {
    if (!isHeader(f) && f.indented && blocks.length) blocks[blocks.length - 1].children.push(f);
    else blocks.push({ parent: f, children: [] });
  }
  return blocks;
}
const GROUP_BLOCKS = PERM_GROUPS.map((g) => ({ title: g.title, blocks: toBlocks(g.flags) }));

// Department band → account module.  The Modules page folded into this
// matrix: the on/off switch lives ON the department header, so "is the
// department even on" and "what can each role do" are one screen.
// System/Shared bands have no switch (core + account are always on).
const GROUP_MODULE: Record<string, string> = {
  Fleet: 'fleet', Dispatch: 'dispatch', Safety: 'safety', HR: 'hr', Accounting: 'accounting',
};
interface ModulesData { enabled: string[]; all: string[] }
// Parents that have sub-rows — collapsed by default (only features show).
const COLLAPSIBLE_KEYS: string[] = GROUP_BLOCKS.flatMap((g) =>
  g.blocks.filter((b) => b.children.length > 0).map((b) => blockKey(b.parent)),
);

interface PermsData {
  current: Record<string, Record<string, boolean>>;
  defaults: Record<string, Record<string, boolean>>;
  fields: string[];
}

// role -> flag -> pending boolean
type Edits = Record<string, Record<string, boolean>>;

interface Change { role: RoleId; label: string; from: string; to: string; granted: boolean }

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

  function toggle(role: RoleId, f: PermFlag) {
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
    for (const role of ROLES)
      for (const g of PERM_GROUPS)
        for (const f of g.flags) {
          if (isHeader(f)) continue;
          const before = cellState(role, f, curFlagVal);
          const after = cellState(role, f, flagVal);
          if (before !== after) out.push({ role, label: f.label, from: before, to: after, granted: after !== 'No access' });
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
      for (const [role, perms] of Object.entries(changedByRole)) {
        await apiJSON('/admin/permissions/roles', { method: 'PUT', body: { role, permissions: perms } });
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

  const cellChanged = (role: RoleId, f: PermFlag) => cellState(role, f, flagVal) !== cellState(role, f, curFlagVal);

  // Render one matrix row.  `collapse` adds an expand/collapse chevron to
  // a parent feature that has sub-rows.
  const renderRow = (f: PermFlag, collapse?: { isCollapsed: boolean; onToggle: () => void }): ReactNode => {
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
          <td colSpan={1 + ROLES.length} className="px-3 py-2 sticky left-0 bg-card z-10">
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {chevronSlot}
              <span className="text-sm font-medium">{f.header}</span>
              {f.description && <span className="text-2xs text-muted-foreground ml-2">{f.description}</span>}
            </div>
          </td>
        </tr>
      );
    }
    return (
      <tr className={`${f.indented ? '' : 'border-t border-border'} hover:bg-muted/20`}>
        <td className={`sticky left-0 bg-card z-10 ${f.indented ? 'pl-4 pr-3' : 'px-3 py-1.5'}`}>
          {/* Indented component: a continuous left rail (the cell carries no
              vertical padding so adjacent rails touch) makes the rows read as
              a group hanging off the feature above. */}
          <div className={f.indented ? 'border-l-2 border-border pl-3 py-1.5' : ''}>
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {!f.indented && chevronSlot}
              <span className={f.indented ? 'text-muted-foreground' : 'font-medium'}>{f.label}</span>
              {isScoped(f) && <span className="text-2xs text-muted-foreground" title="Scoped feature — checkbox = full access">*</span>}
            </div>
            {f.description && <div className="text-2xs text-muted-foreground/70 mt-0.5">{f.description}</div>}
          </div>
        </td>
        {ROLES.map((role) => {
          const on = isGranted(role, f);
          const locked = ownerLocked(role, f);
          const changed = !locked && cellChanged(role, f);
          return (
            <td key={role} className={`text-center px-2 py-1.5 ${changed ? 'bg-primary/10' : ''}`}>
              <button
                onClick={() => toggle(role, f)}
                disabled={locked}
                aria-pressed={on}
                title={locked
                  ? `Owner always keeps "${f.label}" — prevents lockout`
                  : `${ROLE_LABELS[role]} · ${f.label}: ${on ? 'granted' : 'no access'}`}
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
                <tr className="bg-muted/40">
                  <th className="text-left font-semibold px-3 py-2 sticky left-0 bg-muted/40 z-10 min-w-[200px]">Feature</th>
                  {ROLES.map((r) => (
                    <th key={r} className="px-2 py-2 text-center font-semibold text-xs whitespace-nowrap">{ROLE_LABELS[r]}</th>
                  ))}
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
                        <span className={`text-2xs px-1.5 py-0.5 rounded normal-case tracking-normal ${toneClasses('warn')}`}>
                          module off — features hidden from every sidebar
                        </span>
                      )}
                    </span>
                  ) : undefined;
                  return (
                  <FragmentGroup key={group.title} title={group.title} control={control}>
                    {group.blocks.map((block, bi) => {
                      const pk = blockKey(block.parent);
                      const hasChildren = block.children.length > 0;
                      const isColl = hasChildren && collapsed.has(pk);
                      return (
                        <Fragment key={`${group.title}-${pk}-${bi}`}>
                          {renderRow(block.parent, hasChildren ? { isCollapsed: isColl, onToggle: () => toggleCollapse(pk) } : undefined)}
                          {hasChildren && !isColl && block.children.map((c, ci) => (
                            <Fragment key={`${pk}-c-${ci}`}>{renderRow(c)}</Fragment>
                          ))}
                        </Fragment>
                      );
                    })}
                  </FragmentGroup>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* System Services — always-on infrastructure, NOT owner-toggled.
              Access is derived from each role's feature permissions, so there
              is nothing to tick.  This panel documents the model in-place so
              an admin isn't left wondering where the old Alerts / AI rows went. */}
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">System Services</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded normal-case tracking-normal ${toneClasses('ok')}`}>always on</span>
            </div>
            <p className="text-2xs text-muted-foreground mb-3">
              Infrastructure services — not granted here. Each one follows the role&apos;s feature permissions automatically: disable a feature and its slice stops, but the service itself never stops.
            </p>
            <ul className="space-y-2.5">
              <li className="flex items-start gap-2.5">
                <Bell size={16} className="text-muted-foreground mt-0.5 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-foreground">Alerts</div>
                  <div className="text-2xs text-muted-foreground">Every role has the inbox. It shows the alerts for whichever features the role can see — disable a feature (Faults, Health, Fuel, Safety Events, Geofences, Maintenance) and just those alerts drop out. Scope follows the role&apos;s vehicle access — fleet-wide or own-vehicle.</div>
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
        <p className="text-2xs text-muted-foreground mt-2">
          <span className="font-semibold">*</span> scoped feature — ticking grants the role access to the feature. <span className="font-medium text-foreground/80">Whose data</span> they see (All / Company / Vehicle) is set per-user in <span className="font-medium text-foreground/80">Team Management</span> (Company Access + Vehicle Assignments).
        </p>
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
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Department modules</div>
                  {moduleChanges.map((m) => (
                    <div key={m.id} className="text-sm flex items-center justify-between py-0.5">
                      <span>{m.title}</span>
                      <span className={m.on ? 'text-ok' : 'text-danger'}>{m.on ? 'On' : 'Off — hidden from every sidebar'}</span>
                    </div>
                  ))}
                </div>
              )}
              {ROLES.filter((r) => changes.some((c) => c.role === r)).map((role) => (
                <div key={role}>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">{ROLE_LABELS[role]}</div>
                  <ul className="space-y-0.5">
                    {changes.filter((c) => c.role === role).map((c) => (
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
function FragmentGroup({ title, control, children }: { title: string; control?: ReactNode; children: ReactNode }) {
  return (
    <>
      <tr className="bg-muted/50 border-t-2 border-border">
        <td colSpan={1 + ROLES.length} className="px-3 py-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
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
