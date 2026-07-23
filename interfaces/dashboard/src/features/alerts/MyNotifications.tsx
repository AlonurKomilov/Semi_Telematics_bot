/**
 * Notification preferences — the top-level personal page at
 * /notifications/preferences (reached from the topbar Notifications bell's
 * gear).  Notifications are a cross-SOURCE personal concern, not an alerts
 * sub-feature, so this lives on its own door — the alert BOARD stays at
 * /alerts.
 *
 * Structured by SOURCE so each kind of notification has a home:
 *   • Channels        — connect Telegram / Email / Push (the "where").
 *   • Alerts          — BROADCAST + opt-IN matrix (which alerts, where).
 *   • Account activity — TARGETED + opt-OUT notices (invite accepted …).
 *   • System           — platform notices (placeholder until a source
 *                        registers system.* categories).
 *   • In-app           — on-screen banner level + position.
 *
 * Telegram's alert toggles still live on the legacy ``users.alert_*``
 * columns (via /user/me/alerts, mirrored into the matrix); Email/Push and
 * every targeted category live on the notification matrix directly.
 * Group/forum routing is a separate admin surface under /alerts.
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Bell, BellOff, CheckCircle2, Send } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { PageHeader, ErrorState, CardSkeleton } from '@/components/shell';
import EmailChannelCard from './EmailChannelCard';
import PushChannelCard from './PushChannelCard';
import NotifyMatrix from './NotifyMatrix';
import AccountActivitySection from './AccountActivitySection';
import BannerSettingsCard from './BannerSettingsCard';
import BannerLevelCard from './BannerLevelCard';

/** Small uppercase source divider — the per-source rhythm of the page. */
function SourceLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2 mt-6 first:mt-0">
      {children}
    </p>
  );
}

interface AlertPrefsResponse {
  alerts_on: boolean;
  alert_resolve_receipts: boolean;
  relevant_types: string[];
  toggles: Record<string, boolean>;
}

export default function MyNotifications() {
  const [prefs, setPrefs] = useState<AlertPrefsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  // Bumped by the channel cards after connect/verify/device changes so
  // the matrix refetches its column enable-states.
  const [refreshKey, setRefreshKey] = useState(0);
  const bump = useCallback(() => setRefreshKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJSON<AlertPrefsResponse>('/user/me/alerts');
        if (!cancelled) setPrefs(data);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Failed to load preferences');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Optimistic update; the server echoes authoritative state back.
  const setField = async (field: string, value: boolean) => {
    if (!prefs) return;
    const prev = prefs;
    setPrefs({ ...prefs, [field]: value, toggles: { ...prefs.toggles, [field]: value } });
    setSaving(field);
    try {
      const fresh = await apiJSON<AlertPrefsResponse>('/user/me/alerts', {
        method: 'PUT', body: { [field]: value },
      });
      setPrefs(fresh);
    } catch (e) {
      setPrefs(prev);
      toast.error(e instanceof Error ? e.message : 'Save failed');
      throw e;
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <div>
        <PageHeader
          icon={Bell}
          title="Notifications"
          description="Choose what reaches you and where — Telegram, email, and push."
        />
        <CardSkeleton message="Loading preferences…" />
      </div>
    );
  }

  if (!prefs) {
    return (
      <div>
        <PageHeader
          icon={Bell}
          title="Notifications"
          description="Choose what reaches you and where — Telegram, email, and push."
        />
        <ErrorState
          title="Couldn’t load preferences"
          message="The server didn’t respond when fetching your settings. Try again in a moment."
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={Bell}
        title="Notifications"
        description={
          'Connect your channels once, then choose what reaches you and where. Admins set group / forum routing under Alerts; that doesn’t override these personal choices.'
        }
      />

      <SourceLabel>Channels</SourceLabel>
      <div className="grid gap-4 lg:grid-cols-3 items-start">
        {/* Telegram — master switch + resolve receipts */}
        <section className="bg-card border border-border rounded-xl p-4">
          <p className="text-base font-semibold inline-flex items-center gap-2 mb-1">
            <Send size={16} /> Telegram
          </p>
          <p className="text-xs text-muted-foreground mb-3">
            Direct messages from the bot to your personal chat.
          </p>
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={prefs.alerts_on}
              disabled={saving === 'alerts_on'}
              onChange={e => { void setField('alerts_on', e.target.checked).catch(() => {}); }}
              className="accent-primary cursor-pointer mt-0.5"
            />
            <span className="flex-1 min-w-0">
              <span className="text-sm inline-flex items-center gap-1.5">
                {prefs.alerts_on ? <Bell size={14} /> : <BellOff size={14} />}
                Personal alerts enabled
              </span>
              <span className="block text-xs text-muted-foreground mt-0.5">
                Master switch for your Telegram DM alerts.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3 cursor-pointer mt-3 pt-3 border-t border-border/60">
            <input
              type="checkbox"
              checked={prefs.alert_resolve_receipts}
              disabled={saving === 'alert_resolve_receipts'}
              onChange={e => { void setField('alert_resolve_receipts', e.target.checked).catch(() => {}); }}
              className="accent-primary cursor-pointer mt-0.5"
            />
            <span className="flex-1 min-w-0">
              <span className="text-sm inline-flex items-center gap-1.5">
                <CheckCircle2 size={14} className="text-ok" />
                Resolved-alert receipts
              </span>
              <span className="block text-xs text-muted-foreground mt-0.5">
                DM me when an alert auto-resolves (a fault clears, a parked
                truck moves). Off by default to reduce noise.
              </span>
            </span>
          </label>
        </section>

        <EmailChannelCard onChanged={bump} />
        <PushChannelCard onChanged={bump} />
      </div>

      <SourceLabel>Alerts</SourceLabel>
      <NotifyMatrix
        relevantTypes={prefs.relevant_types}
        telegramMasterOn={prefs.alerts_on}
        telegramToggles={prefs.toggles}
        onTelegramToggle={setField}
        refreshKey={refreshKey}
      />

      <SourceLabel>Account activity</SourceLabel>
      <AccountActivitySection refreshKey={refreshKey} />

      <SourceLabel>System</SourceLabel>
      <section className="bg-card border border-border rounded-xl p-4">
        <p className="text-sm text-muted-foreground">
          Platform notices — billing, security, and account changes — will
          appear here. Nothing to configure yet.
        </p>
      </section>

      <SourceLabel>In-app</SourceLabel>
      <div className="grid gap-4 lg:grid-cols-2 items-start">
        <BannerLevelCard />
        <BannerSettingsCard />
      </div>
    </div>
  );
}
