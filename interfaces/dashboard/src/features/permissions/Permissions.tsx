import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield, Check, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { InfoTip } from '../../components/tooltip';
import { toneClasses } from '../../lib/status';
import { RoleLens } from './RoleLens';
import type { RoleLensApi } from './RoleLens';

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

import {
  ALL_MATRIX_FLAGS, DEFAULT_SCOPED_FLAGS, DRIVER_PANEL_FLAGS,
  GROUP_MODULE, OWNER_PROTECTED, contextLabel, isHeader, isScoped,
} from './permRows';
import type { ModulesData, PermFlag } from './permRows';
import { Dialog, DialogContent } from '../../components/ui/dialog';


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

  // The RoleLens edits through the SAME pipeline: same storage keys,
  // same toggle, same pending-edits diff and confirm dialog.
  const roleLensApi: RoleLensApi = {
    roles: ROLES,
    roleLabel: (r) => ROLE_LABELS[r] ?? r,
    tierCols: (r) => roleColumns(r as RoleId).map((c) => ({ key: c.key, label: c.label })),
    granted: (k, f) => isGranted(k, f),
    changed: (k, f) => cellChanged(k, f),
    locked: (k, f) => ownerLocked(k, f),
    onToggle: (k, f) => toggle(k, f),
    // Owner powers are is_primary_owner gates, not flags — the lens shows
    // them read-only on the Owner tab (where an owner looks for them).
    ownerPowers: OWNER_POWERS,
    // "Also held by" — the one question the deleted matrix answered well,
    // now asked per row instead of by scanning a column.  No new request:
    // every role's flags are already in this page's payload.
    heldBy: (f) => ROLES.filter((r) =>
      roleColumns(r).some((c) => isGranted(c.key, f))).map((r) => ROLE_LABELS[r] ?? r),
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
          {/* Departments — an ACCOUNT-wide switch, so it sits above the
              role tabs rather than inside a per-role view.  Off hides the
              department's features from every sidebar.  Rides
              can_manage_account (Account Settings' remit), not the
              matrix permission. */}
          {canManageModules && modData && (
            <div className="mb-3 rounded-lg border border-border bg-card px-4 py-2.5">
              <div className="flex items-center gap-x-4 gap-y-2 flex-wrap">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground inline-flex items-center gap-1">
                  Departments
                  <InfoTip size={12} label="A department that is off hides its features from every sidebar, for every role — the permissions below stay as they are." />
                </span>
                {Object.entries(GROUP_MODULE).map(([title, id]) => {
                  const on = moduleOn(id);
                  const flipped = moduleChanges.some((m) => m.id === id);
                  return (
                    <span key={id} className={`inline-flex items-center gap-1.5 ${flipped ? 'rounded px-1 bg-primary/10' : ''}`}>
                      <button
                        type="button"
                        onClick={() => toggleModule(id)}
                        role="switch"
                        aria-checked={on}
                        aria-label={`${title} department`}
                        className={`relative w-8 h-4 rounded-full transition shrink-0 ${on ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-background shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
                      </button>
                      <span className={`text-xs ${on ? 'text-foreground' : 'text-muted-foreground'}`}>{title}</span>
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          <div className="rounded-lg border border-border bg-card">
            <RoleLens api={roleLensApi} />
          </div>

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
      {/* The click-away guarded on ``!saving``; <Dialog>'s onOpenChange
          does the same job, and brings the focus trap, Escape and the
          background scroll lock the hand-rolled version never had. */}
      <Dialog
        open={confirmOpen}
        onOpenChange={(o) => { if (!o && !saving) setConfirmOpen(false); }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-md p-0 max-h-[80vh] flex flex-col">
          {confirmOpen && (
          <>
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
          </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
