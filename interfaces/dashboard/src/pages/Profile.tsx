/**
 * Per-user preferences page — split out from /admin/settings so the
 * "account-wide things admins configure" and "settings about *me*"
 * live in two distinct places.
 *
 * Anything that affects only the logged-in user belongs here:
 *   - Display name
 *   - UI language
 *   - Personal timezone override (falls back to the account default)
 *   - Working Hours override (the personal active-alerts window that
 *     overrides the account/role default; outside this window non-
 *     critical alerts queue until shift-start)
 *
 * Visible to every authenticated user regardless of role; no admin
 * permission required.  The admin-only counterpart (account timezone,
 * Telegram bot config, working hours, etc.) stays on /admin/settings.
 */

import { useEffect, useRef, useState } from 'react';
import { formatDay } from '../utils/datetime';
import { useTimezone } from '../hooks/useTimezone';
import { useTranslation } from 'react-i18next';
import {
  UserCog,
  Monitor,
  Smartphone,
  Send,
  ShieldCheck,
  X,
  LogOut,
  Mail,
  Link as LinkIcon,
  Unlink,
  Download,
  History,
  CheckCircle2,
  XCircle,
  ExternalLink,
} from 'lucide-react';

import { apiJSON, apiFetch } from '../api/client';
import { PageHeader, ErrorState } from '../components/shell';
import StoredPreferencesCard from '../preferences/StoredPreferencesCard';
import { Modifications } from '../mods';
import { toneClasses } from '../lib/status';
import type { User } from '../types';
import { LANGUAGE_OPTIONS } from '../utils/languages';
import { TIMEZONE_OPTIONS, timezoneLabelWithTime } from '../utils/timezones';
import { useNow } from '../hooks/useNow';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../components/ui/select';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';
import { Badge } from '@/components/ui/badge';

// HOURS array removed in the migration-100 cleanup — the user no
// longer picks shift hours from Profile (admin-managed in Team
// Management → drawer Settings tab).  Profile keeps only the DND
// toggle + a read-only preview of the admin-set schedule.


export default function Profile() {
  const { t } = useTranslation();
  const now = useNow();

  const [lang, setLang] = useState('en');
  const [tz, setTz] = useState('');
  const [accountTz, setAccountTz] = useState('America/New_York');
  // Working Hours values are admin-managed (read-only here); kept as
  // state purely so the schedule preview text re-renders on /user/me
  // load.  The user's only personal control is ``dndEnabled`` below.
  const [quietStart, setQuietStart] = useState<number | null>(null);
  const [quietEnd, setQuietEnd] = useState<number | null>(null);
  // Personal DND toggle (migration 100).  True = honour the schedule
  // shown above (queue non-critical alerts outside the window).
  // False = receive all non-critical alerts 24/7.  Defaults to true
  // so the toggle on first load mirrors the existing user behaviour.
  const [dndEnabled, setDndEnabled] = useState<boolean>(true);
  const [name, setName] = useState('');
  // DND derivation state surfaced by /user/me so the UI can render
  // "auto from Working Hours" vs "personal override" without
  // duplicating the SSoT logic from the backend.
  const [dndSource, setDndSource] = useState<'user_override' | 'work_hours' | 'none'>('none');
  const [workHoursForRole, setWorkHoursForRole] = useState<
    Array<{ id: number; label: string; start_hour: number; end_hour: number; target_role: string }>
  >([]);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Bring a #hash section into view. The browser cannot do this itself
  // here: the page mounts empty and fills in after /user/me resolves, so
  // by the time the target exists the navigation is long over — and the
  // scrollport is the shell's own div, not the document. `scroll-mt-*` on
  // the target handles the sticky header offset.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const t = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    apiJSON<User & {
      dnd_source?: 'user_override' | 'work_hours' | 'none';
      work_hours_for_role?: Array<{ id: number; label: string; start_hour: number; end_hour: number; target_role: string }>;
    }>('/user/me')
      .then((u) => {
        setLang(u.language || 'en');
        setQuietStart(u.quiet_start ?? null);
        setQuietEnd(u.quiet_end ?? null);
        setDndEnabled(u.dnd_enabled ?? true);
        setName(u.display_name || '');
        setDndSource(u.dnd_source || 'none');
        setWorkHoursForRole(u.work_hours_for_role || []);
        if (u.account_timezone) setAccountTz(u.account_timezone);
        // Normalise: when user override matches account default, collapse
        // it to "" so the dropdown shows "Use account default" and the
        // matching explicit option (which we hide as a duplicate) doesn't
        // leave the select in a no-match state.
        const stored = u.timezone || '';
        setTz(stored && stored === u.account_timezone ? '' : stored);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : 'Failed to load profile'),
      );
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSuccess('');
    const body: Record<string, unknown> = {};
    if (lang) body.language = lang;
    // Always send timezone — empty string clears the override.
    body.timezone = tz;
    if (name) body.display_name = name;
    // Personal DND toggle.  Working Hours hours themselves (quiet_start /
    // quiet_end) are admin-managed since migration 100 — the backend
    // explicitly rejects them on /user/preferences, so we never send.
    body.dnd_enabled = dndEnabled;
    try {
      await apiJSON('/user/preferences', { method: 'PUT', body });
      setSuccess('Preferences saved.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  // Timezone dropdown items — the leading "" value keeps the account
  // default as a real, re-selectable choice (matches the native select
  // that had an <option value=""> first row); the rest hide the option
  // equal to the account default to avoid a duplicate row.
  const tzItems = [
    { value: '', label: `Use account default (${timezoneLabelWithTime(accountTz, now)})` },
    ...TIMEZONE_OPTIONS
      .filter((o) => o.value !== accountTz)
      .map((o) => ({ value: o.value, label: timezoneLabelWithTime(o.value, now) })),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserCog}
        title="My Profile"
        description="Personal preferences — only affects what you see and when you get alerts."
      />

      {error && <ErrorState message={error} />}

      <Card render={<section />}>
        <h2 className="text-lg font-semibold mb-1">Personal preferences</h2>
        <p className="text-xs text-muted-foreground mb-4">
          These settings apply to your own dashboard, Telegram alerts, and
          report deliveries. They don't affect anyone else on the account.
        </p>
        {success && (
          <p className="text-ok text-sm mb-3">{success}</p>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Display name */}
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              Display name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
            />
          </div>

          {/* Language */}
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              {t('language.label', 'Language')}
            </label>
            <Select value={lang} onValueChange={(v) => setLang(v ?? '')} items={LANGUAGE_OPTIONS}>
              <SelectTrigger className="w-full" aria-label={t('language.label', 'Language')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {LANGUAGE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Timezone override */}
          <div className="md:col-span-2">
            <label className="block text-xs text-muted-foreground mb-1">
              Timezone override
              <span className="ml-1 text-2xs opacity-70">
                (leave on "Use account default" to follow the company timezone)
              </span>
            </label>
            <Select value={tz} onValueChange={(v) => setTz(v ?? '')} items={tzItems}>
              <SelectTrigger className="w-full" aria-label="Timezone override">
                <SelectValue placeholder={`Use account default (${timezoneLabelWithTime(accountTz, now)})`} />
              </SelectTrigger>
              <SelectContent>
                {tzItems.map((it) => (
                  <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-2xs text-muted-foreground mt-1 opacity-75">
              Pick a specific timezone if you work in a different one from the
              company default.
            </p>
          </div>

          {/* Notifications — personal DND toggle (the user's only
              control over alert delivery timing).  Working Hours are
              admin-managed since migration 100; the schedule preview
              below is read-only.  Toggling DND off opts the user out
              of the schedule entirely — they receive every alert 24/7.
              Critical-severity alerts always come through either way. */}
          <div className="md:col-span-2">
            <label className="block text-xs text-muted-foreground mb-1">
              Notifications
            </label>

            {/* DND toggle row.  Label rewritten to plain English —
                "Quiet outside shift" read as jargon to operators.  The
                new phrasing matches the way drivers actually talk about
                it ("don't bother me when I'm off the clock"). */}
            <div className="flex items-center justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3 mb-2">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">Don't disturb me off-shift</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {dndEnabled
                    ? 'Non-urgent alerts wait until your shift starts. Critical alerts always come through.'
                    : 'You get every alert 24/7. Turn on to silence non-urgent ones when you\'re off-shift.'}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={dndEnabled}
                aria-label="Don't disturb me off-shift"
                onClick={() => setDndEnabled(v => !v)}
                className={`shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition ${
                  dndEnabled ? 'bg-primary' : 'bg-muted-foreground/30'
                } min-h-tap`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-background shadow transition ${
                    dndEnabled ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>

            {/* Schedule preview — show each shift WITH its label so a
                user with multiple matching role schedules can tell
                which line is which.  Bullet list reads cleaner than a
                comma-joined string of hours that mashed them together. */}
            <div className={`rounded border p-3 text-xs ${
              dndSource === 'user_override'
                ? 'border-primary/40 bg-primary/5'
                : dndSource === 'work_hours'
                ? toneClasses('ok')
                : 'border-border bg-muted/30 text-muted-foreground'
            }`}>
              {dndSource === 'user_override' && quietStart !== null && quietEnd !== null && (
                <>
                  <p className="font-medium mb-1">My shift</p>
                  <p>
                    {String(quietStart).padStart(2, '0')}:00 – {String(quietEnd).padStart(2, '0')}:00
                  </p>
                  <p className="text-2xs text-muted-foreground mt-1.5">
                    Personal shift set by your admin. Ask them to change it.
                  </p>
                </>
              )}
              {dndSource === 'work_hours' && (
                <>
                  <p className="font-medium mb-1">
                    My shift{workHoursForRole.length > 1 ? 's' : ''} (from your role)
                  </p>
                  <ul className="space-y-0.5">
                    {workHoursForRole.map((wh) => (
                      <li key={wh.id} className="flex items-baseline gap-2">
                        <span className="font-medium">{wh.label}</span>
                        <span>
                          {String(wh.start_hour).padStart(2, '0')}:00 – {String(wh.end_hour).padStart(2, '0')}:00
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-2xs text-muted-foreground mt-1.5">
                    {workHoursForRole.length > 1
                      ? 'You\'re considered on-shift during ANY of these windows. Ask your admin to change them.'
                      : 'Ask your admin to change this.'}
                  </p>
                </>
              )}
              {dndSource === 'none' && (
                <p>
                  No shift set — alerts deliver 24/7. Ask your admin to set Working Hours for your role.
                </p>
              )}
            </div>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="mt-5 px-4 py-2 bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 rounded text-sm font-medium transition min-h-tap"
        >
          {saving ? 'Saving…' : 'Save preferences'}
        </button>
      </Card>

      <Modifications />
      <StoredPreferencesCard />
      <SignInMethods />
      <RecentActivity />
      <ActiveSessions />
      <DataExport />
    </div>
  );
}


// ── Sign-in methods ──────────────────────────────────────────────
//
// Every account can authenticate by email + password and/or by Telegram.
// The backend keeps them independent, so a user can have one, the
// other, or both.  This panel shows what's currently connected and
// surfaces the deep-link flow to attach the missing one.

function SignInMethods() {
  const { t } = useTranslation();
  const [me, setMe] = useState<User | null>(null);
  const [err, setErr] = useState('');
  const [linking, setLinking] = useState(false);
  const [unlinking, setUnlinking] = useState(false);
  const [linkStatus, setLinkStatus] = useState<'idle' | 'pending' | 'rejected'>('idle');
  const [linkReason, setLinkReason] = useState('');
  const pollHandle = useRef<number | null>(null);
  // Add-email form state — visible inline when the user has no email
  // attached.  No "ask an admin" detour; the /user/credentials endpoint
  // lets the user set their own.
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingCreds, setSavingCreds] = useState(false);
  const [credsOk, setCredsOk] = useState('');

  const stopPolling = () => {
    if (pollHandle.current !== null) {
      window.clearInterval(pollHandle.current);
      pollHandle.current = null;
    }
  };

  const load = () => {
    apiJSON<User>('/user/me')
      .then(setMe)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load'));
  };

  useEffect(() => {
    load();
    return stopPolling;
  }, []);

  const startLink = async () => {
    setErr('');
    setLinkReason('');
    setLinking(true);
    try {
      const r = await apiJSON<{ token: string; deep_link: string; ttl: number }>(
        '/user/telegram/link/init',
        { method: 'POST', body: {} },
      );
      // Open the bot in a new tab so the dashboard stays alive for polling.
      window.open(r.deep_link, '_blank', 'noopener');
      setLinkStatus('pending');
      // Poll every 2s, give up after ttl seconds.
      const deadline = Date.now() + r.ttl * 1000;
      pollHandle.current = window.setInterval(async () => {
        if (Date.now() > deadline) {
          stopPolling();
          setLinkStatus('idle');
          return;
        }
        try {
          const s = await apiJSON<{ status: string; reason?: string }>(
            `/user/telegram/link/status/${r.token}`,
          );
          if (s.status === 'linked') {
            stopPolling();
            setLinkStatus('idle');
            load();
          } else if (s.status === 'rejected') {
            stopPolling();
            setLinkReason(s.reason || 'Refused');
            setLinkStatus('rejected');
          } else if (s.status === 'expired') {
            stopPolling();
            setLinkStatus('idle');
          }
        } catch {
          // Transient network errors — keep polling.
        }
      }, 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to start link');
    } finally {
      setLinking(false);
    }
  };

  const resendVerification = async () => {
    if (!me?.email) return;
    setErr('');
    setCredsOk('');
    try {
      await apiJSON('/auth/resend-verification', {
        method: 'POST', body: { email: me.email },
      });
      setCredsOk(t(
        'profile.signin_verification_sent',
        'Verification email sent. Check your inbox.',
      ));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to resend');
    }
  };

  const submitCredentials = async () => {
    setErr('');
    setCredsOk('');
    if (newPassword !== confirmPassword) {
      setErr(t('profile.signin_pw_mismatch', "Passwords don't match."));
      return;
    }
    if (newPassword.length < 8 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword)) {
      setErr(t(
        'profile.signin_pw_rule',
        'Password must be at least 8 characters and include one letter and one digit.',
      ));
      return;
    }
    setSavingCreds(true);
    try {
      await apiJSON('/user/credentials', {
        method: 'PUT',
        body: { email: newEmail, password: newPassword },
      });
      setCredsOk(t(
        'profile.signin_email_saved',
        'Email + password saved.  Check your inbox for a verification link — until you confirm it, this email can\'t be used to sign in.',
      ));
      setShowEmailForm(false);
      setNewEmail('');
      setNewPassword('');
      setConfirmPassword('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save credentials');
    } finally {
      setSavingCreds(false);
    }
  };

  const unlink = async () => {
    if (!window.confirm(t('profile.confirm_unlink_telegram', 'Disconnect Telegram from this account?  You can re-link later from this same panel.'))) return;
    setUnlinking(true);
    setErr('');
    try {
      const r = await apiFetch('/user/telegram', { method: 'DELETE' });
      if (!r.ok) {
        let detail = `Failed (${r.status})`;
        try {
          const j = await r.json() as { detail?: string };
          if (j.detail) detail = j.detail;
        } catch { /* keep generic */ }
        throw new Error(detail);
      }
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to unlink');
    } finally {
      setUnlinking(false);
    }
  };

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck className="text-muted-foreground size-4.5" />
        <h2 className="text-lg font-semibold">{t('profile.signin_title', 'Sign-in methods')}</h2>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        {t(
          'profile.signin_desc',
          'These are the ways you can sign in to your account. Keeping both connected means you can recover one through the other.',
        )}
      </p>

      {err && <ErrorState message={err} />}
      {credsOk && (
        <p className="text-ok text-sm mb-3">{credsOk}</p>
      )}

      <ul className="divide-y divide-border">
        {/* Email */}
        <li className="flex items-start gap-3 py-3">
          <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${me?.email ? 'bg-primary/15 text-foreground ring-1 ring-primary' : 'bg-muted text-muted-foreground'}`}>
            <Mail className="size-4.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium flex items-center gap-2 flex-wrap">
              {t('profile.signin_email_label', 'Email + password')}
              {me?.email && me.email_verified === false && (
                <Badge tone="warn" className="uppercase tracking-wider font-semibold" title={t(
                    'profile.signin_verify_hint',
                    "We sent a verification link.  Until you click it, this email can't be used to sign in.",
                  )}>
                  {t('profile.signin_unverified_chip', 'Unverified')}
                </Badge>
              )}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5 truncate">
              {me?.email
                ? me.email
                : t(
                    'profile.signin_email_missing',
                    'No email set — add one below to sign in without Telegram.',
                  )}
            </div>
          </div>
          {me?.email && me.email_verified === false ? (
            <button
              type="button"
              onClick={resendVerification}
              className="shrink-0 text-xs text-foreground hover:bg-primary/10 px-2 py-1 rounded min-h-tap ring-1 ring-primary"
            >
              {t('profile.signin_resend_verification', 'Resend link')}
            </button>
          ) : me?.email ? (
            <a
              href="/forgot-password"
              className="shrink-0 inline-flex items-center text-xs text-primary hover:underline px-2 py-1 min-h-tap"
            >
              {t('profile.signin_change_password', 'Change password')}
            </a>
          ) : (
            <button
              type="button"
              onClick={() => setShowEmailForm((v) => !v)}
              className="shrink-0 inline-flex items-center gap-1 text-xs text-foreground hover:bg-primary/10 px-2 py-1 rounded min-h-tap ring-1 ring-primary"
            >
              {showEmailForm
                ? t('profile.signin_email_cancel', 'Cancel')
                : t('profile.signin_email_add', 'Add email')}
            </button>
          )}
        </li>

        {/* Inline "add email + password" form — only when the user has
            no email attached and they've clicked "Add email". */}
        {!me?.email && showEmailForm && (
          <li className="py-3">
            <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-3">
              <p className="text-xs text-muted-foreground">
                {t(
                  'profile.signin_email_form_intro',
                  'Pick an email + password so you can sign in to the dashboard without Telegram. You can do both later if you like.',
                )}
              </p>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('profile.signin_email_field', 'Email address')}
                </label>
                <input
                  type="email"
                  autoComplete="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  className="w-full bg-card border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
                  placeholder="you@example.com"
                />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">
                    {t('profile.signin_password_field', 'Password')}
                  </label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full bg-card border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">
                    {t('profile.signin_password_confirm', 'Confirm password')}
                  </label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full bg-card border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
                  />
                </div>
              </div>
              <p className="text-2xs text-muted-foreground">
                {t(
                  'profile.signin_pw_hint',
                  'At least 8 characters · must include a letter and a digit.',
                )}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={submitCredentials}
                  disabled={savingCreds || !newEmail || !newPassword}
                  className="inline-flex items-center gap-1 text-sm px-3 py-1.5 rounded bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 min-h-tap"
                >
                  {savingCreds
                    ? t('profile.signin_saving', 'Saving…')
                    : t('profile.signin_save_credentials', 'Save email + password')}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowEmailForm(false);
                    setNewEmail('');
                    setNewPassword('');
                    setConfirmPassword('');
                    setErr('');
                  }}
                  className="text-sm px-3 py-1.5 rounded text-muted-foreground hover:bg-muted"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
              </div>
            </div>
          </li>
        )}

        {/* Telegram */}
        <li className="flex items-start gap-3 py-3">
          <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${me?.telegram_id ? 'bg-primary/15 text-foreground ring-1 ring-primary' : 'bg-muted text-muted-foreground'}`}>
            <Send className="size-4.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{t('profile.signin_telegram_label', 'Telegram')}</div>
            <div className="text-xs text-muted-foreground mt-0.5 truncate">
              {me?.telegram_id ? (
                <>
                  {t('profile.signin_telegram_linked', 'Connected')}{' '}
                  <span className="font-mono opacity-75">· tg:{me.telegram_id}</span>
                </>
              ) : linkStatus === 'pending' ? (
                t('profile.signin_telegram_waiting', 'Waiting for confirmation in Telegram…')
              ) : linkStatus === 'rejected' ? (
                <span className="text-danger">
                  {linkReason || t('profile.signin_telegram_refused', 'Link was refused — try again.')}
                </span>
              ) : (
                t('profile.signin_telegram_not_linked', 'Not linked — connect to sign in via the bot.')
              )}
            </div>
          </div>
          {me?.telegram_id ? (
            <button
              onClick={unlink}
              disabled={unlinking}
              className="shrink-0 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 disabled:opacity-40 px-2 py-1 rounded min-h-tap"
            >
              <Unlink className="size-3" />
              {unlinking ? t('profile.signin_unlinking', 'Unlinking…') : t('profile.signin_unlink', 'Disconnect')}
            </button>
          ) : (
            <button
              onClick={startLink}
              disabled={linking || linkStatus === 'pending'}
              className="shrink-0 inline-flex items-center gap-1 text-xs text-foreground hover:bg-primary/10 disabled:opacity-40 px-2 py-1 rounded min-h-tap ring-1 ring-primary"
            >
              <LinkIcon className="size-3" />
              {linking
                ? t('profile.signin_starting', 'Opening…')
                : linkStatus === 'pending'
                ? t('profile.signin_waiting', 'Waiting…')
                : t('profile.signin_link_telegram', 'Connect Telegram')}
            </button>
          )}
        </li>
      </ul>

      {!me?.email && !me?.telegram_id && (
        <p className="text-2xs text-warn mt-2">
          {t(
            'profile.signin_warn_no_methods',
            'No sign-in method is attached to this account — contact an admin to fix this before your session expires.',
          )}
        </p>
      )}
    </Card>
  );
}


// ── Recent activity (login attempts) ─────────────────────────────
//
// Surfaces the audit log so users can spot logins they don't recognise.
// The endpoint already exists; this panel is purely the read-side UI.

interface ActivityRow {
  attempted_at: string;
  email: string | null;
  success: boolean | number;
  failure_reason: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

function RecentActivity() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  useEffect(() => {
    setLoading(true);
    setErr('');
    apiJSON<{ items: ActivityRow[] }>('/user/me/activity?limit=20')
      .then((r) => setRows(r.items || []))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const tzName = useTimezone();
  const formatWhen = (iso: string): string => {
    const d = Date.parse(iso);
    if (Number.isNaN(d)) return iso;
    const sec = Math.floor((Date.now() - d) / 1000);
    if (sec < 60) return t('time.just_now', 'just now');
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ${t('time.ago', 'ago')}`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ${t('time.ago', 'ago')}`;
    const days = Math.floor(hr / 24);
    if (days === 1) return t('time.yesterday', 'yesterday');
    if (days < 30) return `${days}d ${t('time.ago', 'ago')}`;
    // Older than a month: an absolute day, in the user's timezone —
    // the raw UTC slice read one day off during US evenings.
    return formatDay(iso, { timeZone: tzName });
  };

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <History className="text-muted-foreground size-4.5" />
        <h2 className="text-lg font-semibold">{t('profile.activity_title', 'Recent sign-in activity')}</h2>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        {t(
          'profile.activity_desc',
          "Last 20 sign-in attempts on your account.  If you don't recognise one of these, change your password right away.",
        )}
      </p>

      {err && <ErrorState message={err} />}
      {loading && <p className="text-sm text-muted-foreground py-2">{t('common.loading', 'Loading…')}</p>}
      {!loading && !err && rows.length === 0 && (
        <p className="text-sm text-muted-foreground py-2">
          {t('profile.activity_empty', 'No sign-in attempts recorded yet.')}
        </p>
      )}

      <ul className="divide-y divide-border">
        {rows.map((r, idx) => {
          const ok = r.success === true || r.success === 1;
          return (
            <li key={idx} className="flex items-center gap-3 py-2.5 text-sm">
              <div className={`shrink-0 ${ok ? 'text-ok' : 'text-danger'}`}>
                {ok ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="font-medium">
                    {ok
                      ? t('profile.activity_success', 'Signed in')
                      : t('profile.activity_failed', 'Failed attempt')}
                  </span>
                  {!ok && r.failure_reason && (
                    <span className="text-xs text-muted-foreground">· {r.failure_reason}</span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5">
                  <span>{formatWhen(r.attempted_at)}</span>
                  {r.ip_address && <span className="font-mono">· {r.ip_address}</span>}
                  {r.user_agent && <span className="truncate max-w-65">· {r.user_agent}</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}


// ── Data export (GDPR Art. 15 / 20) ──────────────────────────────
//
// Pulls /user/me/export and offers it as a downloadable JSON file.

function DataExport() {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const [err, setErr] = useState('');

  const download = async () => {
    setDownloading(true);
    setErr('');
    try {
      const r = await apiFetch('/user/me/export');
      if (!r.ok) throw new Error(`Failed (${r.status})`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const a = document.createElement('a');
      a.href = url;
      a.download = `my-data-${stamp}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Download failed');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <Download className="text-muted-foreground size-4.5" />
        <h2 className="text-lg font-semibold">{t('profile.export_title', 'Download my data')}</h2>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        {t(
          'profile.export_desc',
          'Get a JSON copy of everything we store about you — profile, sessions, recent sign-ins, company memberships, and vehicle assignments.',
        )}
      </p>

      {err && <ErrorState message={err} />}

      <button
        onClick={download}
        disabled={downloading}
        className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 transition min-h-tap"
      >
        <ExternalLink className="size-3.5" />
        {downloading
          ? t('profile.export_preparing', 'Preparing…')
          : t('profile.export_button', 'Download JSON')}
      </button>
    </Card>
  );
}


// ── Active sessions ──────────────────────────────────────────────
//
// Shows every browser / Mini App currently holding a JWT for this user.
// Display-only in Pass 1; the per-row Revoke ✕ and the "Terminate all
// other sessions" button arrive in Pass 2 once the denylist is wired.

interface SessionRow {
  id: number;
  jti: string;
  device_label: string;
  user_agent: string;
  ip: string;
  created_at: string;
  last_seen: string;
  expires_at: string;
}

function deviceIcon(label: string) {
  const l = label.toLowerCase();
  if (l.startsWith('telegram')) return Send;
  if (l.includes('android') || l.includes('ios')) return Smartphone;
  return Monitor;
}

function formatLastSeen(iso: string, tzName?: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60)   return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60)   return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24)    return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  if (days === 1) return 'yesterday';
  if (days < 30)  return `${days}d ago`;
  return formatDay(iso, { timeZone: tzName });
}

function ActiveSessions() {
  const tzName = useTimezone();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [currentJti, setCurrentJti] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  // Per-row revoke spinner so the UI doesn't go silent on a slow call.
  // String key is the session id; value is "revoking" while in flight.
  const [busyId, setBusyId] = useState<number | null>(null);
  const [terminating, setTerminating] = useState(false);

  const load = () => {
    setLoading(true);
    setErr('');
    apiJSON<{ items: SessionRow[]; current_jti: string }>('/user/sessions')
      .then((r) => {
        setRows(r.items);
        setCurrentJti(r.current_jti);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const revoke = async (s: SessionRow) => {
    if (!window.confirm(`Sign out ${s.device_label || 'this device'}?`)) return;
    setBusyId(s.id);
    try {
      const r = await apiFetch(`/user/sessions/${s.id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`Failed (${r.status})`);
      // Optimistic: drop the row immediately rather than wait for refetch.
      setRows((prev) => prev.filter((x) => x.id !== s.id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to revoke');
    } finally {
      setBusyId(null);
    }
  };

  const terminateOthers = async () => {
    const others = rows.filter((r) => r.jti !== currentJti).length;
    if (others === 0) return;
    if (!window.confirm(
      `Sign out ${others} other session${others === 1 ? '' : 's'}?  ` +
      `Devices other than this one will be signed out at their next page load.`
    )) return;
    setTerminating(true);
    try {
      const r = await apiFetch('/user/sessions/terminate-others', { method: 'POST' });
      if (!r.ok) throw new Error(`Failed (${r.status})`);
      // Drop everything except current.
      setRows((prev) => prev.filter((x) => x.jti === currentJti));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to terminate sessions');
    } finally {
      setTerminating(false);
    }
  };

  // Sort: current device first, then by last_seen DESC.
  const sorted = [...rows].sort((a, b) => {
    if (a.jti === currentJti) return -1;
    if (b.jti === currentJti) return 1;
    return b.last_seen.localeCompare(a.last_seen);
  });
  const otherCount = sorted.filter((s) => s.jti !== currentJti).length;

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck className="text-muted-foreground size-4.5" />
        <SectionHeader>Active sessions</SectionHeader>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Every device currently signed in to your account. Sessions stay
        active until you sign out or the token expires (8 hours for short
        sessions, 30 days when "Remember me" was checked at login).
      </p>

      {err && <ErrorState message={err} />}
      {loading && <p className="text-sm text-muted-foreground py-2">Loading…</p>}
      {!loading && !err && sorted.length === 0 && (
        <p className="text-sm text-muted-foreground py-2">
          No active sessions yet — they'll appear here once you sign in
          again after this update.
        </p>
      )}

      <ul className="divide-y divide-border">
        {sorted.map((s) => {
          const Icon = deviceIcon(s.device_label);
          const isCurrent = s.jti === currentJti;
          const isBusy = busyId === s.id;
          return (
            <li key={s.id} className="flex items-start gap-3 py-3">
              <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${isCurrent ? 'bg-primary/15 text-foreground ring-1 ring-primary' : 'bg-muted text-muted-foreground'}`}>
                <Icon className="size-4.5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{s.device_label || 'Unknown device'}</span>
                  {isCurrent && (
                    <span className="text-2xs uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/15 text-foreground font-semibold ring-1 ring-primary">
                      This device
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5">
                  {s.ip && <span className="font-mono">{s.ip}</span>}
                  <span>signed in {formatLastSeen(s.created_at, tzName)}</span>
                  {!isCurrent && <span>· active {formatLastSeen(s.last_seen, tzName)}</span>}
                </div>
              </div>
              {!isCurrent && (
                <button
                  onClick={() => revoke(s)}
                  disabled={isBusy || terminating}
                  title="Sign out this device"
                  className="shrink-0 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 disabled:opacity-40 disabled:cursor-not-allowed transition min-h-tap min-w-tap"
                >
                  <X className="size-4" />
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {otherCount > 0 && (
        <div className="mt-4 pt-4 border-t border-border">
          <button
            onClick={terminateOthers}
            disabled={terminating || busyId !== null}
            className="inline-flex items-center gap-2 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-40 disabled:cursor-not-allowed px-3 py-2 rounded transition min-h-tap"
          >
            <LogOut className="size-3.5" />
            {terminating ? 'Signing out…' : `Sign out other sessions (${otherCount})`}
          </button>
        </div>
      )}

      <p className="text-2xs text-muted-foreground mt-3">
        Activity updates roughly once a minute. Revoked sessions are
        blocked within a few seconds — the other device sees a sign-in
        prompt on its next request.
      </p>
    </Card>
  );
}
