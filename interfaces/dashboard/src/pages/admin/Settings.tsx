import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon } from 'lucide-react';
import { apiJSON } from '../../api/client';
import type { SettingsResponse, WorkSchedule, User, BotConfig } from '../../types';
import { useAuth } from '../../context/AuthContext';
import { useShellConfig } from '../../hooks/useShellConfig';
import {
  PageHeader,
  ErrorState,
  CardSkeleton,
} from '../../components/shell';
import { TIMEZONE_OPTIONS, timezoneLabelWithTime } from '../../utils/timezones';
import { useNow } from '../../hooks/useNow';
import ForumRoutingSection from './ForumRoutingSection';

const ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'driver'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function Settings() {
  const { t } = useTranslation();
  const { user: authUser } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // Re-renders once a minute so the "current time" suffix on each
  // timezone option stays accurate while the page is open.
  const now = useNow();

  // Account-wide timezone (admin / owner only).
  // ``accountTz`` is what gets stored on accounts.timezone — every cron
  // job + display formatter falls back to this when a user doesn't set
  // a per-user override.  Editable only by users with can_manage_account.
  const [accountTz, setAccountTz] = useState('America/New_York');
  const [accountTzSaving, setAccountTzSaving] = useState(false);
  const [accountTzSuccess, setAccountTzSuccess] = useState('');
  const canManageAccount = !!authUser?.permissions?.can_manage_account;

  // Track editable settings
  const [edits, setEdits] = useState<Record<string, string>>({});

  // Bot configuration (owner only)
  const [botToken, setBotToken] = useState('');
  const [botSaving, setBotSaving] = useState(false);
  const [botSuccess, setBotSuccess] = useState('');
  const [botError, setBotError] = useState('');
  const [showBotDisconnect, setShowBotDisconnect] = useState(false);

  // Schedule form
  const [showSchedule, setShowSchedule] = useState(false);
  const [sLabel, setSLabel] = useState('');
  const [sStart, setSStart] = useState(8);
  const [sEnd, setSEnd] = useState(17);
  const [sRole, setSRole] = useState('driver');

  const { data, isLoading: loading, error: queryError } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: () => apiJSON<SettingsResponse>('/admin/settings'),
  });
  const fetchError = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['admin-settings'] });

  // Sync edits to fetched settings whenever they arrive.
  useEffect(() => { if (data) setEdits(data.settings || {}); }, [data]);

  // Bot config — only fetched for owners. Mutations write into the cache
  // directly so we don't need a separate `setBotConfig` setter.
  const { isOwner } = useShellConfig();
  const { data: botConfig } = useQuery({
    queryKey: ['admin-bot-config'],
    queryFn: () => apiJSON<BotConfig>('/admin/bot-config'),
    enabled: isOwner,
  });
  const setBotConfig = (next: BotConfig | null) => qc.setQueryData(['admin-bot-config'], next);

  const handleConnectBot = async () => {
    setBotSaving(true); setBotError(''); setBotSuccess('');
    try {
      const res = await apiJSON<{ ok: boolean; bot_username: string; bot_id?: number; first_name?: string }>('/admin/bot-config', {
        method: 'PUT', body: { bot_token: botToken },
      });
      setBotConfig({ has_bot: true, bot_username: res.bot_username, bot_id: res.bot_id, first_name: res.first_name, is_running: true });
      setBotToken('');
      setBotSuccess(`Connected @${res.bot_username}`);
    } catch (e) { setBotError(e instanceof Error ? e.message : 'Failed to validate token'); }
    finally { setBotSaving(false); }
  };

  const handleDisconnectBot = async () => {
    setBotSaving(true); setBotError(''); setBotSuccess('');
    try {
      await apiJSON('/admin/bot-config', { method: 'DELETE' });
      setBotConfig({ has_bot: false, bot_username: '' });
      setShowBotDisconnect(false);
      setBotSuccess('Bot disconnected');
    } catch (e) { setBotError(e instanceof Error ? e.message : 'Failed to disconnect'); }
    finally { setBotSaving(false); }
  };

  // Load user preferences from /user/me.  ``timezone`` is the user's
  // override (may be blank, meaning "inherit account default");
  // ``effective_timezone`` is what the rest of the app actually
  // renders in — read-only here, shown as a hint.
  useEffect(() => {
    // Settings only needs the account default for non-admins (admins
    // overwrite this from /admin/timezone below).
    apiJSON<User>('/user/me').then((u) => {
      if (u.account_timezone) setAccountTz(u.account_timezone);
    }).catch(() => {});
  }, []);

  // Load account-level timezone (only when the user can edit it; for
  // everyone else we already have the value baked into /user/me's
  // ``account_timezone`` field, but the dedicated endpoint is what
  // powers the save flow).
  useEffect(() => {
    if (!canManageAccount) return;
    apiJSON<{ timezone: string }>('/admin/timezone')
      .then((r) => setAccountTz(r.timezone || 'America/New_York'))
      .catch(() => {});
  }, [canManageAccount]);

  const handleSaveAccountTz = async () => {
    setAccountTzSaving(true); setError(''); setAccountTzSuccess('');
    try {
      await apiJSON('/admin/timezone', { method: 'PUT', body: { timezone: accountTz } });
      setAccountTzSuccess('Account timezone saved.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save timezone');
    } finally {
      setAccountTzSaving(false);
    }
  };


  const handleSaveSetting = async (key: string) => {
    setSaving(true); setError('');
    try {
      await apiJSON('/admin/settings', { method: 'PUT', body: { key, value: edits[key] } });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleAddSchedule = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true); setError('');
    try {
      await apiJSON('/admin/work-hours', { method: 'POST', body: { label: sLabel, start_hour: sStart, end_hour: sEnd, target_role: sRole } });
      setShowSchedule(false); setSLabel('');
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleDeleteSchedule = async (id: number) => {
    try {
      await apiJSON('/admin/work-hours/' + id, { method: 'DELETE' });
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  if (loading) {
    return (
      <div>
        <PageHeader
          icon={SettingsIcon}
          title={t('pages.settings_title')}
          description={t('pages.settings_desc_short')}
        />
        <div className="space-y-4">
          <CardSkeleton height="h-40" />
          <CardSkeleton height="h-40" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        icon={SettingsIcon}
        title={t('pages.settings_title')}
        description={t('pages.settings_desc_long')}
      />
      {(error || fetchError) && <ErrorState message={error || fetchError} />}

      {/* Account Timezone — admin-only.  Single source of truth that
          drives cron-job timing and every display formatter for users
          who haven't set a personal override.  Per-user override lives
          in "My Preferences" below. */}
      {canManageAccount && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-1">Account Timezone</h2>
          <p className="text-xs text-muted-foreground mb-3">
            Sets the company default. Cron jobs (driver-doc expiry, scorecards,
            coaching, …) fire when it's the right local time here. Users can
            override their own timezone in My Preferences below.
            <br />
            <span className="opacity-75">
              Tip: DST is handled automatically — picking Eastern Time (ET) gives
              you EST in winter and EDT in summer.
            </span>
          </p>
          {accountTzSuccess && (
            <p className="text-green-600 dark:text-green-400 text-sm mb-3">
              {accountTzSuccess}
            </p>
          )}
          <div className="flex items-end gap-3">
            <div className="flex-1 max-w-sm">
              <label className="block text-xs text-muted-foreground mb-1">Timezone</label>
              <select
                value={accountTz}
                onChange={(e) => setAccountTz(e.target.value)}
                className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring"
              >
                {TIMEZONE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {timezoneLabelWithTime(o.value, now)}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={handleSaveAccountTz}
              disabled={accountTzSaving}
              className="bg-primary text-primary-foreground px-3 py-2 rounded text-sm font-medium disabled:opacity-50"
            >
              {accountTzSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </section>
      )}

      {/* Personal preferences have their own page now — keep a pointer
          here so admins coming to Settings can still find them. */}
      <section className="bg-card border border-border rounded-xl p-4 flex items-center justify-between gap-4">
        <div className="text-sm">
          <p className="font-medium">Looking for your personal preferences?</p>
          <p className="text-xs text-muted-foreground">
            Display name, language, your own timezone override, and quiet
            hours live on your Profile page — only affecting what you see.
          </p>
        </div>
        <a
          href="/profile"
          className="px-3 py-2 bg-muted hover:bg-muted/80 border border-border rounded text-sm font-medium transition shrink-0"
        >
          Open My Profile →
        </a>
      </section>

      {/* Account Info */}
      {data?.account && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">Account</h2>
          <dl className="grid grid-cols-3 gap-4 text-sm">
            <div><dt className="text-muted-foreground">Name</dt><dd>{data.account.name || '—'}</dd></div>
            <div><dt className="text-muted-foreground">Tier</dt><dd className="capitalize">{data.account.tier || 'basic'}</dd></div>
            <div><dt className="text-muted-foreground">Status</dt><dd>{data.account.is_active ? <span className="text-green-600 dark:text-green-400">Active</span> : <span className="text-destructive">Inactive</span>}</dd></div>
          </dl>
        </section>
      )}

      {/* Telegram Bot (owner only) */}
      {isOwner && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">Telegram Bot</h2>
          {botSuccess && <p className="text-green-600 dark:text-green-400 text-sm mb-3">{botSuccess}</p>}
          {botError && <p className="text-destructive text-sm mb-3">{botError}</p>}

          {botConfig?.has_bot ? (
            <div>
              <div className="flex items-center gap-3 mb-4">
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
                  botConfig.is_running !== false
                    ? 'bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30'
                    : 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${botConfig.is_running !== false ? 'bg-green-400 animate-pulse' : 'bg-yellow-400'}`} />
                  {botConfig.is_running !== false ? 'Running' : 'Configured'}
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
                {botConfig.first_name && (
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">Bot Name</dt>
                    <dd className="text-sm font-medium">{botConfig.first_name}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-muted-foreground mb-0.5">Username</dt>
                  <dd className="text-sm">
                    <a href={`https://t.me/${botConfig.bot_username}`} target="_blank" rel="noopener noreferrer"
                      className="text-primary hover:underline">@{botConfig.bot_username}</a>
                  </dd>
                </div>
                {botConfig.bot_id && (
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">Bot ID</dt>
                    <dd className="text-sm font-mono text-foreground/80">{botConfig.bot_id}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-muted-foreground mb-0.5">Status</dt>
                  <dd className="text-sm">
                    {botConfig.is_running !== false
                      ? <span className="text-green-600 dark:text-green-400">Active &amp; receiving messages</span>
                      : <span className="text-yellow-600 dark:text-yellow-400">Token saved, bot not running</span>}
                  </dd>
                </div>
              </div>

              <div className="flex items-center gap-3 flex-wrap">
                <a href={`https://t.me/${botConfig.bot_username}`} target="_blank" rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition">
                  Open in Telegram
                </a>
                {showBotDisconnect ? (
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-muted-foreground">Disconnect this bot?</span>
                    <button onClick={handleDisconnectBot} disabled={botSaving}
                      className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition">
                      {botSaving ? 'Disconnecting...' : 'Yes, Disconnect'}
                    </button>
                    <button onClick={() => setShowBotDisconnect(false)}
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded text-xs font-medium transition">
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button onClick={() => setShowBotDisconnect(true)}
                    className="px-3 py-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive rounded text-xs font-medium transition">
                    Disconnect Bot
                  </button>
                )}
              </div>

              {/* Inline alert-routing section — only visible once the
                  bot is connected, since routing requires a working
                  bot account to talk to Telegram. */}
              <ForumRoutingSection />
            </div>
          ) : (
            <div>
              <p className="text-sm text-muted-foreground mb-3">
                Connect your own Telegram bot. Create one via <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">@BotFather</a>, then paste the token below.
              </p>
              <div className="flex items-end gap-3">
                <div className="flex-1">
                  <label className="block text-xs text-muted-foreground mb-1">Bot Token</label>
                  <input type="password" value={botToken} onChange={e => setBotToken(e.target.value)}
                    placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                    className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring font-mono" />
                </div>
                <button onClick={handleConnectBot} disabled={botSaving || botToken.length < 30}
                  className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition whitespace-nowrap">
                  {botSaving ? 'Validating...' : 'Validate & Connect'}
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Editable Settings */}
      <section className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Configuration</h2>
        {Object.keys(edits).length === 0 ? (
          <p className="text-muted-foreground text-sm">No settings configured yet.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(edits).map(([key, val]) => (
              <div key={key} className="flex items-center gap-3">
                <span className="text-sm text-muted-foreground w-48 flex-shrink-0 truncate">{key}</span>
                <input value={val} onChange={e => setEdits(prev => ({ ...prev, [key]: e.target.value }))}
                  className="flex-1 bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
                <button onClick={() => handleSaveSetting(key)} disabled={saving} className="px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-xs font-medium transition">Save</button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* AI Usage */}
      {data?.ai_usage && Object.keys(data.ai_usage).length > 0 && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">AI Usage <span className="text-xs text-muted-foreground font-normal">(last {(data.ai_usage as any).days ?? 30} days)</span></h2>
          {/* Top-level stats */}
          <dl className="grid grid-cols-3 gap-4 text-sm mb-4">
            {Object.entries(data.ai_usage)
              .filter(([, v]) => typeof v !== 'object' || v === null)
              .map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</dt>
                <dd>{v == null ? '—' : typeof v === 'number' ? v.toLocaleString() : String(v)}</dd>
              </div>
            ))}
          </dl>
          {/* By Type breakdown */}
          {(data.ai_usage as any).by_type && Object.keys((data.ai_usage as any).by_type).length > 0 && (
            <div className="mb-4">
              <h3 className="text-sm font-medium text-muted-foreground mb-2">By Type</h3>
              <div className="grid grid-cols-3 gap-3 text-sm">
                {Object.entries((data.ai_usage as any).by_type as Record<string, {requests: number; tokens: number}>).map(([typ, stats]) => (
                  <div key={typ} className="bg-muted rounded-lg p-3">
                    <div className="text-foreground/80 capitalize font-medium">{typ}</div>
                    <div className="text-muted-foreground text-xs mt-1">{(stats as any).requests?.toLocaleString() ?? 0} requests &middot; {(stats as any).tokens?.toLocaleString() ?? 0} tokens</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* By Model breakdown */}
          {(data.ai_usage as any).by_model && Object.keys((data.ai_usage as any).by_model).length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-muted-foreground mb-2">By Model</h3>
              <div className="space-y-2 text-sm">
                {Object.entries((data.ai_usage as any).by_model as Record<string, any>).map(([model, stats]) => (
                  <div key={model} className="bg-muted rounded-lg p-3 flex items-center justify-between">
                    <span className="text-foreground/80 font-mono text-xs">{model}</span>
                    <span className="text-muted-foreground text-xs">{(stats as any).requests?.toLocaleString()} req &middot; {(stats as any).tokens?.toLocaleString()} tok</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* Working Hours */}
      <section className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Working Hours</h2>
          <button onClick={() => setShowSchedule(!showSchedule)} className="px-3 py-1.5 bg-primary hover:bg-primary/90 rounded text-xs font-medium transition">
            {showSchedule ? 'Cancel' : '+ Add Schedule'}
          </button>
        </div>

        {showSchedule && (
          <form onSubmit={handleAddSchedule} className="grid grid-cols-5 gap-3 mb-4">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Label</label>
              <input required value={sLabel} onChange={e => setSLabel(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Start Hour</label>
              <select value={sStart} onChange={e => setSStart(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">End Hour</label>
              <select value={sEnd} onChange={e => setSEnd(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Role</label>
              <select value={sRole} onChange={e => setSRole(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                {ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <button type="submit" disabled={saving} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium text-foreground transition">
                {saving ? 'Saving...' : 'Add'}
              </button>
            </div>
          </form>
        )}

        {data?.schedules && data.schedules.length > 0 ? (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-muted-foreground border-b border-border">
              <th className="py-2 pr-4">Label</th><th className="py-2 pr-4">Hours</th><th className="py-2 pr-4">Role</th><th className="py-2">Actions</th>
            </tr></thead>
            <tbody>
              {data.schedules.map((s: WorkSchedule) => (
                <tr key={s.id} className="border-b border-border/50 hover:bg-muted/30">
                  <td className="py-2 pr-4">{s.label}</td>
                  <td className="py-2 pr-4">{String(s.start_hour).padStart(2, '0')}:00 – {String(s.end_hour).padStart(2, '0')}:00</td>
                  <td className="py-2 pr-4 capitalize">{s.target_role.replace(/_/g, ' ')}</td>
                  <td className="py-2">
                    <button onClick={() => handleDeleteSchedule(s.id)} className="text-destructive hover:text-destructive/80 text-xs">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-muted-foreground text-sm">No schedules configured.</p>
        )}
      </section>
    </div>
  );
}
