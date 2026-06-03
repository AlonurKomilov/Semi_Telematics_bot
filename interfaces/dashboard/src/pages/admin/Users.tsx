import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Users as UsersIcon, X, Truck } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
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
  owner: 'bg-purple-500/20 text-purple-700 dark:text-purple-400 border-purple-500/30',
  admin: 'bg-primary/15 text-primary border-primary/30',
  fleet: 'bg-green-500/20 text-green-700 dark:text-green-400 border-green-500/30',
  safety: 'bg-amber-500/20 text-amber-700 dark:text-amber-400 border-amber-500/30',
  dispatcher: 'bg-cyan-500/20 text-cyan-700 dark:text-cyan-400 border-cyan-500/30',
  driver: 'bg-gray-500/20 text-muted-foreground border-gray-500/30',
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
      active ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
    }`} style={{ width: px, height: px, fontSize: `${size * 0.375}px` }}>
      {ini}
    </div>
  );
}

interface FleetVehicle {
  name: string;
  company?: string;
}

// Identity badges: a small visual cue per row showing which sign-in
// methods the user has attached.  Helps admins spot e.g. "this driver
// has email but never opened the bot, that's why they're not getting
// alerts."
function IdentityBadges({ u }: { u: AdminUser }) {
  const hasEmail = Boolean(u.email);
  const hasTelegram = Boolean(u.telegram_id);
  return (
    <span className="inline-flex items-center gap-1 ml-1">
      <span
        title={hasEmail ? `Email: ${u.email ?? ''}` : 'No email — admins can add one in the detail panel'}
        className={`inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-bold ${
          hasEmail
            ? 'bg-primary/15 text-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        @
      </span>
      <span
        title={
          hasTelegram
            ? `Telegram linked (tg:${u.telegram_id})`
            : 'Telegram not linked yet — send the bot deep-link to this user'
        }
        className={`inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-bold ${
          hasTelegram
            ? 'bg-primary/15 text-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        TG
      </span>
    </span>
  );
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
          u.is_active ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
        }`}>{ini}</div>
        <span className="flex items-center">
          {u.display_name}
          <IdentityBadges u={u} />
        </span>
      </div>
    );
  }},
  { key: 'role', label: 'Role', sortable: true, render: (v) => <RoleBadge role={String(v)} /> },
  { key: 'department', label: 'Department', sortable: true },
  { key: 'vehicles', label: 'Trucks', sortable: false, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    const trucks = u.trucks?.length ? u.trucks : u.truck_num ? [u.truck_num] : [];
    if (!trucks.length) return <span className="text-muted-foreground text-xs">All</span>;
    if (trucks.length <= 2) return <span className="text-xs">{trucks.join(', ')}</span>;
    return (
      <span className="text-xs" title={trucks.join(', ')}>
        {trucks[0]}{' '}
        <span className="px-1.5 py-0.5 bg-muted rounded-full text-[10px] text-muted-foreground">+{trucks.length - 1}</span>
      </span>
    );
  }},
  { key: 'allowed_companies', label: 'Companies', sortable: false, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    if (!u.allowed_companies?.length) return <span className="text-muted-foreground text-xs">All</span>;
    return <span className="text-xs">{u.allowed_companies.join(', ')}</span>;
  }},
  { key: 'email', label: 'Email' },
  { key: 'is_active', label: 'Status', render: (v) => <StatusBadge status={v ? 'active' : 'inactive'} /> },
];

type DetailTab = 'profile' | 'access' | 'settings';

export default function Users() {
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('profile');
  const [roleFilter, setRoleFilter] = useState<string | null>(null);

  // Truck assignment
  const [editVehicles, setEditTrucks] = useState<string[]>([]);
  const [newVehicle, setNewTruck] = useState('');
  const [savingVehicles, setSavingTrucks] = useState(false);

  // Company assignment
  const [allCompanies, setAllCompanies] = useState<{ id: number; code: string; display_name: string }[]>([]);
  const [editCompanyIds, setEditCompanyIds] = useState<number[]>([]);
  // Snapshot of assignments as loaded from the server — compared against
  // editCompanyIds to detect when a driver's company is actually changing
  // (single-company constraint means the change triggers the archive flow
  // on the backend, so we surface a confirmation before save).
  const [initialCompanyIds, setInitialCompanyIds] = useState<number[]>([]);
  const [savingCompanies, setSavingCompanies] = useState(false);
  const [unrestricted, setUnrestricted] = useState(true);

  // Drivers are restricted to one company at a time (backend enforces;
  // the UI mirrors that with a single-select instead of a checkbox list).
  const isDriver = selected?.role === 'driver';

  // Role change
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'role' | 'deactivate' | 'activate' | null>(null);

  // React Query: cached across navigations, deduped, no manual loading state.
  const { data: usersData, isLoading: loading, error: usersError } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => apiJSON<{ users: AdminUser[] }>('/admin/users'),
  });
  const users = useMemo(() => usersData?.users ?? [], [usersData]);
  useEffect(() => {
    if (usersError) setError(usersError instanceof Error ? usersError.message : 'Failed');
  }, [usersError]);

  // Truck autocomplete — cached for 60s as configured globally; harmless if it fails.
  const { data: fleetData } = useQuery({
    queryKey: ['admin-users-fleet-vehicles'],
    queryFn: () => apiJSON<{ vehicles: FleetVehicle[] }>('/vehicles'),
  });
  const fleetVehicles = fleetData?.vehicles ?? [];

  const loadUsers = useCallback(
    () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    [qc],
  );

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

  // Sync editVehicles when selecting a user
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
        const ids = data.companies?.map(a => a.company_id) || [];
        setEditCompanyIds(ids);
        setInitialCompanyIds(ids);
        setUnrestricted(data.unrestricted);
      }).catch(() => { setAllCompanies([]); setEditCompanyIds([]); setInitialCompanyIds([]); setUnrestricted(true); });
    }
  }, [selected]);

  const handleSaveAccess = async (userId: number) => {
    // Driver reassignment triggers the backend archive flow that moves
    // their existing CDL/medical docs into `{old_company}/drivers/_archive/`.
    // Warn the operator before that happens — it's not destructive (files
    // remain in Drive, just under a dated archive folder), but the path
    // change is permanent and worth a deliberate confirmation.
    if (isDriver && initialCompanyIds.length > 0) {
      const targetIds = unrestricted ? [] : editCompanyIds;
      const sameSet =
        targetIds.length === initialCompanyIds.length &&
        targetIds.every(id => initialCompanyIds.includes(id));
      if (!sameSet) {
        const oldCodes = allCompanies
          .filter(c => initialCompanyIds.includes(c.id))
          .map(c => c.code)
          .join(', ');
        if (!window.confirm(
          `This driver's company will change. Their previous documents ` +
          `under "${oldCodes}" will be archived to ` +
          `\`{company}/drivers/_archive/{today}/\` automatically.\n\n` +
          `Continue?`
        )) {
          return;
        }
      }
    }
    setSavingCompanies(true);
    setSavingTrucks(true);
    try {
      const res = await apiJSON<{ archived_companies?: string[] }>(
        '/admin/users/' + userId + '/companies',
        {
          method: 'PUT',
          body: { company_ids: unrestricted ? [] : editCompanyIds },
        },
      );
      const uniqueTrucks = [...new Set(editVehicles)];
      await apiJSON('/admin/users/' + userId + '/trucks', { method: 'PUT', body: { trucks: uniqueTrucks } });
      const archived = res.archived_companies || [];
      setSuccess(
        archived.length
          ? `Access saved. Archived docs from: ${archived.join(', ')}`
          : 'Access saved',
      );
      // Refresh the snapshot so a second save without re-loading the user
      // doesn't re-trigger the confirm or replay the archive flow.
      setInitialCompanyIds(unrestricted ? [] : [...editCompanyIds]);
      await loadUsers();
      if (selected) setSelected({ ...selected, trucks: uniqueTrucks });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSavingCompanies(false); setSavingTrucks(false); }
  };

  const toggleCompany = (id: number) => {
    // Drivers can only sit in one company at a time — clicking another
    // option replaces the current one rather than adding to a set.  For
    // every other role the picker is multi-select (admin/dispatcher
    // managing multiple companies legitimately need broad access).
    if (isDriver) {
      setEditCompanyIds(prev => (prev.length === 1 && prev[0] === id) ? prev : [id]);
      setUnrestricted(false);
      return;
    }
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
      <PageHeader
        icon={UsersIcon}
        title={t('pages.team_title')}
        description={t('pages.team_desc')}
        meta={
          <span className="text-sm text-muted-foreground">{activeCount} active / {users.length} total</span>
        }
      />

      {error && (
        <div className="mb-3"><ErrorState message={error} /></div>
      )}
      {success && <p className="text-green-600 dark:text-green-400 text-sm mb-3">{success}</p>}

      <div className="flex items-center gap-3 mb-4">
        {/* Role filter chips */}
        <div className="flex gap-1.5">
          <button
            onClick={() => setRoleFilter(null)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition ${
              !roleFilter ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground/80'
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
                  roleFilter === key ? ROLE_COLORS[key] : 'border-transparent text-muted-foreground hover:text-foreground/80'
                }`}
              >
                {label} <span className="opacity-60">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {loading && users.length === 0 ? <TableSkeleton rows={6} cols={5} /> : filteredUsers.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title={roleFilter ? `No ${ROLE_LABELS[roleFilter]} users` : 'No team members yet'}
          description={
            roleFilter
              ? 'Try a different role filter.'
              : 'Invite teammates from the Invites page to get started.'
          }
        />
      ) : (
        <>
          <DataTable columns={userColumns} data={filteredUsers as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => setSelected(row as unknown as AdminUser)} />
          {selected && (
            <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
              <div className="w-[480px] bg-card border-l border-border overflow-y-auto" onClick={e => e.stopPropagation()}>
                {/* Header with avatar */}
                <div className="p-6 pb-4 border-b border-border">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <UserAvatar userId={selected.id} name={selected.display_name} size={48} active={selected.is_active} />
                      <div>
                        <h2 className="text-lg font-semibold">{selected.display_name}</h2>
                        <div className="flex items-center gap-2 mt-0.5">
                          <RoleBadge role={selected.role} />
                          <span className="text-xs text-muted-foreground">{selected.department}</span>
                        </div>
                      </div>
                    </div>
                    <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X size={16} /></button>
                  </div>

                  {/* Detail tabs */}
                  <div className="flex gap-1 bg-muted/50 rounded-lg p-0.5">
                    {([
                      { key: 'profile' as DetailTab, label: 'Profile', icon: '👤' },
                      { key: 'access' as DetailTab, label: 'Access', icon: '🔐' },
                      { key: 'settings' as DetailTab, label: 'Settings', icon: '⚙️' },
                    ]).map(t => (
                      <button
                        key={t.key}
                        onClick={() => { setDetailTab(t.key); setConfirmAction(null); }}
                        className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition ${
                          detailTab === t.key ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground/80'
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
                        <Row label="Telegram ID" value={selected.telegram_id ? String(selected.telegram_id) : 'Not linked'} />
                        <Row label="Language" value={(selected.language || '—').toUpperCase()} />
                        <Row label="Status" value={selected.is_active ? 'Active' : 'Inactive'} status={selected.is_active ? 'green' : 'red'} />
                      </dl>
                      {/* Quick summary */}
                      <div className="grid grid-cols-2 gap-3 mt-4">
                        <div className="bg-muted/50 rounded-lg p-3 text-center">
                          <div className="text-lg font-bold text-primary">{editVehicles.length}</div>
                          <div className="text-xs text-muted-foreground">Trucks Assigned</div>
                        </div>
                        <div className="bg-muted/50 rounded-lg p-3 text-center">
                          <div className="text-lg font-bold text-primary truncate">
                            {isDriver
                              ? (allCompanies.find(c => c.id === editCompanyIds[0])?.code || '—')
                              : (unrestricted ? 'All' : editCompanyIds.length)}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {isDriver ? 'Company' : 'Companies'}
                          </div>
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
                          <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider">{t('forms.step_label', { n: 1 })}</span>
                          <div className="flex-1 border-t border-border" />
                        </div>
                        <h3 className="text-sm font-semibold text-foreground/80 mb-3 flex items-center gap-2">
                          🏢 {isDriver ? 'Assigned Company' : 'Company Access'}
                          {!unrestricted && editCompanyIds.length > 0 && !isDriver && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/15 text-primary">{editCompanyIds.length}</span>
                          )}
                        </h3>

                        {/* Driver-only callout: single-company constraint + archive behaviour */}
                        {isDriver && (
                          <div className="mb-3 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300">
                            <p className="font-medium mb-0.5">One company at a time.</p>
                            <p>Changing this driver's company will archive their previous CDL / medical / DQF documents to <code className="font-mono text-[10px]">{'{old company}'}/drivers/_archive/{'{today}'}/</code>.</p>
                          </div>
                        )}

                        {/* Unrestricted toggle — non-driver roles only.
                            Drivers must pick exactly one company; an
                            "all companies" mode would defeat the
                            single-company constraint. */}
                        {!isDriver && (
                          <>
                            <div
                              onClick={() => { setUnrestricted(!unrestricted); if (!unrestricted) setEditCompanyIds([]); }}
                              className={`flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition mb-3 border ${
                                unrestricted
                                  ? 'bg-green-500/10 border-green-500/30'
                                  : 'bg-muted/50 border-border hover:border-border'
                              }`}
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-sm">{unrestricted ? '🌐' : '🔒'}</span>
                                <span className="text-sm font-medium">{unrestricted ? 'All Companies' : 'Selected Companies Only'}</span>
                              </div>
                              <div className="relative">
                                <div className={`w-9 h-5 rounded-full transition ${unrestricted ? 'bg-green-600' : 'bg-muted'}`} />
                                <div className={`absolute top-0.5 left-0.5 w-4 h-4 bg-background rounded-full shadow transition-transform ${unrestricted ? 'translate-x-4' : ''}`} />
                              </div>
                            </div>

                            {unrestricted && (
                              <p className="text-xs text-muted-foreground mb-3">This user can access data from all companies.</p>
                            )}
                          </>
                        )}

                        {(!unrestricted || isDriver) && allCompanies.length > 0 && (
                          <>
                            <div className="flex items-center justify-between mb-2">
                              <p className="text-xs text-muted-foreground">
                                {isDriver ? 'Pick the company this driver works for:' : 'Select which companies this user can access:'}
                              </p>
                              {!isDriver && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (editCompanyIds.length === allCompanies.length) setEditCompanyIds([]);
                                    else setEditCompanyIds(allCompanies.map(c => c.id));
                                  }}
                                  className="text-[10px] text-primary hover:text-primary/80 uppercase tracking-wider"
                                >
                                  {editCompanyIds.length === allCompanies.length ? 'Deselect All' : 'Select All'}
                                </button>
                              )}
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
                                        ? 'bg-primary/10 border-primary/30'
                                        : 'bg-muted/30 border-border hover:border-border'
                                    }`}
                                  >
                                    {/* Visually a radio for drivers (filled circle), a checkbox for everyone else */}
                                    {isDriver ? (
                                      <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center transition ${
                                        checked ? 'border-primary' : 'border-border'
                                      }`}>
                                        {checked && <div className="w-2 h-2 rounded-full bg-primary" />}
                                      </div>
                                    ) : (
                                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                        checked ? 'bg-primary border-primary' : 'border-border'
                                      }`}>
                                        {checked && <span className="text-foreground text-[10px]">✓</span>}
                                      </div>
                                    )}
                                    <div className="flex-1">
                                      <span className="text-sm font-medium">{c.code}</span>
                                      {c.display_name && <span className="text-xs text-muted-foreground ml-2">{c.display_name}</span>}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </>
                        )}

                        {!unrestricted && allCompanies.length === 0 && (
                          <p className="text-xs text-muted-foreground italic py-4 text-center bg-muted/30 rounded-lg">No companies configured</p>
                        )}
                      </div>

                      {/* ── Step 2: Vehicle Assignments ── */}
                      <div className="border-t border-border pt-5">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-bold text-muted-foreground uppercase tracking-wider">{t('forms.step_label', { n: 2 })}</span>
                          <div className="flex-1 border-t border-border" />
                        </div>
                        <h3 className="text-sm font-semibold text-foreground/80 mb-1 flex items-center gap-2">
                          <Truck size={14} className="text-muted-foreground" />
                          Vehicle Assignments
                          {editVehicles.length > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-700 dark:text-amber-400">{editVehicles.length}</span>
                          )}
                        </h3>
                        <p className="text-xs text-muted-foreground mb-3">
                          {unrestricted
                            ? 'Select which vehicles this user can access:'
                            : editCompanyIds.length > 0
                            ? `Showing ${companyFilteredVehicles.length} vehicle${companyFilteredVehicles.length !== 1 ? 's' : ''} from selected compan${editCompanyIds.length > 1 ? 'ies' : 'y'}:`
                            : 'Select companies above to see available vehicles.'
                          }
                        </p>

                        {/* No companies selected warning */}
                        {!unrestricted && editCompanyIds.length === 0 && (
                          <div className="py-6 text-center bg-muted/30 rounded-lg mb-3">
                            <p className="text-xs text-amber-600/70 dark:text-amber-400/70">⚠ Select at least one company above to assign vehicles</p>
                          </div>
                        )}

                        {/* Vehicle checkbox list (like companies) */}
                        {(unrestricted || editCompanyIds.length > 0) && companyFilteredVehicles.length > 0 && (
                          <>
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-2">
                                {editVehicles.length === 0 ? (
                                  <span className="text-xs text-muted-foreground italic">No vehicles assigned — sees all</span>
                                ) : (
                                  <span className="text-xs text-muted-foreground">{editVehicles.length} selected</span>
                                )}
                              </div>
                              <button
                                type="button"
                                onClick={() => {
                                  if (editVehicles.length === companyFilteredVehicles.length) setEditTrucks([]);
                                  else setEditTrucks(companyFilteredVehicles.map(v => v.name));
                                }}
                                className="text-[10px] text-primary hover:text-primary/80 uppercase tracking-wider"
                              >
                                {editVehicles.length === companyFilteredVehicles.length ? 'Deselect All' : 'Select All'}
                              </button>
                            </div>

                            {/* Search filter */}
                            <input
                              value={newVehicle}
                              onChange={e => setNewTruck(e.target.value)}
                              placeholder={t('forms.search_vehicles_placeholder')}
                              className="w-full bg-muted border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-ring mb-2"
                            />

                            <div className="space-y-1.5 mb-3 max-h-64 overflow-y-auto">
                              {companyFilteredVehicles
                                .filter(v => !newVehicle.trim() || v.name.toLowerCase().includes(newVehicle.toLowerCase()))
                                .map(v => {
                                  const checked = editVehicles.includes(v.name);
                                  return (
                                    <div
                                      key={v.name}
                                      onClick={() => {
                                        if (checked) setEditTrucks(editVehicles.filter(t => t !== v.name));
                                        else setEditTrucks([...editVehicles, v.name]);
                                      }}
                                      className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                        checked
                                          ? 'bg-amber-500/10 border-amber-500/30'
                                          : 'bg-muted/30 border-border hover:border-border'
                                      }`}
                                    >
                                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                        checked ? 'bg-amber-600 border-amber-600' : 'border-border'
                                      }`}>
                                        {checked && <span className="text-primary-foreground text-[10px]">✓</span>}
                                      </div>
                                      <div className="flex-1">
                                        <span className="text-sm font-medium">{v.name}</span>
                                        {v.company && <span className="text-xs text-muted-foreground ml-2">{v.company}</span>}
                                      </div>
                                      {checked && editVehicles.indexOf(v.name) === 0 && selected.role === 'driver' && (
                                        <span className="text-[10px] text-primary uppercase tracking-wider">primary</span>
                                      )}
                                    </div>
                                  );
                                })}
                            </div>
                          </>
                        )}

                        {(unrestricted || editCompanyIds.length > 0) && companyFilteredVehicles.length === 0 && (
                          <p className="text-xs text-muted-foreground italic py-4 text-center bg-muted/30 rounded-lg mb-3">No vehicles found</p>
                        )}
                      </div>

                      {/* Single save button for companies + vehicles */}
                      <button
                        onClick={() => handleSaveAccess(selected.id)}
                        disabled={savingCompanies || savingVehicles}
                        className="w-full py-2.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-lg text-sm font-semibold transition"
                      >
                        {savingCompanies || savingVehicles ? 'Saving...' : 'Save Access'}
                      </button>
                    </div>
                  )}

                  {/* ─── SETTINGS TAB ─── */}
                  {detailTab === 'settings' && (
                    <div className="space-y-6">
                      {/* Role change */}
                      <div>
                        <h3 className="text-sm font-semibold text-foreground/80 mb-3">Change Role</h3>
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
                                    ? 'bg-yellow-500/10 border-yellow-500/50 text-yellow-600 dark:text-yellow-400'
                                    : 'bg-muted border-border text-muted-foreground hover:border-border hover:text-foreground/80'
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
                            <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-2">
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
                                className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Danger zone */}
                      <div className="border-t border-border pt-5">
                        <h3 className="text-sm font-semibold text-red-600/80 dark:text-red-400/80 mb-3">Danger Zone</h3>
                        {confirmAction !== 'deactivate' && confirmAction !== 'activate' ? (
                          <button
                            onClick={() => setConfirmAction(selected.is_active ? 'deactivate' : 'activate')}
                            className={`w-full py-2.5 rounded-lg text-sm font-medium transition border ${
                              selected.is_active
                                ? 'border-destructive/40 text-red-600 dark:text-red-400 hover:bg-red-500/10'
                                : 'border-green-600/50 dark:border-green-800 text-green-600 dark:text-green-400 hover:bg-green-500/10'
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
                                ? <span className="text-destructive">Deactivate <strong>{selected.display_name}</strong>? They will lose access immediately.</span>
                                : <span className="text-green-600 dark:text-green-400">Activate <strong>{selected.display_name}</strong>? They will regain access.</span>
                              }
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleToggleActive(selected.id, !selected.is_active)}
                                className={`px-4 py-1.5 rounded text-xs font-medium transition ${
                                  selected.is_active ? 'bg-red-600 hover:bg-red-700 text-white' : 'bg-green-600 hover:bg-green-700 text-white'
                                }`}
                              >
                                {selected.is_active ? 'Deactivate' : 'Activate'}
                              </button>
                              <button
                                onClick={() => setConfirmAction(null)}
                                className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition"
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
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={status === 'green' ? 'text-green-600 dark:text-green-400' : status === 'red' ? 'text-destructive' : ''}>
        {value}
      </dd>
    </div>
  );
}
