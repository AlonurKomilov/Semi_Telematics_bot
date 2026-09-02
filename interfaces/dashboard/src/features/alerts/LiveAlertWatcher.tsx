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
import { useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import { toast } from 'sonner';
import type { Alert, AlertSeverity } from '../../types';
import type { Tone } from '../../lib/status';
import { showBanner } from '../../components/banners';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { formatAlertDetailInline } from '../../utils/alertDescription';
import { useRecentAlerts, useAckAlerts, activeAndClaimed } from './useRecentAlerts';
import { useBannerLevel } from './bannerLevel';
import { claimedBanners, diffNewAlerts, resolvedBanners } from './liveAlerts';

const P_ALERTS = ['can_view_vehicles'];
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
  // alertId → the banner's toast id, for banners currently on screen — so a
  // sticky critical banner can be retired once its alert resolves (checked
  // authoritatively via /alerts/active-among, never inferred from the
  // capped feed).
  const shownRef = useRef<Map<string, string | number>>(new Map());
  // The ALERT behind each on-screen banner, so one can be re-rendered
  // later with something it did not know when first shown (a colleague
  // claiming it).  Kept here rather than looked up in `data`: a sticky
  // critical outlives the capped recent feed, which is the same reason
  // the resolved-check asks /alerts/active-among instead of the feed.
  const shownAlertRef = useRef<Map<string, Alert>>(new Map());
  // Ids already re-rendered with their claimant, so a claim that stands
  // for days does not rebuild its banner on every poll.
  const annotatedRef = useRef<Set<string>>(new Set());
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

  const bannerFor = useCallback(function bannerFor(a: Alert, claimant?: string, existingId?: string | number) {
    const tone = SEVERITY_TONE[a.severity ?? 'info'] ?? 'info';
    const critical = a.severity === 'critical';
    const bannerId = showBanner({
      tone,
      title: `${label(a.alert_type)} — ${a.vehicle_name || 'Vehicle'}`,
      // Company code chip — which company this unit belongs to (server
      // tags it on multi-company accounts only), same as the bell rows.
      tag: a.company,
      onClose: () => {
        shownRef.current.delete(String(a.id));
        shownAlertRef.current.delete(String(a.id));
        annotatedRef.current.delete(String(a.id));
      },
      // `last_detail` is a dedup key ("parking:unknown:8h") — humanize it
      // through the shared formatter, same as the board and the bell.
      // Once somebody has it, the banner says WHO instead of only what.
      // That sentence is the stand-down signal: without it a second
      // dispatcher reads a filled "Work on it" and does the work twice.
      detail: claimant
        ? `🔧 ${claimant} is working on this`
        : formatAlertDetailInline(a),
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
          // The claim, not a resolution — a banner is the pager's face,
          // and the pager's job is finding an owner.  Resolving from a
          // popup without doing the work was exactly the old lie;
          // claiming says the honest thing ("I have it"), retires the
          // banner, and silences the re-page.
          //
          // Once an owner EXISTS the button stops being the primary
          // ask: the alert is owned, not over, so the banner stays
          // (it is still unresolved) but it no longer demands.  Join
          // remains, because a big task takes several hands — the same
          // grammar the board's Working-on cell uses.
          primary: !claimant,
          label: claimant ? 'Join' : 'Work on it',
          onClick: async () => {
            try {
              await apiJSON(`/alerts/${a.id}/work`, { method: 'POST' });
              shownRef.current.delete(String(a.id));   // owned by me
              toast.success('You’re on it — it’s in My working on');
            } catch (e) {
              toast.error(e instanceof Error ? e.message : 'Couldn’t claim it');
            }
          },
        },
      ],
    }, existingId);
    // Track ONLY sticky (critical) banners — they're the ones that never
    // auto-close and so need retiring when their alert resolves.  Non-
    // critical banners self-dismiss on their countdown, and tracking them
    // would let leaked entries (View/auto-close don't fire onClose) pile
    // up and push a fresh critical past the server's id cap.  Bound it
    // defensively too, well under that cap (drop-oldest).
    if (critical) {
      shownRef.current.set(String(a.id), bannerId);
      shownAlertRef.current.set(String(a.id), a);
      while (shownRef.current.size > 48) {
        const oldest = shownRef.current.keys().next().value;
        if (oldest === undefined) break;
        shownRef.current.delete(oldest);
      }
    }
  }, []);

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
  }, [data, dataUpdatedAt, bannerFor]);

  // Retire on-screen banners whose alert has resolved.  Runs each poll:
  // asks the AUTHORITATIVE /alerts/active-among for exactly the shown ids
  // (never the capped feed), dismisses any that came back not-active.
  // Best-effort — a failed check keeps the banners (a sticky safety banner
  // must never vanish on a network blip).
  useEffect(() => {
    if (!active) return;
    const ids = [...shownRef.current.keys()];
    if (!ids.length) return;
    let cancelled = false;
    activeAndClaimed(ids)
      .then(({ active, claimedBy }) => {
        if (cancelled) return;
        for (const [alertId, bannerId] of resolvedBanners(shownRef.current, active)) {
          toast.dismiss(bannerId);
          shownRef.current.delete(alertId);
          shownAlertRef.current.delete(alertId);
          annotatedRef.current.delete(alertId);
        }
        // Someone took it while this banner was on screen.  The alert is
        // OWNED, not over — so the banner stays (it is still unresolved)
        // and re-renders in its own slot naming the owner, instead of
        // going on demanding an owner that has been found.  Once per
        // claim, not once per poll.
        for (const [alertId, who, bannerId] of claimedBanners(
          shownRef.current, claimedBy, annotatedRef.current)) {
          const alert = shownAlertRef.current.get(alertId);
          if (!alert) continue;
          annotatedRef.current.add(alertId);
          bannerFor(alert, who, bannerId);
        }
      })
      .catch(() => { /* keep banners on failure */ });
    return () => { cancelled = true; };
  }, [dataUpdatedAt, active, bannerFor]);

  return null;
}
