import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Users as UsersIcon, X, Truck, User as UserIcon, Shield, Settings as SettingsIcon,
  Building2, Globe, Clock, Check, Mail, Send, Copy, Search,
} from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { toast } from 'sonner';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import RoleBadge, { ROLE_LABEL, ASSIGNABLE_ROLES, roleTone } from '../../components/RoleBadge';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { toneClasses } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { InvitesPanel } from './Invites';
import { WorkHoursPanel } from './WorkHours';
import type { AdminUser, AnyColumn } from '../../types';

// Role rank for the dashboard-side rank-check on the Change Role
// buttons.  Mirrors capabilities/iam/permissions.role_rank on the
// server (Owner=4, Admin=3, Fleet/Safety/Dispatcher=2, Driver=1)
// so the UI hides promotion targets the backend would refuse anyway.
// Without this check, an HR/Fleet-tier user sees Admin in the
// role-change grid and only learns it's forbidden after clicking.
const ROLE_RANK: Record<string, number> = {
  owner: 4,
  admin: 3,
  fleet: 2,
  safety: 2,
  dispatcher: 2,
  // Department personas — peers of dispatcher (rank 2), per the backend
  // ROLE_HIERARCHY in capabilities/permissions/roles.py.  Without these
  // a rank lookup falls back to 0 and the grid mis-gates them.
  hr: 2,
  accounting: 2,
  recruiter: 2,
  driver: 1,
};

/** Display label for a user whose ``display_name`` is blank.
 *  Empty names come from Telegram registrations where the user
 *  never set a name on their TG profile, OR from email signups
 *  with the placeholder removed.  Falling back to literal "?" looked
 *  like data corruption to operators (audit finding #1) — instead
 *  derive a readable identity from what we DO have. */
function nameOrFallback(u: { display_name?: string | null; telegram_id?: number | null; email?: string | null }): string {
  const n = (u.display_name || '').trim();
  if (n) return n;
  if (u.email) return u.email.split('@')[0];
  if (u.telegram_id) return `tg:${u.telegram_id}`;
  return '(unnamed)';
}

/** 12-hour clock formatter matching the WorkHours timeline header.
 *  ``0 → "12 AM"``, ``13 → "1 PM"`` etc. — keeps the Settings tab
 *  consistent with what operators just saw on the Working Hours tab. */
function fmtHourLabel(h: number): string {
  if (h === 0) return '12 AM';
  if (h === 12) return '12 PM';
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

/** Two-letter initials for the avatar.  Strips ``tg:`` prefix so a
 *  Telegram-only user without a name gets ``TG`` instead of ``T`` —
 *  reads more like an intentional placeholder than a glitch. */
function initialsOf(name: string): string {
  if (name.startsWith('tg:')) return 'TG';
  if (name === '(unnamed)') return '–';
  const parts = name.trim().split(/\s+/);
  return parts.length >= 2
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : (parts[0]?.[0] || '–').toUpperCase();
}

// Roles the Owner/Admin can re-assign existing members to — the shared
// assignable-role list (every role except 'owner', which transfers via a
// separate flow).  Sourced from RoleBadge so new personas surface here
// automatically instead of drifting.  The rank gate below still hides
// targets the caller can't outrank.
const ROLES = ASSIGNABLE_ROLES;

function UserAvatar({ userId, name, size = 48, active = true }: { userId: number; name: string; size?: number; active?: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let revoke = '';
    let cancelled = false;
    apiFetch(`/admin/users/${userId}/avatar`).then(res => {
      // 204 No Content = "user has no profile photo" (backend
      // returns this instead of 404 so the dashboard console doesn't
      // log a red error for every avatar-less user — common case
      // since most users haven't set a Telegram profile photo).
      if (!res.ok || res.status === 204) return null;
      return res.blob();
    }).then(blob => {
      // Guard against the rare zero-byte blob (e.g. if a future
      // proxy strips the 204 body in transit) — URL.createObjectURL
      // on an empty blob produces a broken-image URL.
      if (blob && blob.size > 0 && !cancelled) {
        const url = URL.createObjectURL(blob);
        revoke = url;
        setSrc(url);
      }
    }).catch(() => {});
    return () => { cancelled = true; if (revoke) URL.revokeObjectURL(revoke); };
  }, [userId]);

  const ini = initialsOf(name);
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

interface VehicleSummary {
  name: string;
  company?: string;
}

// Identity badges: small icon badges per row showing which sign-in
// methods the user has attached.  Lucide icons instead of literal
// '@'/'TG' chars (cleaner read, design-system compliant).  Helps
// operators spot e.g. "this driver has email but never opened the
// bot, that's why they're not getting alerts."
function IdentityBadges({ u }: { u: AdminUser }) {
  const hasEmail = Boolean(u.email);
  const hasTelegram = Boolean(u.telegram_id);
  return (
    <span className="inline-flex items-center gap-1 ml-1">
      <span
        title={hasEmail ? `Email: ${u.email ?? ''}` : 'No email — admins can add one in the detail panel'}
        aria-label={hasEmail ? 'Has email' : 'No email'}
        className={`inline-flex items-center justify-center w-4 h-4 rounded ${
          hasEmail
            ? 'bg-primary/15 text-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        <Mail size={12} aria-hidden="true" />
      </span>
      <span
        title={
          hasTelegram
            ? `Telegram linked (tg:${u.telegram_id})`
            : 'Telegram not linked yet — send the bot deep-link to this user'
        }
        aria-label={hasTelegram ? 'Telegram linked' : 'Telegram not linked'}
        className={`inline-flex items-center justify-center w-4 h-4 rounded ${
          hasTelegram
            ? 'bg-primary/15 text-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        <Send size={12} aria-hidden="true" />
      </span>
    </span>
  );
}

const userColumns: AnyColumn[] = [
  { key: 'display_name', label: 'Name', sortable: true, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    const displayName = nameOrFallback(u);
    const isFallback = !(u.display_name || '').trim();
    return (
      <div className="flex items-center gap-2">
        {/* Real profile photo when one exists, initial-circle when
            not — UserAvatar handles the fetch + fallback in one
            place so the table cell + drawer header stay visually
            consistent (one user always renders the same way). */}
        <UserAvatar
          userId={u.id}
          name={displayName}
          size={28}
          active={u.is_active}
        />
        <span className="flex items-center">
          <span className={isFallback ? 'text-muted-foreground italic' : ''}>{displayName}</span>
          <IdentityBadges u={u} />
        </span>
      </div>
    );
  }},
  { key: 'role', label: 'Role', sortable: true, render: (v) => <RoleBadge role={String(v)} /> },
  { key: 'vehicles', label: 'Vehicles', sortable: false, render: (_v, row) => {
    const u = row as unknown as AdminUser;
    const trucks = u.trucks?.length ? u.trucks : u.truck_num ? [u.truck_num] : [];
    if (!trucks.length) return <span className="text-muted-foreground text-xs">All</span>;
    if (trucks.length <= 2) return <span className="text-xs">{trucks.join(', ')}</span>;
    return (
      <span className="text-xs" title={trucks.join(', ')}>
        {trucks[0]}{' '}
        <span className="px-1.5 py-0.5 bg-muted rounded-full text-3xs text-muted-foreground">+{trucks.length - 1}</span>
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

export default function TeamManagement() {
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('profile');
  const [roleFilter, setRoleFilter] = useState<string | null>(null);
  // Page-level tab: the member list vs the Invites panel (folded in from
  // the old standalone /admin/invites page).  The Invites tab only shows
  // for users who can actually invite.
  // Page-level tab: members list / invites / working hours.  The
  // Working Hours tab replaces the standalone /admin/work-hours
  // page — same panel, just hosted here so all team-shift config
  // lives under one nav entry.  Gated on can_manage_work_hours —
  // the Settings component's own delegation flag.
  const [pageTab, setPageTab] = useState<'members' | 'invites' | 'working-hours'>('members');
  const { has } = useViewPermissions();
  const canInvite = has('can_invite');
  const canManageWorkHours = has('can_manage_work_hours');

  // Vehicle assignment.  Variable + setter names matched (the old
  // legacy ``setEditVehicles`` setter on an ``editVehicles`` state was
  // confusing to readers).  Same applies to the search input + saving
  // flag — renamed for consistency with the rest of the feature.
  const [editVehicles, setEditVehicles] = useState<string[]>([]);
  const [vehicleQuery, setVehicleQuery] = useState('');
  const [savingVehicles, setSavingVehicles] = useState(false);

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
  // Single Vehicle Access scope.  Replaces the old two-step
  // "Step 1 Company / Step 2 Vehicle" UI for non-drivers — the
  // overlap (All Companies → every vehicle anyway) was confusing.
  //   'all'     → unrestricted=true, company_ids=[], trucks=[]
  //   'company' → unrestricted=false, company_ids=[...], trucks=[]
  //   'vehicle' → unrestricted=false, company_ids=derived from
  //               selected vehicles' carriers, trucks=[...]
  // Driver flow ignores this state — drivers are forced to the
  // single-company picker above the scope-picker branch.
  type AccessScope = 'all' | 'company' | 'vehicle';
  const [accessScope, setAccessScope] = useState<AccessScope>('all');
  // Switch scope + clear the now-irrelevant selections so a switch
  // between modes doesn't smuggle stale picks into Save.  E.g.
  // operator picks 5 companies, switches to 'vehicle', the company
  // ids would otherwise still be sent.
  const setAccessScopeAndSync = (s: AccessScope) => {
    setAccessScope(s);
    if (s === 'all') { setEditCompanyIds([]); setEditVehicles([]); }
    else if (s === 'company') { setEditVehicles([]); }
    else if (s === 'vehicle') { setEditCompanyIds([]); }
  };

  // Drivers are restricted to one company at a time (backend enforces;
  // the UI mirrors that with a single-select instead of a checkbox list).
  const isDriver = selected?.role === 'driver';
  // Self-edit guard: operator can't change their own role or
  // deactivate themselves from this drawer.  Lockout prevention —
  // accidentally deactivating the only Admin / removing your own
  // permission set is recoverable only by another Owner.  Disables
  // the Change Role grid + Danger Zone in Settings when shown.
  const isSelfEdit = !!(me && selected && Number(me.id) === selected.id);
  // Caller's own role rank — drives the Change Role grid's disabled
  // state below (UI mirrors the server-side rank check at
  // capabilities/iam/permissions.validate_invite_role so operators
  // don't click a button only to get a 403 toast).
  const myRank = me ? (ROLE_RANK[me.role] ?? 0) : 0;

  // Role change
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'role' | 'deactivate' | 'activate' | null>(null);
  // Per-user Working Hours assignment — admin picks one of the
  // named schedules from the catalog (or leaves the user on the
  // role-level fallback).  Replaces the older free-form hour pickers;
  // the Working Hours tab is the single source of truth for shift
  // definitions, the user row just points at a chosen row.  Saving
  // state used by the dropdown's busy attribute during the PUT.
  const [savingQuiet, setSavingQuiet] = useState(false);
  // Driver-archive confirmation.  When the operator changes a
  // driver's company, the backend archives that driver's docs into
  // {old}/drivers/_archive/{today}/.  This used to fire a
  // window.confirm() — replaced with an inline panel so the
  // confirmation pattern matches the rest of the drawer.
  const [pendingAccessSave, setPendingAccessSave] = useState<{ oldCodes: string } | null>(null);

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
  const { data: vehiclesData } = useQuery({
    queryKey: ['admin-users-vehicles'],
    // Fetch ALL vehicles — picker needs the complete list so "Select
    // All" actually selects all.  Backend caps page_size at 200
    // (interfaces/api/routes/vehicles.py:221 ``le=200``) so we
    // walk pages until total_pages is exhausted instead of taking
    // the first 200 (which used to truncate fleets >200).
    // Sequential rather than parallel because most accounts are
    // <200 vehicles → single round-trip; the loop only fires a
    // second/third request for the rare 200+ fleet.
    queryFn: async () => {
      const all: VehicleSummary[] = [];
      let page = 1;
      while (true) {
        const res = await apiJSON<{
          vehicles: VehicleSummary[];
          total_pages: number;
        }>(`/vehicles?page_size=200&page=${page}`);
        all.push(...(res.vehicles ?? []));
        if (page >= (res.total_pages ?? 1)) break;
        page++;
      }
      return { vehicles: all };
    },
  });
  const vehicleList = vehiclesData?.vehicles ?? [];

  // Working Hours catalog — fetched so the drawer Settings tab can
  // render the schedule dropdown (admin assigns a user to one of
  // these named rows instead of typing custom hours).  Stale-time
  // matches the 60s default; refresh implicitly when the user opens
  // the Working Hours tab and edits a row (queries are independent).
  const { data: workHoursData } = useQuery({
    queryKey: ['admin-work-hours-catalog'],
    queryFn: () => apiJSON<{ schedules: Array<{
      id: number; label: string; start_hour: number; end_hour: number; target_role: string;
    }> }>('/admin/work-hours'),
  });
  const workHoursCatalog = workHoursData?.schedules ?? [];

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

  // Set or clear the user's per-user quiet-hours override.  ``clear``
  // skips the start/end fields and sends a both-null body so the
  // backend wipes the override and the user falls back to the role-
  // level Working Hours.  Save is forbidden when start === end —
  // that's a zero-width window which would silence nothing.
  // Assign the user to a named Working Hours schedule from the
  // catalog — passing ``null`` clears the assignment so the user
  // inherits the role-level Working Hours.  Replaces the older
  // free-form hours picker; the catalog is the SSoT for shift
  // definitions, the user just points at a row.
  const handleAssignSchedule = async (userId: number, scheduleId: number | null) => {
    setSavingQuiet(true);
    try {
      await apiJSON('/admin/users/' + userId + '/assigned-work-hours', {
        method: 'PUT',
        body: { schedule_id: scheduleId },
      });
      setSuccess(scheduleId === null ? 'Reverted to role schedule' : 'Schedule assigned');
      if (selected) {
        setSelected({
          ...selected,
          assigned_work_hours_id: scheduleId,
        });
      }
      await loadUsers();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSavingQuiet(false); }
  };

  // Vehicles filtered by selected companies
  const companyFilteredVehicles = useMemo(() => {
    if (unrestricted) return vehicleList;
    if (editCompanyIds.length === 0) return [];
    const selectedCodes = new Set(
      allCompanies.filter(c => editCompanyIds.includes(c.id)).map(c => c.code.toUpperCase())
    );
    return vehicleList.filter(v => {
      const vCompany = (v.company || '').toUpperCase();
      return selectedCodes.has(vCompany);
    });
  }, [vehicleList, unrestricted, editCompanyIds, allCompanies]);

  // Sync editVehicles when selecting a user
  useEffect(() => {
    if (selected) {
      const trucks = selected.trucks?.length ? [...selected.trucks] : selected.truck_num ? [selected.truck_num] : [];
      setEditVehicles(trucks);
      setVehicleQuery('');
      setDetailTab('profile');
      setConfirmAction(null);
      setPendingRole(null);
      setPendingAccessSave(null);
      setSavingQuiet(false);
      setSuccess('');
      // Load company assignments + derive scope.  Three server
      // states collapse into the new three scope modes:
      //   unrestricted + trucks=[] → 'all'
      //   unrestricted + trucks=[v] → 'vehicle' (truck filter wins)
      //   !unrestricted + companies=[c] (trucks=[]) → 'company'
      //   !unrestricted + companies=[c] + trucks=[v] → 'vehicle'
      // Drivers are forced to 'company' by their separate flow
      // and the derivation is harmless for that case.
      apiJSON<{ companies: { company_id: number }[]; all_companies: { id: number; code: string; display_name: string }[]; unrestricted: boolean }>(
        '/admin/users/' + selected.id + '/companies'
      ).then(data => {
        setAllCompanies(data.all_companies || []);
        const ids = data.companies?.map(a => a.company_id) || [];
        setEditCompanyIds(ids);
        setInitialCompanyIds(ids);
        setUnrestricted(data.unrestricted);
        const hasVehicles = trucks.length > 0;
        if (hasVehicles) setAccessScope('vehicle');
        else if (!data.unrestricted) setAccessScope('company');
        else setAccessScope('all');
      }).catch(() => { setAllCompanies([]); setEditCompanyIds([]); setInitialCompanyIds([]); setUnrestricted(true); setAccessScope('all'); });
    }
  }, [selected]);

  const handleSaveAccess = async (userId: number, confirmedArchive = false) => {
    // Driver reassignment triggers the backend archive flow that moves
    // their existing CDL/medical docs into `{old_company}/drivers/_archive/`.
    // Show an inline confirmation panel before that happens — files
    // aren't lost (they live under the archive path in Drive) but the
    // path change is permanent and worth a deliberate ack.  Inline UX
    // matches the role-change + deactivate confirmation patterns
    // (replaces the previous window.confirm() blocking dialog).
    if (isDriver && initialCompanyIds.length > 0 && !confirmedArchive) {
      const targetIds = unrestricted ? [] : editCompanyIds;
      const sameSet =
        targetIds.length === initialCompanyIds.length &&
        targetIds.every(id => initialCompanyIds.includes(id));
      if (!sameSet) {
        const oldCodes = allCompanies
          .filter(c => initialCompanyIds.includes(c.id))
          .map(c => c.code)
          .join(', ');
        setPendingAccessSave({ oldCodes });
        return;  // operator clicks Confirm in the inline panel to retry
      }
    }
    setPendingAccessSave(null);
    setSavingCompanies(true);
    setSavingVehicles(true);
    try {
      // Translate the scope state back to the backend's
      // (unrestricted, company_ids, trucks) shape.  Driver flow
      // still uses ``unrestricted`` + ``editCompanyIds`` directly
      // because the driver UI doesn't render the scope picker.
      const uniqueTrucks = [...new Set(editVehicles)];
      let companyIdsForSave: number[];
      if (isDriver) {
        companyIdsForSave = unrestricted ? [] : editCompanyIds;
      } else if (accessScope === 'all') {
        companyIdsForSave = [];
      } else if (accessScope === 'company') {
        companyIdsForSave = editCompanyIds;
      } else {
        // 'vehicle' scope — derive company set from the chosen
        // vehicles' carriers so the backend filter chain is
        // consistent (unrestricted=false → company-gated → truck-
        // gated).  Without this the bag-of-trucks would slip past
        // the company isolation when the operator later widens the
        // user's role.
        const carrierCodes = new Set(
          vehicleList
            .filter(v => uniqueTrucks.includes(v.name) && v.company)
            .map(v => (v.company as string).toUpperCase()),
        );
        companyIdsForSave = allCompanies
          .filter(c => carrierCodes.has(c.code.toUpperCase()))
          .map(c => c.id);
      }
      const sendUnrestricted = isDriver ? unrestricted : (accessScope === 'all');
      const res = await apiJSON<{ archived_companies?: string[] }>(
        '/admin/users/' + userId + '/companies',
        {
          method: 'PUT',
          body: { company_ids: sendUnrestricted ? [] : companyIdsForSave },
        },
      );
      await apiJSON('/admin/users/' + userId + '/trucks', { method: 'PUT', body: { trucks: uniqueTrucks } });
      const archived = res.archived_companies || [];
      setSuccess(
        archived.length
          ? `Access saved. Archived docs from: ${archived.join(', ')}`
          : 'Access saved',
      );
      // Refresh the snapshot so a second save without re-loading the user
      // doesn't re-trigger the confirm or replay the archive flow.
      setInitialCompanyIds(sendUnrestricted ? [] : [...companyIdsForSave]);
      await loadUsers();
      if (selected) setSelected({ ...selected, trucks: uniqueTrucks });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSavingCompanies(false); setSavingVehicles(false); }
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

  // initialsOf + nameOrFallback (module scope) supersede the local
  // helper this component used to have — single source of truth for
  // the empty-name fallback semantics across table cell + drawer.

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

      {/* Page-level tabs.  Each tab is a different team-management
          surface:
            Members        — the member list + per-user drawer
            Invites        — invite creation + lifecycle (folded in
                             from the old /admin/invites page)
            Working Hours  — per-role on-shift schedules (folded in
                             from the old /admin/work-hours page;
                             alerts pause outside these windows)
          Tabs render only when the caller has permission for them
          so HR/Fleet without can_invite see just Members. */}
      {(canInvite || canManageWorkHours) && (
        <div role="tablist" aria-label="Team management sections" className="flex gap-1 mb-4 border-b border-border">
          {([
            { key: 'members'        as const, label: 'Members',       show: true },
            { key: 'invites'        as const, label: 'Invites',       show: canInvite },
            { key: 'working-hours'  as const, label: 'Working Hours', show: canManageWorkHours },
          ]).filter(t => t.show).map(({ key, label }) => {
            const sel = pageTab === key;
            return (
              <button
                key={key}
                role="tab"
                aria-selected={sel}
                onClick={() => setPageTab(key)}
                className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                  sel
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}

      {pageTab === 'invites' ? (
        <InvitesPanel />
      ) : pageTab === 'working-hours' ? (
        <WorkHoursPanel />
      ) : (
        <>

      {error && (
        <div className="mb-3"><ErrorState message={error} /></div>
      )}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      <div className="flex items-center gap-3 mb-4">
        {/* Role filter chips */}
        <div className="flex gap-1.5">
          <button
            onClick={() => setRoleFilter(null)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition ${
              !roleFilter ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground/80'
            }`}
          >
            All <span className="opacity-60">{users.length}</span>
          </button>
          {Object.entries(ROLE_LABEL).map(([key, label]) => {
            const count = roleCounts[key] || 0;
            if (!count) return null;
            return (
              <button
                key={key}
                onClick={() => setRoleFilter(roleFilter === key ? null : key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition border ${
                  roleFilter === key ? toneClasses(roleTone(key)) : 'border-transparent text-muted-foreground hover:text-foreground/80'
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
          title={roleFilter ? `No ${ROLE_LABEL[roleFilter]} users` : 'No team members yet'}
          description={
            roleFilter
              ? 'Try a different role filter.'
              : 'Invite teammates from the Invites page to get started.'
          }
        />
      ) : (
        <>
          <DataTable
            columns={userColumns}
            data={filteredUsers as unknown as Record<string, unknown>[]}
            searchKey={['display_name', 'email', 'role', 'truck_num']}
            onRowClick={(row) => setSelected(row as unknown as AdminUser)}
          />
          {selected && (
            <UserDrawerShell
              displayName={nameOrFallback(selected)}
              onClose={() => setSelected(null)}
            >
              <div className="p-6 pb-4 border-b border-border">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <UserAvatar userId={selected.id} name={nameOrFallback(selected)} size={48} active={selected.is_active} />
                    <div>
                      <h2 id="user-drawer-title" className="text-lg font-semibold">
                        {nameOrFallback(selected)}
                      </h2>
                      <div className="flex items-center gap-2 mt-0.5">
                        <RoleBadge role={selected.role} />
                      </div>
                    </div>
                  </div>
                  <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X size={16} /></button>
                </div>

                {/* Detail tabs — lucide icons + ARIA tab semantics
                    so screen readers get the tablist treatment.
                    role="tablist"/"tab"/"tabpanel" wiring lets keyboard
                    users arrow-key between tabs (base-ui pattern). */}
                <div role="tablist" aria-label="User details sections" className="flex gap-1 bg-muted/50 rounded-lg p-0.5">
                  {([
                    { key: 'profile' as DetailTab, label: 'Profile', icon: UserIcon },
                    { key: 'access' as DetailTab, label: 'Access', icon: Shield },
                    { key: 'settings' as DetailTab, label: 'Settings', icon: SettingsIcon },
                  ]).map(tab => {
                    const Icon = tab.icon;
                    const sel = detailTab === tab.key;
                    return (
                      <button
                        key={tab.key}
                        role="tab"
                        aria-selected={sel}
                        aria-controls={`user-drawer-panel-${tab.key}`}
                        id={`user-drawer-tab-${tab.key}`}
                        onClick={() => { setDetailTab(tab.key); setConfirmAction(null); }}
                        className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition inline-flex items-center justify-center gap-1.5 ${
                          sel ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground/80'
                        }`}
                      >
                        <Icon size={12} aria-hidden="true" />
                        {tab.label}
                      </button>
                    );
                  })}
                </div>
              </div>

                <div className="p-6">
                  {/* ─── PROFILE TAB ─── */}
                  {detailTab === 'profile' && (
                    <div
                      role="tabpanel"
                      id="user-drawer-panel-profile"
                      aria-labelledby="user-drawer-tab-profile"
                      className="space-y-4"
                    >
                      <dl className="space-y-3 text-sm">
                        <Row label="Email" value={selected.email || '—'} />
                        <Row
                          label="Telegram ID"
                          value={selected.telegram_id ? String(selected.telegram_id) : 'Not linked'}
                          action={selected.telegram_id && (
                            <button
                              type="button"
                              onClick={() => {
                                navigator.clipboard.writeText(String(selected.telegram_id))
                                  .then(() => toast.success('Telegram ID copied'))
                                  .catch(() => toast.error('Copy failed'));
                              }}
                              className="text-muted-foreground hover:text-primary p-0.5"
                              aria-label="Copy Telegram ID"
                              title="Copy Telegram ID"
                            >
                              <Copy size={12} />
                            </button>
                          )}
                        />
                        <Row label="Language" value={(selected.language || '—').toUpperCase()} />
                        <Row
                          label="Status"
                          value={selected.is_active ? 'Active' : 'Inactive'}
                          status={selected.is_active ? 'ok' : 'danger'}
                        />
                      </dl>
                      {/* Vehicle Access summary — one card, scope-aware.
                          Replaces the prior 2×1 grid whose two cells often
                          duplicated meaning ("0 Vehicles + All Companies"
                          really meant "sees everything").  Reads back the
                          scope the operator set in the Access tab. */}
                      <div className="bg-muted/50 rounded-lg p-3 mt-4">
                        <div className="text-xs text-muted-foreground mb-1">Vehicle Access</div>
                        <div className="text-base font-semibold text-foreground inline-flex items-center gap-2">
                          {isDriver ? (
                            <>
                              <Building2 size={14} aria-hidden="true" className="text-muted-foreground" />
                              {allCompanies.find(c => c.id === editCompanyIds[0])?.code || '—'}
                              <span className="text-xs text-muted-foreground font-normal">
                                {editVehicles.length > 0 && `· ${editVehicles.length} vehicle${editVehicles.length === 1 ? '' : 's'}`}
                              </span>
                            </>
                          ) : accessScope === 'all' ? (
                            <><Globe size={14} aria-hidden="true" className="text-muted-foreground" />All vehicles</>
                          ) : accessScope === 'company' ? (
                            <>
                              <Building2 size={14} aria-hidden="true" className="text-muted-foreground" />
                              {editCompanyIds.length} {editCompanyIds.length === 1 ? 'company' : 'companies'}
                            </>
                          ) : (
                            <>
                              <Truck size={14} aria-hidden="true" className="text-muted-foreground" />
                              {editVehicles.length} {editVehicles.length === 1 ? 'vehicle' : 'vehicles'}
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* ─── ACCESS TAB ─── */}
                  {detailTab === 'access' && (
                    <div
                      role="tabpanel"
                      id="user-drawer-panel-access"
                      aria-labelledby="user-drawer-tab-access"
                      className="space-y-4"
                    >
                      {/* Driver flow stays a single-company picker — driver
                          identity is tied to one carrier at a time (CDL/DOT
                          ledger + the archive flow at handleSaveAccess
                          enforces this).  Non-drivers get the simpler 3-mode
                          scope picker below. */}
                      {isDriver ? (
                        <>
                          <div className={`px-3 py-2 rounded-lg border text-xs ${toneClasses('warn')}`}>
                            <p className="font-medium mb-0.5 inline-flex items-center gap-1">
                              <Building2 size={12} aria-hidden="true" />
                              One company at a time.
                            </p>
                            <p>Changing this driver's company will archive their previous CDL / medical / DQF documents to <code className="font-mono text-3xs">{'{old company}'}/drivers/_archive/{'{today}'}/</code>.</p>
                          </div>
                          <h3 className="text-sm font-semibold text-foreground/80 flex items-center gap-2">
                            <Building2 size={14} className="text-muted-foreground" aria-hidden="true" />
                            Assigned Company
                          </h3>
                          {allCompanies.length === 0 ? (
                            <p className="text-xs text-muted-foreground italic py-4 text-center bg-muted/30 rounded-lg">No companies configured</p>
                          ) : (
                            <div className="space-y-1.5">
                              {allCompanies.map(c => {
                                const checked = editCompanyIds.includes(c.id);
                                return (
                                  <div
                                    key={c.id}
                                    onClick={() => toggleCompany(c.id)}
                                    className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                      checked
                                        ? 'bg-primary/10 border-primary/30'
                                        : 'bg-muted/30 border-border hover:border-primary/30'
                                    }`}
                                  >
                                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center transition ${
                                      checked ? 'border-primary' : 'border-border'
                                    }`}>
                                      {checked && <div className="w-2 h-2 rounded-full bg-primary" />}
                                    </div>
                                    <div className="flex-1">
                                      <span className="text-sm font-medium">{c.code}</span>
                                      {c.display_name && <span className="text-xs text-muted-foreground ml-2">{c.display_name}</span>}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </>
                      ) : (
                        <>
                          {/* Non-driver: single Vehicle Access scope picker.
                              The previous "Step 1 Company / Step 2 Vehicle"
                              two-step model duplicated meaning (selecting
                              All Companies → every vehicle anyway) and
                              forced the operator to think about scope
                              twice.  Three exclusive modes:
                                All     — sees everything (no further config)
                                Company — limit to specific companies; their
                                          vehicles inherited automatically
                                Vehicle — limit to specific vehicles directly
                                          (company is derived from the
                                          selected vehicles' carriers) */}
                          <h3 className="text-sm font-semibold text-foreground/80 flex items-center gap-2">
                            <Truck size={14} className="text-muted-foreground" aria-hidden="true" />
                            Vehicle Access
                          </h3>
                          <div
                            role="radiogroup"
                            aria-label="Vehicle access scope"
                            className="grid grid-cols-3 gap-1 bg-muted rounded-lg p-1"
                          >
                            {([
                              { key: 'all'     as const, label: 'All',     icon: Globe,     desc: 'sees everything' },
                              { key: 'company' as const, label: 'Company', icon: Building2, desc: 'limit by company' },
                              { key: 'vehicle' as const, label: 'Vehicle', icon: Truck,     desc: 'limit by vehicle' },
                            ]).map(opt => {
                              const Icon = opt.icon;
                              const sel = accessScope === opt.key;
                              return (
                                <button
                                  key={opt.key}
                                  type="button"
                                  role="radio"
                                  aria-checked={sel}
                                  onClick={() => setAccessScopeAndSync(opt.key)}
                                  className={`px-2 py-2 rounded-md text-xs font-medium transition inline-flex flex-col items-center gap-0.5 ${
                                    sel ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                                  }`}
                                >
                                  <Icon size={14} aria-hidden="true" />
                                  {opt.label}
                                </button>
                              );
                            })}
                          </div>
                          {/* Scope-specific body */}
                          {accessScope === 'all' && (
                            <p className="text-xs text-muted-foreground italic py-3 px-3 bg-muted/30 rounded-lg">
                              This user can access data from all companies and all vehicles.
                            </p>
                          )}
                          {accessScope === 'company' && (
                            <div>
                              <div className="flex items-center justify-between mb-2">
                                <p className="text-xs text-muted-foreground">Pick which companies this user can access:</p>
                                {allCompanies.length > 0 && (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      if (editCompanyIds.length === allCompanies.length) setEditCompanyIds([]);
                                      else setEditCompanyIds(allCompanies.map(c => c.id));
                                    }}
                                    className="text-3xs text-primary hover:text-primary/80 uppercase tracking-wider"
                                  >
                                    {editCompanyIds.length === allCompanies.length ? 'Deselect All' : 'Select All'}
                                  </button>
                                )}
                              </div>
                              {allCompanies.length === 0 ? (
                                <p className="text-xs text-muted-foreground italic py-4 text-center bg-muted/30 rounded-lg">No companies configured</p>
                              ) : (
                                <div className="space-y-1.5">
                                  {allCompanies.map(c => {
                                    const checked = editCompanyIds.includes(c.id);
                                    return (
                                      <div
                                        key={c.id}
                                        onClick={() => toggleCompany(c.id)}
                                        className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                          checked
                                            ? 'bg-primary/10 border-primary/30'
                                            : 'bg-muted/30 border-border hover:border-primary/30'
                                        }`}
                                      >
                                        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                          checked ? 'bg-primary border-primary' : 'border-border'
                                        }`}>
                                          {checked && <Check size={12} className="text-primary-foreground" aria-hidden="true" />}
                                        </div>
                                        <div className="flex-1">
                                          <span className="text-sm font-medium">{c.code}</span>
                                          {c.display_name && <span className="text-xs text-muted-foreground ml-2">{c.display_name}</span>}
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                          {accessScope === 'vehicle' && (
                            <div>
                              <div className="flex items-center justify-between mb-2">
                                <p className="text-xs text-muted-foreground">
                                  {editVehicles.length === 0
                                    ? 'Pick the vehicles this user can access:'
                                    : `${editVehicles.length} selected`}
                                </p>
                                {vehicleList.length > 0 && (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      if (editVehicles.length === vehicleList.length) setEditVehicles([]);
                                      else setEditVehicles(vehicleList.map(v => v.name));
                                    }}
                                    className="text-3xs text-primary hover:text-primary/80 uppercase tracking-wider"
                                  >
                                    {editVehicles.length === vehicleList.length ? 'Deselect All' : 'Select All'}
                                  </button>
                                )}
                              </div>
                              {vehicleList.length === 0 ? (
                                <p className="text-xs text-muted-foreground italic py-4 text-center bg-muted/30 rounded-lg">No vehicles found</p>
                              ) : (
                                <>
                                  <div className="relative mb-2">
                                    <Search
                                      size={14}
                                      aria-hidden="true"
                                      className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
                                    />
                                    <input
                                      value={vehicleQuery}
                                      onChange={e => setVehicleQuery(e.target.value)}
                                      placeholder={t('forms.search_vehicles_placeholder')}
                                      className="w-full bg-muted border border-border rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:border-ring"
                                    />
                                  </div>
                                  <div className="space-y-1.5 max-h-64 overflow-y-auto">
                                    {(() => {
                                      const filtered = vehicleList
                                        .filter(v => !vehicleQuery.trim() || v.name.toLowerCase().includes(vehicleQuery.toLowerCase()));
                                      if (filtered.length === 0) {
                                        return (
                                          <p className="text-xs text-muted-foreground italic py-3 text-center bg-muted/30 rounded-lg">
                                            No vehicles match "{vehicleQuery.trim()}".
                                          </p>
                                        );
                                      }
                                      return filtered.map(v => {
                                        const checked = editVehicles.includes(v.name);
                                        return (
                                          <div
                                            key={`${v.company ?? ''}::${v.name}`}
                                            onClick={() => {
                                              if (checked) setEditVehicles(editVehicles.filter(t => t !== v.name));
                                              else setEditVehicles([...editVehicles, v.name]);
                                            }}
                                            className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border ${
                                              checked
                                                ? 'bg-primary/10 border-primary/30'
                                                : 'bg-muted/30 border-border hover:border-primary/30'
                                            }`}
                                          >
                                            <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                                              checked ? 'bg-primary border-primary' : 'border-border'
                                            }`}>
                                              {checked && <Check size={12} className="text-primary-foreground" aria-hidden="true" />}
                                            </div>
                                            <div className="flex-1">
                                              <span className="text-sm font-medium">{v.name}</span>
                                              {v.company && <span className="text-xs text-muted-foreground ml-2">{v.company}</span>}
                                            </div>
                                          </div>
                                        );
                                      });
                                    })()}
                                  </div>
                                </>
                              )}
                            </div>
                          )}
                        </>
                      )}

                      {/* Driver-archive inline confirmation — replaces
                          the previous window.confirm() blocking dialog.
                          Shows when the operator is about to change a
                          driver's company; the backend will archive their
                          existing CDL/medical/DQF docs into
                          {old}/drivers/_archive/{today}/. */}
                      {pendingAccessSave ? (
                        <div className={`p-3 rounded-lg border text-xs ${toneClasses('warn')}`}>
                          <p className="font-medium mb-1">
                            Change this driver's company?
                          </p>
                          <p className="mb-2">
                            Previous documents under <strong>{pendingAccessSave.oldCodes}</strong> will be archived to <code className="font-mono text-3xs">{'{company}'}/drivers/_archive/{'{today}'}/</code> — files remain in Drive but move to the dated archive folder.
                          </p>
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleSaveAccess(selected.id, true)}
                              disabled={savingCompanies || savingVehicles}
                              className="px-4 py-1.5 bg-warn text-white hover:opacity-90 disabled:opacity-50 rounded text-xs font-medium transition"
                            >
                              {savingCompanies || savingVehicles ? 'Saving…' : 'Confirm & save'}
                            </button>
                            <button
                              onClick={() => setPendingAccessSave(null)}
                              disabled={savingCompanies || savingVehicles}
                              className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => handleSaveAccess(selected.id)}
                          disabled={savingCompanies || savingVehicles}
                          className="w-full py-2.5 bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 rounded-lg text-sm font-semibold transition"
                        >
                          {savingCompanies || savingVehicles ? 'Saving…' : 'Save Access'}
                        </button>
                      )}
                    </div>
                  )}

                  {/* ─── SETTINGS TAB ─── */}
                  {detailTab === 'settings' && (
                    <div
                      role="tabpanel"
                      id="user-drawer-panel-settings"
                      aria-labelledby="user-drawer-tab-settings"
                      className="space-y-6"
                    >
                      {/* Role change — rank-checked.  Buttons for roles
                          equal-to or above the operator's own rank get
                          disabled, mirroring the server-side rank check
                          in capabilities/iam/permissions.validate_invite_role.
                          Without this, an HR-tier user sees Admin in the
                          grid and only finds out it's forbidden after
                          clicking + watching the 403 toast. */}
                      <div>
                        <h3 className="text-sm font-semibold text-foreground/80 mb-3">Change Role</h3>
                        {/* Self-edit guard — operator can't change their
                            own role from this drawer (lockout-prevention
                            pattern already documented in MEMORY.md project_option_c_nav_permissions). */}
                        {isSelfEdit ? (
                          <p className={`text-xs px-3 py-2 rounded ${toneClasses('warn')}`}>
                            You can't change your own role here. Ask another Owner / Admin to make this change.
                          </p>
                        ) : (
                          <div className="grid grid-cols-2 gap-2">
                            {ROLES.map(r => {
                              const isCurrent = selected.role === r;
                              const isPending = pendingRole === r;
                              // Rank check: this caller can promote to
                              // a role STRICTLY below their own (mirrors
                              // backend ``invite_rank >= caller_rank``).
                              const targetRank = ROLE_RANK[r] ?? 0;
                              const blockedByRank = targetRank >= myRank;
                              return (
                                <button
                                  key={r}
                                  disabled={blockedByRank && !isCurrent}
                                  // ``current`` label dropped from the button face — the role-
                                  // tone highlight (green for fleet, blue for admin, etc.)
                                  // already reads as "this is the current role".  Tooltip
                                  // + aria-current preserve the semantic for keyboard /
                                  // screen-reader users without visual noise.
                                  title={isCurrent
                                    ? `${ROLE_LABEL[r]} (current role)`
                                    : blockedByRank
                                    ? `You can't assign ${ROLE_LABEL[r]} — your role doesn't outrank it.`
                                    : undefined}
                                  aria-current={isCurrent ? 'true' : undefined}
                                  onClick={() => { if (!isCurrent && !blockedByRank) { setPendingRole(r); setConfirmAction('role'); } }}
                                  className={`px-3 py-2 rounded-lg text-sm font-medium transition border ${
                                    isCurrent
                                      ? `${toneClasses(roleTone(r))} border-current cursor-default`
                                      : isPending
                                      ? toneClasses('warn')
                                      : blockedByRank
                                      ? 'bg-muted/30 border-border text-muted-foreground/50 cursor-not-allowed opacity-60'
                                      : 'bg-muted border-border text-muted-foreground hover:border-primary/30 hover:text-foreground/80'
                                  }`}
                                >
                                  {ROLE_LABEL[r]}
                                </button>
                              );
                            })}
                          </div>
                        )}
                        {confirmAction === 'role' && pendingRole && !isSelfEdit && (
                          <div className={`mt-3 p-3 border rounded-lg ${toneClasses('warn')}`}>
                            <p className="text-sm text-warn mb-2">
                              Change {nameOrFallback(selected)}'s role to <strong>{ROLE_LABEL[pendingRole]}</strong>?
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleRoleChange(selected.id, pendingRole)}
                                className="px-4 py-1.5 bg-warn text-white hover:opacity-90 rounded text-xs font-medium transition"
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

                      {/* Working Hours — admin picks one of the named
                          schedules from the catalog (Team Management →
                          Working Hours tab is the SSoT for definitions).
                          Selecting a schedule writes ``assigned_work_hours_id``
                          on the user row; choosing "Use role schedule"
                          clears it so the user inherits the role-level
                          Working Hours.  Rank-gated server-side; UI
                          hides for self-edit + when target outranks. */}
                      {!isSelfEdit && (ROLE_RANK[selected.role] ?? 0) < myRank && (
                        <div className="border-t border-border pt-5">
                          <h3 className="text-sm font-semibold text-foreground/80 mb-3 inline-flex items-center gap-2">
                            <Clock size={14} className="text-muted-foreground" aria-hidden="true" />
                            Working Hours
                          </h3>
                          <label className="block text-2xs text-muted-foreground mb-1 uppercase tracking-wider">
                            Schedule
                          </label>
                          <select
                            value={selected.assigned_work_hours_id ?? ''}
                            disabled={savingQuiet}
                            onChange={e => {
                              const v = e.target.value;
                              handleAssignSchedule(selected.id, v === '' ? null : Number(v));
                            }}
                            className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:border-ring disabled:opacity-50"
                          >
                            <option value="">Use role schedule (default)</option>
                            {workHoursCatalog.map(s => (
                              <option key={s.id} value={s.id}>
                                {s.label} · {fmtHourLabel(s.start_hour)} – {fmtHourLabel(s.end_hour)}
                                {s.target_role !== 'all' ? ` · ${s.target_role}` : ''}
                              </option>
                            ))}
                          </select>
                          <p className="text-2xs text-muted-foreground mt-1.5">
                            {workHoursCatalog.length === 0
                              ? 'No schedules yet — create one in the Working Hours tab first.'
                              : 'To add or edit shifts, open the Working Hours tab above.'}
                          </p>
                        </div>
                      )}

                      {/* Danger zone — Deactivate User.  Self-edit blocked:
                          accidentally deactivating yourself locks you out
                          of the account, recoverable only via another
                          Owner/Admin or support escalation. */}
                      <div className="border-t border-border pt-5">
                        <h3 className="text-sm font-semibold text-danger mb-3">Danger Zone</h3>
                        {isSelfEdit ? (
                          <p className={`text-xs px-3 py-2 rounded ${toneClasses('warn')}`}>
                            You can't deactivate your own account here. Ask another Owner / Admin if you need to suspend access.
                          </p>
                        ) : confirmAction !== 'deactivate' && confirmAction !== 'activate' ? (
                          <button
                            onClick={() => setConfirmAction(selected.is_active ? 'deactivate' : 'activate')}
                            className={`w-full py-2.5 rounded-lg text-sm font-medium transition border ${
                              selected.is_active
                                ? 'border-danger-bd text-danger hover:bg-danger-bg'
                                : 'border-ok-bd text-ok hover:bg-ok-bg'
                            }`}
                          >
                            {selected.is_active ? 'Deactivate User' : 'Activate User'}
                          </button>
                        ) : (
                          <div className={`p-3 rounded-lg border ${
                            selected.is_active ? toneClasses('danger') : toneClasses('ok')
                          }`}>
                            <p className="text-sm mb-2">
                              {selected.is_active
                                ? <span className="text-danger">Deactivate <strong>{nameOrFallback(selected)}</strong>? They will lose access immediately.</span>
                                : <span className="text-ok">Activate <strong>{nameOrFallback(selected)}</strong>? They will regain access.</span>
                              }
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleToggleActive(selected.id, !selected.is_active)}
                                className={`px-4 py-1.5 rounded text-xs font-medium transition text-primary-foreground ${
                                  selected.is_active ? 'bg-danger hover:opacity-90' : 'bg-ok hover:opacity-90'
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
            </UserDrawerShell>
          )}
        </>
      )}
        </>
      )}
    </div>
  );
}

function Row({ label, value, status, action }: {
  label: string;
  value: string;
  status?: 'ok' | 'danger';
  action?: React.ReactNode;
}) {
  return (
    <div className="flex justify-between items-center gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={`inline-flex items-center gap-1 ${
        status === 'ok' ? 'text-ok' : status === 'danger' ? 'text-danger' : ''
      }`}>
        <span>{value}</span>
        {action}
      </dd>
    </div>
  );
}

/**
 * Side-drawer shell — backdrop + slide-in panel with all the
 * dialog-shaped a11y attributes the operator console needs.
 *
 *   - role="dialog" + aria-modal so screen readers announce
 *     "dialog, User details" and trap their virtual cursor.
 *   - aria-labelledby points at the drawer's <h2> heading.
 *   - Escape key closes (matches every other dialog in the app).
 *   - Backdrop click closes (preserved from original).
 *   - autoFocus on the close button so keyboard users have a
 *     reachable first stop; native focus management handles the
 *     rest (Tab cycles within the drawer's tabindex tree).
 *   - max-w-md (28rem / 448px) replaces the original w-[480px]
 *     arbitrary value — on the 4px scale and follows the design
 *     system's "no arbitrary layout values" rule.
 */
function UserDrawerShell({
  displayName,
  onClose,
  children,
}: {
  displayName: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // Auto-focus the first focusable inside the drawer on open.  We
  // ref the dialog panel itself and query its DOM after mount —
  // simpler than threading refs through the children and avoids the
  // previous "hidden sr-only button" trick that triggered Chrome's
  // ``Blocked aria-hidden on an element because its descendant
  // retained focus`` warning (focus inside an aria-hidden tree).
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    // Focus the first interactive element (the close X button) so
    // keyboard users land inside the drawer without Tab-hunting.
    const first = panelRef.current?.querySelector<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    // Backdrop intentionally NOT aria-hidden — the dialog inside
    // owns the semantics via role="dialog" + aria-modal, and
    // aria-hidden on an ancestor of a focused element trips the
    // browser ``Blocked aria-hidden`` warning + breaks screen-
    // reader navigation into the dialog.
    <div
      className="fixed inset-0 bg-black/60 z-50 flex justify-end"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-drawer-title"
        aria-label={`Details for ${displayName}`}
        className="w-full sm:max-w-md bg-card border-l border-border overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
