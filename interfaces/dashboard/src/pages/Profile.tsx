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

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { UserCog } from 'lucide-react';

import { apiJSON } from '../api/client';
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
    </div>
  );
}
