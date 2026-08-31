import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Users as UsersIcon,
  X,
  Truck,
  User as UserIcon,
  Shield,
  Settings as SettingsIcon,
  Building2,
  Globe,
  Clock,
  Check,
  Mail,
  Send,
  Copy,
  Search,
  Crown,
  IdCard,
} from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Sheet, SheetContent, SheetBody } from '../../components/ui/sheet';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { apiJSON, apiFetch } from '../../api/client';
import { toast } from 'sonner';
import DataGrid, { type DataGridSegment } from '../../components/datagrid';
import { ActionMenu } from '../../components/ui/context-menu';
import { ActivityTrailDialog, ActivityTrailTrigger } from '../../components/activity-trail/ActivityTrailDialog';
import { useTeamMembersQuery } from '../../hooks/useTeamMembers';
import StatusBadge from '../../components/StatusBadge';
import RoleBadge, { ROLE_LABEL, ASSIGNABLE_ROLES, roleTone } from '../../components/RoleBadge';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { toneClasses, toneText } from '../../lib/status';
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
      active ? 'bg-primary/15 text-foreground ring-1 ring-primary' : 'bg-muted text-muted-foreground'
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
            ? 'bg-primary/15 text-foreground ring-1 ring-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        <Mail className="size-3" aria-hidden="true" />
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
            ? 'bg-primary/15 text-foreground ring-1 ring-primary'
            : 'bg-muted text-muted-foreground/50 opacity-60'
        }`}
      >
        <Send className="size-3" aria-hidden="true" />
      </span>
    </span>
  );
}

/** EFFECTIVE role label — the tier IS the display name, so the list reads
 *  "Owner / Co-owner / Full admin / Admin / Recruiter Manager / Recruiter"
 *  instead of a bare base role + side-tags.
 *    · owner        → "Owner" (primary) / "Co-owner"
 *    · senior tier  → the tier label, prefixed with the role when the label
 *      alone doesn't mention it ("Full admin" stands alone; recruiter's
 *      "Manager" becomes "Recruiter Manager")
 *    · otherwise    → the base role label. */
function effectiveRoleLabel(u: Pick<AdminUser, 'role' | 'is_manager' | 'is_primary_owner' | 'tier_senior_label'>): string {
  const base = ROLE_LABEL[u.role] ?? u.role;
  if (u.role === 'owner') return u.is_primary_owner ? 'Owner' : 'Co-owner';
  if (u.is_manager && u.tier_senior_label) {
    const t = u.tier_senior_label;
    return t.toLowerCase().includes(u.role.toLowerCase()) ? t : `${base} ${t}`;
  }
  return base;
}

/** Crown marker for the PRIMARY owner — the one un-removable seat. */
function PrimaryOwnerMark() {
  return (
    <span
      title="Primary owner — can't be removed; alone manages co-owners + account deletion."
      className="inline-flex text-primary"
    >
      <Crown className="size-3" aria-hidden="true" />
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
  {
    key: 'role', label: 'Role', sortable: true,
    // Role column shows the EFFECTIVE tier as the pill ("Full admin",
    // "Co-owner", "Recruiter Manager").  The filter matches on the same
    // effective label so each tier is its own dropdown option — an
    // operator can filter to just Full admins or just Co-owners.
    filterable: true,
    filterValue: (row) => effectiveRoleLabel(row as unknown as AdminUser),
    filterLabel: (row) => effectiveRoleLabel(row as unknown as AdminUser),
    render: (v, row) => {
      const u = row as unknown as AdminUser;
      return (
        <span className="inline-flex items-center gap-1.5">
          <RoleBadge role={String(v)} label={effectiveRoleLabel(u)} />
          {String(v) === 'owner' && u.is_primary_owner && <PrimaryOwnerMark />}
        </span>
      );
    },
  },
  {
    key: 'vehicles', label: 'Vehicles', sortable: false,
    // Filter against the truck list as a single string ("All" for
    // unrestricted) — same shape the cell renders so typing a
    // truck number narrows to that driver.
    filterable: true,
    filterValue: (row) => {
      const u = row as unknown as AdminUser;
      const trucks = u.trucks?.length ? u.trucks : u.truck_num ? [u.truck_num] : [];
      return trucks.length ? trucks.join(' ') : 'All';
    },
    render: (_v, row) => {
      const u = row as unknown as AdminUser;
      const trucks = u.trucks?.length ? u.trucks : u.truck_num ? [u.truck_num] : [];
      if (!trucks.length) return <span className="text-muted-foreground text-xs">All</span>;
      if (trucks.length <= 2) return <span className="text-xs">{trucks.join(', ')}</span>;
      return (
        <span className="text-xs" title={trucks.join(', ')}>
          {trucks[0]}{' '}
          <span className="px-1.5 py-0.5 bg-muted rounded-full text-2xs text-muted-foreground">+{trucks.length - 1}</span>
        </span>
      );
    },
  },
  {
    key: 'allowed_companies', label: 'Companies', sortable: false,
    filterable: true,
    filterValue: (row) => {
      const u = row as unknown as AdminUser;
      return u.allowed_companies?.length ? u.allowed_companies.join(' ') : 'All';
    },
    render: (_v, row) => {
      const u = row as unknown as AdminUser;
      if (!u.allowed_companies?.length) return <span className="text-muted-foreground text-xs">All</span>;
      return <span className="text-xs">{u.allowed_companies.join(', ')}</span>;
    },
  },
  { key: 'email', label: 'Email', filterable: true },
  { key: 'lifecycle', label: 'Status', filterable: true, render: (v) => <StatusBadge status={String(v || 'active')} /> },
];

// Derived sign-in lifecycle from the members API ("pending" = provisioned
// but can't sign in yet — imported from an integration or added by a
// manager; flips to "active" when Telegram is linked or a password is set).
// Local extension: fold into types/index.ts AdminUser once it's free to edit.
type MemberLifecycle = 'active' | 'pending' | 'inactive';
type MemberRow = AdminUser & {
  lifecycle?: MemberLifecycle;
  samsara_driver_id?: string | null;
  datatruck_driver_id?: string | null;
};

// Lifecycle split for the grid's segment tabs.  Same ``?? 'active'``
// fallback the Status column render uses, so tab membership always
// agrees with the row's badge.  Role slicing is NOT a tab — it's the
// Role column filter (effective-tier labels).
const memberLifecycle = (r: Record<string, unknown>): MemberLifecycle => {
  const v = (r as unknown as MemberRow).lifecycle;
  return v === 'pending' || v === 'inactive' ? v : 'active';
};
const TEAM_SEGMENTS: DataGridSegment[] = [
  { key: 'active',   label: 'Active',   match: (r) => memberLifecycle(r) === 'active' },
  { key: 'pending',  label: 'Pending',  match: (r) => memberLifecycle(r) === 'pending' },
  { key: 'inactive', label: 'Inactive', match: (r) => memberLifecycle(r) === 'inactive' },
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
  const [historyOpen, setHistoryOpen] = useState(false);
  // Page-level tab: the member list vs the Invites panel (folded in from
  // the old standalone /admin/invites page).  The Invites tab only shows
  // for users who can actually invite.
  // Page-level tab: members list / invites / working hours.  The
  // Working Hours tab replaces the standalone /admin/work-hours
  // page — same panel, just hosted here so all team-shift config
  // lives under one nav entry.  Gated on can_manage_work_hours —
  // the Settings component's own delegation flag.
  const [pageTab, setPageTab] = useState<'members' | 'invites' | 'working-hours' | 'integration-links'>('members');
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
  // Co-owner promotion / demotion flow (primary-owner only).  ``promote-pw``
  // → enter password → email a code → ``promote-code`` → enter code → done.
  // ``demote-pw`` → password → remove co-owner.
  const [ownerFlow, setOwnerFlow] = useState<null | 'promote-pw' | 'promote-code' | 'demote-pw'>(null);
  const [ownerPassword, setOwnerPassword] = useState('');
  const [ownerCode, setOwnerCode] = useState('');
  const [ownerBusy, setOwnerBusy] = useState(false);
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

  // React Query: cached across navigations, deduped, no manual loading
  // state.  Shared hook (useTeamMembers) — the topbar TeamHero reads
  // the same cache entry, so its counts always equal this page's.
  const { data: usersData, isLoading: loading, error: usersError } = useTeamMembersQuery();
  // Count badge for the Integration-links surface tab — eager fetch of
  // the same cache entry the panel reads, so the badge is live before
  // the tab is opened and can't disagree with the panel's own header.
  const { data: linksData } = useIntegrationLinksQuery();
  const linksTotal = unlinkedTotal(linksData);
  const allUsers = useMemo(() => usersData?.users ?? [], [usersData]);
  // Drivers are managed in the Drivers feature — hide them from the STAFF list
  // by default (a chip reveals them; still needed here for promote-to-staff).
  const [showDrivers, setShowDrivers] = useState(false);
  const driverCount = useMemo(() => allUsers.filter((u) => u.role === 'driver').length, [allUsers]);
  const users = useMemo(
    () => (showDrivers ? allUsers : allUsers.filter((u) => u.role !== 'driver')),
    [allUsers, showDrivers],
  );
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
  // Schedule-picker items for the drawer.  The leading "" value is the
  // real "Use role schedule (default)" choice (clears the assignment),
  // NOT a disabled placeholder — so it stays selectable in the list.
  const scheduleItems = useMemo(() => [
    { value: '', label: 'Use role schedule (default)' },
    ...workHoursCatalog.map((s) => ({
      value: String(s.id),
      label: `${s.label} · ${fmtHourLabel(s.start_hour)} – ${fmtHourLabel(s.end_hour)}${s.target_role !== 'all' ? ` · ${s.target_role}` : ''}`,
    })),
  ], [workHoursCatalog]);

  const loadUsers = useCallback(
    () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    [qc],
  );

  // Filtered users by role

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

  // Set/clear the per-user manager tier (orthogonal to role — the user's
  // role is unchanged; they just gain/lose the team-lead grants).
  const handleManagerToggle = async (userId: number, is_manager: boolean) => {
    try {
      await apiJSON('/admin/users/' + userId + '/manager', { method: 'PUT', body: { is_manager } });
      setSuccess(is_manager
        ? 'Promoted to manager — takes effect after they next sign in'
        : 'Manager tier removed — takes effect after they next sign in');
      await loadUsers();
      if (selected) setSelected({ ...selected, is_manager });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  // ── Co-owner promotion / demotion (primary-owner only) ──
  const resetOwnerFlow = () => { setOwnerFlow(null); setOwnerPassword(''); setOwnerCode(''); };

  // Step 1 of promote — password → email a 6-digit code.
  const handlePromoteOwnerRequest = async (userId: number) => {
    setOwnerBusy(true);
    try {
      const res = await apiJSON<{ email: string }>(
        '/admin/users/' + userId + '/promote-owner',
        { method: 'POST', body: { password: ownerPassword } },
      );
      setSuccess('Confirmation code sent to ' + res.email);
      setOwnerPassword('');
      setOwnerFlow('promote-code');
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setOwnerBusy(false); }
  };

  // Step 2 of promote — code → apply (Admin becomes co-owner).
  const handlePromoteOwnerConfirm = async (userId: number) => {
    setOwnerBusy(true);
    try {
      await apiJSON('/admin/users/' + userId + '/promote-owner/confirm',
        { method: 'POST', body: { code: ownerCode } });
      setSuccess('Co-owner added');
      resetOwnerFlow();
      await loadUsers();
      if (selected) setSelected({ ...selected, role: 'owner', is_primary_owner: false });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setOwnerBusy(false); }
  };

  // Remove a co-owner (→ Admin) — password only.
  const handleDemoteOwner = async (userId: number) => {
    setOwnerBusy(true);
    try {
      await apiJSON('/admin/users/' + userId + '/demote-owner',
        { method: 'POST', body: { password: ownerPassword } });
      setSuccess('Co-owner removed');
      resetOwnerFlow();
      await loadUsers();
      if (selected) setSelected({ ...selected, role: 'admin' });
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setOwnerBusy(false); }
  };

  // Reset the owner flow whenever the drawer switches users, so a half-
  // finished promote/demote doesn't leak into the next user.
  useEffect(() => { setOwnerFlow(null); setOwnerPassword(''); setOwnerCode(''); }, [selected?.id]);

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
      {/* Always shown — Members + Integration links exist for every
          viewer of this page; Invites / Working Hours stay
          permission-gated per entry. */}
      {(
        <div role="tablist" aria-label="Team management sections" className="flex gap-1 mb-4 border-b border-border">
          {([
            { key: 'members'           as const, label: 'Members',           show: true },
            { key: 'invites'           as const, label: 'Invites',           show: canInvite },
            { key: 'working-hours'     as const, label: 'Working Hours',     show: canManageWorkHours },
            // External identities (Datatruck drivers / load names) that
            // aren't member rows yet — Invites' sibling: people on the
            // way IN, deliberately NOT a members-grid segment tab (the
            // grid tabs slice the members dataset; these 100+ rows
            // aren't members and would break the tab arithmetic).
            { key: 'integration-links' as const, label: 'Integration links', show: true },
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
                {key === 'integration-links' && linksTotal != null && linksTotal > 0 && (
                  <span className="ml-1.5 tabular-nums text-2xs px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                    {linksTotal}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {pageTab === 'invites' ? (
        <InvitesPanel />
      ) : pageTab === 'working-hours' ? (
        <WorkHoursPanel />
      ) : pageTab === 'integration-links' ? (
        <IntegrationLinksPanel
          // allUsers, not the driver-filtered `users`: the panel derives its
          // link pool from driver rows, which the staff list hides by default.
          members={allUsers}
          onChanged={() => { void loadUsers(); }}
        />
      ) : (
        <>

      {error && (
        <div className="mb-3"><ErrorState message={error} /></div>
      )}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      {/* The role chip row + passive "Pending sign-in" pill that
          lived here are gone: lifecycle = the grid's Active / Pending
          / Inactive segment tabs (Pending's live count is now a
          clickable tab), role slicing = the Role column filter
          (effective-tier labels), and the composition counts live in
          the topbar TeamHero. */}

      {/* Integration links moved to its own surface tab — external
          identities aren't members, so they don't belong above (or
          inside) the members grid. */}

      {loading && allUsers.length === 0 ? <TableSkeleton rows={6} cols={5} /> : allUsers.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title="No team members yet"
          description="Invite teammates from the Invites page to get started."
        />
      ) : (
        <>
          {driverCount > 0 && (
            <div className="flex items-center justify-end mb-2">
              <button
                onClick={() => setShowDrivers((v) => !v)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-2xs font-medium border transition ${
                  showDrivers
                    ? 'bg-primary/10 border-primary text-foreground'
                    : 'bg-muted border-border text-muted-foreground hover:border-primary/30'
                } min-h-tap`}
                title="Drivers are managed in the Drivers feature; shown here for role changes (promote to staff)."
              >
                <IdCard className="size-3" />
                {showDrivers ? 'Hide drivers' : `Show drivers (${driverCount})`}
              </button>
            </div>
          )}
          <DataGrid
            tableId="team-management"
            segments={TEAM_SEGMENTS}
            columns={userColumns}
            data={users as unknown as Record<string, unknown>[]}
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
                        <RoleBadge role={selected.role} label={effectiveRoleLabel(selected)} />
                        {selected.role === 'owner' && selected.is_primary_owner && <PrimaryOwnerMark />}
                      </div>
                    </div>
                  </div>
                  <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X className="size-4" /></button>
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
                        } min-h-tap`}
                      >
                        <Icon className="size-3" aria-hidden="true" />
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
                        <IdentityLinks
                          key={selected.id}
                          member={selected as MemberRow}
                          onPatched={(patch) => {
                            setSelected({ ...(selected as MemberRow), ...patch } as AdminUser);
                            void loadUsers();
                          }}
                        />
                        <Row label="Language" value={(selected.language || '—').toUpperCase()} />
                        <Row
                          label="Status"
                          value={(selected as MemberRow).lifecycle === 'pending' ? 'Pending sign-in' : selected.is_active ? 'Active' : 'Inactive'}
                          status={selected.is_active ? 'ok' : 'danger'}
                        />
                        {/* Role changes, tier flips, company access —
                            the member's own who-did-what. */}
                        <div className="pt-2 border-t border-border">
                          <ActivityTrailTrigger onClick={() => setHistoryOpen(true)} />
                          <ActivityTrailDialog
                            entityType="user"
                            entityId={selected.id}
                            title={`${nameOrFallback(selected)} — activity history`}
                            open={historyOpen}
                            onOpenChange={setHistoryOpen}
                          />
                        </div>
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
                              <Building2 aria-hidden="true" className="text-muted-foreground size-3.5" />
                              {allCompanies.find(c => c.id === editCompanyIds[0])?.code || '—'}
                              <span className="text-xs text-muted-foreground font-normal">
                                {editVehicles.length > 0 && `· ${editVehicles.length} vehicle${editVehicles.length === 1 ? '' : 's'}`}
                              </span>
                            </>
                          ) : accessScope === 'all' ? (
                            <><Globe aria-hidden="true" className="text-muted-foreground size-3.5" />All vehicles</>
                          ) : accessScope === 'company' ? (
                            <>
                              <Building2 aria-hidden="true" className="text-muted-foreground size-3.5" />
                              {editCompanyIds.length} {editCompanyIds.length === 1 ? 'company' : 'companies'}
                            </>
                          ) : (
                            <>
                              <Truck aria-hidden="true" className="text-muted-foreground size-3.5" />
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
                              <Building2 className="size-3" aria-hidden="true" />
                              One company at a time.
                            </p>
                            <p>Changing this driver's company will archive their previous CDL / medical / DQF documents to <code className="font-mono text-2xs py-1 -my-1 min-h-tap">{'{old company}'}/drivers/_archive/{'{today}'}/</code>.</p>
                          </div>
                          <h3 className="text-sm font-semibold text-foreground/80 flex items-center gap-2">
                            <Building2 className="text-muted-foreground size-3.5" aria-hidden="true" />
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
                            <Truck className="text-muted-foreground size-3.5" aria-hidden="true" />
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
                                  <Icon className="size-3.5" aria-hidden="true" />
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
                                    className="text-2xs text-primary hover:text-primary/80 uppercase tracking-wider py-1 -my-1 min-h-tap"
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
                                          {checked && <Check className="text-primary-foreground size-3" aria-hidden="true" />}
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
                                    className="text-2xs text-primary hover:text-primary/80 uppercase tracking-wider py-1 -my-1 min-h-tap"
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
                                      aria-hidden="true"
                                      className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none size-3.5"
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
                                              {checked && <Check className="text-primary-foreground size-3" aria-hidden="true" />}
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
                            Previous documents under <strong>{pendingAccessSave.oldCodes}</strong> will be archived to <code className="font-mono text-2xs">{'{company}'}/drivers/_archive/{'{today}'}/</code> — files remain in Drive but move to the dated archive folder.
                          </p>
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleSaveAccess(selected.id, true)}
                              disabled={savingCompanies || savingVehicles}
                              className="px-4 py-1.5 bg-warn text-warn-foreground hover:opacity-90 disabled:opacity-50 rounded text-xs font-medium transition min-h-tap"
                            >
                              {savingCompanies || savingVehicles ? 'Saving…' : 'Confirm & save'}
                            </button>
                            <button
                              onClick={() => setPendingAccessSave(null)}
                              disabled={savingCompanies || savingVehicles}
                              className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => handleSaveAccess(selected.id)}
                          disabled={savingCompanies || savingVehicles}
                          className="w-full py-2.5 bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 rounded-lg text-sm font-semibold transition"
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
                          <p className={`text-xs px-3 py-2 rounded-md ${toneClasses('warn')}`}>
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
                                className="px-4 py-1.5 bg-warn text-warn-foreground hover:opacity-90 rounded text-xs font-medium transition min-h-tap"
                              >
                                Confirm
                              </button>
                              <button
                                onClick={() => { setConfirmAction(null); setPendingRole(null); }}
                                className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Manager tier — a per-user seniority ON the current
                          role (NOT a role change).  Only shown for roles that
                          have a manager tier (``manager_capable``); rank-gated
                          server-side + mirrored here.  The user keeps their
                          dashboard; they just gain the team-lead grants.
                          Rendered as the app's canonical settings switch
                          (matches the DND toggle in Profile). */}
                      {selected.manager_capable && !isSelfEdit && (() => {
                        const cannotModify = (ROLE_RANK[selected.role] ?? 0) >= myRank;
                        const roleLabel = ROLE_LABEL[selected.role] ?? selected.role;
                        const tierLabel = selected.tier_senior_label ?? 'Manager';
                        return (
                          <div>
                            <h3 className="text-sm font-semibold text-foreground/80 mb-2">Seniority</h3>
                            <div className="flex items-center justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3">
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium inline-flex items-center gap-1.5">
                                  <Shield className="text-primary shrink-0 size-3.5" aria-hidden="true" />
                                  {tierLabel}
                                </p>
                                <p className="text-xs text-muted-foreground mt-0.5">
                                  {selected.is_manager
                                    ? `Keeps the ${roleLabel} dashboard, plus the extra ${tierLabel.toLowerCase()} permissions (shown with a shield in the Permissions matrix).`
                                    : `Promote to ${tierLabel} — same ${roleLabel} dashboard, plus the extra permissions shown with a shield in the Permissions matrix.`}
                                </p>
                                {cannotModify && (
                                  <p className="text-2xs text-muted-foreground/70 mt-1">
                                    Your role doesn't outrank {roleLabel}.
                                  </p>
                                )}
                                {!cannotModify && (
                                  <p className="text-2xs text-muted-foreground/70 mt-1">
                                    Takes effect after {roleLabel} next signs in — an active session keeps the current tier until then.
                                  </p>
                                )}
                              </div>
                              <button
                                type="button"
                                role="switch"
                                aria-checked={selected.is_manager}
                                aria-label={`${tierLabel} tier`}
                                disabled={cannotModify}
                                onClick={() => handleManagerToggle(selected.id, !selected.is_manager)}
                                className={`shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition ${
                                  cannotModify
                                    ? 'bg-muted-foreground/20 cursor-not-allowed opacity-60'
                                    : selected.is_manager ? 'bg-primary' : 'bg-muted-foreground/30'
                                } min-h-tap`}
                              >
                                <span
                                  className={`inline-block h-5 w-5 transform rounded-full bg-background shadow transition ${
                                    selected.is_manager ? 'translate-x-5' : 'translate-x-0.5'
                                  }`}
                                />
                              </button>
                            </div>
                          </div>
                        );
                      })()}

                      {/* Ownership — co-owner promote/demote.  Visible ONLY to
                          the PRIMARY owner, and never for self.  Promote is a
                          two-factor flow (password → emailed code); demote is
                          password-only. */}
                      {me?.is_primary_owner && !isSelfEdit
                        && (selected.role === 'admin'
                            || (selected.role === 'owner' && !selected.is_primary_owner)) && (
                        <div>
                          <h3 className="text-sm font-semibold text-foreground/80 mb-2">Ownership</h3>
                          {selected.role === 'admin' ? (
                            <div className="rounded-lg border border-border bg-muted/30 p-3">
                              <p className="text-xs text-muted-foreground mb-3">
                                Make {nameOrFallback(selected)} a <span className="font-medium text-foreground/80">co-owner</span> —
                                full owner access (billing, users, settings). They won't
                                become the primary owner and can't remove you or delete the
                                account. Confirmed with your password + an emailed code.
                              </p>
                              {ownerFlow === null && (
                                <button
                                  onClick={() => setOwnerFlow('promote-pw')}
                                  className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border border-border bg-muted text-muted-foreground hover:border-primary/30 hover:text-foreground/80 transition"
                                >
                                  <Crown className="size-3.5" /> Make co-owner
                                </button>
                              )}
                              {ownerFlow === 'promote-pw' && (
                                <div className="space-y-2">
                                  <input
                                    type="password" autoComplete="current-password"
                                    value={ownerPassword} onChange={(e) => setOwnerPassword(e.target.value)}
                                    placeholder="Your password"
                                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm"
                                  />
                                  <div className="flex gap-2">
                                    <button
                                      disabled={ownerBusy || !ownerPassword}
                                      onClick={() => handlePromoteOwnerRequest(selected.id)}
                                      className="px-4 py-1.5 bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 rounded text-xs font-medium transition min-h-tap"
                                    >Send code</button>
                                    <button onClick={resetOwnerFlow} className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap">Cancel</button>
                                  </div>
                                </div>
                              )}
                              {ownerFlow === 'promote-code' && (
                                <div className="space-y-2">
                                  <p className="text-2xs text-muted-foreground">Enter the 6-digit code we emailed you.</p>
                                  <input
                                    inputMode="numeric" maxLength={6}
                                    value={ownerCode} onChange={(e) => setOwnerCode(e.target.value.replace(/\D/g, ''))}
                                    placeholder="6-digit code"
                                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm tracking-widest"
                                  />
                                  <div className="flex gap-2">
                                    <button
                                      disabled={ownerBusy || ownerCode.length < 6}
                                      onClick={() => handlePromoteOwnerConfirm(selected.id)}
                                      className="px-4 py-1.5 bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 rounded text-xs font-medium transition min-h-tap"
                                    >Confirm co-owner</button>
                                    <button onClick={resetOwnerFlow} className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap">Cancel</button>
                                  </div>
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="rounded-lg border border-border bg-muted/30 p-3">
                              <p className="text-xs text-muted-foreground mb-3">
                                Remove {nameOrFallback(selected)}'s owner access — they
                                become an <span className="font-medium text-foreground/80">Admin</span>. Confirm with your password.
                              </p>
                              {ownerFlow !== 'demote-pw' ? (
                                <button
                                  onClick={() => setOwnerFlow('demote-pw')}
                                  className={`inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border transition ${toneClasses('danger')} border-current`}
                                >Remove owner</button>
                              ) : (
                                <div className="space-y-2">
                                  <input
                                    type="password" autoComplete="current-password"
                                    value={ownerPassword} onChange={(e) => setOwnerPassword(e.target.value)}
                                    placeholder="Your password"
                                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm"
                                  />
                                  <div className="flex gap-2">
                                    <button
                                      disabled={ownerBusy || !ownerPassword}
                                      onClick={() => handleDemoteOwner(selected.id)}
                                      className="px-4 py-1.5 bg-danger text-danger-foreground hover:opacity-90 disabled:opacity-50 rounded text-xs font-medium transition min-h-tap"
                                    >Confirm removal</button>
                                    <button onClick={resetOwnerFlow} className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap">Cancel</button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}

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
                            <Clock className="text-muted-foreground size-3.5" aria-hidden="true" />
                            Working Hours
                          </h3>
                          <label className="block text-2xs text-muted-foreground mb-1 uppercase tracking-wider">
                            Schedule
                          </label>
                          <Select
                            value={String(selected.assigned_work_hours_id ?? '')}
                            disabled={savingQuiet}
                            onValueChange={(v) => handleAssignSchedule(selected.id, v ? Number(v) : null)}
                            items={scheduleItems}
                          >
                            <SelectTrigger className="w-full" aria-label="Schedule">
                              <SelectValue placeholder="Use role schedule (default)" />
                            </SelectTrigger>
                            <SelectContent>
                              {scheduleItems.map((it) => (
                                <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
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
                          <p className={`text-xs px-3 py-2 rounded-md ${toneClasses('warn')}`}>
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
                                // The foreground moves WITH the fill. It used to
                                // sit outside the branches as
                                // `text-primary-foreground` on a `bg-danger` /
                                // `bg-ok` fill — two different families — which
                                // measured 2.78 and 1.93 on the dark themes.
                                className={`px-4 py-1.5 rounded text-xs font-medium transition ${
                                  selected.is_active
                                    ? 'bg-danger text-danger-foreground hover:opacity-90'
                                    : 'bg-ok text-ok-foreground hover:opacity-90'
                                } min-h-tap`}
                              >
                                {selected.is_active ? 'Deactivate' : 'Activate'}
                              </button>
                              <button
                                onClick={() => setConfirmAction(null)}
                                className="px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground transition min-h-tap"
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

// ── Drawer identity links ───────────────────────────────────────
//
// The sign-in / integration identities of ONE member, managed in place:
// Telegram (mint a sign-in deep link the manager hands to the person),
// Samsara driver (live roster picker) and Datatruck driver (staged
// roster picker).  Rosters load once per drawer open; refs already held
// by another member are disabled so nothing gets double-linked.

interface DatatruckSourceEntry {
  external_id: string; name: string; status: string;
  truck_unit: string;
  linked_user_id: number | null;
}
interface SamsaraSourceEntry {
  samsara_driver_id: string; name: string; company_code: string;
  deactivated: boolean; linked_user_id: number | null;
}
interface SourcesResponse {
  datatruck: DatatruckSourceEntry[];
  samsara: SamsaraSourceEntry[];
  samsara_error: string | null;
}

function IdentityLinks({ member, onPatched }: {
  member: MemberRow;
  onPatched: (patch: Partial<MemberRow>) => void;
}) {
  const isDriver = String(member.role) === 'driver';
  const [sources, setSources] = useState<SourcesResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [samsaraPick, setSamsaraPick] = useState('');
  const [dtPick, setDtPick] = useState('');
  const [inviteLink, setInviteLink] = useState<string | null>(null);

  useEffect(() => {
    if (!isDriver) return;          // pickers only render for drivers
    let alive = true;
    apiJSON<SourcesResponse>('/admin/users/integration-sources')
      .then((d) => { if (alive) setSources(d); })
      .catch(() => { /* rows fall back to read-only */ });
    return () => { alive = false; };
  }, [isDriver]);

  const act = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const setSamsara = (id: string) => act(async () => {
    await apiJSON(`/admin/users/${member.id}/samsara-driver-id`, {
      method: 'PUT', body: { samsara_driver_id: id },
    });
    toast.success(id ? 'Samsara driver linked' : 'Samsara driver unlinked');
    setSamsaraPick('');
    onPatched({ samsara_driver_id: id || null });
  });

  const setDatatruck = (id: string) => act(async () => {
    const r = await apiJSON<{ loads_backfilled: number }>(
      `/admin/users/${member.id}/link-datatruck-driver`,
      { method: 'POST', body: { external_id: id } },
    );
    toast.success(id
      ? `Datatruck driver linked${r.loads_backfilled ? ` · ${r.loads_backfilled} load${r.loads_backfilled === 1 ? '' : 's'} attached` : ''}`
      : 'Datatruck driver unlinked');
    setDtPick('');
    onPatched({ datatruck_driver_id: id || null });
  });

  const mintTelegramLink = () => act(async () => {
    const r = await apiJSON<{ deep_link: string; expires_hours: number }>(
      `/admin/users/${member.id}/telegram-invite`, { method: 'POST' },
    );
    setInviteLink(r.deep_link);
  });

  const copyText = (text: string, okMsg: string) => {
    navigator.clipboard.writeText(text)
      .then(() => toast.success(okMsg))
      .catch(() => toast.error('Copy failed'));
  };

  const samsaraName = member.samsara_driver_id
    ? sources?.samsara.find((s) => s.samsara_driver_id === member.samsara_driver_id)?.name
      || member.samsara_driver_id
    : null;
  const dtName = member.datatruck_driver_id
    ? sources?.datatruck.find((d) => d.external_id === member.datatruck_driver_id)?.name
      || member.datatruck_driver_id
    : null;

  // Every identity row keeps its control in the SAME place — the value
  // slot on the right.  Unlinked = a fixed-width picker whose placeholder
  // reads "Not linked" + a Link button; linked = the resolved name + an
  // Unlink button.  Fixed w-44 keeps the two pickers identical regardless
  // of option text length.  Already-linked / inactive rows stay disabled
  // in the list so a manager can't double-assign one external identity.
  const samsaraItems = (sources?.samsara ?? []).map((s) => ({
    value: s.samsara_driver_id,
    label: `${s.name}${s.company_code ? ` · ${s.company_code}` : ''}${s.linked_user_id != null ? ' (linked)' : s.deactivated ? ' (inactive)' : ''}`,
    disabled: s.linked_user_id != null || s.deactivated,
  }));
  const datatruckItems = (sources?.datatruck ?? []).map((d) => ({
    value: d.external_id,
    label: `${d.name}${d.truck_unit ? ` · ${d.truck_unit}` : ''}${d.linked_user_id != null ? ' (linked)' : ''}`,
    disabled: d.linked_user_id != null,
  }));

  return (
    <>
      <Row
        label="Telegram ID"
        value={member.telegram_id ? String(member.telegram_id) : 'Not linked'}
        action={member.telegram_id ? (
          <button
            type="button"
            onClick={() => copyText(String(member.telegram_id), 'Telegram ID copied')}
            className="text-muted-foreground hover:text-primary p-0.5"
            aria-label="Copy Telegram ID"
            title="Copy Telegram ID"
          >
            <Copy className="size-3" />
          </button>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={busy}
            onClick={mintTelegramLink}
          >
            Sign-in link
          </Button>
        )}
      />
      {!member.telegram_id && inviteLink && (
        <div className="rounded bg-muted/50 px-2 py-1.5">
          <div className="flex items-center gap-1.5">
            <span className="flex-1 truncate font-mono text-2xs text-foreground">{inviteLink}</span>
            <button
              type="button"
              onClick={() => copyText(inviteLink, 'Sign-in link copied')}
              className="text-muted-foreground hover:text-primary p-0.5"
              aria-label="Copy sign-in link"
              title="Copy sign-in link"
            >
              <Copy className="size-3" />
            </button>
          </div>
          <p className="mt-1 text-2xs text-muted-foreground">
            Valid 72 h. Share it with this member — opening it in Telegram links
            their account and activates sign-in.
          </p>
        </div>
      )}
      {isDriver && (
        <>
          <div className="flex justify-between items-center gap-2">
            <dt className="text-muted-foreground">Samsara driver</dt>
            <dd className="inline-flex min-w-0 items-center gap-1.5">
              {member.samsara_driver_id ? (
                <>
                  <span className="truncate text-foreground">{samsaraName}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy}
                    onClick={() => setSamsara('')}
                  >
                    Unlink
                  </Button>
                </>
              ) : (
                <>
                  <Select value={samsaraPick} onValueChange={(v) => setSamsaraPick(v ?? '')} items={samsaraItems}>
                    <SelectTrigger className="w-44" aria-label="Link Samsara driver"><SelectValue placeholder="Not linked" /></SelectTrigger>
                    <SelectContent>
                      {samsaraItems.map((it) => (
                        <SelectItem key={it.value} value={it.value} disabled={it.disabled}>{it.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy || !samsaraPick}
                    onClick={() => setSamsara(samsaraPick)}
                  >
                    Link
                  </Button>
                </>
              )}
            </dd>
          </div>
          <div className="flex justify-between items-center gap-2">
            <dt className="text-muted-foreground">Datatruck driver</dt>
            <dd className="inline-flex min-w-0 items-center gap-1.5">
              {member.datatruck_driver_id ? (
                <>
                  <span className="truncate text-foreground">{dtName}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy}
                    onClick={() => setDatatruck('')}
                  >
                    Unlink
                  </Button>
                </>
              ) : (
                <>
                  <Select value={dtPick} onValueChange={(v) => setDtPick(v ?? '')} items={datatruckItems}>
                    <SelectTrigger className="w-44" aria-label="Link Datatruck driver"><SelectValue placeholder="Not linked" /></SelectTrigger>
                    <SelectContent>
                      {datatruckItems.map((it) => (
                        <SelectItem key={it.value} value={it.value} disabled={it.disabled}>{it.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={busy || !dtPick}
                    onClick={() => setDatatruck(dtPick)}
                  >
                    Link
                  </Button>
                </>
              )}
            </dd>
          </div>
        </>
      )}
    </>
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
 *   - max-w-md (28rem / 448px) replaces the original w-120
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
  // Everything this used to hand-roll — an Escape listener on window, a
  // querySelector for the first focusable, a ref on the panel, and a
  // comment explaining how to dodge Chrome's ``Blocked aria-hidden``
  // warning — is what <Sheet> (Base UI Dialog) does properly, including
  // the two it could never do from here: a real focus TRAP (the old code
  // only placed initial focus; Tab still walked out into the page) and a
  // background scroll lock.
  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side="right"
        className="p-0"
        size="lg"
        aria-labelledby="user-drawer-title"
        aria-label={`Details for ${displayName}`}
        showCloseButton={false}
      >
        <SheetBody label={`Details for ${displayName}`}>
          {children}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}


// ── Integration links panel ─────────────────────────────────────
//
// Synced people not linked to a member yet: Datatruck drivers (from the
// import plan) + dispatcher/driver names on loads.  Each row offers
// "link to an existing member" (continues that person's data) or "add as
// pending user" (no login until they sign in).  Lives in its own
// surface tab (Integration links) with a live count badge.

interface LinkPlanEntry {
  external_id: string; name: string; phone: string; email: string;
  matched_user_id?: number; matched_name?: string; reason?: string;
}
interface LinksResponse {
  // External-integration identities only.  Load-sheet names are NOT here —
  // "Loads" isn't an integration (rows can be manual or datatruck); a free-
  // text name on a load is operational data, not an integration identity.
  datatruck_drivers: {
    create: LinkPlanEntry[]; link: LinkPlanEntry[]; review: LinkPlanEntry[];
    counts: Record<string, number>;
  };
}

// Shared query for the integration-links plan — read by BOTH the
// surface-tab count badge and the panel body, so the badge is live
// before the tab is ever opened and the two can't disagree.
function useIntegrationLinksQuery() {
  return useQuery({
    queryKey: ['integration-links'],
    queryFn: () => apiJSON<LinksResponse>('/admin/users/integration-links'),
  });
}
function unlinkedTotal(data: LinksResponse | undefined): number | null {
  if (!data) return null;
  return data.datatruck_drivers.counts.create
    + data.datatruck_drivers.counts.link
    + data.datatruck_drivers.counts.review;
}

function IntegrationLinksPanel({ members, onChanged }: {
  members: AdminUser[];
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const { data } = useIntegrationLinksQuery();
  const [busy, setBusy] = useState(false);

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      toast.success(okMsg);
      void qc.invalidateQueries({ queryKey: ['integration-links'] });
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const linkDriver = (externalId: string, userId: number) =>
    act(() => apiJSON(`/admin/users/${userId}/link-datatruck-driver`, {
      method: 'POST', body: { external_id: externalId },
    }), 'Driver linked');

  const provision = (body: Record<string, string>) =>
    act(() => apiJSON('/admin/users/provision', { method: 'POST', body }),
      'Added as pending member');

  const drivers = members.filter((m) => String(m.role) === 'driver');
  const total = unlinkedTotal(data);

  // Flattened row model for the grid — one row per unlinked identity,
  // whatever its origin (Datatruck import plan bucket or a load-sheet
  // name).  ``typeLabel`` doubles as the Source column's filter value.
  interface LinkRow extends Record<string, unknown> {
    id: string;
    name: string;
    /** Integration origin — which system surfaced this identity. */
    source: string;
    /** What the person is — same vocabulary as the members grid. */
    role: 'driver' | 'dispatcher';
    email?: string;
    phone?: string;
    loads?: number;
    field?: 'driver' | 'dispatcher';
    external_id?: string;
    bucket?: 'link' | 'create' | 'review';
    matched_user_id?: number;
    matched_name?: string;
    reason?: string;
  }
  const rows = useMemo<LinkRow[]>(() => {
    if (!data) return [];
    const out: LinkRow[] = [];
    (['link', 'create', 'review'] as const).forEach((bucket) =>
      data.datatruck_drivers[bucket].forEach((d) => out.push({
        id: `dt-${d.external_id}`,
        name: d.name,
        source: 'Datatruck',
        role: 'driver',
        email: d.email || undefined,
        phone: d.phone || undefined,
        external_id: d.external_id,
        bucket,
        matched_user_id: d.matched_user_id,
        matched_name: d.matched_name,
        reason: d.reason,
      })),
    );
    return out;
  }, [data]);


  // One-press link: the button IS the picker — pressing it lists the
  // eligible members and choosing one links immediately.  Replaces
  // the old two-step ``[Link to member… ▾][Link]`` pair, whose
  // separate dead-until-picked Link button read as confusing.
  // The verb stays "Link" (NOT "Merge"): nothing merges here — the
  // external identity is attached to the member and the association
  // is reversible; "Merge" would imply combining two records into
  // one, which this never does.
  const LinkMemberMenu = ({ pool, onPick }: {
    pool: AdminUser[];
    onPick: (userId: number) => void;
  }) => {
    const trigger = (
      <Button
        type="button"
        variant="outline"
        size="xs"
        disabled={busy || pool.length === 0}
        title={pool.length === 0 ? 'No eligible members to link' : undefined}
      >
        Link to member…
      </Button>
    );
    // Empty pool → the disabled button stands alone (ActionMenu renders
    // nothing when it has no items, which would drop the affordance).
    if (pool.length === 0) return trigger;
    return (
      <ActionMenu
        align="start"
        items={pool.map((m) => ({
          key: String(m.id),
          label: m.display_name || `#${m.id}`,
          onSelect: () => onPick(m.id),
        }))}
      >
        {trigger}
      </ActionMenu>
    );
  };

  const columns: AnyColumn[] = [
    { key: 'name', label: 'Name', sortable: true },
    {
      // Origin system only ("Datatruck" import plan / names seen on
      // "Loads") — what the person IS lives in the Role column, same
      // split as everywhere else in the app.
      key: 'source', label: 'Source', sortable: true, filterable: true,
    },
    {
      key: 'role', label: 'Role', sortable: true,
      filterable: true,
      filterValue: (row) => String((row as LinkRow).role),
      filterLabel: (row) => ROLE_LABEL[(row as LinkRow).role] ?? String((row as LinkRow).role),
      render: (v) => <RoleBadge role={String(v)} />,
    },
    {
      key: 'details', label: 'Details', sortable: false,
      csvValue: (row) => {
        const r = row as LinkRow;
        return r.loads != null
          ? `${r.loads} load${r.loads === 1 ? '' : 's'}`
          : [r.email, r.phone].filter(Boolean).join(' · ');
      },
      render: (_v, row) => {
        const r = row as LinkRow;
        return (
          <span className="text-xs text-muted-foreground">
            {r.loads != null
              ? `on ${r.loads} load${r.loads === 1 ? '' : 's'}`
              : [r.email, r.phone].filter(Boolean).join(' · ') || '—'}
            {r.bucket === 'review' && r.reason && (
              <> · <span className={toneText('warn')}>{r.reason}</span></>
            )}
          </span>
        );
      },
    },
    {
      key: 'suggested', label: 'Suggested match', sortable: false,
      csvValue: (row) => (row as LinkRow).matched_name ?? '',
      render: (_v, row) => {
        const r = row as LinkRow;
        if (r.bucket === 'link' && r.matched_user_id != null) {
          return (
            <Button
              type="button"
              size="xs"
              disabled={busy}
              onClick={(e) => { e.stopPropagation(); void linkDriver(r.external_id!, r.matched_user_id!); }}
            >
              Link to {r.matched_name}
            </Button>
          );
        }
        return <span className="text-muted-foreground text-xs">—</span>;
      },
    },
    {
      key: 'actions', label: 'Actions', sortable: false, locked: true,
      csvValue: () => '',
      render: (_v, row) => {
        const r = row as LinkRow;
        return (
          <span className="inline-flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <LinkMemberMenu
              pool={drivers}
              onPick={(uid) => linkDriver(r.external_id!, uid)}
            />
            {r.bucket !== 'link' && (
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={busy}
                onClick={() => provision({
                  kind: 'driver', name: r.name, email: r.email || '',
                  phone: r.phone || '', datatruck_driver_id: r.external_id!,
                })}
              >
                Add as pending member
              </Button>
            )}
          </span>
        );
      },
    },
  ];

  if (!data) return <TableSkeleton rows={6} cols={5} />;
  if (total === 0) {
    return (
      <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
        All integration identities are linked — nothing to do here. ✓
      </div>
    );
  }
  return (
    <DataGrid
      tableId="integration-links"
      columns={columns}
      data={rows}
      searchKey={['name', 'email']}
      searchPlaceholder="Search name or email…"
    />
  );
}
