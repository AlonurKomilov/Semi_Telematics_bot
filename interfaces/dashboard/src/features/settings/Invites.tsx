import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { VehiclePicker, type VehicleSummary } from '@/features/maintenance/pickers';
import { Link as LinkIcon, Plus, Trash2, Copy, Check, Loader2, TimerReset, Mail, Send, ChevronDown, AlertCircle, ShieldAlert } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '../../components/ui/dropdown-menu';
import { apiJSON, ApiError } from '../../api/client';
import type { InviteInfo, InvitesResponse } from '../../types';
import DataGrid, { type DataGridSegment } from '../../components/DataGrid';
import RoleBadge, { ROLE_LABEL, ASSIGNABLE_ROLES } from '../../components/RoleBadge';
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
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import type { AnyColumn } from '../../types';
import { toneClasses, toneText, statusClasses } from '../../lib/status';
import { Tip } from '../../components/tooltip';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';

// Role choices for the create-invite form — the shared assignable-role
// list (owner excluded; the server rank-check forbids inviting peers /
// superiors anyway).  Sourced from RoleBadge so new personas appear here
// automatically.  Labels come from the canonical ROLE_LABEL map.
const INVITABLE_ROLES = ASSIGNABLE_ROLES;
const INVITE_ROLE_ITEMS = INVITABLE_ROLES.map((val) => ({ value: val, label: ROLE_LABEL[val] }));
const INVITE_HOURS_ITEMS = [
  { value: '1', label: '1 hour' },
  { value: '6', label: '6 hours' },
  { value: '24', label: '24 hours' },
  { value: '72', label: '3 days' },
  { value: '168', label: '7 days' },
  { value: '720', label: '30 days' },
];

// Default extension delta.  Surfaced as a single constant so the
// request body, optimistic timestamp, toast text, and button tooltip
// can't drift apart if a future "choose hours" affordance lands.
const EXTEND_HOURS = 24;
const EXTEND_HOURS_MS = EXTEND_HOURS * 60 * 60 * 1000;

type StatusKey = 'pending' | 'used' | 'revoked' | 'expired';

/** Canonical lifecycle-status derivation for an invite row.  Single
 *  source of truth shared by StatusBadge, the status-chip counts,
 *  and the filteredInvites selector — eliminates the previously
 *  duplicated ternary chains and the future-drift risk that comes
 *  with them.  Priority: used > revoked > expired > pending (the
 *  affirmative outcome wins, then operator action, then time, then
 *  the default).  See StatusBadge docstring for the rationale. */
function inviteStatus(i: { is_used: boolean; is_revoked?: boolean; is_expired: boolean }): StatusKey {
  if (i.is_used) return 'used';
  if (i.is_revoked) return 'revoked';
  if (i.is_expired) return 'expired';
  return 'pending';
}

// Lifecycle split for the grid's segment tabs.  Pending is the
// working set (default tab); Used is history-positive; Closed folds
// revoked + expired together — both are dead links, and the Status
// column filter separates them when it matters.
const INVITE_SEGMENTS: DataGridSegment[] = [
  { key: 'pending', label: 'Pending', match: (r) => inviteStatus(r as unknown as InviteInfo) === 'pending' },
  { key: 'used',    label: 'Used',    match: (r) => inviteStatus(r as unknown as InviteInfo) === 'used' },
  {
    key: 'closed',
    label: 'Closed',
    match: (r) => {
      const st = inviteStatus(r as unknown as InviteInfo);
      return st === 'revoked' || st === 'expired';
    },
  },
];

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
  // All four statuses go through the design-system token helpers —
  // 'pending' uses ``statusClasses('pending')`` which the STATUS_TONE
  // map at lib/status.ts already maps to 'info', so the pill stays
  // info-blue across the dashboard's theme picker rather than
  // re-colouring with the primary brand colour.
  const cls = 'px-2 py-0.5 rounded-md text-xs border';
  const status = inviteStatus(invite);
  const tone =
    status === 'used' ? toneClasses('ok')
    : status === 'revoked' ? toneClasses('danger')
    : status === 'expired' ? toneClasses('neutral')
    : statusClasses('pending');
  const label =
    status === 'used' ? 'Used'
    : status === 'revoked' ? 'Revoked'
    : status === 'expired' ? 'Expired'
    : 'Pending';
  return <span className={`${cls} ${tone}`}>{label}</span>;
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
  const tz = useTimezone();
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState<string | null>(null);
  const [botUsername, setBotUsername] = useState('4truckBot');
  // Origin where /signup/<code> works.  Comes from /auth/config so it
  // matches the env-driven AUTH_BASE_URL on the server (defaults to
  // https://4truck.us — the apex where Login.tsx is reachable, NOT
  // the dash./app./api. subdomains where it would 404).  Falls back
  // to window.location.origin only during the brief load window.
  const [signupBase, setSignupBase] = useState<string | null>(null);

  // Create form
  const [showForm, setShowForm] = useState(false);
  const [role, setRole] = useState('fleet');
  const [truckNum, setTruckNum] = useState('');
  // Selected vehicle for driver invites — picker writes the vehicle
  // name to truckNum on select; we mirror the VehicleSummary here so
  // re-opening the dialog can pre-select the same row visually.
  const [pickedVehicle, setPickedVehicle] = useState<VehicleSummary | null>(null);

  // Fleet vehicles for the picker (driver role only).  Walks all
  // pages — backend caps page_size at 200 so for fleets >200 a
  // single fetch silently truncated the picker.  Sequential paging
  // (most accounts fit in one round-trip).  staleTime keeps it from
  // refetching mid-dialog while the operator types.
  const { data: vehiclesData, isLoading: vehiclesLoading } = useQuery({
    queryKey: ['invite-vehicle-picker'],
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
    staleTime: 60_000,
  });
  const vehicleList = vehiclesData?.vehicles ?? [];
  const [hours, setHours] = useState(24);
  const [creating, setCreating] = useState(false);
  // Send-via channel for the create form.  Three options:
  //   'telegram' — copies https://t.me/<bot>?start=join_<code>
  //   'url'      — copies https://<dashboard>/signup/<code>
  //   'email'    — stamps recipient_email + ships via SMTP/Resend API
  // Default is the operator's LAST CHOICE (persisted in localStorage),
  // with first-open default 'telegram' — preserves the muscle memory
  // of every existing operator who has been creating Telegram invites
  // before the 3-channel split landed.  A first-time operator at a
  // non-Telegram shop will see "Telegram" pre-selected but the
  // segmented control is right there and one click switches them.
  // A first-time operator at a Telegram shop gets exactly what they
  // expect.  Operators who pick URL or Email get it persisted as
  // their personal default going forward.
  type Channel = 'telegram' | 'url' | 'email';
  const CHANNEL_STORAGE_KEY = 'invites.lastChannel';
  const readStoredChannel = (): Channel => {
    try {
      const v = localStorage.getItem(CHANNEL_STORAGE_KEY);
      if (v === 'telegram' || v === 'url' || v === 'email') return v;
    } catch { /* localStorage disabled — fall through */ }
    return 'telegram';
  };
  const [channel, setChannel] = useState<Channel>(readStoredChannel);
  const [recipientEmail, setRecipientEmail] = useState('');
  const persistChannel = (c: Channel) => {
    setChannel(c);
    try { localStorage.setItem(CHANNEL_STORAGE_KEY, c); } catch { /* ignore */ }
  };

  // Duplicate-recipient check.  When the operator types in the
  // Email-channel recipient input, debounce-fetch
  // /admin/invite/check-recipient.  If the email already belongs to
  // an active user in this account, show an inline warning so the
  // operator can either confirm (existing member needs re-onboarding
  // for some reason) or cancel before the round-trip.  Same-account
  // only — does NOT reveal cross-account existence.
  const [duplicateMember, setDuplicateMember] = useState<{
    display_name: string;
    role: string;
  } | null>(null);
  useEffect(() => {
    if (channel !== 'email') { setDuplicateMember(null); return; }
    const addr = recipientEmail.trim().toLowerCase();
    if (!addr.includes('@') || addr.length < 5) {
      setDuplicateMember(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const res = await apiJSON<{
          exists: boolean;
          display_name?: string;
          role?: string;
        }>(`/admin/invite/check-recipient?email=${encodeURIComponent(addr)}`);
        if (cancelled) return;
        if (res.exists) {
          setDuplicateMember({
            display_name: res.display_name || '',
            role: res.role || '',
          });
        } else {
          setDuplicateMember(null);
        }
      } catch {
        // 429 or network blip — silent, don't block the operator
        if (!cancelled) setDuplicateMember(null);
      }
    }, 300);  // debounce
    return () => { cancelled = true; clearTimeout(timer); };
  }, [channel, recipientEmail]);

  // Resend-email per-button in-flight gate.  Mirrors the revoking/
  // extending pattern so anyMutationInFlight covers all three.
  const [resending, setResending] = useState<number | null>(null);

  // Revoke flow.  ``confirming`` holds the invite the operator clicked
  // Revoke on (drives the Dialog open state); ``revoking`` is the id
  // currently in-flight to the API (drives per-button disabled state).
  // Two pieces of state so a slow network doesn't freeze the Dialog
  // visual — the dialog stays open until the API resolves, the button
  // label flips to "Revoking…", and only THEN we close + reload.
  const [confirming, setConfirming] = useState<InviteInfo | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);

  // Extend flow.  Same shape as revoke: per-button in-flight state
  // by id (drives the per-row Clock button's "Extending…" label and
  // disables the button while the round-trip is pending).  No
  // confirmation dialog — extend is non-destructive and the operator
  // can always extend again or revoke.  Single-click → default 24h.
  const [extending, setExtending] = useState<number | null>(null);


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
      // Always fetch the FULL set (used + revoked included) — the
      // grid's Pending / Used / Closed segment tabs own the lifecycle
      // split client-side, which replaced the old "Show all" toggle.
      // Invite volumes are tiny, so there's no cost to loading all.
      const d = await apiJSON<InvitesResponse>('/admin/invites?pending_only=false&include_revoked=true');
      setInvites(d.invites || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    // /auth/bot-info doesn't exist — call /auth/config, which returns
    // the per-account bot when the request carries an admin JWT (so
    // invite links land on the account's branded bot rather than the
    // global login bot — the right semantic for "join my company").
    // signup_base_url is the apex origin where /signup/<code> works
    // (env-driven; defaults to 4truck.us).  We need it because URL-
    // channel invites issued from dash.4truck.us must point at the
    // apex (where Login.tsx lives) — using window.location.origin
    // here would 404 the recipient.
    apiJSON<{
      bot_username: string;
      signup_base_url?: string;
    }>('/auth/config')
      .then(d => {
        if (d.bot_username) setBotUsername(d.bot_username);
        if (d.signup_base_url) setSignupBase(d.signup_base_url);
      })
      .catch(() => {});
  }, []);

  async function create() {
    setCreating(true);
    try {
      const body: Record<string, unknown> = { role, hours };
      // Gate truck_num on CURRENT role, not on the state string
      // having content — otherwise switching role=driver → role=fleet
      // (which hides the Vehicle picker but preserves truckNum state)
      // attaches the stale vehicle to a non-driver invite.
      if (role === 'driver' && truckNum.trim()) {
        body.truck_num = truckNum.trim();
      }
      // Email channel: include recipient_email when the operator
      // explicitly chose the Email segment AND typed an address.
      // Backend re-validates format and refuses 422 on garbage; we
      // do a coarse client-side check here so the operator sees the
      // problem before the round-trip.
      if (channel === 'email') {
        const addr = recipientEmail.trim().toLowerCase();
        if (!addr || !addr.includes('@')) {
          toast.error(t('toasts.invite_email_invalid', {
            defaultValue: 'Enter a valid recipient email',
          }));
          setCreating(false);
          return;
        }
        if (addr.includes(',') || addr.includes(';')) {
          toast.error(t('toasts.invite_email_one_recipient', {
            defaultValue: 'One recipient per invite — create separate invites for each person',
          }));
          setCreating(false);
          return;
        }
        body.recipient_email = addr;
      }
      const inv = await apiJSON<InviteInfo & {
        channel?: 'link' | 'email';
        email_status?: 'sent' | 'queued_failed' | null;
      }>('/admin/invite', { method: 'POST', body });
      // Channel-aware clipboard URL:
      //   telegram → Telegram deep-link
      //   url      → web signup URL (path-segment, kept out of
      //              Referer + CDN query logs)
      //   email    → web signup URL as FALLBACK (Telegram link
      //              would be useless to an email recipient who
      //              isn't on the bot)
      // The previous implementation always copied the Telegram link
      // regardless of channel — wrong for email-channel operators
      // whose recipient is web-only.
      const url = buildInviteUrl(channel, inv.code);
      try {
        await navigator.clipboard.writeText(url);
        setCopied(inv.code);
        setTimeout(() => setCopied(null), 2000);
        if (channel === 'email') {
          if (inv.email_status === 'sent') {
            toast.success(t('toasts.invite_emailed_url_copied', {
              defaultValue: 'Invite emailed — signup URL also copied to clipboard',
            }));
          } else {
            toast.error(t('toasts.invite_email_failed', {
              defaultValue: 'Invite created but email failed — signup URL copied as fallback',
            }));
          }
        } else if (channel === 'telegram') {
          toast.success(t('toasts.invite_created_telegram_copied', {
            defaultValue: 'Invite created — Telegram link copied to clipboard',
          }));
        } else {
          toast.success(t('toasts.invite_created_url_copied', {
            defaultValue: 'Invite created — signup URL copied to clipboard',
          }));
        }
      } catch {
        toast.error(t('toasts.invite_copy_failed', {
          defaultValue: `Invite created — copy manually: ${url}`,
          url,
        }));
      }
      setShowForm(false);
      // Reset per-driver field + per-recipient address on success;
      // leave role/hours so bulk-onboarding flows ("invite three
      // drivers") don't have to re-fill the same form three times.
      // Channel is the operator's persisted last-choice — not reset.
      setTruckNum('');
      setPickedVehicle(null);
      setRecipientEmail('');
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

  /**
   * Resend an email-channel invite to its original recipient.
   * Single-click, no confirmation — the audit-log captures every
   * resend (incl. attempt count), so an operator who panic-clicks
   * 3× produces 3 audit rows but the per-button 3 s cooldown after
   * success prevents the dominant accidental-double-click case.
   *
   * Refuses on used/revoked (404 from server) and on expired (409 —
   * the operator has to extend first; we surface the message so
   * they know what to do).
   */
  async function resendInviteEmail(invite: InviteInfo) {
    setResending(invite.id);
    try {
      await apiJSON(`/admin/invites/${invite.id}/resend-email`, {
        method: 'POST',
        body: {},
      });
      toast.success(t('toasts.invite_email_resent', {
        defaultValue: 'Invite resent',
      }));
      try { await load({ silent: true }); } catch { /* surfaced */ }
    } catch (e) {
      const isGone = e instanceof ApiError && e.status === 404;
      const isConflict = e instanceof ApiError && e.status === 409;
      if (isGone) {
        toast.info(t('toasts.invite_email_not_available', {
          defaultValue: 'Invite is no longer available',
        }));
        try { await load({ silent: true }); } catch { /* surfaced */ }
      } else if (isConflict) {
        // 409 covers BOTH "invite expired — extend first" AND
        // "this invite was not issued via email".  The server
        // detail string is the authoritative copy; surface it.
        toast.error(e instanceof Error ? e.message : 'Cannot resend this invite');
      } else {
        toast.error(e instanceof Error ? e.message : 'Resend failed');
      }
    } finally {
      // 3 s post-success cooldown is handled by the disabled
      // state going off when ``resending`` clears — we clear it
      // immediately; the operator's natural reading time is the
      // gate.  A heavier cooldown would belong here if real abuse
      // signal shows up.
      setResending(null);
    }
  }

  /**
   * Build the recipient-facing URL for an invite code.  Single
   * source of truth — three channels, three URL shapes:
   *   telegram → ``https://t.me/<bot>?start=join_<code>``
   *   url      → ``<apex>/signup/<code>``  (path-segment URL)
   *   email    → same as url (operator's clipboard fallback)
   *
   * Apex origin comes from /auth/config's ``signup_base_url`` field
   * (env-driven on the server; defaults to ``https://4truck.us`` —
   * the APEX, not a persona subdomain).  This matters because
   * Login.tsx lives ONLY on the apex; a URL like
   * ``https://dash.4truck.us/signup/CODE`` would 404 the unauth
   * recipient.  Falls back to window.location.origin during the
   * brief load window before /auth/config resolves.
   */
  function buildInviteUrl(c: Channel, code: string): string {
    if (c === 'telegram') {
      return `https://t.me/${botUsername}?start=join_${code}`;
    }
    const base = signupBase || window.location.origin;
    return `${base}/signup/${code}`;
  }

  /**
   * Per-row clipboard helper — copies whichever URL the operator
   * picks from the per-row split-button (Copy Telegram / Copy URL).
   * The ``which`` arg lets the same handler serve both DropdownMenu
   * items without duplicating the writeText/timeout/error scaffolding.
   */
  function copyLink(code: string, which: 'telegram' | 'url' = 'telegram') {
    const url = buildInviteUrl(which, code);
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
    // Flip the row in place — the segment tabs move it from Pending
    // to Closed automatically (full set is always loaded now).
    setInvites(prev =>
      prev.map(i =>
        i.id === invite.id
          ? { ...i, is_revoked: true, revoked_at: nowIso }
          : i,
      ),
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

  /**
   * Extend an expired-or-soon-to-expire invite by 24 hours.
   *
   * No confirmation dialog — extend is non-destructive (the operator
   * can always extend again or revoke if they change their mind).
   * Single-click → POST → toast.  Default 24h is the most common
   * "the user clicked too late" recovery; a power-user case for
   * longer extensions would be its own future PR.
   *
   * Optimistic update bumps ``expires_at`` locally so the row's
   * "Expired" pill flips to "Pending" instantly; silent reconcile
   * picks up the authoritative timestamp.  On error we reconcile
   * via load() rather than maintain a snapshot — same rationale as
   * confirmRevoke.
   */
  async function extendInvite(invite: InviteInfo) {
    setExtending(invite.id);
    // Optimistic: bump expires_at by EXTEND_HOURS from now so the
    // StatusBadge immediately flips Expired → Pending.  Server is
    // the source of truth — silent reconcile after.
    // Capture priorExpiry BEFORE the mutation so we can roll back
    // on non-404 errors.  Without the rollback an error path that
    // ALSO fails to reconcile (load() blip) would leave the
    // operator looking at an "Pending" row that is actually
    // server-side still expired — failing open in the worst
    // direction (operator hands a dead link to a recruit).
    const priorExpiry = invite.expires_at;
    const optimisticExpiry = new Date(Date.now() + EXTEND_HOURS_MS).toISOString();
    setInvites(prev =>
      prev.map(i =>
        i.id === invite.id ? { ...i, expires_at: optimisticExpiry } : i,
      ),
    );
    try {
      await apiJSON(`/admin/invites/${invite.id}/extend`, {
        method: 'POST',
        body: { hours: EXTEND_HOURS },
      });
      toast.success(t('toasts.invite_extended', {
        defaultValue: `Invite extended by ${EXTEND_HOURS} hours`,
        hours: EXTEND_HOURS,
      }));
      try { await load({ silent: true }); } catch { /* surfaced via setError */ }
    } catch (e) {
      // 404 is the uniform "not found / used / revoked / race-lost"
      // shape from the endpoint — keep the OLD expiry visible
      // (which says Expired/Used/whatever the row really is) and
      // surface as info, not error.
      const isGone = e instanceof ApiError && e.status === 404;
      if (isGone) {
        // Roll back so the row shows its true server-side state.
        setInvites(prev =>
          prev.map(i =>
            i.id === invite.id ? { ...i, expires_at: priorExpiry } : i,
          ),
        );
        toast.info(t('toasts.invite_extend_not_available', {
          defaultValue: 'Invite is no longer available to extend',
        }));
      } else {
        // Generic failure (network 500/429) — roll back the
        // optimistic bump so the UI is correct even if the silent
        // reconcile below ALSO fails.  Failing closed on extend is
        // the right direction: better to underclaim runway than
        // hand out a link that's already dead server-side.
        setInvites(prev =>
          prev.map(i =>
            i.id === invite.id ? { ...i, expires_at: priorExpiry } : i,
          ),
        );
        toast.error(e instanceof Error ? e.message : String(e));
      }
      try { await load({ silent: true }); } catch { /* surfaced via setError */ }
    } finally {
      setExtending(null);
    }
  }

  // Apply client-side filters on top of the fetched data.  Server
  // already handles the coarse cut (pending_only / include_revoked
  // via the Show-all toggle); this narrows further for operator
  // search.  Memoised so DataGrid doesn't re-render unnecessarily.
  // Search haystack includes role (both raw key and label) so the
  // operator's natural "find me the admin invite" mental model
  // works — role chips remain available for click-once filtering.
  // Search / role / status slicing all live in the grid now: toolbar
  // search (searchKey), the Role + Status column filters, and the
  // Pending / Used / Closed segment tabs.

  const columns: AnyColumn[] = [
    {
      key: 'role',
      label: 'Role',
      // Filter matches on the role code, displays the friendly label
      // from ROLE_LABEL so the dropdown reads "Fleet Manager" / "HR"
      // instead of the internal "fleet" / "hr" codes.
      filterable: true,
      filterValue: (row) => String((row as { role?: string }).role ?? ''),
      filterLabel: (row) => {
        const r = String((row as { role?: string }).role ?? '');
        return ROLE_LABEL[r] ?? r;
      },
      render: (v) => {
        return <RoleBadge role={String(v)} />;
      },
    },
    {
      key: 'truck_num', label: 'Vehicle',
      filterable: true,
      render: (v) => (v as string) || '—',
    },
    {
      key: 'expires_at',
      label: 'Expires',
      render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—',
    },
    {
      key: '_status',
      label: 'Status',
      // Select filter separates Revoked from Expired inside the
      // Closed segment tab (the tab folds them together).
      filterable: true,
      filterValue: (row) => inviteStatus(row as unknown as InviteInfo),
      filterLabel: (row) => {
        const st = inviteStatus(row as unknown as InviteInfo);
        return st.charAt(0).toUpperCase() + st.slice(1);
      },
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
        const isEmailChannel = inv.channel === 'email' || !!inv.sent_to_email;
        const isBounced = !!inv.email_bounced_at && inv.email_bounce_type === 'hard';
        const isSoftDelivery = !!inv.email_bounced_at && inv.email_bounce_type === 'soft';
        const isComplained = !!inv.email_complained_at;
        const canCopy = !inv.is_used && !inv.is_revoked && !inv.is_expired;
        // Resend is offered when the invite is email-channel, still
        // redeemable, AND not in a permanent failure state.  Both
        // bounce and complaint disable resend: resending to a hard-
        // bounced address bounces again; resending to a complainer
        // burns sender reputation harder + may trip Resend's account
        // suspension.  Operator gets the "Revoke this dead invite"
        // affordance instead, then chooses whether to create a new one.
        const canResend = isEmailChannel && !inv.is_used && !inv.is_revoked && !inv.is_expired && !isBounced && !isComplained;
        // The "dead invite" Revoke button replaces both the regular
        // Revoke and the Resend buttons on bounced/complained rows.
        // Dropped the "& recreate" promise from the previous label —
        // the button just revokes; the operator can hit "New invite"
        // afterwards.  Promising a chained flow we don't deliver
        // would be worse than naming the button accurately.
        const isDeadEmailChannel = isEmailChannel && (isBounced || isComplained) && !inv.is_used && !inv.is_revoked;
        // canRevoke is the plain "delete this invite" button.  When
        // isDeadEmailChannel fires, we hide the plain Revoke so the
        // operator doesn't see two visually-distinct buttons that do
        // the same thing.  isDeadEmailChannel's button uses danger
        // tone with explicit copy explaining why; that's the right
        // affordance for these rows.
        const canRevoke = !inv.is_used && !inv.is_revoked && !isDeadEmailChannel;
        const canExtend = !inv.is_used && !inv.is_revoked && inv.is_expired;
        if (!canCopy && !canRevoke && !canExtend && !canResend && !isDeadEmailChannel) {
          return <span className="text-xs text-muted-foreground">—</span>;
        }
        const code = String(inv.code);
        const anyMutationInFlight = revoking !== null || extending !== null || resending !== null;
        const isThisRowRevoking = revoking === inv.id;
        const isThisRowExtending = extending === inv.id;
        const isThisRowResending = resending === inv.id;
        const isJustCopied = copied === code;
        // Bounced/complained/soft-delivery badges go ALONGSIDE the
        // recipient address, not instead of it — the operator needs
        // to see WHICH address bounced to know what to recreate with.
        // Reason text lives in the title attribute on hover.
        const badgeFor = isBounced
          ? { tone: 'danger' as const, label: t('invites.badge_bounced',  { defaultValue: 'Bounced' }),  icon: AlertCircle }
          : isComplained
            ? { tone: 'danger' as const, label: t('invites.badge_complained', { defaultValue: 'Reported as spam' }), icon: ShieldAlert }
            : isSoftDelivery
              ? { tone: 'warn' as const,  label: t('invites.badge_soft_delivery', { defaultValue: 'Delivery issues' }), icon: AlertCircle }
              : null;
        return (
          <div className="inline-flex items-center gap-2 flex-wrap">
            {/* Email-channel marker — envelope icon + truncated
                recipient address.  Bounce/complaint badge renders
                NEXT TO the address, not replacing it (operator
                needs the recipient to act). */}
            {isEmailChannel && inv.sent_to_email && (
              <span
                className="inline-flex items-center gap-1 text-2xs text-muted-foreground"
                title={`Sent to ${inv.sent_to_email}${inv.email_send_count && inv.email_send_count > 1 ? ` (${inv.email_send_count} attempts)` : ''}${inv.email_bounce_reason ? ' — ' + inv.email_bounce_reason : ''}`}
              >
                <Mail size={12} className={isBounced || isComplained ? toneText('danger') : undefined} />
                <span className="truncate max-w-[160px]">{inv.sent_to_email}</span>
                {inv.email_send_count != null && inv.email_send_count > 1 && (
                  <span className="opacity-60">×{inv.email_send_count}</span>
                )}
              </span>
            )}
            {badgeFor && (
              <Tip label={inv.email_bounce_reason || badgeFor.label}>
                <span
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-2xs font-medium ${toneClasses(badgeFor.tone)}`}
                >
                  <badgeFor.icon size={12} />
                  {badgeFor.label}
                </span>
              </Tip>
            )}
            {canCopy && (
              <DropdownMenu>
                <DropdownMenuTrigger
                  className={`inline-flex items-center gap-1 text-xs hover:opacity-80 transition-colors ${
                    isJustCopied ? toneText('ok') : 'text-primary'
                  }`}
                  title={t('actions.copy_invite_link', { defaultValue: 'Copy invite link' })}
                  render={(props) => (
                    <button type="button" {...props}>
                      {isJustCopied ? <Check size={12} /> : <Copy size={12} />}
                      <span className="ml-1">
                        {isJustCopied
                          ? t('actions.copied', { defaultValue: 'Copied' })
                          : t('actions.copy', { defaultValue: 'Copy' })}
                      </span>
                      <ChevronDown size={12} className="ml-0.5 opacity-60" aria-hidden="true" />
                    </button>
                  )}
                />
                <DropdownMenuContent align="start">
                  <DropdownMenuItem onClick={() => copyLink(code, 'telegram')}>
                    <Send size={12} className="mr-2" />
                    {t('actions.copy_telegram', { defaultValue: 'Copy Telegram link' })}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => copyLink(code, 'url')}>
                    <LinkIcon size={12} className="mr-2" />
                    {t('actions.copy_url', { defaultValue: 'Copy URL' })}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {canResend && (
              <button
                onClick={() => resendInviteEmail(inv)}
                disabled={anyMutationInFlight}
                aria-busy={isThisRowResending}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary disabled:opacity-50 disabled:cursor-wait transition-colors"
                title={t('actions.resend_email', { defaultValue: 'Resend the invite email to the same recipient' })}
              >
                {isThisRowResending
                  ? <Loader2 size={12} className="animate-spin" />
                  : <Send size={12} />}
                <span>
                  {isThisRowResending
                    ? t('actions.resending', { defaultValue: 'Resending…' })
                    : t('actions.resend', { defaultValue: 'Resend' })}
                </span>
              </button>
            )}
            {isDeadEmailChannel && (
              <button
                onClick={() => setConfirming(inv)}
                disabled={anyMutationInFlight}
                aria-busy={isThisRowRevoking}
                className={`inline-flex items-center gap-1 text-xs ${toneText('danger')} hover:opacity-80 disabled:opacity-50 disabled:cursor-wait transition-colors`}
                title={isBounced
                  ? t('actions.revoke_bounced_hint', {
                      defaultValue: 'Resending to a bounced address bounces again — revoke this invite, then create a new one with the corrected address.',
                    })
                  : t('actions.revoke_complained_hint', {
                      defaultValue: 'Recipient reported this as spam — revoke the invite. Sending again would damage your sender reputation.',
                    })}
              >
                <Trash2 size={12} />
                <span>
                  {isThisRowRevoking
                    ? t('actions.revoking', { defaultValue: 'Revoking…' })
                    : t('actions.revoke', { defaultValue: 'Revoke' })}
                </span>
              </button>
            )}
            {canExtend && (
              <button
                onClick={() => extendInvite(inv)}
                disabled={anyMutationInFlight}
                aria-busy={isThisRowExtending}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary disabled:opacity-50 disabled:cursor-wait transition-colors"
                title={t('actions.extend_invite_24h', { defaultValue: 'Extend this invite by 24 hours (same code)' })}
              >
                {isThisRowExtending
                  ? <Loader2 size={12} className="animate-spin" />
                  : <TimerReset size={12} />}
                <span>
                  {isThisRowExtending
                    ? t('actions.extending', { defaultValue: 'Extending…' })
                    : t('actions.extend', { defaultValue: 'Extend' })}
                </span>
              </button>
            )}
            {canRevoke && (
              <button
                onClick={() => setConfirming(inv)}
                disabled={anyMutationInFlight}
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
        {/* The "Show all" toggle is gone — the fetch always loads the
            full set and the grid's segment tabs slice it. */}
        <button
          ref={newInviteBtnRef}
          onClick={() => { setRecipientEmail(''); setShowForm(true); }}
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
          title="No invites have been issued"
          description="Create an invite to add a new teammate — pick the role and how long the link should be valid."
          action={
            <button
              onClick={() => { setRecipientEmail(''); setShowForm(true); }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={14} />
              New invite
            </button>
          }
        />
      ) : (
        <DataGrid
          tableId="invites"
          segments={INVITE_SEGMENTS}
          columns={columns}
          data={invites as unknown as Record<string, unknown>[]}
          searchKey={['code', 'truck_num', 'role']}
          searchPlaceholder="Search code, vehicle, or role…"
        />
      )}

      {/* Create modal — uses ui/dialog primitive (base-ui).
          Migrated from a hand-rolled ``<div className="fixed inset-0
          …">`` so the form inherits Escape-to-close, focus-trap
          (Tab stays inside the dialog instead of escaping to the
          page behind), outside-click dismissal, scroll-lock, and
          ARIA labelling for screen readers.  Wrapping the body in
          a ``<form>`` also gets Enter-to-submit for free, which the
          old overlay didn't have — operators can now type
          vehicle, hit Enter, and the link is on their clipboard. */}
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
              {/* Send-via segmented control.  Link = current zero-cost
                  flow (default — never sticky from a prior open).
                  Email = stamps recipient_email on the row and ships
                  the link via SMTP.  Two-option control because more
                  channels (SMS?) aren't on the roadmap yet — a single
                  checkbox "also send by email" would be ambiguous if
                  a third channel ever lands. */}
              {/* Three-channel segmented control.  Two-line layout
                  on narrow viewports avoids the i18n overflow concern
                  (Russian "Электронная почта" = 16 chars would clip
                  in grid-cols-3 at the dialog's 512px width).  The
                  ``flex-wrap`` lets each button shrink to its content
                  while preserving the rounded-pill segmented look. */}
              <div>
                <label className="block text-sm text-muted-foreground mb-1">
                  {t('forms.send_via', { defaultValue: 'Send via' })}
                </label>
                <div className="flex flex-wrap gap-1 bg-muted rounded p-0.5 border border-border">
                  {([
                    { key: 'telegram' as const, icon: Send,    label: t('actions.telegram', { defaultValue: 'Telegram' }) },
                    { key: 'url'      as const, icon: LinkIcon, label: t('actions.url',      { defaultValue: 'URL link' }) },
                    { key: 'email'    as const, icon: Mail,    label: t('actions.email',    { defaultValue: 'Email' }) },
                  ]).map(({ key, icon: Icon, label }) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => persistChannel(key)}
                      className={`flex-1 min-w-20 px-3 py-1.5 text-xs font-medium rounded transition ${
                        channel === key
                          ? 'bg-primary text-primary-foreground'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <span className="inline-flex items-center justify-center gap-1.5">
                        <Icon size={12} />{label}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {channel === 'email' && (
                <div>
                  <label className="block text-sm text-muted-foreground mb-1">
                    {t('forms.recipient_email', { defaultValue: 'Recipient email' })}
                  </label>
                  <input
                    type="email"
                    value={recipientEmail}
                    onChange={(e) => setRecipientEmail(e.target.value)}
                    className="w-full bg-muted rounded px-3 py-2 text-sm text-foreground border border-border"
                    placeholder={t('forms.recipient_email_placeholder', { defaultValue: 'driver@company.com' })}
                    required
                    autoComplete="off"
                  />
                  <p className="text-2xs text-muted-foreground mt-1">
                    {t('forms.recipient_email_hint', {
                      defaultValue: 'One recipient per invite. The link is also copied to your clipboard as a fallback.',
                    })}
                  </p>
                  {duplicateMember && (
                    <div className={`mt-2 rounded-md border px-2 py-1.5 text-2xs ${toneClasses('warn')}`}>
                      <div className="font-medium">
                        {t('forms.recipient_email_duplicate_title', {
                          defaultValue: 'Already a member: {{name}} ({{role}})',
                          name: duplicateMember.display_name || '—',
                          role: ROLE_LABEL[duplicateMember.role.toLowerCase()] || duplicateMember.role,
                        })}
                      </div>
                      <div className="opacity-80 mt-0.5">
                        {t('forms.recipient_email_duplicate_desc', {
                          defaultValue: 'This email belongs to a user in your account. Sending will generate an invite they can use to switch roles or restore access — but it won\'t replace their existing membership.',
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div>
                <label className="block text-sm text-muted-foreground mb-1">Role</label>
                <Select value={role} onValueChange={(v) => setRole(v ?? '')} items={INVITE_ROLE_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="Role"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {INVITE_ROLE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>

              {role === 'driver' && (
                <div>
                  <label className="block text-sm text-muted-foreground mb-1">
                    {t('forms.vehicle_optional', { defaultValue: 'Vehicle (optional)' })}
                  </label>
                  <VehiclePicker
                    value={truckNum}
                    onChange={(name, vehicle) => {
                      setTruckNum(name);
                      setPickedVehicle(vehicle);
                    }}
                    vehicles={vehicleList}
                    loading={vehiclesLoading}
                  />
                  <p className="text-2xs text-muted-foreground mt-1">
                    {t('forms.vehicle_hint', {
                      defaultValue: 'Pick the vehicle this driver will operate, or leave blank to assign later.',
                    })}
                  </p>
                  {/* pickedVehicle hint — surface the picked vehicle's status
                      so the operator sees they're inviting a driver to a
                      stopped/idle vehicle, etc. */}
                  {pickedVehicle && (
                    <p className="text-2xs text-muted-foreground mt-1 opacity-70">
                      {pickedVehicle.company} · {pickedVehicle.status}
                    </p>
                  )}
                </div>
              )}

              <div>
                <label className="block text-sm text-muted-foreground mb-1">Expires in (hours)</label>
                <Select value={String(hours)} onValueChange={(v) => setHours(Number(v))} items={INVITE_HOURS_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="Expires in (hours)"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {INVITE_HOURS_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
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
                  : channel === 'email'
                    ? t('actions.create_and_email', { defaultValue: 'Create & Send Email' })
                    : channel === 'telegram'
                      ? t('actions.create_and_copy_telegram', { defaultValue: 'Create & Copy Telegram' })
                      : t('actions.create_and_copy_url', { defaultValue: 'Create & Copy URL' })}
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
                {confirming.truck_num && (
                  <>
                    <span>·</span>
                    <span>{t('forms.vehicle_label', { defaultValue: 'Vehicle' })} {confirming.truck_num}</span>
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
