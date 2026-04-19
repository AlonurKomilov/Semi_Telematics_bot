import { useState, useEffect, useCallback, useMemo } from 'react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import { useAuth } from '../../context/AuthContext';
import type { AdminUser, AnyColumn } from '../../types';

const ROLES = ['admin', 'fleet', 'safety', 'dispatcher', 'driver'] as const;

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatcher',
  driver: 'Driver',
};

const ROLE_COLORS: Record<string, string> = {
  owner: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  admin: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  fleet: 'bg-green-500/20 text-green-400 border-green-500/30',
  safety: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  dispatcher: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  driver: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

function RoleBadge({ role }: { role: string }) {
  const cls = ROLE_COLORS[role] || ROLE_COLORS.driver;
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {ROLE_LABELS[role] || role}
    </span>
  );
}

function UserAvatar({ userId, name, size = 48, active = true }: { userId: number; name: string; size?: number; active?: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let revoke = '';
    let cancelled = false;
    apiFetch(`/admin/users/${userId}/avatar`).then(res => {
      if (res.ok) return res.blob();
      return null;
    }).then(blob => {
      if (blob && !cancelled) {
        const url = URL.createObjectURL(blob);
        revoke = url;
        setSrc(url);
      }
    }).catch(() => {});
    return () => { cancelled = true; if (revoke) URL.revokeObjectURL(revoke); };
  }, [userId]);

  const parts = name.trim().split(/\s+/);
  const ini = parts.length >= 2
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : (parts[0]?.[0] || '?').toUpperCase();

  const px = `${size}px`;
  if (src) {
    return <img src={src} alt={name} className="rounded-full object-cover" style={{ width: px, height: px }} />;
  }
  return (
    <div className={`rounded-full flex items-center justify-center font-bold ${
      active ? 'bg-blue-600/20 text-blue-400' : 'bg-gray-700 text-gray-500'
    }`} style={{ width: px, height: px, fontSize: `${size * 0.375}px` }}>
      {ini}
    </div>
  );
}

interface FleetVehicle {
  name: string;
  company?: string;
}

const userColumns: AnyColumn[] = [
  { key: 'display_name', label: 'Name', sortable: true, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    const parts = u.display_name.trim().split(/\s+/);
    const ini = parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : (parts[0]?.[0] || '?').toUpperCase();
    return (
      <div className="flex items-center gap-2">
        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold ${
          u.is_active ? 'bg-blue-600/20 text-blue-400' : 'bg-gray-700 text-gray-500'
        }`}>{ini}</div>
        <span>{u.display_name}</span>
      </div>
    );
  }},
  { key: 'role', label: 'Role', sortable: true, render: (v) => <RoleBadge role={String(v)} /> },
  { key: 'department', label: 'Department', sortable: true },
  { key: 'trucks', label: 'Trucks', sortable: false, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    const trucks = u.trucks?.length ? u.trucks : u.truck_num ? [u.truck_num] : [];
    if (!trucks.length) return <span className="text-gray-500 text-xs">All</span>;
    if (trucks.length <= 2) return <span className="text-xs">{trucks.join(', ')}</span>;
    return (
      <span className="text-xs" title={trucks.join(', ')}>
        {trucks[0]}{' '}
        <span className="px-1.5 py-0.5 bg-gray-700 rounded-full text-[10px] text-gray-400">+{trucks.length - 1}</span>
      </span>
    );
  }},
  { key: 'allowed_companies', label: 'Companies', sortable: false, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    if (!u.allowed_companies?.length) return <span className="text-gray-500 text-xs">All</span>;
    return <span className="text-xs">{u.allowed_companies.join(', ')}</span>;
  }},
  { key: 'email', label: 'Email' },
  { key: 'is_active', label: 'Status', render: (v) => <StatusBadge status={v ? 'active' : 'inactive'} /> },
];

type DetailTab = 'profile' | 'access' | 'settings';

export default function Users() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('profile');
  const [roleFilter, setRoleFilter] = useState<string | null>(null);

  // Truck assignment
  const [editTrucks, setEditTrucks] = useState<string[]>([]);
  const [newTruck, setNewTruck] = useState('');
  const [savingTrucks, setSavingTrucks] = useState(false);
  const [fleetVehicles, setFleetVehicles] = useState<FleetVehicle[]>([]);

  // Company assignment
  const [allCompanies, setAllCompanies] = useState<{ id: number; code: string; display_name: string }[]>([]);
  const [editCompanyIds, setEditCompanyIds] = useState<number[]>([]);
  const [savingCompanies, setSavingCompanies] = useState(false);
  const [unrestricted, setUnrestricted] = useState(true);

  // Role change
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'role' | 'deactivate' | 'activate' | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiJSON<{ users: AdminUser[] }>('/admin/users');
      setUsers(data.users || []);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, []);

  // Load fleet vehicles for truck autocomplete
  const loadFleetVehicles = useCallback(async () => {
    try {
      const data = await apiJSON<{ vehicles: FleetVehicle[] }>('/fleet/vehicles');
      setFleetVehicles(data.vehicles || []);
    } catch { /* ignore - autocomplete just won't work */ }
  }, []);

  useEffect(() => { loadUsers(); loadFleetVehicles(); }, [loadUsers, loadFleetVehicles]);

  // Filtered users by role
  const filteredUsers = useMemo(() => {
    if (!roleFilter) return users;
    return users.filter(u => u.role === roleFilter);
  }, [users, roleFilter]);

  // Role counts for filter chips
  const roleCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    users.forEach(u => { counts[u.role] = (counts[u.role] || 0) + 1; });
    return counts;
  }, [users]);

  const handleRoleChange = async (userId: number, role: string) => {
    try {
      await apiJSON('/admin/users/' + userId + '/role', { method: 'PUT', body: { role } });
      setSuccess('Role updated');
      setConfirmAction(null);
      setPendingRole(null);
      await loadUsers();
      if (selected) {
        setSelected({ ...selected, role });
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  const handleToggleActive = async (userId: number, active: boolean) => {
    try {
      await apiJSON('/admin/users/' + userId + '/status', { method: 'PUT', body: { is_active: active } });
      setSuccess(active ? 'User activated' : 'User deactivated');
      setConfirmAction(null);
      await loadUsers();
      if (selected) setSelected({ ...selected, is_active: active });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  // Vehicles filtered by selected companies
  const companyFilteredVehicles = useMemo(() => {
    if (unrestricted) return fleetVehicles;
    if (editCompanyIds.length === 0) return [];
    const selectedCodes = new Set(
      allCompanies.filter(c => editCompanyIds.includes(c.id)).map(c => c.code.toUpperCase())
    );
    return fleetVehicles.filter(v => {
      const vCompany = (v.company || '').toUpperCase();
      return selectedCodes.has(vCompany);
    });
  }, [fleetVehicles, unrestricted, editCompanyIds, allCompanies]);

  // Sync editTrucks when selecting a user
  useEffect(() => {
    if (selected) {
      const trucks = selected.trucks?.length ? [...selected.trucks] : selected.truck_num ? [selected.truck_num] : [];
      setEditTrucks(trucks);
      setNewTruck('');
      setDetailTab('profile');
      setConfirmAction(null);
      setPendingRole(null);
      setSuccess('');
      // Load company assignments
      apiJSON<{ companies: { company_id: number }[]; all_companies: { id: number; code: string; display_name: string }[]; unrestricted: boolean }>(
        '/admin/users/' + selected.id + '/companies'
      ).then(data => {
        setAllCompanies(data.all_companies || []);
        setEditCompanyIds(data.companies?.map(a => a.company_id) || []);
        setUnrestricted(data.unrestricted);
      }).catch(() => { setAllCompanies([]); setEditCompanyIds([]); setUnrestricted(true); });
    }
  }, [selected]);

  const handleSaveAccess = async (userId: number) => {
    setSavingCompanies(true);
    setSavingTrucks(true);
    try {
      await apiJSON('/admin/users/' + userId + '/companies', {
        method: 'PUT',
        body: { company_ids: unrestricted ? [] : editCompanyIds },
      });
      const uniqueTrucks = [...new Set(editTrucks)];
      await apiJSON('/admin/users/' + userId + '/trucks', { method: 'PUT', body: { trucks: uniqueTrucks } });
      setSuccess('Access saved');
      await loadUsers();
      if (selected) setSelected({ ...selected, trucks: uniqueTrucks });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSavingCompanies(false); setSavingTrucks(false); }
  };

  const toggleCompany = (id: number) => {
    setEditCompanyIds(prev => prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id]);
    setUnrestricted(false);
  };

  const activeCount = users.filter(u => u.is_active).length;

  // Clear success messages after 3s
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(''), 3000);
    return () => clearTimeout(t);
  }, [success]);

  const initials = (name: string) => {
    const parts = name.trim().split(/\s+/);
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : (parts[0]?.[0] || '?').toUpperCase();
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Team Management</h1>
        <span className="text-sm text-gray-400">{activeCount} active / {users.length} total</span>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
      {success && <p className="text-green-400 text-sm mb-3">{success}</p>}

      <div className="flex items-center gap-3 mb-4">
        {/* Role filter chips */}
        <div className="flex gap-1.5">
          <button
            onClick={() => setRoleFilter(null)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition ${
              !roleFilter ? 'bg-gray-600 text-white' : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            All
          </button>
          {Object.entries(ROLE_LABELS).map(([key, label]) => {
            const count = roleCounts[key] || 0;
            if (!count) return null;
            return (
              <button
                key={key}
                onClick={() => setRoleFilter(roleFilter === key ? null : key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition border ${
                  roleFilter === key ? ROLE_COLORS[key] : 'border-transparent text-gray-500 hover:text-gray-300'
                }`}
              >
                {label} <span className="opacity-60">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {loading && users.length === 0 ? <p className="text-gray-500">Loading...</p> : (
        <>
          <DataTable columns={userColumns} data={filteredUsers as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => setSelected(row as unknown as AdminUser)} />
          {selected && (
            <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
              <div className="w-[480px] bg-gray-900 border-l border-gray-800 overflow-y-auto" onClick={e => e.stopPropagation()}>
                {/* Header with avatar */}
                <div className="p-6 pb-4 border-b border-gray-800">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <UserAvatar userId={selected.id} name={selected.display_name} size={48} active={selected.is_active} />
                      <div>
                        <h2 className="text-lg font-semibold">{selected.display_name}</h2>
                        <div className="flex items-center gap-2 mt-0.5">
                          <RoleBadge role={selected.role} />
                          <span className="text-xs text-gray-500">{selected.department}</span>
                        </div>
                      </div>
                    </div>
                    <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white p-1">✕</button>
                  </div>

                  {/* Detail tabs */}
                  <div className="flex gap-1 bg-gray-800/50 rounded-lg p-0.5">
                    {([
                      { key: 'profile' as DetailTab, label: 'Profile', icon: '👤' },
                      { key: 'access' as DetailTab, label: 'Access', icon: '🔐' },
                      { key: 'settings' as DetailTab, label: 'Settings', icon: '⚙️' },
                    ]).map(t => (
                      <button
                        key={t.key}
                        onClick={() => { setDetailTab(t.key); setConfirmAction(null); }}
                        className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition ${
                          detailTab === t.key ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'
                        }`}
                      >
                        {t.icon} {t.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="p-6">
                  {/* ─── PROFILE TAB ─── */}
                  {detailTab === 'profile' && (
                    <div className="space-y-4">
                      <dl className="space-y-3 text-sm">
                        <Row label="Email" value={selected.email || '—'} />
                        <Row label="Telegram ID" value={String(selected.telegram_id)} />
                        <Row label="Language" value={(selected.language || '—').toUpperCase()} />
                        <Row label="Status" value={selected.is_active ? 'Active' : 'Inactive'} status={selected.is_active ? 'green' : 'red'} />
                      </dl>
                      {/* Quick summary */}
                      <div className="grid grid-cols-2 gap-3 mt-4">
                        <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                          <div className="text-lg font-bold text-blue-400">{editTrucks.length}</div>
                          <div className="text-xs text-gray-500">Trucks Assigned</div>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                          <div className="text-lg font-bold text-blue-400">
                            {unrestricted ? 'All' : editCompanyIds.length}
                          </div>
                          <div className="text-xs text-gray-500">Companies</div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* ─── ACCESS TAB ─── */}
                  {detailTab === 'access' && (
                    <div className="space-y-6">
                      {/* ── Step 1: Company Access ── */}
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">Step 1</span>
                          <div className="flex-1 border-t border-gray-800" />
                        </div>
                        <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
                          🏢 Company Access
                          {!unrestricted && editCompanyIds.length > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400">{editCompanyIds.length}</span>
                          )}
                        </h3>

                        {/* Unrestricted toggle */}
                        <div
                          onClick={() => { setUnrestricted(!unrestricted); if (!unrestricted) setEditCompanyIds([]); }}
                          className={`flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition mb-3 border ${
                            unrestricted
                              ? 'bg-green-500/10 border-green-500/30'
                              : 'bg-gray-800/50 border-gray-700 hover:border-gray-600'
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-sm">{unrestricted ? '🌐' : '🔒'}</span>
                            <span className="text-sm font-medium">{unrestricted ? 'All Companies' : 'Selected Companies Only'}</span>
                          </div>
                          <div className="relative">
                            <div className={`w-9 h-5 rounded-full transition ${unrestricted ? 'bg-green-600' : 'bg-gray-700'}`} />
                            <div className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${unrestricted ? 'translate-x-4' : ''}`} />
                          </div>
                        </div>

                        {unrestricted && (
                          <p className="text-xs text-gray-500 mb-3">This user can access data from all companies.</p>
                        )}

                        {!unrestricted && allCompanies.length > 0 && (
                          <>
                            <div className="flex items-center justify-between mb-2">
                              <p className="text-xs text-gray-500">Select which companies this user can access:</p>
                              <button
                                type="button"
                                onClick={() => {
                                  if (editCompanyIds.length === allCompanies.length) setEditCompanyIds([]);
                                  else setEditCompanyIds(allCompanies.map(c => c.id));
                                }}
                                className="text-[10px] text-blue-400 hover:text-blue-300 uppercase tracking-wider"
                              >
                                {editCompanyIds.length === allCompanies.length ? 'Deselect All' : 'Select All'}
                              </button>
                            </div>
                            <div className="space-y-1.5 mb-3">
                              {allCompanies.map(c => {
                                const checked = editCompanyIds.includes(c.id);
                                return (
                                  <div
                                    key={c.id}
                                    onClick={() => toggleCompany(c.id)}
                                    className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                      checked
                                        ? 'bg-blue-500/10 border-blue-500/30'
                                        : 'bg-gray-800/30 border-gray-800 hover:border-gray-700'
                                    }`}
                                  >
                                    <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                      checked ? 'bg-blue-600 border-blue-600' : 'border-gray-600'
                                    }`}>
                                      {checked && <span className="text-white text-[10px]">✓</span>}
                                    </div>
                                    <div className="flex-1">
                                      <span className="text-sm font-medium">{c.code}</span>
                                      {c.display_name && <span className="text-xs text-gray-500 ml-2">{c.display_name}</span>}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </>
                        )}

                        {!unrestricted && allCompanies.length === 0 && (
                          <p className="text-xs text-gray-500 italic py-4 text-center bg-gray-800/30 rounded-lg">No companies configured</p>
                        )}
                      </div>

                      {/* ── Step 2: Vehicle Assignments ── */}
                      <div className="border-t border-gray-800 pt-5">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">Step 2</span>
                          <div className="flex-1 border-t border-gray-800" />
                        </div>
                        <h3 className="text-sm font-semibold text-gray-300 mb-1 flex items-center gap-2">
                          🚛 Vehicle Assignments
                          {editTrucks.length > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400">{editTrucks.length}</span>
                          )}
                        </h3>
                        <p className="text-xs text-gray-500 mb-3">
                          {unrestricted
                            ? 'Select which vehicles this user can access:'
                            : editCompanyIds.length > 0
                            ? `Showing ${companyFilteredVehicles.length} vehicle${companyFilteredVehicles.length !== 1 ? 's' : ''} from selected compan${editCompanyIds.length > 1 ? 'ies' : 'y'}:`
                            : 'Select companies above to see available vehicles.'
                          }
                        </p>

                        {/* No companies selected warning */}
                        {!unrestricted && editCompanyIds.length === 0 && (
                          <div className="py-6 text-center bg-gray-800/30 rounded-lg mb-3">
                            <p className="text-xs text-amber-400/70">⚠ Select at least one company above to assign vehicles</p>
                          </div>
                        )}

                        {/* Vehicle checkbox list (like companies) */}
                        {(unrestricted || editCompanyIds.length > 0) && companyFilteredVehicles.length > 0 && (
                          <>
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-2">
                                {editTrucks.length === 0 ? (
                                  <span className="text-xs text-gray-500 italic">No vehicles assigned — sees all</span>
                                ) : (
                                  <span className="text-xs text-gray-500">{editTrucks.length} selected</span>
                                )}
                              </div>
                              <button
                                type="button"
                                onClick={() => {
                                  if (editTrucks.length === companyFilteredVehicles.length) setEditTrucks([]);
                                  else setEditTrucks(companyFilteredVehicles.map(v => v.name));
                                }}
                                className="text-[10px] text-blue-400 hover:text-blue-300 uppercase tracking-wider"
                              >
                                {editTrucks.length === companyFilteredVehicles.length ? 'Deselect All' : 'Select All'}
                              </button>
                            </div>

                            {/* Search filter */}
                            <input
                              value={newTruck}
                              onChange={e => setNewTruck(e.target.value)}
                              placeholder="Search vehicles..."
                              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500 mb-2"
                            />

                            <div className="space-y-1.5 mb-3 max-h-64 overflow-y-auto">
                              {companyFilteredVehicles
                                .filter(v => !newTruck.trim() || v.name.toLowerCase().includes(newTruck.toLowerCase()))
                                .map(v => {
                                  const checked = editTrucks.includes(v.name);
                                  return (
                                    <div
                                      key={v.name}
                                      onClick={() => {
                                        if (checked) setEditTrucks(editTrucks.filter(t => t !== v.name));
                                        else setEditTrucks([...editTrucks, v.name]);
                                      }}
                                      className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                        checked
                                          ? 'bg-amber-500/10 border-amber-500/30'
                                          : 'bg-gray-800/30 border-gray-800 hover:border-gray-700'
                                      }`}
                                    >
                                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                        checked ? 'bg-amber-600 border-amber-600' : 'border-gray-600'
                                      }`}>
                                        {checked && <span className="text-white text-[10px]">✓</span>}
                                      </div>
                                      <div className="flex-1">
                                        <span className="text-sm font-medium">{v.name}</span>
                                        {v.company && <span className="text-xs text-gray-500 ml-2">{v.company}</span>}
                                      </div>
                                      {checked && editTrucks.indexOf(v.name) === 0 && selected.role === 'driver' && (
                                        <span className="text-[10px] text-blue-400 uppercase tracking-wider">primary</span>
                                      )}
                                    </div>
                                  );
                                })}
                            </div>
                          </>
                        )}

                        {(unrestricted || editCompanyIds.length > 0) && companyFilteredVehicles.length === 0 && (
                          <p className="text-xs text-gray-500 italic py-4 text-center bg-gray-800/30 rounded-lg mb-3">No vehicles found</p>
                        )}
                      </div>

                      {/* Single save button for companies + vehicles */}
                      <button
                        onClick={() => handleSaveAccess(selected.id)}
                        disabled={savingCompanies || savingTrucks}
                        className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-semibold transition"
                      >
                        {savingCompanies || savingTrucks ? 'Saving...' : 'Save Access'}
                      </button>
                    </div>
                  )}

                  {/* ─── SETTINGS TAB ─── */}
                  {detailTab === 'settings' && (
                    <div className="space-y-6">
                      {/* Role change */}
                      <div>
                        <h3 className="text-sm font-semibold text-gray-300 mb-3">Change Role</h3>
                        <div className="grid grid-cols-2 gap-2">
                          {ROLES.map(r => {
                            const isCurrent = selected.role === r;
                            const isPending = pendingRole === r;
                            return (
                              <button
                                key={r}
                                onClick={() => { if (!isCurrent) { setPendingRole(r); setConfirmAction('role'); } }}
                                className={`px-3 py-2 rounded-lg text-sm font-medium transition border ${
                                  isCurrent
                                    ? `${ROLE_COLORS[r]} border-current`
                                    : isPending
                                    ? 'bg-yellow-500/10 border-yellow-500/50 text-yellow-400'
                                    : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-300'
                                }`}
                              >
                                {ROLE_LABELS[r]}
                                {isCurrent && <span className="ml-1 text-[10px] opacity-60">current</span>}
                              </button>
                            );
                          })}
                        </div>
                        {confirmAction === 'role' && pendingRole && (
                          <div className="mt-3 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                            <p className="text-sm text-yellow-400 mb-2">
                              Change {selected.display_name}'s role to <strong>{ROLE_LABELS[pendingRole]}</strong>?
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleRoleChange(selected.id, pendingRole)}
                                className="px-4 py-1.5 bg-yellow-600 hover:bg-yellow-700 rounded text-xs font-medium transition"
                              >
                                Confirm
                              </button>
                              <button
                                onClick={() => { setConfirmAction(null); setPendingRole(null); }}
                                className="px-4 py-1.5 text-xs text-gray-400 hover:text-white transition"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Danger zone */}
                      <div className="border-t border-gray-800 pt-5">
                        <h3 className="text-sm font-semibold text-red-400/80 mb-3">Danger Zone</h3>
                        {confirmAction !== 'deactivate' && confirmAction !== 'activate' ? (
                          <button
                            onClick={() => setConfirmAction(selected.is_active ? 'deactivate' : 'activate')}
                            className={`w-full py-2.5 rounded-lg text-sm font-medium transition border ${
                              selected.is_active
                                ? 'border-red-800 text-red-400 hover:bg-red-500/10'
                                : 'border-green-800 text-green-400 hover:bg-green-500/10'
                            }`}
                          >
                            {selected.is_active ? 'Deactivate User' : 'Activate User'}
                          </button>
                        ) : (
                          <div className={`p-3 rounded-lg border ${
                            selected.is_active ? 'bg-red-500/10 border-red-500/30' : 'bg-green-500/10 border-green-500/30'
                          }`}>
                            <p className="text-sm mb-2">
                              {selected.is_active
                                ? <span className="text-red-400">Deactivate <strong>{selected.display_name}</strong>? They will lose access immediately.</span>
                                : <span className="text-green-400">Activate <strong>{selected.display_name}</strong>? They will regain access.</span>
                              }
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleToggleActive(selected.id, !selected.is_active)}
                                className={`px-4 py-1.5 rounded text-xs font-medium transition ${
                                  selected.is_active ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700'
                                }`}
                              >
                                {selected.is_active ? 'Deactivate' : 'Activate'}
                              </button>
                              <button
                                onClick={() => setConfirmAction(null)}
                                className="px-4 py-1.5 text-xs text-gray-400 hover:text-white transition"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Row({ label, value, status }: { label: string; value: string; status?: 'green' | 'red' }) {
  return (
    <div className="flex justify-between items-center">
      <dt className="text-gray-400">{label}</dt>
      <dd className={status === 'green' ? 'text-green-400' : status === 'red' ? 'text-red-400' : ''}>
        {value}
      </dd>
    </div>
  );
}
