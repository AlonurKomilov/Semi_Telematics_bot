/**
 * My Notifications — per-user personal-DM alert preferences.
 *
 * This page is the dashboard twin of the bot's ``/alerts`` command.
 * Each user owns their own per-alert-type DM toggle plus the new
 * "🟢 Resolve receipts" opt-in.  The visible toggle list is
 * role-tailored on the server side — a Safety user doesn't see a
 * Fuel toggle, a Dispatcher doesn't see Health, etc.  See
 * ``capabilities/alerting/relevance.py`` for the role→alert-type
 * mapping.
 *
 * Group/forum routing (which alert types post to which Telegram
 * topic) is controlled by admins on Account Settings → Forum
 * Routing.  The two surfaces are independent: turning OFF your
 * personal DM for Fuel doesn't stop alerts landing in the Fuel
 * topic the team chat subscribes to.
 *
 * Reached via the avatar dropdown (top-right of every page) →
 * "My Notifications".
 */
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Bell, BellOff, Loader2 } from 'lucide-react';
import { apiJSON } from '../api/client';
import { PageHeader } from '../components/shell';

interface AlertPrefsResponse {
  alerts_on: boolean;
  alert_resolve_receipts: boolean;
  relevant_types: string[];
  toggles: Record<string, boolean>;
}

// Human-friendly labels for each alert type.  Keys mirror the
// ``alert_<type>`` column names so we can derive labels from a
// single mapping rather than threading them through the API.
const TYPE_LABEL: Record<string, string> = {
  alert_faults:   'Engine Faults',
  alert_health:   'Vehicle Health',
  alert_fuel:     'Fuel & DEF',
  alert_geofence: 'Geofence Entry/Exit',
  alert_events:   'Safety Events',
  alert_camera:   'Camera Issues',
  alert_parking:  'Unsafe Parking',
};

const TYPE_DESCRIPTION: Record<string, string> = {
  alert_faults:   'SPN/FMI fault codes the engine ECU emits.',
  alert_health:   'Mechanical telemetry: oil, coolant, battery, DEF.',
  alert_fuel:     'Low fuel + DEF level + efficiency events.',
  alert_geofence: 'Vehicle enters or leaves a platform zone.',
  alert_events:   'Harsh braking, cornering, speeding, etc.',
  alert_camera:   'Dashcam tampering / obstruction detected.',
  alert_parking:  'Unauthorised long stop in an unsafe location.',
};

export default function MyNotifications() {
  const [prefs, setPrefs] = useState<AlertPrefsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

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

  // Optimistic update so the checkbox feels instant; rolls back on
  // server error.  The server echoes the post-update state back so
  // we re-sync from authoritative storage rather than trust our
  // local change.
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
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <div>
        <PageHeader
          icon={Bell}
          title="My Notifications"
          description="Choose which alerts arrive in your personal Telegram chat."
        />
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 size={14} className="animate-spin" />
          Loading…
        </div>
      </div>
    );
  }

  if (!prefs) {
    return (
      <div>
        <PageHeader
          icon={Bell}
          title="My Notifications"
          description="Choose which alerts arrive in your personal Telegram chat."
        />
        <p className="text-sm text-destructive">Couldn&apos;t load preferences.</p>
      </div>
    );
  }

  const masterOff = !prefs.alerts_on;

  return (
    <div>
      <PageHeader
        icon={Bell}
        title="My Notifications"
        description={
          'Personal alert preferences for your Telegram DM. Admins set group / forum routing separately on Account Settings — the two don’t override each other.'
        }
      />

      {/* Master switch */}
      <section className="bg-card border border-border rounded-xl p-4 mb-4">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={prefs.alerts_on}
            disabled={saving === 'alerts_on'}
            onChange={e => setField('alerts_on', e.target.checked)}
            className="accent-primary cursor-pointer mt-0.5"
          />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium inline-flex items-center gap-2">
              {prefs.alerts_on ? <Bell size={14} /> : <BellOff size={14} />}
              Personal alerts enabled
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Master switch. When off, no alerts arrive in your Telegram DM regardless of the per-category toggles below.
            </p>
          </div>
        </label>
      </section>

      {/* Per-type toggles (role-tailored by the server) */}
      <section className="bg-card border border-border rounded-xl p-4 mb-4">
        <p className="text-xs uppercase tracking-wide text-muted-foreground mb-3">
          Alert categories
        </p>
        {prefs.relevant_types.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Your role doesn&apos;t have any alert categories configured.
          </p>
        ) : (
          <div className="divide-y divide-border/60">
            {prefs.relevant_types.map(atype => {
              const field = `alert_${atype}`;
              const on = !!prefs.toggles[field];
              return (
                <label
                  key={atype}
                  className={`flex items-start gap-3 py-2.5 cursor-pointer ${
                    masterOff ? 'opacity-50' : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={masterOff || saving === field}
                    onChange={e => setField(field, e.target.checked)}
                    className="accent-primary cursor-pointer mt-0.5"
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm">{TYPE_LABEL[field] || atype}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {TYPE_DESCRIPTION[field] || ''}
                    </p>
                  </div>
                </label>
              );
            })}
          </div>
        )}
      </section>

      {/* Resolve-receipt toggle (always shown regardless of role) */}
      <section className="bg-card border border-border rounded-xl p-4">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={prefs.alert_resolve_receipts}
            disabled={saving === 'alert_resolve_receipts'}
            onChange={e => setField('alert_resolve_receipts', e.target.checked)}
            className="accent-primary cursor-pointer mt-0.5"
          />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">🟢 Send me resolve receipts</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              When an alert auto-resolves (e.g. a parked truck starts moving, a fault clears), send me a confirmation DM. Off by default to reduce chat noise; turn on if you want closure-loop confirmations.
            </p>
          </div>
        </label>
      </section>
    </div>
  );
}
