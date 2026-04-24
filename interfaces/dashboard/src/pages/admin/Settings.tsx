import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import type { SettingsResponse, WorkSchedule, User, BotConfig } from '../../types';
import { useAuth } from '../../context/AuthContext';

const ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'driver'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

const LANGUAGES: Record<string, string> = {
  en: '🇺🇸 English',
  es: '🇪🇸 Español',
  ru: '🇷🇺 Русский',
  uk: '🇺🇦 Українська',
  fr: '🇫🇷 Français',
  so: '🇸🇴 Soomaali',
  am: '🇪🇹 አማርኛ',
  uz: '🇺🇿 O\'zbekcha',
  pa: '🇮🇳 ਪੰਜਾਬੀ',
};

const TIMEZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Los_Angeles', 'America/Phoenix', 'America/Anchorage',
  'Pacific/Honolulu', 'UTC',
];

export default function Settings() {
  const { user: authUser } = useAuth();
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // User prefs
  const [prefLang, setPrefLang] = useState('en');
  const [prefTz, setPrefTz] = useState('America/New_York');
  const [prefQuietStart, setPrefQuietStart] = useState<number | ''>('');
  const [prefQuietEnd, setPrefQuietEnd] = useState<number | ''>('');
  const [prefName, setPrefName] = useState('');
  const [prefSaving, setPrefSaving] = useState(false);
  const [prefSuccess, setPrefSuccess] = useState('');

  // Track editable settings
  const [edits, setEdits] = useState<Record<string, string>>({});

  // Bot configuration (owner only)
  const [botConfig, setBotConfig] = useState<BotConfig | null>(null);
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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiJSON<SettingsResponse>('/admin/settings');
      setData(d);
      setEdits(d.settings || {});
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Load bot config (owner only)
  useEffect(() => {
    if (authUser?.role !== 'owner') return;
    apiJSON<BotConfig>('/admin/bot-config').then(setBotConfig).catch(() => {});
  }, [authUser?.role]);

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

  // Load user preferences from /user/me
  useEffect(() => {
    apiJSON<User>('/user/me').then((u) => {
      setPrefLang(u.language || 'en');
      setPrefTz(u.timezone || 'America/New_York');
      setPrefQuietStart(u.quiet_start ?? '');
      setPrefQuietEnd(u.quiet_end ?? '');
      setPrefName(u.display_name || '');
    }).catch(() => {});
  }, []);

  const handleSavePrefs = async () => {
    setPrefSaving(true); setError(''); setPrefSuccess('');
    const body: Record<string, unknown> = {};
    if (prefLang) body.language = prefLang;
    if (prefTz) body.timezone = prefTz;
    if (prefName) body.display_name = prefName;
    if (prefQuietStart !== '') body.quiet_start = prefQuietStart;
    if (prefQuietEnd !== '') body.quiet_end = prefQuietEnd;
    try {
      await apiJSON('/user/preferences', { method: 'PUT', body });
      setPrefSuccess('Preferences saved!');
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setPrefSaving(false); }
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

  if (loading) return <p className="text-muted-foreground">Loading...</p>;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Settings</h1>
      {error && <p className="text-destructive text-sm">{error}</p>}

      {/* My Preferences */}
      <section className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">My Preferences</h2>
        {prefSuccess && <p className="text-green-600 dark:text-green-400 text-sm mb-3">{prefSuccess}</p>}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
            <input value={prefName} onChange={e => setPrefName(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Language</label>
            <select value={prefLang} onChange={e => setPrefLang(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring">
              {Object.entries(LANGUAGES).map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Timezone</label>
            <select value={prefTz} onChange={e => setPrefTz(e.target.value)}
              className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring">
              {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-xs text-muted-foreground mb-1">Quiet Start (hour)</label>
              <select value={prefQuietStart} onChange={e => setPrefQuietStart(e.target.value === '' ? '' : +e.target.value)}
                className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring">
                <option value="">Off</option>
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="block text-xs text-muted-foreground mb-1">Quiet End (hour)</label>
              <select value={prefQuietEnd} onChange={e => setPrefQuietEnd(e.target.value === '' ? '' : +e.target.value)}
                className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring">
                <option value="">Off</option>
                {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
              </select>
            </div>
          </div>
        </div>
        <button onClick={handleSavePrefs} disabled={prefSaving}
          className="mt-4 px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition">
          {prefSaving ? 'Saving...' : 'Save Preferences'}
        </button>
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
      {authUser?.role === 'owner' && (
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
