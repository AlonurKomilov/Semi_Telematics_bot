import { useState, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { PageHeader, CardSkeleton } from '../../components/shell';

const ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'driver'] as const;

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet Manager',
  safety: 'Safety Manager',
  dispatcher: 'Dispatcher',
  driver: 'Driver',
};

type ScopeValue = 'all' | 'company' | 'assigned' | 'none';

interface ScopedFlag {
  allKey: string;
  ownKey: string;
  label: string;
  scoped: true;
}

interface SimpleFlag {
  key: string;
  label: string;
  scoped?: false;
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

const PERM_GROUPS: PermGroup[] = [
  {
    title: 'Core',
    icon: '📊',
    flags: [
      { allKey: 'can_vehicle_all', ownKey: 'can_vehicle_own', label: 'Vehicles', scoped: true },
      { allKey: 'can_alerts_all', ownKey: 'can_alerts_own', label: 'Alerts', scoped: true },
      { allKey: 'can_geofence_all', ownKey: 'can_geofence_own', label: 'Geofences', scoped: true },
      { key: 'can_digest', label: 'Report Subscriptions' },
    ],
  },
  {
    title: 'Fleet',
    icon: '🚛',
    flags: [
      { key: 'can_faults', label: 'Faults Report' },
      { key: 'can_critical', label: 'Critical Faults' },
      { key: 'can_health', label: 'Health Report' },
      { key: 'can_efficiency', label: 'Efficiency Report' },
      { key: 'can_fuel', label: 'Fuel Report' },
      { allKey: 'can_maintenance_all', ownKey: 'can_maintenance_own', label: 'Maintenance', scoped: true },
      { key: 'can_rolling_stopped', label: 'Rolling / Stopped' },
    ],
  },
  {
    title: 'Dispatch',
    icon: '🗺️',
    flags: [
      { allKey: 'can_location_map', ownKey: 'can_location_own', label: 'Live Map', scoped: true },
      { allKey: 'can_route_all', ownKey: 'can_route_own', label: 'Routes', scoped: true },
    ],
  },
  {
    title: 'Safety & Compliance',
    icon: '🛡️',
    flags: [
      { allKey: 'can_scorecard_all', ownKey: 'can_scorecard_own', label: 'Scorecards', scoped: true },
      { allKey: 'can_events_all', ownKey: 'can_events_own', label: 'Safety Events', scoped: true },
      { allKey: 'can_risk_report_all', ownKey: 'can_risk_report_own', label: 'Stakeholder Risk Summary', scoped: true },
      { key: 'can_fuel_cost', label: 'Fuel Costs' },
      { key: 'can_cost_per_mile', label: 'Cost per Mile' },
    ],
  },
  {
    title: 'Admin',
    icon: '👥',
    flags: [
      { key: 'can_manage_account', label: 'Account Settings' },
      { key: 'can_manage_users', label: 'Manage Users' },
      { key: 'can_manage_companies', label: 'Manage Companies' },
      { key: 'can_invite', label: 'Send Invites' },
    ],
  },
  {
    title: 'Payroll',
    icon: '💵',
    flags: [
      { key: 'can_payroll_admin', label: 'Manage Bonus Rules & Runs' },
      { key: 'can_payroll_view_own', label: 'View Own Paystubs' },
    ],
  },
  {
    title: 'Coaching',
    icon: '🎓',
    flags: [
      { key: 'can_coaching_admin', label: 'Manage Coaching Rules & Assignments' },
      { key: 'can_coaching_view_own', label: 'View & Acknowledge Own Coaching' },
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
  const { refreshPermissions } = useRoleView();
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
          title="Role Permissions"
          description="Decide what each role is allowed to see and do. Changes take effect on the next page load for active users."
        />
        <CardSkeleton height="h-96" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Shield}
        title="Role Permissions"
        description="Decide what each role is allowed to see and do. Toggle a permission to change it for everyone in that role; per-company overrides are also supported."
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
            <span className="text-xs text-red-600 dark:text-red-400">Reset all to defaults?</span>
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
      {success && <p className="text-green-600 dark:text-green-400 text-sm">{success}</p>}

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
            <span className={`text-xs px-2 py-0.5 rounded ${
              hasCompanyOverride
                ? 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400'
                : 'bg-muted/50 text-muted-foreground'
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
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-yellow-500 text-background text-[10px] rounded-full flex items-center justify-center font-bold">
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
                      <span className="text-sm flex items-center gap-2">
                        {flag.label}
                        {selectedCompany === null && scope !== defScope && !isChanged && (
                          <span className="text-[10px] text-yellow-500/70 uppercase tracking-wider">custom</span>
                        )}
                        {selectedCompany !== null && scope !== acctScope && !isChanged && (
                          <span className="text-[10px] text-primary/70 uppercase tracking-wider">override</span>
                        )}
                      </span>
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
                    className="flex items-center justify-between py-1 px-2 rounded hover:bg-muted/50 cursor-pointer group"
                  >
                    <span className="text-sm flex items-center gap-2">
                      {flag.label}
                      {selectedCompany === null && enabled !== isDefault && !isChanged && (
                        <span className="text-[10px] text-yellow-500/70 uppercase tracking-wider">custom</span>
                      )}
                      {selectedCompany !== null && differsFromAccountWide && !isChanged && (
                        <span className="text-[10px] text-primary/70 uppercase tracking-wider">override</span>
                      )}
                    </span>
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
                className="px-4 py-2 text-sm text-destructive hover:text-destructive/80 border border-destructive/40 hover:border-red-600 rounded-lg transition disabled:opacity-50"
              >
                Remove Override
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-600 dark:text-red-400">Remove company override?</span>
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
