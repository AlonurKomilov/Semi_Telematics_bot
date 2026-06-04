import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses } from '../../lib/status';

// Order mirrors the persona-selector dropdown so the column layout
// matches what an Owner already sees there.  HR + Accounting were
// added to ROLE_PERMISSIONS (capabilities/iam/permissions.py) but
// the frontend column list had drifted; without them an Owner
// couldn't customize per-account permissions for those two personas
// even though the backend GET /admin/permissions/roles returned
// them all along.
const ROLES = [
  'owner', 'admin', 'fleet', 'dispatcher', 'safety', 'hr', 'accounting', 'driver',
] as const;

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  dispatcher: 'Dispatch',
  safety: 'Safety',
  hr: 'HR',
  accounting: 'Accounting',
  driver: 'Driver',
};

type ScopeValue = 'all' | 'company' | 'assigned' | 'none';

interface ScopedFlag {
  allKey: string;
  ownKey: string;
  label: string;
  scoped: true;
  /** Optional one-line subtitle rendered under the label.  Use when
   *  the flag gates more than its label suggests (an "overloaded"
   *  flag — see can_faults, can_manage_users, can_manage_account)
   *  so an Owner toggling it sees the full blast radius before
   *  saving. */
  description?: string;
}

interface SimpleFlag {
  key: string;
  label: string;
  scoped?: false;
  /** Optional one-line subtitle. */
  description?: string;
  /** Render as a sub-row visually attached to the row immediately
   *  above — used to express admin/own pairs (admin can edit X /
   *  owners can view their own X) without inventing a new dual-toggle
   *  control.  The pair stays as two flags (they're genuinely
   *  separate operations), but reads as one decision. */
  indented?: boolean;
}

type PermFlag = ScopedFlag | SimpleFlag;

interface PermGroup {
  title: string;
  icon: string;
  flags: PermFlag[];
}

const SCOPE_OPTIONS: { value: ScopeValue; label: string; active: string }[] = [
  { value: 'none', label: 'None', active: 'bg-muted text-foreground' },
  { value: 'assigned', label: 'Vehicle', active: 'bg-amber-600 text-white' },
  { value: 'company', label: 'Company', active: 'bg-primary text-primary-foreground' },
  { value: 'all', label: 'All', active: 'bg-green-600 text-white' },
];

// PERM_GROUPS — admin-facing organization of permission flags.
//
// Structure deliberately mirrors the dashboard sidebar so an admin can
// jump from "I see this in the nav" to "this is where I customize it"
// without re-learning the layout.  Until 2026-05-19 the grouping had
// no relationship to the sidebar: "Core" was a junk drawer, "Fleet"
// held reports, "Safety & Compliance" held cost reports, and feature-
// group names collided with role names ("Fleet" group vs "Fleet
// Manager" role column).  The five groups below match the five
// sidebar sections; flag names themselves are unchanged so backend
// enforcement keeps working.
const PERM_GROUPS: PermGroup[] = [
  {
    // Operations — day-to-day operational features (where vehicles
    // are, where they go, what's wrong with them).  Renamed from
    // "Fleet Operations" so the group title no longer collides with
    // the "Fleet" role column header — the contents are cross-role
    // (every working persona touches Live Map / Vehicles / Geofences)
    // and the prefix was misleading admins into thinking the group
    // gated Fleet-only access.
    title: 'Operations',
    icon: '🚛',
    flags: [
      { allKey: 'can_location_map', ownKey: 'can_location_own', label: 'Live Map', scoped: true },
      { allKey: 'can_vehicle_all',  ownKey: 'can_vehicle_own',  label: 'Vehicles', scoped: true },
      { allKey: 'can_route_all',    ownKey: 'can_route_own',    label: 'Routes', scoped: true },
      { allKey: 'can_geofence_all', ownKey: 'can_geofence_own', label: 'Geofences', scoped: true },
      { allKey: 'can_maintenance_all', ownKey: 'can_maintenance_own', label: 'Maintenance & Work Orders', scoped: true },
      { allKey: 'can_inspections_all', ownKey: 'can_inspections_own', label: 'PTI Inspections', scoped: true },
      { key: 'can_manage_poi_layers', label: 'Manage POI Layers' },
      {
        key: 'can_rolling_stopped',
        label: 'AI: Engine-state Lookup',
        description: 'Lets the AI assistant answer "what\'s rolling, idling, or off right now?" — no dashboard page',
      },
    ],
  },
  {
    // Monitoring & Compliance — driver scoring, safety events, alerts.
    // Renamed from "Safety & Compliance" so the title no longer
    // collides with the "Safety" role column header.  The group's
    // contents are about *ongoing monitoring* (scorecards trend over
    // time; safety events are real-time incidents; alerts are
    // notifications about anomalies) — "Monitoring & Compliance"
    // captures that without using a role name.
    title: 'Monitoring & Compliance',
    icon: '🛡️',
    flags: [
      { allKey: 'can_scorecard_all', ownKey: 'can_scorecard_own', label: 'Driver Scorecards', scoped: true },
      { allKey: 'can_events_all',    ownKey: 'can_events_own',    label: 'Safety Events', scoped: true },
      {
        allKey: 'can_alerts_all', ownKey: 'can_alerts_own',
        label: 'Alerts',
        scoped: true,
        description: 'Also gates the Parking page (unsafe-parking events are gated through the alerts flag)',
      },
    ],
  },
  {
    // Reports — read-only aggregations from /reports/*.  Previously
    // bundled with Costs in a single "Reports & Costs" group, but the
    // two have different route trees and different audiences (Owner
    // reads reports; Accounting manages costs).
    title: 'Reports',
    icon: '📊',
    flags: [
      {
        key: 'can_faults',
        label: 'Faults Report',
        description: 'Also gates Cameras page + AI Chat + AI Summary',
      },
      { key: 'can_health',     label: 'Health Report' },
      { key: 'can_efficiency', label: 'Efficiency Report' },
      { key: 'can_fuel',       label: 'Fuel Report' },
      { allKey: 'can_risk_report_all', ownKey: 'can_risk_report_own', label: 'Risk Summary Report', scoped: true },
      {
        key: 'can_digest',
        label: 'Scheduled Reports',
        description: 'Lets the user schedule recurring report deliveries (Telegram PDF) at a chosen frequency + local hour',
      },
    ],
  },
  {
    // Costs — cost-management pages.  Routed under /costs/* in the
    // dashboard sidebar; gating belongs to Accounting/Owner audience.
    title: 'Costs',
    icon: '💰',
    flags: [
      { key: 'can_fuel_cost',     label: 'Fuel Costs' },
      { key: 'can_cost_per_mile', label: 'Cost per Mile' },
      { key: 'can_cost_reports',  label: 'Cost Reports' },
    ],
  },
  {
    // Workforce — driver-facing identity and HR-adjacent features.
    // Absorbs the previous standalone "Coaching" and "Payroll" groups
    // because they share a Workforce subject area in the dashboard
    // sidebar (/workforce/drivers, /coaching, /payroll).
    title: 'Workforce',
    icon: '🪪',
    flags: [
      { key: 'can_manage_driver_docs', label: 'Manage Driver Documents' },
      { key: 'can_driver_docs_own',    label: 'View Own Driver Documents',         indented: true },
      { key: 'can_coaching_admin',     label: 'Manage Coaching Rules & Assignments' },
      { key: 'can_coaching_view_own',  label: 'View & Acknowledge Own Coaching',   indented: true },
      { key: 'can_payroll_admin',      label: 'Manage Bonus Rules & Payroll Runs' },
      { key: 'can_payroll_view_own',   label: 'View Own Paystubs',                 indented: true },
    ],
  },
  {
    // Administration — account-level controls.  Renamed from "Admin"
    // to avoid collision with the Admin ROLE column header at the top
    // of the page.
    title: 'Administration',
    icon: '👥',
    flags: [
      {
        key: 'can_manage_account',
        label: 'Account Settings',
        description: 'Also gates Role Permissions, Storage, Working Hours, Scorecard Rules',
      },
      {
        key: 'can_manage_users',
        label: 'Manage Users',
        description: 'Also gates the Audit Log',
      },
      { key: 'can_manage_companies', label: 'Manage Companies' },
      { key: 'can_invite',           label: 'Send Invites' },
      { key: 'can_manage_billing',   label: 'Manage Billing & Subscription' },
    ],
  },
];

interface PermsData {
  current: Record<string, Record<string, boolean>>;
  defaults: Record<string, Record<string, boolean>>;
  fields: string[];
}

interface CompanyInfo {
  id: number;
  code: string;
  display_name: string;
}

interface OverridesData {
  companies: CompanyInfo[];
  overrides: Record<string, string[]>;   // company_id -> roles[]
  override_perms: Record<string, Record<string, boolean>>; // "cid:role" -> perms
}

export default function RolePermissions() {
  const { t } = useTranslation();
  const { refreshPermissions } = useRoleView();
  const { refreshUser } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState('');
  const [selectedRole, setSelectedRole] = useState('admin');
  const [selectedCompany, setSelectedCompany] = useState<number | null>(null);
  const [edits, setEdits] = useState<Record<string, boolean>>({});
  const [resetAllConfirm, setResetAllConfirm] = useState(false);
  const [deleteOverrideConfirm, setDeleteOverrideConfirm] = useState(false);

  const { data, isLoading: rolesLoading, error: rolesErr } = useQuery({
    queryKey: ['perms-roles'],
    queryFn: () => apiJSON<PermsData>('/admin/permissions/roles'),
  });
  const { data: overridesData } = useQuery({
    queryKey: ['perms-overrides'],
    queryFn: () => apiJSON<OverridesData>('/admin/permissions/roles/overrides'),
  });
  const loading = rolesLoading;
  const fetchError = rolesErr instanceof Error ? rolesErr.message : '';
  const load = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['perms-roles'] }),
      qc.invalidateQueries({ queryKey: ['perms-overrides'] }),
    ]);

  const companies = overridesData?.companies ?? [];
  const hasCompanies = companies.length > 0;

  // Does the currently selected company+role have a DB override?
  const hasCompanyOverride = useMemo(() => {
    if (selectedCompany === null || !overridesData) return false;
    const roles = overridesData.overrides[String(selectedCompany)] ?? [];
    return roles.includes(selectedRole);
  }, [selectedCompany, selectedRole, overridesData]);

  // Base permissions for the selected role (account-wide from /roles endpoint)
  const accountWidePerms = data?.current[selectedRole] ?? {};

  // Resolve current permissions: company override if it exists, else account-wide
  const basePerms = useMemo(() => {
    if (selectedCompany !== null && hasCompanyOverride && overridesData) {
      const key = `${selectedCompany}:${selectedRole}`;
      return overridesData.override_perms[key] ?? { ...accountWidePerms };
    }
    return { ...accountWidePerms };
  }, [selectedCompany, selectedRole, hasCompanyOverride, overridesData, accountWidePerms]);

  // Current permissions with local edits applied
  const currentPerms = useMemo(() => {
    return { ...basePerms, ...edits };
  }, [basePerms, edits]);

  const defaultPerms = data?.defaults[selectedRole] ?? {};

  // Check if there are unsaved changes
  const hasChanges = useMemo(() => {
    return Object.entries(edits).some(([k, v]) => basePerms[k] !== v);
  }, [basePerms, edits]);

  // Check if account-wide current differs from factory defaults
  const isCustomized = useMemo(() => {
    if (!data) return false;
    const cur = data.current[selectedRole] ?? {};
    const def = data.defaults[selectedRole] ?? {};
    return Object.keys(def).some((k) => cur[k] !== def[k]);
  }, [data, selectedRole]);

  function handleRoleChange(role: string) {
    setSelectedRole(role);
    setEdits({});
    setSuccess('');
    setDeleteOverrideConfirm(false);
  }

  function handleCompanyChange(companyId: number | null) {
    setSelectedCompany(companyId);
    setEdits({});
    setSuccess('');
    setDeleteOverrideConfirm(false);
  }

  function toggleFlag(key: string) {
    setEdits((prev) => ({ ...prev, [key]: !currentPerms[key] }));
    setSuccess('');
  }

  function getScope(allKey: string, ownKey: string): ScopeValue {
    const a = !!currentPerms[allKey];
    const o = !!currentPerms[ownKey];
    if (a && o) return 'all';
    if (a) return 'company';
    if (o) return 'assigned';
    return 'none';
  }

  function setScope(allKey: string, ownKey: string, scope: ScopeValue) {
    const map: Record<ScopeValue, [boolean, boolean]> = {
      all: [true, true], company: [true, false], assigned: [false, true], none: [false, false],
    };
    const [a, o] = map[scope];
    setEdits((prev) => ({ ...prev, [allKey]: a, [ownKey]: o }));
    setSuccess('');
  }

  async function handleSave() {
    if (!hasChanges) return;
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await apiJSON('/admin/permissions/roles', {
        method: 'PUT',
        body: {
          role: selectedRole,
          permissions: edits,
          ...(selectedCompany !== null ? { company_id: selectedCompany } : {}),
        },
      });
      setEdits({});
      const label = selectedCompany !== null
        ? `${ROLE_LABELS[selectedRole]} (${companies.find(c => c.id === selectedCompany)?.code ?? ''}) saved`
        : `${ROLE_LABELS[selectedRole]} saved`;
      setSuccess(label);
      await load();
      refreshPermissions();
      // Force the saving admin's OWN /user/me to refresh so their
      // sidebar/route guards reflect the change immediately, without
      // waiting for the next tab-focus event.  Other already-logged-in
      // users on this account pick up the change on their next focus.
      try { await refreshUser(); } catch { /* best-effort */ }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await apiJSON('/admin/permissions/roles/reset', {
        method: 'POST',
        body: {
          role: selectedRole,
          permissions: {},
          ...(selectedCompany !== null ? { company_id: selectedCompany } : {}),
        },
      });
      setEdits({});
      setSuccess(`${ROLE_LABELS[selectedRole]} reset to defaults`);
      await load();
      refreshPermissions();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reset');
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteOverride() {
    if (selectedCompany === null) return;
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await apiJSON('/admin/permissions/roles/delete-override', {
        method: 'POST',
        body: { role: selectedRole, company_id: selectedCompany },
      });
      setEdits({});
      setDeleteOverrideConfirm(false);
      const code = companies.find(c => c.id === selectedCompany)?.code ?? '';
      setSuccess(`${ROLE_LABELS[selectedRole]} override for ${code} removed`);
      await load();
      refreshPermissions();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove override');
    } finally {
      setSaving(false);
    }
  }

  async function handleResetAll() {
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await apiJSON('/admin/permissions/roles/reset-all', { method: 'POST' });
      setEdits({});
      setResetAllConfirm(false);
      setSuccess('All roles reset to factory defaults');
      await load();
      refreshPermissions();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reset');
    } finally {
      setSaving(false);
    }
  }

  // Count company overrides for a role
  function overrideCount(role: string): number {
    if (!overridesData) return 0;
    return Object.values(overridesData.overrides)
      .filter(roles => roles.includes(role)).length;
  }

  if (loading) {
    return (
      <div>
        <PageHeader
          icon={Shield}
          title={t('pages.role_perms_title')}
          description={t('pages.role_perms_desc_short')}
        />
        <CardSkeleton height="h-96" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Shield}
        title={t('pages.role_perms_title')}
        description={t('pages.role_perms_desc_long')}
        actions={
          !resetAllConfirm ? (
          <button
            onClick={() => setResetAllConfirm(true)}
            className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground border border-border hover:border-border/80 rounded-md transition"
          >
            Reset all roles
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-xs text-destructive">Reset all to defaults?</span>
            <button
              onClick={handleResetAll}
              disabled={saving}
              className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition"
            >
              Confirm
            </button>
            <button
              onClick={() => setResetAllConfirm(false)}
              className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition"
            >
              Cancel
            </button>
          </div>
        )
        }
      />

      {(error || fetchError) && <p className="text-destructive text-sm">{error || fetchError}</p>}
      {success && <p className="text-ok text-sm">{success}</p>}

      {/* Company selector */}
      {hasCompanies && (
        <div className="flex items-center gap-3">
          <label className="text-sm text-muted-foreground">Company:</label>
          <select
            value={selectedCompany ?? ''}
            onChange={(e) => handleCompanyChange(e.target.value ? Number(e.target.value) : null)}
            className="bg-card border border-border rounded-lg px-3 py-2 text-sm focus:border-ring focus:outline-none"
          >
            <option value="">All Companies (account-wide)</option>
            {companies.map((c) => {
              const companyOverrides = overridesData?.overrides[String(c.id)] ?? [];
              const badge = companyOverrides.length > 0
                ? ` (${companyOverrides.length} override${companyOverrides.length > 1 ? 's' : ''})`
                : '';
              return (
                <option key={c.id} value={c.id}>
                  {c.code}{c.display_name ? ` — ${c.display_name}` : ''}{badge}
                </option>
              );
            })}
          </select>
          {selectedCompany !== null && (
            <span className={`text-xs px-2 py-0.5 rounded border ${
              hasCompanyOverride
                ? toneClasses('warn')
                : toneClasses('neutral')
            }`}>
              {hasCompanyOverride ? 'Company Override' : 'Inherited'}
            </span>
          )}
        </div>
      )}

      {/* Role selector tabs */}
      <div className="flex gap-1 bg-card border border-border rounded-xl p-1">
        {ROLES.map((role) => {
          const oc = overrideCount(role);
          return (
            <button
              key={role}
              onClick={() => handleRoleChange(role)}
              className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition relative ${
                selectedRole === role
                  ? 'bg-muted/80 text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {ROLE_LABELS[role]}
              {oc > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-warn text-background text-3xs rounded-full flex items-center justify-center font-bold">
                  {oc}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Permission groups */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {PERM_GROUPS.map((group) => (
          <section
            key={group.title}
            className="bg-card border border-border rounded-xl p-5"
          >
            <h3 className="font-semibold mb-3 flex items-center gap-2">
              <span>{group.icon}</span>
              {group.title}
            </h3>
            <div className="space-y-2">
              {group.flags.map((flag) => {
                if (flag.scoped) {
                  const scope = getScope(flag.allKey, flag.ownKey);
                  const resolveScope = (a: boolean, o: boolean): ScopeValue => {
                    if (a && o) return 'all'; if (a) return 'company'; if (o) return 'assigned'; return 'none';
                  };
                  const defScope = resolveScope(!!defaultPerms[flag.allKey], !!defaultPerms[flag.ownKey]);
                  const acctScope = resolveScope(!!accountWidePerms[flag.allKey], !!accountWidePerms[flag.ownKey]);
                  const isChanged = flag.allKey in edits || flag.ownKey in edits;
                  return (
                    <div
                      key={flag.allKey}
                      className="flex items-center justify-between py-1 px-2 rounded hover:bg-muted/50"
                    >
                      <div className="flex flex-col min-w-0 pr-3">
                        <span className="text-sm flex items-center gap-2">
                          {flag.label}
                          {selectedCompany === null && scope !== defScope && !isChanged && (
                            <span className="text-3xs text-warn uppercase tracking-wider">custom</span>
                          )}
                          {selectedCompany !== null && scope !== acctScope && !isChanged && (
                            <span className="text-3xs text-info uppercase tracking-wider">override</span>
                          )}
                        </span>
                        {flag.description && (
                          <span className="text-2xs text-muted-foreground mt-0.5">
                            {flag.description}
                          </span>
                        )}
                      </div>
                      <div className="flex rounded-lg overflow-hidden border border-border">
                        {SCOPE_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => setScope(flag.allKey, flag.ownKey, opt.value)}
                            className={`px-2.5 py-1 text-xs font-medium transition ${
                              scope === opt.value ? opt.active : 'bg-muted text-muted-foreground hover:text-foreground/80'
                            }`}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                }
                const enabled = !!currentPerms[flag.key];
                const isDefault = defaultPerms[flag.key];
                const isChanged = flag.key in edits;
                const differsFromAccountWide = selectedCompany !== null && enabled !== !!accountWidePerms[flag.key];
                return (
                  <label
                    key={flag.key}
                    className={`flex items-center justify-between py-1 px-2 rounded hover:bg-muted/50 cursor-pointer group ${
                      flag.indented ? 'ml-5 border-l-2 border-border/40 pl-3 -mt-1' : ''
                    }`}
                  >
                    <div className="flex flex-col min-w-0 pr-3">
                      <span className="text-sm flex items-center gap-2">
                        {flag.label}
                        {selectedCompany === null && enabled !== isDefault && !isChanged && (
                          <span className="text-3xs text-warn uppercase tracking-wider">custom</span>
                        )}
                        {selectedCompany !== null && differsFromAccountWide && !isChanged && (
                          <span className="text-3xs text-info uppercase tracking-wider">override</span>
                        )}
                      </span>
                      {flag.description && (
                        <span className="text-2xs text-muted-foreground mt-0.5">
                          {flag.description}
                        </span>
                      )}
                    </div>
                    <div className="relative">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={enabled}
                        onChange={() => toggleFlag(flag.key)}
                      />
                      <div className="w-9 h-5 bg-muted rounded-full peer-checked:bg-primary transition-colors" />
                      <div className="absolute top-0.5 left-0.5 w-4 h-4 bg-background rounded-full shadow peer-checked:translate-x-4 transition-transform" />
                    </div>
                  </label>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-between bg-card border border-border rounded-xl p-4">
        <div className="flex items-center gap-3">
          {selectedCompany === null && isCustomized && (
            <button
              onClick={handleReset}
              disabled={saving}
              className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground border border-border hover:border-border/80 rounded-lg transition disabled:opacity-50"
            >
              Reset {ROLE_LABELS[selectedRole]} to Defaults
            </button>
          )}
          {selectedCompany !== null && hasCompanyOverride && (
            !deleteOverrideConfirm ? (
              <button
                onClick={() => setDeleteOverrideConfirm(true)}
                disabled={saving}
                className="px-4 py-2 text-sm text-destructive hover:text-destructive/80 border border-destructive/40 hover:border-destructive rounded-lg transition disabled:opacity-50"
              >
                Remove Override
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-destructive">Remove company override?</span>
                <button
                  onClick={handleDeleteOverride}
                  disabled={saving}
                  className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition"
                >
                  Confirm
                </button>
                <button
                  onClick={() => setDeleteOverrideConfirm(false)}
                  className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition"
                >
                  Cancel
                </button>
              </div>
            )
          )}
        </div>
        <button
          onClick={handleSave}
          disabled={!hasChanges || saving}
          className="px-6 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-lg text-sm font-medium transition"
        >
          {saving ? 'Saving...' : selectedCompany !== null ? 'Save Company Override' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
}
