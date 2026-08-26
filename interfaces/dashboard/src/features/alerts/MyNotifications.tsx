/**
 * Notification preferences — the top-level personal page at
 * /notifications/preferences (reached from the topbar Notifications bell's
 * gear).  Notifications are a cross-SOURCE personal concern, not an alerts
 * sub-feature, so this lives on its own door — the alert BOARD stays at
 * /alerts.
 *
 * Structured by SOURCE so each kind of notification has a home:
 *   • Channels        — connect Telegram / Email / Push (the "where").
 *   • Alerts          — BROADCAST + opt-IN matrix (which alerts, where),
 *                        one row per alert TYPE.
 *   • My triggers     — the SAME question at a finer grain: one row per
 *                        trigger a person wrote, because the type matrix
 *                        above can only say "all of them, one way".  What
 *                        each trigger WATCHES is set on Alerts → Triggers;
 *                        only where it reaches you is answered here.
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
import TriggerDeliveryMatrix from './TriggerDeliveryMatrix';
import AccountActivitySection from './AccountActivitySection';
import BannerSettingsCard from './BannerSettingsCard';
import BannerLevelCard from './BannerLevelCard';
import { useSavedFlash } from './_shared/useSavedFlash';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

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
  // Autosave has no Save button, so success was silent — one transient
  // "Saved" for the whole page (a toast per toggle would bury the screen).
  const { saved, flashSaved } = useSavedFlash();

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
      flashSaved();
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

      {/* Autosave confirmation — every control here writes immediately, so
          success needs to be visible somewhere.  The live region is always
          mounted but its CONTENT is conditional: assistive tech announces
          on a content mutation, so an always-present node that only
          changes opacity would never be read out. */}
      <p className="text-xs text-ok inline-flex items-center gap-1.5 h-4" aria-live="polite">
        {saved && (<><CheckCircle2 className="size-3.5" aria-hidden /> Saved</>)}
      </p>

      <SourceLabel>Channels</SourceLabel>
      <div className="grid gap-4 lg:grid-cols-3 items-start">
        {/* Telegram — master switch + resolve receipts */}
        <Card render={<section />}>
          <SectionHeader size="card" icon={<Send className="size-4" />} className="mb-1">
            Telegram
          </SectionHeader>
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
                {prefs.alerts_on ? <Bell className="size-3.5" /> : <BellOff className="size-3.5" />}
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
                <CheckCircle2 className="text-ok size-3.5" />
                Resolved-alert receipts
              </span>
              <span className="block text-xs text-muted-foreground mt-0.5">
                DM me when an alert auto-resolves (a fault clears, a parked
                truck moves). Off by default to reduce noise.
              </span>
            </span>
          </label>
        </Card>

        <EmailChannelCard onChanged={bump} />
        <PushChannelCard onChanged={bump} />
      </div>

      {/* Two labels, not one.  Both cards used to sit under a single
          "Alerts" heading with no gap between them and near-identical
          titles ("Notify me when" / "Alert me when…"), so the pair read
          as one object — and the matrix, which answers WHERE, was the
          first thing a person met when their question was WHETHER.
          SourceLabel's mt-6 is also what puts air between the cards. */}
      <SourceLabel>Alerts — where they reach you</SourceLabel>
      <NotifyMatrix
        onSaved={flashSaved}
        relevantTypes={prefs.relevant_types}
        telegramMasterOn={prefs.alerts_on}
        telegramToggles={prefs.toggles}
        onTelegramToggle={setField}
        refreshKey={refreshKey}
      />

      {/* Same question as the matrix above, different rows.  WHAT a
          trigger watches is an alert question and lives on the Alerts
          page; WHERE it reaches you is a notification question, and every
          other answer to that is on this page.  Its rows are individual
          triggers rather than one "my triggers" row, because per-trigger
          is the whole grain the matrix above cannot express. */}
      <SourceLabel>My triggers — where they reach you</SourceLabel>
      <TriggerDeliveryMatrix
        telegramMasterOn={prefs.alerts_on}
        refreshKey={refreshKey}
        onSaved={flashSaved}
      />

      <SourceLabel>Account activity</SourceLabel>
      <AccountActivitySection refreshKey={refreshKey} section="personal" onSaved={flashSaved} />

      <SourceLabel>System</SourceLabel>
      <AccountActivitySection refreshKey={refreshKey} section="system" onSaved={flashSaved} />

      <SourceLabel>In-app</SourceLabel>
      <div className="grid gap-4 lg:grid-cols-2 items-start">
        <BannerLevelCard />
        <BannerSettingsCard />
      </div>
    </div>
  );
}
