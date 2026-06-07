import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Link as LinkIcon, Plus, Trash2 } from 'lucide-react';
import { apiJSON, ApiError } from '../../api/client';
import type { InviteInfo, InvitesResponse } from '../../types';
import DataTable from '../../components/DataTable';
import RoleBadge, { ROLE_LABEL } from '../../components/RoleBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import type { AnyColumn } from '../../types';
import { toneClasses } from '../../lib/status';

// Role choices for the create-invite form.  Owner is intentionally
// excluded — the rank-check on the server already forbids inviting
// peers / superiors; this just keeps the dropdown tidy.  Reads
// labels from the canonical ROLE_LABEL map in components/RoleBadge.tsx.
const INVITABLE_ROLES = ['admin', 'fleet', 'safety', 'dispatcher', 'driver'] as const;

/**
 * Pill that summarises an invite's lifecycle state.
 *
 * Priority order matters — a revoked-but-not-expired invite must read
 * as REVOKED, not as Pending (operator action wins over the time-based
 * lifecycle).  Likewise a revoked-then-expired row stays REVOKED
 * because the operator made the decision; the natural expiry is
 * trailing context.  Used > Revoked > Expired > Pending isn't
 * arbitrary either: a used invite is the only one that successfully
 * onboarded someone, so the affirmative outcome wins.
 */
function StatusBadge({ invite }: { invite: InviteInfo }) {
  const cls = 'px-2 py-0.5 rounded-full text-xs';
  if (invite.is_used) return <span className={`${cls} ${toneClasses('ok')}`}>Used</span>;
  if (invite.is_revoked) return <span className={`${cls} ${toneClasses('danger')}`}>Revoked</span>;
  if (invite.is_expired) return <span className={`${cls} ${toneClasses('neutral')}`}>Expired</span>;
  return <span className={`${cls} bg-primary/15 text-primary`}>Pending</span>;
}

/**
 * Invites body — toolbar + table + create modal, WITHOUT a PageHeader.
 * Rendered two ways: as a tab inside the Team Management page (the
 * canonical home — inviting is part of managing the team), and as the
 * standalone /admin/invites page (kept for HR's onboarding sidebar and
 * direct links) via the thin ``Invites`` wrapper below.
 */
export function InvitesPanel() {
  const { t } = useTranslation();
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [botUsername, setBotUsername] = useState('4truckBot');

  // Create form
  const [showForm, setShowForm] = useState(false);
  const [role, setRole] = useState('fleet');
  const [department, setDepartment] = useState('general');
  const [truckNum, setTruckNum] = useState('');
  const [hours, setHours] = useState(24);
  const [creating, setCreating] = useState(false);

  // Revoke flow.  ``confirming`` holds the invite the operator clicked
  // Revoke on (drives the Dialog open state); ``revoking`` is the id
  // currently in-flight to the API (drives per-button disabled state).
  // Two pieces of state so a slow network doesn't freeze the Dialog
  // visual — the dialog stays open until the API resolves, the button
  // label flips to "Revoking…", and only THEN we close + reload.
  const [confirming, setConfirming] = useState<InviteInfo | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    // Clear any stale error from the previous attempt; otherwise a
    // failed load → successful retry shows the red banner ABOVE the
    // populated table (the success path overwrites ``invites`` but
    // not ``error``, so they layer).
    setError('');
    try {
      // "Show all" drives BOTH the pending_only=false flag (surface used
      // rows) AND include_revoked=true (surface revoked rows) — the two
      // are orthogonal on the API but presented as one operator toggle
      // because "show everything that's ever been an invite" is the
      // mental model that actually maps to a single checkbox.
      const params = showAll
        ? 'pending_only=false&include_revoked=true'
        : 'pending_only=true';
      const d = await apiJSON<InvitesResponse>(`/admin/invites?${params}`);
      setInvites(d.invites || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [showAll]);

  useEffect(() => {
    // /auth/bot-info doesn't exist — call /auth/config, which returns
    // the per-account bot when the request carries an admin JWT (so
    // invite links land on the account's branded bot rather than the
    // global login bot — the right semantic for "join my company").
    apiJSON<{ bot_username: string }>('/auth/config')
      .then(d => { if (d.bot_username) setBotUsername(d.bot_username); })
      .catch(() => {});
  }, []);

  async function create() {
    setCreating(true);
    let created = false;
    try {
      const body: Record<string, unknown> = { role, department, hours };
      if (truckNum.trim()) body.truck_num = truckNum.trim();
      const inv = await apiJSON<InviteInfo>('/admin/invite', { method: 'POST', body });
      created = true;
      copyLink(inv.code);
      setShowForm(false);
      setTruckNum('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setCreating(false);
    }
    // Refresh AFTER the create try/catch so a load() failure on a
    // slow refresh doesn't blame the create (the invite was already
    // created — surfacing "Create failed" because the list reload
    // timed out is misleading and stops the operator from copying
    // the link they just generated).
    if (created) {
      try {
        await load();
      } catch {
        /* load() already toasts via its own catch; nothing to do */
      }
    }
  }

  function copyLink(code: string) {
    const url = `https://t.me/${botUsername}?start=join_${code}`;
    navigator.clipboard.writeText(url)
      .then(() => {
        setCopied(code);
        setTimeout(() => setCopied(null), 2000);
      })
      .catch(() => {
        // Clipboard permission denied (HTTP origin, no user gesture,
        // focus loss, etc.) — surface the URL so the operator can
        // copy by hand instead of silently failing.
        toast.error(`Could not auto-copy — copy this manually: ${url}`);
      });
  }

  /**
   * Revoke confirmation handler.  Stays open while the API call is
   * in-flight so the operator sees the destructive action complete
   * rather than the dialog vanishing the moment they click Revoke.
   *
   * Idempotency: the server returns 404 if the row was already
   * revoked by another tab.  We surface that as a friendly
   * ``toast.info`` rather than ``toast.error`` because the operator's
   * desired post-condition (this code is dead) is satisfied either
   * way.  Other failures (network, 403 rank-check, 429 rate limit)
   * keep the dialog open with toast.error so the operator can retry.
   */
  async function confirmRevoke(invite: InviteInfo) {
    setRevoking(invite.id);
    try {
      await apiJSON(`/admin/invites/${invite.id}`, { method: 'DELETE' });
      toast.success(t('toasts.invite_revoked', { defaultValue: 'Invite revoked' }));
      setConfirming(null);
      // Clear the in-flight flag BEFORE awaiting load() so the user
      // can open a fresh Dialog mid-reload without it inheriting the
      // disabled "Revoking…" state from the prior request.
      setRevoking(null);
      await load();
      return;
    } catch (e) {
      // Branch on the HTTP status code (ApiError carries it) rather
      // than regex-matching the human-readable detail string — the
      // detail copy can drift (i18n / wording polish) but the 404
      // semantics for "row is gone / never was" are stable.  Same
      // 404 path covers both "you raced another tab" and "operator
      // never had this id"; surfacing as success-info instead of
      // error matches the operator's mental model ("the desired
      // post-condition holds").
      const isGone = e instanceof ApiError && e.status === 404;
      if (isGone) {
        toast.info(t('toasts.invite_already_revoked', { defaultValue: 'Invite already revoked' }));
        setConfirming(null);
        setRevoking(null);
        await load();
        return;
      }
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      // Safety net: if we returned cleanly the early setRevoking(null)
      // above already fired; this re-fire is a no-op.  On the error
      // path that doesn't early-return (network 500 etc.), this is
      // the only thing that re-enables the Dialog's Revoke button.
      setRevoking(null);
    }
  }

  const columns: AnyColumn[] = [
    {
      key: 'role',
      label: 'Role',
      render: (v) => {
        return <RoleBadge role={String(v)} />;
      },
    },
    { key: 'department', label: 'Department' },
    { key: 'truck_num', label: 'Truck', render: (v) => (v as string) || '—' },
    {
      key: 'expires_at',
      label: 'Expires',
      render: (v) => v ? new Date(v as string).toLocaleString() : '—',
    },
    {
      key: '_status',
      label: 'Status',
      render: (_, row) => <StatusBadge invite={row as unknown as InviteInfo} />,
    },
    {
      key: 'code',
      label: 'Link',
      render: (_, row) => {
        // The Link cell carries action buttons keyed off the invite's
        // lifecycle:
        //   pending (still joinable):     [📋 Copy]  ·  [🗑 Revoke]
        //   expired but never redeemed:                [🗑 Revoke]
        //   used / already revoked:        —
        //
        // Revoke is permitted on expired-but-unused rows so an
        // operator can convert a natural "Expired" into a "Revoked"
        // for forensic clarity (a copy of the link still exists in
        // someone's chat history — the audit-trail benefit of an
        // explicit revoke is real even after the time-based gate
        // already locked the link).  The backend allows it; this
        // matching frontend gate keeps the two layers in sync.
        // Copy stays hidden on expired rows because the link itself
        // is dead — no point handing out a dead link.
        const inv = row as unknown as InviteInfo;
        const canCopy = !inv.is_used && !inv.is_revoked && !inv.is_expired;
        const canRevoke = !inv.is_used && !inv.is_revoked;
        if (!canCopy && !canRevoke) {
          return <span className="text-xs text-muted-foreground">—</span>;
        }
        const code = String(inv.code);
        const isRevoking = revoking === inv.id;
        return (
          <div className="inline-flex items-center gap-2">
            {canCopy && (
              <>
                <button
                  onClick={() => copyLink(code)}
                  className="text-primary hover:text-primary/80 text-xs"
                  title="Copy invite link"
                >
                  {copied === code ? '✅ Copied' : '📋 Copy'}
                </button>
                {canRevoke && <span className="text-muted-foreground/40">·</span>}
              </>
            )}
            {canRevoke && (
              <button
                onClick={() => setConfirming(inv)}
                disabled={isRevoking}
                aria-busy={isRevoking}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive disabled:opacity-50 disabled:cursor-wait transition-colors"
                title={t('actions.revoke', { defaultValue: 'Revoke invite' })}
              >
                <Trash2 size={12} />
                <span>
                  {isRevoking
                    ? t('actions.revoking', { defaultValue: 'Revoking…' })
                    : t('actions.revoke', { defaultValue: 'Revoke' })}
                </span>
              </button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div>
      {/* Toolbar — was the PageHeader's actions slot; lives in the panel
          now so it travels with both the Team Management tab and the
          standalone page. */}
      <div className="mb-4 flex items-center justify-end gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
            className="rounded bg-muted border-border"
          />
          Show all
        </label>
        <button
          onClick={() => setShowForm(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
        >
          <Plus size={14} />
          New invite
        </button>
      </div>

      {error && (
        <div className="mb-3"><ErrorState message={error} /></div>
      )}

      {loading ? (
        <TableSkeleton rows={6} cols={6} />
      ) : invites.length === 0 ? (
        <EmptyState
          icon={LinkIcon}
          title={showAll ? 'No invites have been issued' : 'No active invites'}
          description="Create an invite to add a new teammate — pick the role and how long the link should be valid."
          action={
            <button
              onClick={() => setShowForm(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={14} />
              New invite
            </button>
          }
        />
      ) : (
        <DataTable columns={columns} data={invites as unknown as Record<string, unknown>[]} />
      )}

      {/* Create modal — pre-existing hand-rolled overlay.  Migration to
          ui/dialog is intentionally deferred to a separate PR (see the
          revoke design notes) so the dialog primitive's first dashboard
          consumer (the revoke confirmation below) ships against a
          minimal surface area first. */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowForm(false)}>
          <div className="bg-card rounded-xl border border-border p-6 w-96" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-4">{t('modals.create_invite')}</h2>

            <label className="block text-sm text-muted-foreground mb-1">Role</label>
            <select value={role} onChange={(e) => setRole(e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3">
              {INVITABLE_ROLES.map((val) => <option key={val} value={val}>{ROLE_LABEL[val]}</option>)}
            </select>

            <label className="block text-sm text-muted-foreground mb-1">Department</label>
            <input
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3"
            />

            {role === 'driver' && (
              <>
                <label className="block text-sm text-muted-foreground mb-1">Truck # (optional)</label>
                <input
                  value={truckNum}
                  onChange={(e) => setTruckNum(e.target.value)}
                  className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-3"
                  placeholder={t('forms.truck_example')}
                />
              </>
            )}

            <label className="block text-sm text-muted-foreground mb-1">Expires in (hours)</label>
            <select value={hours} onChange={(e) => setHours(+e.target.value)} className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border mb-4">
              <option value={1}>1 hour</option>
              <option value={6}>6 hours</option>
              <option value={24}>24 hours</option>
              <option value={72}>3 days</option>
              <option value={168}>7 days</option>
              <option value={720}>30 days</option>
            </select>

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground">{t('common.cancel')}</button>
              <button onClick={create} disabled={creating} className="px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium disabled:opacity-50">
                {creating ? 'Creating...' : 'Create & Copy Link'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Revoke confirmation — first consumer of ui/dialog in the
          dashboard.  Read-only body (no form inputs) so this is the
          lowest-risk surface to introduce the primitive on. */}
      <Dialog
        open={confirming !== null}
        onOpenChange={(open) => {
          // Allow Escape/outside-click to close ONLY when no request is
          // in-flight — half-completed revokes that re-render with a
          // closed dialog leak the in-flight state otherwise.
          if (!open && revoking === null) setConfirming(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('modals.revoke_invite_title', { defaultValue: 'Revoke this invite?' })}
            </DialogTitle>
            <DialogDescription>
              {t('modals.revoke_invite_body', {
                defaultValue: 'The link will stop working immediately. If someone has already copied it, they will no longer be able to join.',
              })}
            </DialogDescription>
          </DialogHeader>
          {confirming && (
            <div className="text-xs text-muted-foreground border border-border rounded-md p-3 bg-muted/30">
              <div className="flex items-center gap-2">
                <RoleBadge role={confirming.role} />
                <span>·</span>
                <span>{confirming.department}</span>
                {confirming.truck_num && (
                  <>
                    <span>·</span>
                    <span>Truck {confirming.truck_num}</span>
                  </>
                )}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirming(null)}
              disabled={revoking !== null}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => confirming && confirmRevoke(confirming)}
              disabled={revoking !== null}
              aria-busy={revoking !== null}
            >
              {revoking !== null
                ? t('actions.revoking', { defaultValue: 'Revoking…' })
                : t('actions.revoke', { defaultValue: 'Revoke' })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Standalone /admin/invites page — PageHeader + the shared panel.  Kept
 * for HR's onboarding sidebar entry and any direct links; Owner/Admin
 * now reach invites via the Team Management → Invites tab.
 */
export default function Invites() {
  const { t } = useTranslation();
  return (
    <div>
      <PageHeader
        icon={LinkIcon}
        title={t('pages.invites_title')}
        description={t('pages.invites_desc')}
      />
      <InvitesPanel />
    </div>
  );
}
