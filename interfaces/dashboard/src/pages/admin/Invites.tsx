import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Link as LinkIcon, Plus, Trash2, Copy, Check, Loader2 } from 'lucide-react';
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
import { toneClasses, toneText } from '../../lib/status';

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

  // Stable focus anchor for the revoke flow.  base-ui restores focus to
  // the dialog's trigger when it closes, but the optimistic update in
  // confirmRevoke unmounts the per-row Trash icon BEFORE the dialog
  // animates out — base-ui's restore target becomes a detached node and
  // focus falls back to document.body, stranding keyboard-driven
  // operators.  Routing focus to the always-present "New invite" button
  // gives them a stable, sensible next-action anchor.
  const newInviteBtnRef = useRef<HTMLButtonElement>(null);

  async function load({ silent = false }: { silent?: boolean } = {}) {
    // Silent mode skips the loading=true flip so a post-action
    // reconcile (after create / revoke) doesn't flash a 6-row
    // TableSkeleton over the table the operator is reading.  Initial
    // mount + manual refreshes still call load() without options so
    // the first paint shows the skeleton correctly.
    if (!silent) setLoading(true);
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
      if (!silent) setLoading(false);
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
    try {
      // Department falls back to 'general' if the operator cleared the
      // input — the column default is 'general' but Pydantic accepts
      // an explicit empty string and bypasses it.  Trimming here is
      // the only thing standing between the operator and a row with
      // department=''.
      const dep = department.trim() || 'general';
      const body: Record<string, unknown> = { role, department: dep, hours };
      // Gate truck_num on CURRENT role, not on the state string
      // having content — otherwise switching role=driver → role=fleet
      // (which hides the Truck# input but preserves truckNum state)
      // attaches the stale truck number to a non-driver invite.
      if (role === 'driver' && truckNum.trim()) {
        body.truck_num = truckNum.trim();
      }
      const inv = await apiJSON<InviteInfo>('/admin/invite', { method: 'POST', body });
      // Await the clipboard write BEFORE closing the dialog so a
      // clipboard rejection (HTTP origin, focus loss, permission
      // denied) surfaces while the operator is still in the dialog
      // context — they can hit Create again or copy the URL the
      // toast surfaces.  copyLink itself is fire-and-forget; this
      // shape gives us per-call control over the close timing.
      const url = `https://t.me/${botUsername}?start=join_${inv.code}`;
      try {
        await navigator.clipboard.writeText(url);
        setCopied(inv.code);
        setTimeout(() => setCopied(null), 2000);
        toast.success(t('toasts.invite_created_copied', {
          defaultValue: 'Invite created — link copied to clipboard',
        }));
      } catch {
        // Clipboard failed but the invite IS created.  Show the
        // URL so the operator can copy by hand.
        toast.error(t('toasts.invite_copy_failed', {
          defaultValue: `Invite created — copy manually: ${url}`,
          url,
        }));
      }
      setShowForm(false);
      // Reset per-driver field on success; leave role/department/
      // hours so bulk-onboarding flows ("invite three drivers")
      // don't have to re-fill the same form three times.  This is
      // intentional UX, not a forgotten reset — see panel-state
      // note near useState block.
      setTruckNum('');
      // Silent background reconcile picks up the new row in the
      // table.  No skeleton flash thanks to the silent flag.
      try { await load({ silent: true }); } catch { /* surfaced */ }
    } catch (e) {
      // Surface the error as a toast so it renders ABOVE the open
      // dialog (sonner mounts at the document root with very high
      // z-index); the panel-level ErrorState banner is hidden behind
      // the dialog backdrop and the operator can't see it.
      toast.error(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setCreating(false);
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
    // Single-row optimistic update — flip just THIS row, never the
    // whole array.  The prior implementation snapshotted ``invites``
    // and restored it on error; that overwrote any concurrent state
    // change (showAll toggle, sibling revoke, background refresh)
    // that landed during the in-flight network call.  Operating on
    // one row by id avoids the entire class of stale-snapshot bugs
    // and makes rollback trivially correct: just flip the same row
    // back to its pre-revoke shape.
    const nowIso = new Date().toISOString();
    setInvites(prev =>
      showAll
        ? prev.map(i =>
            i.id === invite.id
              ? { ...i, is_revoked: true, revoked_at: nowIso }
              : i,
          )
        : prev.filter(i => i.id !== invite.id),
    );
    try {
      await apiJSON(`/admin/invites/${invite.id}`, { method: 'DELETE' });
      toast.success(t('toasts.invite_revoked', { defaultValue: 'Invite revoked' }));
      setConfirming(null);
      // Move focus to a stable anchor BEFORE base-ui's restore-focus
      // logic tries to send it to the (already-unmounted) trigger.
      // Without this, keyboard-driven operators lose focus to
      // document.body on the pending-only view where the row vanishes.
      newInviteBtnRef.current?.focus();
      // Clear in-flight flag BEFORE the silent reconcile so the user
      // can open a fresh Dialog mid-reload without inheriting the
      // disabled "Revoking…" state.
      setRevoking(null);
      // Silent reconcile — no skeleton flash; picks up any server-
      // side state we didn't predict (another tab revoked a sibling
      // row, etc.).  Swallow load failures; the optimistic UI is
      // already correct.
      try { await load({ silent: true }); } catch { /* fine */ }
      return;
    } catch (e) {
      // Branch on the HTTP status code (ApiError carries it) rather
      // than regex-matching the human-readable detail string — the
      // detail copy can drift but the 404 semantics for "row is
      // gone / never was" are stable.
      const isGone = e instanceof ApiError && e.status === 404;
      if (isGone) {
        // Server agrees the row is dead — keep the optimistic mutation,
        // don't restore.  This avoids the "row reappears, table flashes
        // skeleton, row disappears again" sequence the previous code
        // produced when it both restored the snapshot AND called load().
        toast.info(t('toasts.invite_already_revoked', { defaultValue: 'Invite already revoked' }));
        setConfirming(null);
        newInviteBtnRef.current?.focus();
        setRevoking(null);
        return;
      }
      // Real failure (network, 403, 429) — rollback the single-row
      // mutation by re-fetching authoritative state.  Avoids the
      // stale-snapshot bug entirely.  Keep the dialog open + toast
      // so the operator can retry.
      toast.error(e instanceof Error ? e.message : String(e));
      try { await load({ silent: true }); } catch { /* surfaced via setError */ }
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
        // Per-row Revoke disables when ANY revoke is in-flight (not
        // just the one for this row).  Closes off the path where the
        // operator clicks Trash on row B while A is in-flight,
        // opening a second confirmation dialog whose Revoke button is
        // disabled-but-displayed-as-"Revoking…" for what looks like B
        // but is actually A.  The dialog-button gate (revoking !==
        // null) was the last line of defence; this is the first.
        const anyRevokeInFlight = revoking !== null;
        const isThisRowRevoking = revoking === inv.id;
        const isJustCopied = copied === code;
        return (
          <div className="inline-flex items-center gap-2">
            {canCopy && (
              <>
                <button
                  onClick={() => copyLink(code)}
                  className={`inline-flex items-center gap-1 text-xs hover:opacity-80 transition-colors ${
                    isJustCopied ? toneText('ok') : 'text-primary'
                  }`}
                  title="Copy invite link"
                >
                  {isJustCopied ? <Check size={12} /> : <Copy size={12} />}
                  <span>
                    {isJustCopied
                      ? t('actions.copied', { defaultValue: 'Copied' })
                      : t('actions.copy', { defaultValue: 'Copy' })}
                  </span>
                </button>
                {canRevoke && <span className="text-muted-foreground/40">·</span>}
              </>
            )}
            {canRevoke && (
              <button
                onClick={() => setConfirming(inv)}
                disabled={anyRevokeInFlight}
                aria-busy={isThisRowRevoking}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive disabled:opacity-50 disabled:cursor-wait transition-colors"
                title={t('actions.revoke', { defaultValue: 'Revoke invite' })}
              >
                <Trash2 size={12} />
                <span>
                  {isThisRowRevoking
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
          ref={newInviteBtnRef}
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

      {/* Create modal — uses ui/dialog primitive (base-ui).
          Migrated from a hand-rolled ``<div className="fixed inset-0
          …">`` so the form inherits Escape-to-close, focus-trap
          (Tab stays inside the dialog instead of escaping to the
          page behind), outside-click dismissal, scroll-lock, and
          ARIA labelling for screen readers.  Wrapping the body in
          a ``<form>`` also gets Enter-to-submit for free, which the
          old overlay didn't have — operators can now type
          department, hit Enter, and the link is on their clipboard. */}
      <Dialog
        open={showForm}
        onOpenChange={(open, eventDetails) => {
          // base-ui still dispatches its internal "close" transition
          // unless we call eventDetails.cancel() — otherwise the popup
          // briefly fades out for one frame before our controlled
          // ``open=true`` re-asserts (visible flicker on Escape /
          // outside-click during a mid-create POST).  Cancelling here
          // tells base-ui to leave the open state alone entirely.
          if (!open && creating) {
            eventDetails.cancel();
            return;
          }
          if (!open) setShowForm(false);
        }}
      >
        <DialogContent showCloseButton={false}>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!creating) void create();
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('modals.create_invite')}</DialogTitle>
              <DialogDescription>
                {t('modals.create_invite_desc', {
                  defaultValue: 'Pick a role and how long the join link should stay valid.',
                })}
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-3">
              <div>
                <label className="block text-sm text-muted-foreground mb-1">Role</label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                >
                  {INVITABLE_ROLES.map((val) => <option key={val} value={val}>{ROLE_LABEL[val]}</option>)}
                </select>
              </div>

              <div>
                <label className="block text-sm text-muted-foreground mb-1">Department</label>
                <input
                  value={department}
                  onChange={(e) => setDepartment(e.target.value)}
                  className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                />
              </div>

              {role === 'driver' && (
                <div>
                  <label className="block text-sm text-muted-foreground mb-1">Truck # (optional)</label>
                  <input
                    value={truckNum}
                    onChange={(e) => setTruckNum(e.target.value)}
                    className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                    placeholder={t('forms.truck_example')}
                  />
                </div>
              )}

              <div>
                <label className="block text-sm text-muted-foreground mb-1">Expires in (hours)</label>
                <select
                  value={hours}
                  onChange={(e) => setHours(+e.target.value)}
                  className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                >
                  <option value={1}>1 hour</option>
                  <option value={6}>6 hours</option>
                  <option value={24}>24 hours</option>
                  <option value={72}>3 days</option>
                  <option value={168}>7 days</option>
                  <option value={720}>30 days</option>
                </select>
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowForm(false)}
                disabled={creating}
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={creating}
                aria-busy={creating}
              >
                {creating && <Loader2 size={14} className="animate-spin" />}
                {creating
                  ? t('actions.creating', { defaultValue: 'Creating…' })
                  : t('actions.create_and_copy', { defaultValue: 'Create & Copy Link' })}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Revoke confirmation — first consumer of ui/dialog in the
          dashboard.  Read-only body (no form inputs) so this is the
          lowest-risk surface to introduce the primitive on. */}
      <Dialog
        open={confirming !== null}
        onOpenChange={(open, eventDetails) => {
          // Cancel base-ui's internal close transition during an
          // in-flight revoke; otherwise the popup briefly fades on
          // Escape / outside-click before our controlled open=true
          // re-asserts.  See create-dialog onOpenChange for the
          // longer rationale.
          if (!open && revoking !== null) {
            eventDetails.cancel();
            return;
          }
          if (!open) setConfirming(null);
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
