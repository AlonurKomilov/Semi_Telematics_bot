/**
 * Live alert banners — pops new alerts on screen while you're ON the
 * dashboard (web push covers the CLOSED-dashboard case; this is the
 * open-tab companion).
 *
 * Mounted once app-wide (App.tsx, authed branch).  Renders nothing — it
 * polls the shared recent-alerts feed, diffs new ids (diffNewAlerts owns
 * the no-flood rules), and shows one AppBanner per genuinely new alert,
 * gated by the user's level preference (All / Critical only / Off).
 *
 * Critical banners are STICKY (a safety alert must not auto-vanish);
 * warning/info auto-dismiss on a countdown.  Per poll it shows at most a
 * few, then one "+N more" summary — never a wall of pop-ups.
 */
import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import type { Alert, AlertSeverity } from '../../types';
import type { Tone } from '../../lib/status';
import { showBanner } from '../../components/banners';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useRecentAlerts, useAckAlerts } from './useRecentAlerts';
import { useBannerLevel } from './bannerLevel';
import { diffNewAlerts } from './liveAlerts';

const P_ALERTS = ['can_alerts_all', 'can_alerts_vehicle'];
// 60s ambient cadence — "a new alert within a minute" for an OPEN tab;
// urgency is web push's + the alerting pipeline's job, not this glance.
// Steady-state cost is bounded: react-query skips the interval while the
// tab is BACKGROUNDED (refetchIntervalInBackground unset) + no
// refetch-on-focus (main.tsx), so idle/many-tab offices add ~zero load —
// only a focused, alerts-permitted tab polls, and it shares ONE query
// with the bell.
const POLL_MS = 60_000;
const MAX_BANNERS_PER_POLL = 3;
const AUTO_DISMISS_SECONDS = 10;

const SEVERITY_TONE: Record<AlertSeverity, Tone> = {
  critical: 'danger',
  warning: 'warn',
  info: 'info',
};

const TYPE_LABEL: Record<string, string> = {
  fault: 'Engine fault', faults: 'Engine fault',
  health: 'Vehicle health',
  fuel: 'Fuel / DEF',
  geofence: 'Geofence',
  event: 'Safety event', events: 'Safety event',
  camera: 'Camera issue',
  parking: 'Unsafe parking',
};

function label(type?: string): string {
  return TYPE_LABEL[type ?? ''] ?? 'Alert';
}

export default function LiveAlertWatcher() {
  const { hasAny } = useViewPermissions();
  const canAlerts = hasAny(...P_ALERTS);
  const level = useBannerLevel();
  const active = canAlerts && level !== 'off';

  const navigate = useNavigate();
  const ackAlerts = useAckAlerts();
  const { data, dataUpdatedAt } = useRecentAlerts(active, POLL_MS);

  // Per-tab memory of ids already handled (baselined or banner'd).  Keyed
  // by id ALONE — deliberately: keying on id+last_seen would re-banner a
  // still-pending, still-firing alert on every poll (last_seen bumps each
  // cycle).  Trade-off: a health alert that re-fires under the SAME
  // alert_history id after being acked won't re-pop as a LIVE banner in an
  // already-open tab — but it still reappears in the bell + board (and web
  // push fires for the closed-dashboard case), so nothing is lost, only
  // the transient pop is suppressed for that re-fire.  Grows only to the
  // low thousands of short strings over a multi-day session — negligible.
  const seenRef = useRef<Set<string>>(new Set());
  const firstLoadRef = useRef(true);
  // Latest values read inside the data effect without making it re-run on
  // their change (only a genuine new fetch should diff).
  const levelRef = useRef(level);
  levelRef.current = level;
  const navRef = useRef(navigate);
  navRef.current = navigate;
  const ackRef = useRef(ackAlerts);
  ackRef.current = ackAlerts;

  // Turning banners OFF re-baselines: coming back ON must not flood with
  // everything that piled up while off (the bell already carries those).
  useEffect(() => {
    if (level === 'off') {
      firstLoadRef.current = true;
      seenRef.current = new Set();
    }
  }, [level]);

  // One diff per successful fetch (dataUpdatedAt changes only on a real
  // network result, not a cache read).
  useEffect(() => {
    if (!data || levelRef.current === 'off') return;
    const { toShow, seen } = diffNewAlerts(
      data.alerts,
      seenRef.current,
      levelRef.current === 'critical' ? 'critical' : 'all',
      firstLoadRef.current,
    );
    seenRef.current = seen;
    if (firstLoadRef.current) {
      firstLoadRef.current = false;     // baseline set; showed nothing
      return;
    }
    if (!toShow.length) return;

    toShow.slice(0, MAX_BANNERS_PER_POLL).forEach((a) => bannerFor(a));
    const overflow = toShow.length - MAX_BANNERS_PER_POLL;
    if (overflow > 0) {
      showBanner({
        tone: 'info',
        title: `${overflow} more new alert${overflow !== 1 ? 's' : ''}`,
        seconds: AUTO_DISMISS_SECONDS,
        countdown: 'dismiss',
        actions: [{ label: 'Open Alerts', primary: true, onClick: () => navRef.current('/alerts') }],
      });
    }

    function bannerFor(a: Alert) {
      const tone = SEVERITY_TONE[a.severity ?? 'info'] ?? 'info';
      const critical = a.severity === 'critical';
      showBanner({
        tone,
        title: `${label(a.alert_type)} — ${a.vehicle_name || 'Vehicle'}`,
        // Company code chip — which company this unit belongs to (server
        // tags it on multi-company accounts only), same as the bell rows.
        tag: a.company,
        detail: a.message || a.last_detail,
        // Live age + occurrence so the banner is honest about WHEN: a
        // fresh fire reads "2m ago", a recurring one "×5 · 2m ago", and a
        // sticky critical that lingers keeps ticking ("3d ago") instead of
        // masquerading as current.
        ageSince: a.last_seen || a.created_at,
        occurrence: a.occurrence_count,
        // Critical stays until dismissed/acked; others auto-close.
        seconds: critical ? undefined : AUTO_DISMISS_SECONDS,
        countdown: 'dismiss',
        actions: [
          { label: 'View', onClick: () => navRef.current('/alerts') },
          {
            label: 'Acknowledge',
            onClick: async () => {
              try {
                await ackRef.current([a.id]);
                toast.success('Acknowledged');
              } catch (e) {
                toast.error(e instanceof Error ? e.message : 'Couldn’t acknowledge');
              }
            },
          },
        ],
      });
    }
  }, [data, dataUpdatedAt]);

  return null;
}
