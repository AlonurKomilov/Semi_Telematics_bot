import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon, ArrowRight, Link2, Clock } from 'lucide-react';
import { apiJSON } from '../../api/client';
import type { SettingsResponse, WorkSchedule, User, BotConfig, AnyColumn } from '../../types';
import DataGrid from '../../components/datagrid';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import {
  PageHeader,
  ErrorState,
  CardSkeleton,
} from '../../components/shell';
import { TIMEZONE_OPTIONS, timezoneLabelWithTime } from '../../utils/timezones';
import { useNow } from '../../hooks/useNow';
import { rollupByDisplayLabel } from '../../features/ai/helpers';
import { Link } from 'react-router-dom';
import DeliveryModeSelector from './delivery/DeliveryModeSelector';
import { FeatureConfigGear } from '../_lib/FeatureConfigGear';
import SettingsConfigPanel from './config/SettingsConfigPanel';
import SubBotRoster from './delivery/SubBotRoster';
import BotHealthSection from './BotHealthSection';
import DangerZoneSection from './DangerZoneSection';
import { toneClasses } from '../../lib/status';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { Input } from '../../components/ui/input';
import { ConfigMovedNotice } from '../_lib/ConfigMovedNotice';

const ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'hr', 'accounting', 'recruiter', 'driver'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const ROLE_ITEMS = ROLES.map((r) => ({ value: r, label: r.replace(/_/g, ' ') }));
const HOUR_ITEMS = HOURS.map((h) => ({ value: String(h), label: `${String(h).padStart(2, '0')}:00` }));

export default function Settings() {
  const { t } = useTranslation();
  const { has: viewHas } = useViewPermissions();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // Re-renders once a minute so the "current time" suffix on each
  // timezone option stays accurate while the page is open.
  const now = useNow();
  const tzItems = TIMEZONE_OPTIONS.map((o) => ({ value: o.value, label: timezoneLabelWithTime(o.value, now) }));

  // Account-wide timezone (admin / owner only).
  // ``accountTz`` is what gets stored on accounts.timezone — every cron
  // job + display formatter falls back to this when a user doesn't set
  // a per-user override.  Editable only by users with can_manage_account.
  const [accountTz, setAccountTz] = useState('America/New_York');
  const [accountTzSaving, setAccountTzSaving] = useState(false);
  const [accountTzSuccess, setAccountTzSuccess] = useState('');
  // The name outsiders see instead of the registered company name.
  // Blank is a real setting (show nobody), so it can't be conflated
  // with "not loaded" — hence the separate loaded flag.
  const [publicName, setPublicName] = useState('');
  const [registeredName, setRegisteredName] = useState('');
  const [publicNameLoaded, setPublicNameLoaded] = useState(false);
  const [publicNameSaving, setPublicNameSaving] = useState(false);
  const [publicNameSuccess, setPublicNameSuccess] = useState('');
  // VIEW-AWARE permissions (useViewPermissions, not the raw logged-in
  // user): the page must render the PREVIEWED persona's Settings under
  // View-As — an owner previewing "Fleet · Manager" sees the manager's
  // scoped page, not their own.  Backend enforcement is independent
  // (every owner section's endpoint re-checks can_manage_account /
  // primary-owner server-side).
  const canManageAccount = viewHas('can_manage_account');
  // CONFIG is a third action, not a stronger Manage.  The account_settings
  // VALUES on this page (timezone, language, digest hour, alert defaults,
  // scorecard subject) are owned by the config family — see
  // capabilities/settings_registry.py — while the page's own operations
  // (bot config, forum routing, modules, danger zone) stay on Manage.
  // GET /admin/settings mirrors this: it admits Manage holders but returns
  // an EMPTY `settings` dict unless the caller also holds config.
  const canConfigAccount = viewHas('can_manage_config_all');
  // Role MANAGERS reach Settings for the Telegram Bot card ONLY (their
  // own role's Sub bot).  The page renders just that card for them — no
  // /admin/settings fetch, so they never hit its can_manage_account 403.
  const canManageRoleBot = viewHas('can_manage_role_bot');
  const canInvite = viewHas('can_invite');
  const canManageWorkHours = viewHas('can_manage_work_hours');

  // Vendor-directory contribution consent (UX audit 2026-07-16): the
  // auto-pipeline default (ON) becomes inspectable + owner-editable.
  const [dirSharing, setDirSharing] = useState<boolean | null>(null);
  const [dirSharingSaving, setDirSharingSaving] = useState(false);
  useEffect(() => {
    if (!canManageAccount) return;
    apiJSON<{ enabled: boolean }>('/vendors/identity-sharing')
      .then(r => setDirSharing(r.enabled))
      .catch(() => setDirSharing(null));
  }, [canManageAccount]);
  const toggleDirSharing = async (enabled: boolean) => {
    setDirSharingSaving(true);
    try {
      await apiJSON('/vendors/identity-sharing', { method: 'PUT', body: { enabled } });
      setDirSharing(enabled);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update directory sharing');
    } finally {
      setDirSharingSaving(false);
    }
  };

  // Market-data sharing (give-to-get) — the DURABLE home of the
  // consent, with OFF as reachable as ON (the vendor-profile card only
  // offers quick-enable).  Hidden entirely while the platform switch
  // is dark (available=false).
  const [marketSharing, setMarketSharing] = useState<{ available: boolean; enabled: boolean } | null>(null);
  const [marketSaving, setMarketSaving] = useState(false);
  useEffect(() => {
    if (!canManageAccount) return;
    apiJSON<{ available: boolean; enabled: boolean }>('/vendors/market-sharing')
      .then(setMarketSharing)
      .catch(() => setMarketSharing(null));
  }, [canManageAccount]);
  const toggleMarketSharing = async (enabled: boolean) => {
    setMarketSaving(true);
    try {
      await apiJSON('/vendors/market-sharing', { method: 'PUT', body: { enabled } });
      setMarketSharing(m => (m ? { ...m, enabled } : m));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update market sharing');
    } finally {
      setMarketSaving(false);
    }
  };


  // Track editable settings
  const [edits, setEdits] = useState<Record<string, string>>({});

  // Bot configuration (owner only).  Feedback rides the same toast
  // channel as every other action on the bot card — the old inline
  // botSuccess/botError texts were the card's second feedback system.
  const [botToken, setBotToken] = useState('');
  const [botSaving, setBotSaving] = useState(false);
  const [showBotDisconnect, setShowBotDisconnect] = useState(false);

  // Schedule form
  const [showSchedule, setShowSchedule] = useState(false);
  const [sLabel, setSLabel] = useState('');
  const [sStart, setSStart] = useState(8);
  const [sEnd, setSEnd] = useState(17);
  const [sRole, setSRole] = useState('driver');

  const { data, isLoading: loading, error: queryError } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: () => apiJSON<SettingsResponse>('/settings/config'),
    // Either action needs this payload: Manage for account info + AI
    // usage, Config for the settings dict.  Config-only holders are real
    // — MANAGER_GRANTS gives a Safety manager can_manage_config_all
    // without can_manage_account — and gating the fetch on Manage alone
    // left them staring at "No settings configured yet" forever.
    enabled: canManageAccount || canConfigAccount,
  });
  const fetchError = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['admin-settings'] });

  // Sync edits to fetched settings whenever they arrive.
  useEffect(() => { if (data) setEdits(data.settings || {}); }, [data]);

  // Bot config — fetched for account admins (the bot card's audience).
  // Mutations write into the cache directly so we don't need a separate
  // `setBotConfig` setter.
  const { data: botConfig } = useQuery({
    queryKey: ['admin-bot-config'],
    queryFn: () => apiJSON<BotConfig>('/admin/bot-config'),
    enabled: canManageAccount || canManageRoleBot,
  });
  const setBotConfig = (next: BotConfig | null) => qc.setQueryData(['admin-bot-config'], next);

  const handleConnectBot = async () => {
    setBotSaving(true);
    try {
      const res = await apiJSON<{ ok: boolean; bot_username: string; bot_id?: number; first_name?: string }>('/admin/bot-config', {
        method: 'PUT', body: { bot_token: botToken },
      });
      setBotConfig({ has_bot: true, bot_username: res.bot_username, bot_id: res.bot_id, first_name: res.first_name, is_running: true });
      setBotToken('');
      toast.success(t('bot_card.toast_connected', { username: res.bot_username }));
    } catch (e) { toast.error(e instanceof Error ? e.message : t('bot_card.toast_connect_error')); }
    finally { setBotSaving(false); }
  };

  const handleDisconnectBot = async () => {
    setBotSaving(true);
    try {
      await apiJSON('/admin/bot-config', { method: 'DELETE' });
      setBotConfig({ has_bot: false, bot_username: '' });
      setShowBotDisconnect(false);
      toast.success(t('bot_card.toast_disconnected'));
    } catch (e) { toast.error(e instanceof Error ? e.message : t('bot_card.toast_disconnect_error')); }
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

  useEffect(() => {
    if (!canManageAccount) return;
    apiJSON<{ public_display_name: string; registered_name: string }>(
      '/admin/account/public-identity',
    ).then((r) => {
      setPublicName(r.public_display_name || '');
      setRegisteredName(r.registered_name || '');
      setPublicNameLoaded(true);
    }).catch(() => {});
  }, [canManageAccount]);

  const handleSavePublicName = async () => {
    setPublicNameSaving(true); setError(''); setPublicNameSuccess('');
    try {
      const value = publicName.trim();
      await apiJSON('/admin/account/public-identity', {
        method: 'PUT', body: { public_display_name: value },
      });
      setPublicName(value);
      setPublicNameSuccess(value
        ? `Outside forms now show "${value}".`
        : 'Outside forms now show no company name.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save the public name');
    } finally {
      setPublicNameSaving(false);
    }
  };


  const handleSaveSetting = async (key: string) => {
    setSaving(true); setError('');
    try {
      await apiJSON('/settings/config', { method: 'PUT', body: { key, value: edits[key] } });
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

  if (loading && (canManageAccount || canConfigAccount)) {
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
        /* The account_settings VALUES move to the gear; the page keeps
           its operations (bot config, forum routing, modules, public
           identity, danger zone). A card headed "Configuration" sitting
           among them put a config surface in the same tier as the
           operational ones — on the one page where that distinction is
           hardest to see, because everything here is called a setting.

           The gear says "Account", not the page title: "<Feature>
           configuration" would render "Settings configuration" here,
           which collides with Settings the feature. Every other feature
           keeps the standard label. */
        actions={(
          <FeatureConfigGear feature="Account" size="xl">
            <SettingsConfigPanel
              edits={edits}
              setEdits={setEdits}
              saving={saving}
              onSave={handleSaveSetting}
            />
          </FeatureConfigGear>
        )}
      />
      <ConfigMovedNotice what="Account values" />

      {(canManageAccount || canConfigAccount) && (error || fetchError) && <ErrorState message={error || fetchError} />}

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
              Tip: DST is handled automatically — picking Eastern Time gives
              you EST in winter and EDT in summer.
            </span>
          </p>
          {accountTzSuccess && (
            <p className="text-ok text-sm mb-3">
              {accountTzSuccess}
            </p>
          )}
          <div className="flex items-end gap-3">
            <div className="flex-1 max-w-sm">
              <label className="block text-xs text-muted-foreground mb-1">Timezone</label>
              <Select value={accountTz} onValueChange={(v) => setAccountTz(v ?? '')} items={tzItems}>
                <SelectTrigger className="w-full" aria-label="Timezone"><SelectValue placeholder="Select timezone" /></SelectTrigger>
                <SelectContent>
                  {tzItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
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

      {/* Public identity — what an outside business sees on token-gated
          forms we send them.  Separate from the registered name, which
          stays the internal label everywhere.  Blank is a real choice:
          those forms name no one rather than falling back. */}
      {canManageAccount && publicNameLoaded && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-1">Public Company Name</h2>
          <p className="text-xs text-muted-foreground mb-3 max-w-xl">
            Shown to outside companies on forms you send them — today the
            carrier profile form and its invite email. Your registered name
            {registeredName ? ` (${registeredName})` : ''} is never shown there.
            {' '}Leave this blank and those forms name no company at all, while
            recruiters can still name one per invite link — that combination
            gives the most per-carrier control.
            {' '}Set a name here and every link shows it unless a recruiter
            overrides it, but no single link can then be left anonymous.
            {' '}Changing it also changes what links you already sent display.
          </p>
          {publicNameSuccess && (
            <p className="text-ok text-sm mb-3">{publicNameSuccess}</p>
          )}
          <div className="flex items-end gap-3">
            <div className="flex-1 max-w-sm">
              <label className="block text-xs text-muted-foreground mb-1" htmlFor="public-display-name">
                Public name
              </label>
              <Input
                id="public-display-name"
                value={publicName}
                maxLength={120}
                onChange={(e) => setPublicName(e.target.value)}
                placeholder="Leave blank to show no company name"
              />
            </div>
            <button
              onClick={handleSavePublicName}
              disabled={publicNameSaving}
              className="bg-primary text-primary-foreground px-3 py-2 rounded text-sm font-medium disabled:opacity-50"
            >
              {publicNameSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </section>
      )}

      {/* Vendor directory contribution — consent visibility for the
          automatic pipeline.  Copy mirrors DIRECTORY_DISCLOSURE so the
          promise is identical everywhere it appears. */}
      {canManageAccount && dirSharing !== null && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-1">Vendor Directory</h2>
          <p className="text-xs text-muted-foreground mb-3 max-w-xl">
            When a shop's address completes its record, its name and contact
            info are sent to the shared 4truck directory for review — never
            your invoices or spend. Verified shops become visible to all
            companies (and on the map). Turning this off keeps your shops
            private; browsing and using the public directory still works.
          </p>
          <label className="inline-flex items-center gap-2.5 text-sm text-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={dirSharing}
              disabled={dirSharingSaving}
              onChange={(e) => toggleDirSharing(e.target.checked)}
              className="accent-primary"
            />
            Contribute vendor identities to the public directory
          </label>
        </section>
      )}

      {/* Market-data sharing (give-to-get) — the durable consent home:
          OFF is as reachable as ON here (the vendor-profile card only
          quick-enables).  Hidden while the platform feature is dark. */}
      {canManageAccount && marketSharing?.available && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-1">Market Price Data</h2>
          <p className="text-xs text-muted-foreground mb-3 max-w-xl">
            See what other fleets typically pay — per shop, and per part
            by state — in exchange for contributing your own prices to the
            same anonymous pool. Ranges only ever show aggregates from 3+
            companies; your company name and invoices are never shown to
            anyone. Turning this off stops both contributing and seeing
            ranges.
          </p>
          <label className="inline-flex items-center gap-2.5 text-sm text-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={marketSharing.enabled}
              disabled={marketSaving}
              onChange={(e) => toggleMarketSharing(e.target.checked)}
              className="accent-primary"
            />
            Share anonymized price data and see market ranges
          </label>
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
            <div><dt className="text-muted-foreground">Status</dt><dd>{data.account.is_active ? <span className="text-ok">Active</span> : <span className="text-danger">Inactive</span>}</dd></div>
          </dl>
        </section>
      )}

      {/* Telegram Bot — the account bot CREDENTIAL only.  Gated on
          can_manage_account (owner + full-admin), matching the backend
          /admin/bot-config PUT/DELETE gate.  All alert routing (mode,
          groups, Sub bots, topics) moved to Alerts → Group delivery so
          role managers configure their own group there without needing
          Settings access. */}
      {(canManageAccount || canManageRoleBot) && (
        <section className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-3">{t('bot_card.title')}</h2>
          {/* Manager card has no account-bot identity — say what it IS
              for them (UX audit A/C1). */}
          {!canManageAccount && (
            <p className="-mt-2 mb-3 text-xs text-muted-foreground">{t('bot_card.manager_subtitle')}</p>
          )}

          {botConfig?.has_bot ? (
            <div>
              {/* Delivery mode — the owner's topology choice, on top.
                  Owner-only; managers can't change the account topology. */}
              {canManageAccount && <DeliveryModeSelector canManageAccount={canManageAccount} />}

              {/* Group binding + topics live on Alerts → Group delivery —
                  a real action, not a footnote (UX audit A2/P2). */}
              <Link
                to="/alerts/group-delivery"
                className="inline-flex items-center gap-1 mb-2 px-3 py-1.5 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
              >
                {t('bot_card.configure_routing')}
                <ArrowRight size={14} />
              </Link>
              {/* Binding a group there is the REQUIRED step; the Sub bot
                  below is optional (UX audit A/C3 — essential vs optional). */}
              <p className="mb-5 text-xs text-muted-foreground">{t('bot_card.routing_required_hint')}</p>

              {/* Sub bots — attach the per-role SENDER bot.  Owner sees
                  every role; a manager sees only their own row.  The
                  group + topics for that role live on Group delivery. */}
              <SubBotRoster canManageAccount={canManageAccount} />

              {/* CONNECTION — the bot-credential half of the card,
                  OWNER-ONLY: a role manager manages their Sub bot above;
                  the account bot's identity/credential isn't their
                  surface (owner info-scope decision 2026-07-23). */}
              {canManageAccount && (<>
              <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('bot_card.connection_label')}
              </div>
              <div className="flex items-center gap-3 mb-4">
                {/* Flat status chip (no border) — border is the action
                    marker on this card.  Labels reuse the roster's
                    running/not-running pair. */}
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium ${
                  botConfig.is_running !== false
                    ? toneClasses('ok')
                    : toneClasses('warn')
                }`}>
                  <span className={`w-2 h-2 rounded-full ${botConfig.is_running !== false ? 'bg-ok animate-pulse' : 'bg-warn'}`} />
                  {botConfig.is_running !== false ? t('alert_routing.running') : t('alert_routing.configured')}
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
                {botConfig.first_name && (
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t('bot_card.bot_name')}</dt>
                    <dd className="text-sm font-medium">{botConfig.first_name}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-muted-foreground mb-0.5">{t('bot_card.username')}</dt>
                  <dd className="text-sm">
                    <a href={`https://t.me/${botConfig.bot_username}`} target="_blank" rel="noopener noreferrer"
                      className="text-primary hover:underline">@{botConfig.bot_username}</a>
                  </dd>
                </div>
                {botConfig.bot_id && (
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t('bot_card.bot_id')}</dt>
                    <dd className="text-sm font-mono text-foreground/80">{botConfig.bot_id}</dd>
                  </div>
                )}
                {/* No "Status" row — the chip above already carries the
                    running state; saying it twice was audit finding D1. */}
              </div>

              {/* Delivery health — does the bot ACTUALLY deliver
                  (token, webhook hygiene, group membership, topics)?
                  Probed daily + after connect; re-check on demand. */}
              <BotHealthSection hasBot={botConfig.has_bot} />

              <div className="flex items-center gap-3 flex-wrap">
                <a href={`https://t.me/${botConfig.bot_username}`} target="_blank" rel="noopener noreferrer"
                  className="px-3 py-1.5 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition">
                  {t('bot_card.open_telegram')}
                </a>
                {/* Disconnect is an account-credential action — owner only. */}
                {canManageAccount && (showBotDisconnect ? (
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-muted-foreground">{t('bot_card.disconnect_confirm')}</span>
                    <button onClick={handleDisconnectBot} disabled={botSaving}
                      className="px-3 py-1.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 rounded text-xs font-medium text-destructive-foreground transition">
                      {botSaving ? t('bot_card.disconnecting') : t('bot_card.disconnect_yes')}
                    </button>
                    <button onClick={() => setShowBotDisconnect(false)}
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded text-xs font-medium transition">
                      {t('common.cancel')}
                    </button>
                  </div>
                ) : (
                  <button onClick={() => setShowBotDisconnect(true)}
                    className="px-3 py-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive rounded text-xs font-medium transition">
                    {t('bot_card.disconnect')}
                  </button>
                ))}
              </div>
              </>)}
            </div>
          ) : !canManageAccount ? (
            // Manager, no account bot yet — only an owner can connect one.
            <p className="text-sm text-muted-foreground">{t('bot_card.no_bot_manager')}</p>
          ) : (
            <div>
              <p className="text-sm text-muted-foreground mb-3">
                {t('bot_card.connect_intro_pre')} <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">@BotFather</a>{t('bot_card.connect_intro_post')}
              </p>
              <div className="flex items-end gap-3">
                <div className="flex-1">
                  <label className="block text-xs text-muted-foreground mb-1">{t('bot_card.token_label')}</label>
                  {/* type=text, not password — a password field here
                      attracts saved-credential autofill (seen live on
                      the roster inputs; same fix applied there). */}
                  <input type="text" value={botToken} onChange={e => setBotToken(e.target.value)}
                    placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                    autoComplete="off" spellCheck={false}
                    className="w-full bg-muted border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-ring font-mono" />
                </div>
                <button onClick={handleConnectBot} disabled={botSaving || botToken.length < 30}
                  className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition whitespace-nowrap">
                  {botSaving ? t('bot_card.validating') : t('bot_card.validate')}
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Manager view: the OTHER Settings components their own flags
          grant (owner reaches these via Team Management / the settings
          group; a manager's page lists just what they hold).  General
          settings stay owner-only and simply don't render. */}
      {!canManageAccount && (canInvite || canManageWorkHours) && (
        <section className="bg-card border border-border rounded-xl p-5">
          <div className="space-y-2">
            {canInvite && (
              <Link to="/invites" className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 hover:border-ring transition">
                <Link2 size={16} className="text-muted-foreground shrink-0" />
                <span className="flex-1 min-w-0">
                  <span className="block text-sm font-medium text-foreground">{t('bot_card.card_invites')}</span>
                  <span className="block text-xs text-muted-foreground">{t('bot_card.card_invites_desc')}</span>
                </span>
                <ArrowRight size={14} className="text-muted-foreground shrink-0" />
              </Link>
            )}
            {canManageWorkHours && (
              <Link to="/work-hours" className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 hover:border-ring transition">
                <Clock size={16} className="text-muted-foreground shrink-0" />
                <span className="flex-1 min-w-0">
                  <span className="block text-sm font-medium text-foreground">{t('bot_card.card_work_hours')}</span>
                  <span className="block text-xs text-muted-foreground">{t('bot_card.card_work_hours_desc')}</span>
                </span>
                <ArrowRight size={14} className="text-muted-foreground shrink-0" />
              </Link>
            )}
          </div>
        </section>
      )}

      {/* Editable Settings — every row here is an account_settings key,
          which capabilities/settings_registry.py owns via the config
          family.  Gated on the config flag rather than the page's Manage:
          the server returns an empty `settings` dict without it, so a
          Manage-only holder would otherwise see this card claiming "No
          settings configured yet" when in truth they simply cannot read
          them. */}

      {/* AI Usage */}
      {canManageAccount && data?.ai_usage && Object.keys(data.ai_usage).length > 0 && (
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
          {/* By Type breakdown — uses shared label rollup so this page
              and the Billing page show identical strings. */}
          {(data.ai_usage as any).by_type && Object.keys((data.ai_usage as any).by_type).length > 0 && (
            <div className="mb-4">
              <h3 className="text-sm font-medium text-muted-foreground mb-2">By Type</h3>
              <div className="grid grid-cols-3 gap-3 text-sm">
                {rollupByDisplayLabel((data.ai_usage as any).by_type as Record<string, {requests: number; tokens: number}>).map(([label, stats]) => (
                  <div key={label} className="bg-muted rounded-lg p-3">
                    <div className="text-foreground/80 font-medium">{label}</div>
                    <div className="text-muted-foreground text-xs mt-1">{stats.requests.toLocaleString()} requests &middot; {stats.tokens.toLocaleString()} tokens</div>
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
      {canManageAccount && (
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
              <Select value={String(sStart)} onValueChange={(v) => setSStart(Number(v))} items={HOUR_ITEMS}>
                <SelectTrigger className="w-full" aria-label="Start Hour"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {HOUR_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">End Hour</label>
              <Select value={String(sEnd)} onValueChange={(v) => setSEnd(Number(v))} items={HOUR_ITEMS}>
                <SelectTrigger className="w-full" aria-label="End Hour"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {HOUR_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Role</label>
              <Select value={sRole} onValueChange={(v) => setSRole(v ?? '')} items={ROLE_ITEMS}>
                <SelectTrigger className="w-full" aria-label="Role"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ROLE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end">
              <button type="submit" disabled={saving} className="px-4 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition">
                {saving ? 'Saving...' : 'Add'}
              </button>
            </div>
          </form>
        )}

        {data?.schedules && data.schedules.length > 0 ? (
          <DataGrid
            columns={[
              { key: 'label', label: 'Label', sortable: true },
              {
                key: 'start_hour', label: 'Hours', sortable: true,
                render: (_v, row) => {
                  const s = row as unknown as WorkSchedule;
                  return (
                    <span>
                      {String(s.start_hour).padStart(2, '0')}:00 – {String(s.end_hour).padStart(2, '0')}:00
                    </span>
                  );
                },
              },
              {
                key: 'target_role', label: 'Role', sortable: true,
                render: (v) => (
                  <span className="capitalize">{String(v).replace(/_/g, ' ')}</span>
                ),
              },
              {
                key: '_actions', label: 'Actions', sortable: false,
                render: (_v, row) => {
                  const s = row as unknown as WorkSchedule;
                  return (
                    <button
                      onClick={() => handleDeleteSchedule(s.id)}
                      className="text-destructive hover:text-destructive/80 text-xs"
                    >
                      Delete
                    </button>
                  );
                },
              },
            ] satisfies AnyColumn[]}
            data={data.schedules as unknown as Record<string, unknown>[]}
            enableToolbar={false}
            enablePagination={false}
          />
        ) : (
          <p className="text-muted-foreground text-sm">No schedules configured.</p>
        )}
      </section>
      )}

      {/* Owner-only account deletion — renders nothing for other roles. */}
      {canManageAccount && <DangerZoneSection />}
    </div>
  );
}
