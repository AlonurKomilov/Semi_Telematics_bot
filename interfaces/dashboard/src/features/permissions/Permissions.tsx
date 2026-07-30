import { useState, useMemo, Fragment, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Shield, Check, X, Lock, ChevronRight, ChevronDown, Bell, Bot, FileText, Smartphone, AlertTriangle } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useRoleView } from '../../context/RoleViewContext';
import { useAuth } from '../../context/AuthContext';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses, toneText } from '../../lib/status';
import { usePreference } from '../../preferences';
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
  ALL_MATRIX_FLAGS, COLLAPSIBLE_KEYS, DEFAULT_SCOPED_FLAGS,
  DRIVER_PANEL_FLAGS, DRIVER_RECORDS, DRIVER_TRUCK, GROUP_BLOCKS,
  GROUP_MODULE, OWNER_PROTECTED, blockKey, contextLabel, isHeader,
  isScoped,
} from './permRows';
import type { Block, ModulesData, PermFlag, ScopedFlag, SimpleFlag } from './permRows';


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

  // Which lens is open — remembered per device.  "role" (the focused
  // per-role editor) is the default; the matrix stays one click away
  // for cross-role audits.
  const { value: lens, setValue: setLens } = usePreference('permissions.lens');

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

  // Render one matrix row.  `depth` is the row's tree depth (0 = top-level;
  // 1 = child; 2 = a sub-feature's own child); `collapse` adds an
  // expand/collapse chevron to any parent that has sub-rows.
  const renderRow = (f: PermFlag, depth = 0, collapse?: { isCollapsed: boolean; onToggle: () => void }): ReactNode => {
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
          <td colSpan={1 + columns.length} className="px-3 py-2 sticky left-0 bg-card z-10">
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {chevronSlot}
              <span className="text-sm font-medium">{f.header}</span>
              {f.description && <span className="text-2xs text-muted-foreground ml-2">{f.description}</span>}
            </div>
          </td>
        </tr>
      );
    }
    const nested = depth > 0;
    return (
      <tr className={`${nested ? '' : 'border-t border-border'} hover:bg-muted/20`}>
        <td className={`sticky left-0 bg-card z-10 ${nested ? (depth > 1 ? 'pl-8 pr-3' : 'pl-4 pr-3') : 'px-3 py-1.5'}`}>
          {/* Nested row: a continuous left rail (the cell carries no
              vertical padding so adjacent rails touch) makes the rows read as
              a group hanging off the parent above; depth 2 indents further. */}
          <div className={nested ? 'border-l-2 border-border pl-3 py-1.5' : ''}>
            <div className={`flex items-center gap-1.5 ${collapse ? 'cursor-pointer select-none' : ''}`} {...clickableProps}>
              {(!nested || collapse) && chevronSlot}
              <span className={nested ? 'text-muted-foreground' : 'font-medium'}>{f.label}</span>
              {isScoped(f) && <span className="text-2xs text-muted-foreground" title="Scoped feature — checkbox = full access">*</span>}
              {/* The muted kind tag: this row's single flag is WRITE-level
                  though its label is the feature noun (Billing, Coaching…) —
                  without it, granting reads as read-only view access. */}
              {f.writeLevel && (
                <span className="text-3xs uppercase tracking-wide text-muted-foreground border border-border rounded px-1">
                  manage
                </span>
              )}
            </div>
            {f.description && <div className="text-2xs text-muted-foreground/70 mt-0.5">{f.description}</div>}
          </div>
        </td>
        {columns.map((col, ci) => {
          // Every tier column is EDITABLE and edits its OWN storage key
          // (base role / {role}__manager / owner).  Owner escape-hatch flags
          // stay locked-on so an owner can't self-lock-out.
          const on = isGranted(col.key, f);
          const locked = ownerLocked(col.key, f);
          const changed = !locked && cellChanged(col.key, f);
          const leftBorder = col.tier === 'senior' ? 'border-l border-border/40' : 'border-l border-border';
          return (
            <td key={`${col.key}-${ci}`} className={`text-center px-2 py-1.5 ${leftBorder} ${changed ? 'bg-primary/10' : ''}`}>
              <button
                onClick={() => toggle(col.key, f)}
                disabled={locked}
                aria-pressed={on}
                title={locked
                  ? `Owner always keeps "${f.label}" — prevents lockout`
                  : `${keyLabel[col.key]} · ${contextLabel(f)}: ${on ? 'granted' : 'no access'}`}
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

  // One driver-panel row: a single-column toggle (the driver has no tiers and
  // no "whose data" choice — always their own truck), editing the `driver`
  // storage key.  Reuses the matrix's grant/toggle logic so the change flows
  // through the same save bar + confirm dialog.
  const renderDriverRow = (f: ScopedFlag | SimpleFlag): ReactNode => {
    const on = isGranted('driver', f);
    const changed = cellChanged('driver', f);
    const rowKey = isScoped(f) ? f.vehicleKey : f.key;
    return (
      <li key={rowKey} className={`flex items-center justify-between gap-3 rounded px-1.5 py-1 ${changed ? 'bg-primary/10' : ''}`}>
        <div className="min-w-0">
          <div className="text-sm text-foreground">{f.label}</div>
          {f.description && <div className="text-2xs text-muted-foreground/70">{f.description}</div>}
        </div>
        <button
          type="button"
          onClick={() => toggle('driver', f)}
          role="switch"
          aria-checked={on}
          aria-label={f.label}
          title={`${f.label}: ${on ? 'granted' : 'no access'}`}
          className={`relative w-8 h-4 rounded-full transition shrink-0 ${on ? 'bg-primary' : 'bg-muted'}`}
        >
          <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-background shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
        </button>
      </li>
    );
  };

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
          <div className="flex items-center justify-between mb-2">
            <div className="inline-flex bg-muted border border-border rounded-md p-0.5" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={lens === 'role'}
                onClick={() => setLens('role')}
                className={`text-xs px-3 py-1 rounded transition ${lens === 'role' ? 'bg-card text-foreground font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                One role
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={lens === 'matrix'}
                onClick={() => setLens('matrix')}
                className={`text-xs px-3 py-1 rounded transition ${lens === 'matrix' ? 'bg-card text-foreground font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                Compare all roles
              </button>
            </div>
            {lens === 'matrix' ? (
              <button
                type="button"
                onClick={() => setCollapsed(allExpanded ? new Set(COLLAPSIBLE_KEYS) : new Set())}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                {allExpanded ? 'Collapse all' : 'Expand all'}
              </button>
            ) : <span />}
          </div>
          <div className="rounded-lg border border-border overflow-x-auto bg-card">
            {lens === 'role' ? <RoleLens api={roleLensApi} /> : (
            <table className="w-full text-sm border-collapse">
              <thead>
                {/* Row 1 — role group headers (tiered roles span their 2 sub-columns) */}
                <tr className="bg-muted/40">
                  <th rowSpan={2} className="text-left font-semibold px-3 py-2 sticky left-0 bg-muted/40 z-10 min-w-[200px] align-bottom">Feature</th>
                  {ROLES.map((r) => {
                    const cols = roleColumns(r);
                    return cols.length === 2 ? (
                      <th key={r} colSpan={2} className="px-2 pt-2 pb-1 text-center font-semibold text-xs whitespace-nowrap border-l border-border">
                        <span className="inline-flex items-center gap-1 justify-center">
                          {ROLE_LABELS[r] ?? r}
                          {tiersMeta[r] && (
                            <span title={`Has a senior tier — a ${tiersMeta[r].senior_label} gains ${tiersMeta[r].grants.length} extra permission(s).`} className="inline-flex">
                              <Shield size={12} className="text-primary/70 shrink-0" aria-label="Has a senior tier" />
                            </span>
                          )}
                        </span>
                      </th>
                    ) : (
                      <th key={r} rowSpan={2} className="px-2 py-2 text-center font-semibold text-xs whitespace-nowrap border-l border-border align-middle">
                        {ROLE_LABELS[r] ?? r}
                      </th>
                    );
                  })}
                </tr>
                {/* Row 2 — tier sub-labels under each tiered role */}
                <tr className="bg-muted/40">
                  {ROLES.map((r) => {
                    const cols = roleColumns(r);
                    if (cols.length !== 2) return null;
                    return cols.map((c, i) => (
                      <th key={`${r}-${c.tier}`} className={`px-2 pb-2 pt-0 text-center text-2xs font-medium text-muted-foreground whitespace-nowrap ${i === 0 ? 'border-l border-border' : ''}`}>
                        {c.label}
                      </th>
                    ));
                  })}
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
                        <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('warn')}`}>
                          module off — features hidden from every sidebar
                        </span>
                      )}
                    </span>
                  ) : undefined;
                  return (
                  <FragmentGroup key={group.title} title={group.title} control={control} colSpan={1 + columns.length}>
                    {(function renderBlocks(blocks: Block[], depth: number): ReactNode {
                      return blocks.map((block, bi) => {
                        const pk = blockKey(block.parent);
                        const hasChildren = block.children.length > 0;
                        const isColl = hasChildren && collapsed.has(pk);
                        return (
                          <Fragment key={`${group.title}-${pk}-${bi}`}>
                            {renderRow(block.parent, depth, hasChildren ? { isCollapsed: isColl, onToggle: () => toggleCollapse(pk) } : undefined)}
                            {hasChildren && !isColl && renderBlocks(block.children, depth + 1)}
                          </Fragment>
                        );
                      });
                    })(group.blocks, 0)}
                  </FragmentGroup>
                  );
                })}
                {/* OWNER POWERS — read-only rows.  Only the Owner Primary vs
                    Co-owner columns differ here (Primary yes, Co-owner no);
                    every other role shows a dash.  This is what makes the
                    Owner two-column split truthful. */}
                <tr className="bg-muted/30 border-t border-border">
                  <td colSpan={1 + columns.length} className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Owner powers <span className="normal-case font-normal text-2xs text-muted-foreground/70">— primary-owner only · not editable</span>
                  </td>
                </tr>
                {OWNER_POWERS.map((p) => (
                  <tr key={p.key} className="hover:bg-muted/20">
                    <td className="sticky left-0 bg-card z-10 px-3 py-1.5">
                      <span className="font-medium">{p.label}</span>
                      <div className="text-2xs text-muted-foreground/70 mt-0.5">{p.description}</div>
                    </td>
                    {columns.map((col) => {
                      const isOwner = col.role === 'owner';
                      const primary = isOwner && col.tier === 'senior';
                      return (
                        <td key={`${col.role}-${col.tier}-${p.key}`} className={`text-center px-2 py-1.5 ${col.tier === 'senior' ? 'border-l border-border/40' : 'border-l border-border'}`}>
                          {isOwner ? (
                            <span
                              title={`${col.label}: ${primary ? 'yes' : 'no'} — ${p.label}`}
                              className={`inline-flex items-center justify-center w-5 h-5 rounded border ${primary ? 'bg-primary/60 border-primary/60 text-primary-foreground' : 'border-border/50 text-transparent'}`}
                            >
                              {primary ? <Check size={14} strokeWidth={3} /> : null}
                            </span>
                          ) : (
                            <span className="text-muted-foreground/40 text-xs" title="Owners only">–</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </div>

          {/* Driver — self-service.  The driver role lives HERE, not in the
              staff matrix: a driver only ever sees their OWN truck + records,
              in the Telegram mini app.  Edits write the `driver` role's stored
              perms, which the mini app reads via /api/user/me — so a change
              reaches every driver's app on next load. */}
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Smartphone size={16} className="text-primary shrink-0" />
              <span className="text-base font-semibold text-foreground">Driver — self-service</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('info')}`}>mobile app</span>
            </div>
            <p className="text-2xs text-muted-foreground mb-3">
              Drivers don&apos;t manage anything and use the Telegram <span className="font-medium text-foreground/80">mini app</span> only, so they sit apart from the matrix above. Tick what a driver sees of their <span className="font-medium text-foreground/80">own truck</span> and <span className="font-medium text-foreground/80">own records</span> — changes reach every driver&apos;s app on next load. The <span className="font-medium text-foreground/80">Alerts</span> inbox and <span className="font-medium text-foreground/80">AI assistant</span> come automatically with the Vehicle grant.
            </p>

            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Own truck</div>
            <ul className="space-y-0.5 mb-4">
              {DRIVER_TRUCK.map((f) => {
                // The Vehicle grant silently carries the Alerts inbox + AI tab
                // (derived, not stored — roles.derive_service_perms).  When it's
                // OFF, name that consequence at the point of the loss so an
                // admin can't strip a driver's inbox + assistant without knowing.
                if (isScoped(f) && f.vehicleKey === 'can_vehicle_vehicle' && !isGranted('driver', f)) {
                  return (
                    <Fragment key="veh-derived">
                      {renderDriverRow(f)}
                      <li className={`flex items-start gap-1.5 pl-1.5 pb-0.5 text-2xs ${toneText('warn')}`}>
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                        <span>Alerts inbox &amp; AI assistant are off too — both ride the Vehicle grant.</span>
                      </li>
                    </Fragment>
                  );
                }
                return renderDriverRow(f);
              })}
            </ul>

            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">Own records</div>
            <ul className="space-y-0.5">
              {DRIVER_RECORDS.map(renderDriverRow)}
            </ul>
          </div>

          {/* System Services — always-on infrastructure, NOT owner-toggled.
              Access is derived from each role's feature permissions, so there
              is nothing to tick.  This panel documents the model in-place so
              an admin isn't left wondering where the old Alerts / AI rows went. */}
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">System Services</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded-md normal-case tracking-normal ${toneClasses('ok')}`}>always on</span>
            </div>
            <p className="text-2xs text-muted-foreground mb-3">
              Infrastructure services — not granted here. Each one follows the role&apos;s feature permissions automatically: disable a feature and its slice stops, but the service itself never stops.
            </p>
            <ul className="space-y-2.5">
              <li className="flex items-start gap-2.5">
                <Bell size={16} className="text-muted-foreground mt-0.5 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-foreground">Alerts</div>
                  <div className="text-2xs text-muted-foreground">Every role has the inbox. It shows the alerts for whichever features the role can see — disable a feature (Faults, Health, Fuel, Safety Events, Geofences, Maintenance) and just those alerts drop out. Scope follows the role&apos;s vehicle access — account-wide or own-vehicle.</div>
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
        <>
          <p className="text-2xs text-muted-foreground mt-2">
            Row grammar: an untagged row is what the role can <span className="font-medium text-foreground/80">see</span> · a <span className="font-medium text-foreground/80">Manage</span> row (or the <span className="uppercase tracking-wide text-3xs border border-border rounded px-1">manage</span> tag) is what it can <span className="font-medium text-foreground/80">do</span> · the two <span className="font-medium text-foreground/80">Config</span> rows delegate configuration · Alerts, AI and Reports are always-on services (panel below) — nothing to grant.
          </p>
          <p className="text-2xs text-muted-foreground mt-1">
            <span className="font-semibold">*</span> scoped feature — ticking grants the role access to the feature. <span className="font-medium text-foreground/80">Whose data</span> they see (All / Company / Vehicle) is set per-user in <span className="font-medium text-foreground/80">Team Management</span> (Company Access + Vehicle Assignments).
          </p>
          <p className="text-2xs text-muted-foreground mt-1 inline-flex items-center gap-1 flex-wrap">
            <Shield size={12} className="text-primary/70 shrink-0" />
            <span>
              a <span className="font-medium text-foreground/80">shield</span> on a role header means it has tiers (e.g. Standard / Full admin). Each tier is edited <span className="font-medium text-foreground/80">independently</span> — changing one column never affects the other. A person's tier is set per-user in <span className="font-medium text-foreground/80">Team Management</span>. The <span className="font-medium text-foreground/80">Owner powers</span> rows are primary-owner-only and can't be edited.
            </span>
          </p>
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
          </div>
        </div>
      )}
    </div>
  );
}

// A permission group: a header row spanning all columns + its feature rows.
function FragmentGroup({ title, control, children, colSpan }: { title: string; control?: ReactNode; children: ReactNode; colSpan: number }) {
  return (
    <>
      <tr className="bg-muted/50 border-t-2 border-border">
        <td colSpan={colSpan} className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
