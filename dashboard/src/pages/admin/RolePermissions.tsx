import { useState, useEffect, useCallback, useMemo } from 'react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';

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
  { value: 'none', label: 'None', active: 'bg-gray-600 text-white' },
  { value: 'assigned', label: 'Vehicle', active: 'bg-amber-600 text-white' },
  { value: 'company', label: 'Company', active: 'bg-blue-600 text-white' },
  { value: 'all', label: 'All', active: 'bg-green-600 text-white' },
];

const PERM_GROUPS: PermGroup[] = [
  {
    title: 'Core',
    icon: '📊',
    flags: [
      { allKey: 'can_truck_all', ownKey: 'can_truck_own', label: 'Vehicles', scoped: true },
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
  const [data, setData] = useState<PermsData | null>(null);
  const [overridesData, setOverridesData] = useState<OverridesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState('');
  const [selectedRole, setSelectedRole] = useState('admin');
  const [selectedCompany, setSelectedCompany] = useState<number | null>(null);
  const [edits, setEdits] = useState<Record<string, boolean>>({});
  const [resetAllConfirm, setResetAllConfirm] = useState(false);
  const [deleteOverrideConfirm, setDeleteOverrideConfirm] = useState(false);

  const companies = overridesData?.companies ?? [];
  const hasCompanies = companies.length > 0;

  // Does the currently selected company+role have a DB override?
  const hasCompanyOverride = useMemo(() => {
    if (selectedCompany === null || !overridesData) return false;
    const roles = overridesData.overrides[String(selectedCompany)] ?? [];
    return roles.includes(selectedRole);
  }, [selectedCompany, selectedRole, overridesData]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [d, ov] = await Promise.all([
        apiJSON<PermsData>('/admin/permissions/roles'),
        apiJSON<OverridesData>('/admin/permissions/roles/overrides').catch(() => null),
      ]);
      setData(d);
      if (ov) setOverridesData(ov);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load permissions');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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

  if (loading) return <p className="text-gray-500">Loading permissions...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Role Permissions</h1>
        {!resetAllConfirm ? (
          <button
            onClick={() => setResetAllConfirm(true)}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 rounded transition"
          >
            Reset All Roles
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-xs text-red-400">Reset all to defaults?</span>
            <button
              onClick={handleResetAll}
              disabled={saving}
              className="px-3 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded text-xs font-medium transition"
            >
              Confirm
            </button>
            <button
              onClick={() => setResetAllConfirm(false)}
              className="px-3 py-1.5 text-xs text-gray-400 hover:text-white transition"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}
      {success && <p className="text-green-400 text-sm">{success}</p>}

      {/* Company selector */}
      {hasCompanies && (
        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-400">Company:</label>
          <select
            value={selectedCompany ?? ''}
            onChange={(e) => handleCompanyChange(e.target.value ? Number(e.target.value) : null)}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
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
                ? 'bg-yellow-500/20 text-yellow-400'
                : 'bg-gray-700/50 text-gray-500'
            }`}>
              {hasCompanyOverride ? 'Company Override' : 'Inherited'}
            </span>
          )}
        </div>
      )}

      {/* Role selector tabs */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1">
        {ROLES.map((role) => {
          const oc = overrideCount(role);
          return (
            <button
              key={role}
              onClick={() => handleRoleChange(role)}
              className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition relative ${
                selectedRole === role
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {ROLE_LABELS[role]}
              {oc > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-yellow-500 text-black text-[10px] rounded-full flex items-center justify-center font-bold">
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
            className="bg-gray-900 border border-gray-800 rounded-xl p-5"
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
                      className="flex items-center justify-between py-1 px-2 rounded hover:bg-gray-800/50"
                    >
                      <span className="text-sm flex items-center gap-2">
                        {flag.label}
                        {selectedCompany === null && scope !== defScope && !isChanged && (
                          <span className="text-[10px] text-yellow-500/70 uppercase tracking-wider">custom</span>
                        )}
                        {selectedCompany !== null && scope !== acctScope && !isChanged && (
                          <span className="text-[10px] text-blue-400/70 uppercase tracking-wider">override</span>
                        )}
                      </span>
                      <div className="flex rounded-lg overflow-hidden border border-gray-700">
                        {SCOPE_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => setScope(flag.allKey, flag.ownKey, opt.value)}
                            className={`px-2.5 py-1 text-xs font-medium transition ${
                              scope === opt.value ? opt.active : 'bg-gray-800 text-gray-500 hover:text-gray-300'
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
                    className="flex items-center justify-between py-1 px-2 rounded hover:bg-gray-800/50 cursor-pointer group"
                  >
                    <span className="text-sm flex items-center gap-2">
                      {flag.label}
                      {selectedCompany === null && enabled !== isDefault && !isChanged && (
                        <span className="text-[10px] text-yellow-500/70 uppercase tracking-wider">custom</span>
                      )}
                      {selectedCompany !== null && differsFromAccountWide && !isChanged && (
                        <span className="text-[10px] text-blue-400/70 uppercase tracking-wider">override</span>
                      )}
                    </span>
                    <div className="relative">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={enabled}
                        onChange={() => toggleFlag(flag.key)}
                      />
                      <div className="w-9 h-5 bg-gray-700 rounded-full peer-checked:bg-blue-600 transition-colors" />
                      <div className="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow peer-checked:translate-x-4 transition-transform" />
                    </div>
                  </label>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-xl p-4">
        <div className="flex items-center gap-3">
          {selectedCompany === null && isCustomized && (
            <button
              onClick={handleReset}
              disabled={saving}
              className="px-4 py-2 text-sm text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 rounded-lg transition disabled:opacity-50"
            >
              Reset {ROLE_LABELS[selectedRole]} to Defaults
            </button>
          )}
          {selectedCompany !== null && hasCompanyOverride && (
            !deleteOverrideConfirm ? (
              <button
                onClick={() => setDeleteOverrideConfirm(true)}
                disabled={saving}
                className="px-4 py-2 text-sm text-red-400 hover:text-red-300 border border-red-800 hover:border-red-600 rounded-lg transition disabled:opacity-50"
              >
                Remove Override
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-400">Remove company override?</span>
                <button
                  onClick={handleDeleteOverride}
                  disabled={saving}
                  className="px-3 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded text-xs font-medium transition"
                >
                  Confirm
                </button>
                <button
                  onClick={() => setDeleteOverrideConfirm(false)}
                  className="px-3 py-1.5 text-xs text-gray-400 hover:text-white transition"
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
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-medium transition"
        >
          {saving ? 'Saving...' : selectedCompany !== null ? 'Save Company Override' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
}
