/**
 * Per-user preferences page — split out from /admin/settings so the
 * "account-wide things admins configure" and "settings about *me*"
 * live in two distinct places.
 *
 * Anything that affects only the logged-in user belongs here:
 *   - Display name
 *   - UI language
 *   - Personal timezone override (falls back to the account default)
 *   - Quiet hours (DND window for Telegram alerts)
 *
 * Visible to every authenticated user regardless of role; no admin
 * permission required.  The admin-only counterpart (account timezone,
 * Telegram bot config, working hours, etc.) stays on /admin/settings.
 */

import { useEffect, useRef, useState } from 'react';
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
import type { User } from '../types';
import { LANGUAGE_OPTIONS } from '../utils/languages';
import { TIMEZONE_OPTIONS, timezoneLabelWithTime } from '../utils/timezones';
import { useNow } from '../hooks/useNow';

const HOURS = Array.from({ length: 24 }, (_, i) => i);


export default function Profile() {
  const { t } = useTranslation();
  const now = useNow();

  const [lang, setLang] = useState('en');
  const [tz, setTz] = useState('');
  const [accountTz, setAccountTz] = useState('America/New_York');
  const [quietStart, setQuietStart] = useState<number | ''>('');
  const [quietEnd, setQuietEnd] = useState<number | ''>('');
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

  useEffect(() => {
    apiJSON<User & {
      dnd_source?: 'user_override' | 'work_hours' | 'none';
      work_hours_for_role?: Array<{ id: number; label: string; start_hour: number; end_hour: number; target_role: string }>;
    }>('/user/me')
      .then((u) => {
        setLang(u.language || 'en');
        setQuietStart(u.quiet_start ?? '');
        setQuietEnd(u.quiet_end ?? '');
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
    // Always send quiet hours (including null) so the user can CLEAR
    // an existing override and fall back to the Working Hours-derived
    // DND.  Sending only when non-empty would silently keep the old
    // override forever.
    body.quiet_start = quietStart === '' ? null : quietStart;
    body.quiet_end   = quietEnd   === '' ? null : quietEnd;
    try {
      await apiJSON('/user/preferences', { method: 'PUT', body });
      setSuccess('Preferences saved.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserCog}
        title="My Profile"
        description="Personal preferences — only affects what you see and when you get alerts."
      />

      {error && <ErrorState message={error} />}

      <section className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-1">Personal preferences</h2>
        <p className="text-xs text-muted-foreground mb-4">
          These settings apply to your own dashboard, Telegram alerts, and
          report deliveries. They don't affect anyone else on the account.
        </p>
        {success && (
          <p className="text-green-600 dark:text-green-400 text-sm mb-3">{success}</p>
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
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
            >
              {LANGUAGE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Timezone override */}
          <div className="md:col-span-2">
            <label className="block text-xs text-muted-foreground mb-1">
              Timezone override
              <span className="ml-1 text-[10px] opacity-70">
                (leave on "Use account default" to follow the company timezone)
              </span>
            </label>
            <select
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
            >
              <option value="">
                Use account default ({timezoneLabelWithTime(accountTz, now)})
              </option>
              {TIMEZONE_OPTIONS
                // Hide the option that matches the account default so the
                // dropdown reads as a single canonical row per zone (no
                // visual duplicate of "Eastern Time" when the account
                // default is already Eastern).
                .filter((o) => o.value !== accountTz)
                .map((o) => (
                  <option key={o.value} value={o.value}>
                    {timezoneLabelWithTime(o.value, now)}
                  </option>
                ))}
            </select>
            <p className="text-[10px] text-muted-foreground mt-1 opacity-75">
              Pick a specific timezone if you work in a different one from the
              company default.
            </p>
          </div>

          {/* Quiet hours / DND with derivation banner */}
          <div className="md:col-span-2">
            <label className="block text-xs text-muted-foreground mb-1">
              Quiet hours (Do Not Disturb)
            </label>
            <div className={`rounded border p-3 mb-2 text-xs ${
              dndSource === 'user_override'
                ? 'border-primary/40 bg-primary/5'
                : dndSource === 'work_hours'
                ? 'border-green-500/40 bg-green-500/5 text-green-700 dark:text-green-400'
                : 'border-border bg-muted/30 text-muted-foreground'
            }`}>
              {dndSource === 'user_override' && (
                <>
                  <p className="font-medium">🌙 Personal override active</p>
                  <p className="mt-0.5">
                    Your alerts are silenced between the hours you've set below. Clear both fields to fall back to your team's Working Hours.
                  </p>
                </>
              )}
              {dndSource === 'work_hours' && (
                <>
                  <p className="font-medium">⏰ Auto from your team's Working Hours</p>
                  <p className="mt-0.5">
                    Alerts deliver during these shifts (set by your admin):
                  </p>
                  <ul className="mt-1 list-disc list-inside">
                    {workHoursForRole.map((wh) => (
                      <li key={wh.id}>
                        <span className="font-medium">{wh.label}</span> · {String(wh.start_hour).padStart(2, '0')}:00 – {String(wh.end_hour).padStart(2, '0')}:00
                        {wh.target_role !== 'all' && (
                          <span className="text-muted-foreground"> ({wh.target_role})</span>
                        )}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-[11px]">
                    Set custom hours below to override these defaults for yourself only.
                  </p>
                </>
              )}
              {dndSource === 'none' && (
                <>
                  <p className="font-medium">🔔 DND is off — alerts deliver 24/7</p>
                  <p className="mt-0.5">
                    Your team has no Working Hours configured and you have no personal override.
                    Set hours below to silence alerts during specific times,
                    or ask your admin to define Working Hours for your role.
                  </p>
                </>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  Quiet hours start{dndSource === 'work_hours' && ' (override)'}
                </label>
                <select
                  value={quietStart}
                  onChange={(e) =>
                    setQuietStart(e.target.value === '' ? '' : +e.target.value)
                  }
                  className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
                >
                  <option value="">Use Working Hours</option>
                  {HOURS.map((h) => (
                    <option key={h} value={h}>
                      {String(h).padStart(2, '0')}:00
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  Quiet hours end{dndSource === 'work_hours' && ' (override)'}
                </label>
                <select
                  value={quietEnd}
                  onChange={(e) =>
                    setQuietEnd(e.target.value === '' ? '' : +e.target.value)
                  }
                  className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
                >
                  <option value="">Use Working Hours</option>
                  {HOURS.map((h) => (
                    <option key={h} value={h}>
                      {String(h).padStart(2, '0')}:00
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="mt-5 px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition"
        >
          {saving ? 'Saving…' : 'Save preferences'}
        </button>
      </section>

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
    <section className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={18} className="text-muted-foreground" />
        <h2 className="text-lg font-semibold">{t('profile.signin_title', 'Sign-in methods')}</h2>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        {t(
          'profile.signin_desc',
          'These are the ways you can sign in to your account. Keeping both connected means you can recover one through the other.',
        )}
      </p>

      {err && <ErrorState message={err} />}

      <ul className="divide-y divide-border">
        {/* Email */}
        <li className="flex items-start gap-3 py-3">
          <div className="shrink-0 w-9 h-9 rounded-full flex items-center justify-center bg-primary/15 text-primary">
            <Mail size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{t('profile.signin_email_label', 'Email + password')}</div>
            <div className="text-xs text-muted-foreground mt-0.5 truncate">
              {me?.email
                ? me.email
                : t('profile.signin_email_missing', 'No email set — ask an admin to add one.')}
            </div>
          </div>
          {me?.email && (
            <a
              href="/forgot-password"
              className="shrink-0 text-xs text-primary hover:underline px-2 py-1"
            >
              {t('profile.signin_change_password', 'Change password')}
            </a>
          )}
        </li>

        {/* Telegram */}
        <li className="flex items-start gap-3 py-3">
          <div className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${me?.telegram_id ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>
            <Send size={18} />
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
                <span className="text-destructive">
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
              className="shrink-0 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 disabled:opacity-40 px-2 py-1 rounded"
            >
              <Unlink size={12} />
              {unlinking ? t('profile.signin_unlinking', 'Unlinking…') : t('profile.signin_unlink', 'Disconnect')}
            </button>
          ) : (
            <button
              onClick={startLink}
              disabled={linking || linkStatus === 'pending'}
              className="shrink-0 inline-flex items-center gap-1 text-xs text-primary hover:bg-primary/10 disabled:opacity-40 px-2 py-1 rounded"
            >
              <LinkIcon size={12} />
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
        <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-2">
          {t(
            'profile.signin_warn_no_methods',
            'No sign-in method is attached to this account — contact an admin to fix this before your session expires.',
          )}
        </p>
      )}
    </section>
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
    return iso.slice(0, 10);
  };

  return (
    <section className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-1">
        <History size={18} className="text-muted-foreground" />
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
              <div className={`shrink-0 ${ok ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
                {ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
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
                  {r.user_agent && <span className="truncate max-w-[260px]">· {r.user_agent}</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
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
    <section className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-1">
        <Download size={18} className="text-muted-foreground" />
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
        className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded bg-primary hover:bg-primary/90 disabled:opacity-50 transition"
      >
        <ExternalLink size={14} />
        {downloading
          ? t('profile.export_preparing', 'Preparing…')
          : t('profile.export_button', 'Download JSON')}
      </button>
    </section>
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

function formatLastSeen(iso: string): string {
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
  return iso.slice(0, 10);
}

function ActiveSessions() {
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
    <section className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={18} className="text-muted-foreground" />
        <h2 className="text-lg font-semibold">Active sessions</h2>
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
              <div className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${isCurrent ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>
                <Icon size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{s.device_label || 'Unknown device'}</span>
                  {isCurrent && (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/15 text-primary font-semibold">
                      This device
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5">
                  {s.ip && <span className="font-mono">{s.ip}</span>}
                  <span>signed in {formatLastSeen(s.created_at)}</span>
                  {!isCurrent && <span>· active {formatLastSeen(s.last_seen)}</span>}
                </div>
              </div>
              {!isCurrent && (
                <button
                  onClick={() => revoke(s)}
                  disabled={isBusy || terminating}
                  title="Sign out this device"
                  className="shrink-0 p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 disabled:opacity-40 disabled:cursor-not-allowed transition"
                >
                  <X size={16} />
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
            className="inline-flex items-center gap-2 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-40 disabled:cursor-not-allowed px-3 py-2 rounded transition"
          >
            <LogOut size={14} />
            {terminating ? 'Signing out…' : `Sign out other sessions (${otherCount})`}
          </button>
        </div>
      )}

      <p className="text-[11px] text-muted-foreground mt-3">
        Activity updates roughly once a minute. Revoked sessions are
        blocked within a few seconds — the other device sees a sign-in
        prompt on its next request.
      </p>
    </section>
  );
}
