/**
 * Alert details drawer — slides over the page when the operator opens
 * a row in AlertsResults (any cell, or the alert id by keyboard).
 *
 * Named "alert" throughout, deliberately: the board, the checkbox, the
 * bell and this drawer all address the same object, and it had grown
 * four different nouns.  The file/registry key still says "incident"
 * (internal identifiers only) to avoid churning the persona layout
 * lists mid-flight.
 *
 * Composes existing widgets without duplicating their data layer:
 *   • Header  — type badge, severity dot, vehicle name, last seen.
 *   • Description — same formatter the queue uses, full text (no truncate).
 *   • Quick links — to Vehicle page (for the truck), Scorecards
 *                   (filtered to driver if alert carries acknowledged_by
 *                   metadata), and Coaching (assignments view).  Routes
 *                   exist independently; this drawer is a navigation hub,
 *                   not a data sink.
 *   • Video preview — for safety_events alerts that have a video_url
 *                     and the operator has ``can_camera``.  Fetches
 *                     /safety/events/{event_id}/video to refresh the
 *                     S3 signature on demand (existing endpoint).
 *
 * Mounted for EVERY persona (see layouts.ts — it's in UNIVERSAL and in
 * each named list).  That matters: the queue row no longer carries the
 * full description as hover text, so this drawer is the only place the
 * untruncated text exists.  Open-state lives in AlertsSelectionContext
 * (drillInAlert) rather than here, so any layout can host it.
 *
 * Permission-gated:
 *   • can_camera ⇒ video preview block shown
 *   • can_view_scorecards ⇒ scorecard link shown
 *   • can_manage_coaching ⇒ coaching link shown
 *
 * No new backend dependency.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import {
  X, ExternalLink, Truck, BarChart3, GraduationCap, Video, CheckCircle2,
  Wrench,
} from 'lucide-react';
import { apiJSON, ApiError } from '../../../api/client';
import { Button } from '../../../components/ui/button';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { reportSeen } from '../_shared/seenReporter';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';
import { useAckAlerts } from '../useRecentAlerts';
import {
  TypeBadge, SeverityDot, AckMarker, isAckable,
} from '../_shared/components';
import { toneText } from '../../../lib/status';
import { formatAlertDescription } from '../../../utils/alertDescription';
import { formatDate } from '../../../utils/datetime';
import { useTimezone } from '../../../hooks/useTimezone';
import { Sheet, SheetContent } from '../../../components/ui/sheet';
import type { Alert } from '../../../types';

export default function IncidentDrillInDrawer() {
  const { t } = useTranslation();
  const { drillInAlert, closeDrillIn } = useAlertsSelection();
  // Opening the drawer is the strongest form of "seen" there is — a
  // deliberate look at ONE alert — so it reports directly, no dwell
  // clock.  This is also the bell's seen path: a bell row click lands
  // here via ?alertId=.
  useEffect(() => {
    const id = Number(drillInAlert?.id);
    if (Number.isFinite(id)) reportSeen(id);
  }, [drillInAlert]);
  // Close on Escape — global listener bound only while the drawer is open.
  useEffect(() => {
    if (!drillInAlert) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeDrillIn();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drillInAlert, closeDrillIn]);

  if (!drillInAlert) return null;

  return (
    <>
      {/* Backdrop — click-outside closes. */}
      {/* <Sheet> rather than a hand-rolled overlay.  The old shape — a
          backdrop <button> plus an <aside role="dialog"> — looked like a
          modal and behaved like a div: no focus trap, no Escape, no
          aria-modal, and the page behind it still scrolled.  ``open`` is
          always true because this component mounts only while the drawer
          is open; onOpenChange routes Escape and backdrop clicks to the
          same close the ✕ already used. */}
      <Sheet open onOpenChange={(o) => { if (!o) closeDrillIn(); }}>
        <SheetContent
          side="right"
          size="md"
          aria-label={`Alert #${drillInAlert.id} details`}
          // DrawerHeader already carries a ✕ (see TripsDrawer).
          showCloseButton={false}
        >
          <DrawerHeader alert={drillInAlert} onClose={closeDrillIn} />
          <DrawerBody alert={drillInAlert} />
          <DrawerFooter alert={drillInAlert} onAcknowledged={closeDrillIn} />
        </SheetContent>
      </Sheet>
    </>
  );
}


function DrawerHeader({ alert, onClose }: {
  alert: Alert;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const tz = useTimezone();
  return (
    <header className="px-5 py-4 border-b border-border flex items-start gap-3">
      <SeverityDot severity={alert.severity} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <TypeBadge type={alert.alert_type || 'unknown'} kind={alert.kind} />
          <span className="text-xs text-muted-foreground font-mono">
            #{alert.id}
          </span>
        </div>
        <h2 className="mt-1.5 text-base font-semibold text-foreground truncate">
          {alert.vehicle_name || alert.vehicle_id || 'Unknown vehicle'}
        </h2>
        {alert.last_seen && (
          <p className="text-xs text-muted-foreground mt-0.5">
            Last fired {formatDate(alert.last_seen, { timeZone: tz })}
            {(alert.occurrence_count ?? 1) > 1 && (
              <span className={`ml-2 ${toneText('warn')}`}>
                × {alert.occurrence_count}
              </span>
            )}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={onClose}
        className="text-muted-foreground hover:text-foreground p-1 -m-1 min-h-tap"
        aria-label={t('alerts.drillin.close')}
      >
        <X className="size-4" />
      </button>
    </header>
  );
}


/**
 * The one thing an operator opens an alert to DO.
 *
 * Without it the drawer was read-only: you had to close it, find the row
 * again, tick its checkbox and use the bulk bar — so the detail view
 * didn't contain the action it exists to enable.  Acknowledging closes
 * the drawer, because the record has left the queue you were working.
 */
function DrawerFooter({ alert, onAcknowledged }: {
  alert: Alert;
  onAcknowledged: () => void;
}) {
  const ackAlerts = useAckAlerts();
  const [busy, setBusy] = useState(false);
  const [claimedNow, setClaimedNow] = useState(false);

  // Already resolved: state the outcome instead of offering an action
  // that would no-op.
  if (!isAckable(alert)) {
    return (
      <footer className="px-5 py-4 border-t border-border shrink-0">
        <AckMarker alert={alert} />
      </footer>
    );
  }

  const mine = claimedNow || !!alert.working_me;

  // The drawer is the FIRST surface most people meet after a row click,
  // so it teaches the trio or teaches the dead model — there is no
  // neutral.  Primary follows your relationship to the task: not yours
  // yet → "Work on it" (the claim); yours → "Done" (the resolution).
  const claim = async () => {
    setBusy(true);
    try {
      await apiJSON(`/alerts/${alert.id}/work`, { method: 'POST' });
      setClaimedNow(true);
      toast.success('You’re on it — it’s in My working on');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Couldn’t claim it');
    } finally {
      setBusy(false);
    }
  };

  const resolve = async () => {
    // A one-way write that speaks for the whole account — the one press
    // in the trio that earns a stop-and-confirm.
    if (!window.confirm(
      'Resolve this alert for everyone?\n\nRecorded under your name.')) return;
    setBusy(true);
    try {
      // The shared helper, not a local POST: it also invalidates
      // ['shell','overview-stats'], which is where the bell badge and the
      // Overview card read from.
      await ackAlerts([alert.id]);
      toast.success(`Alert #${alert.id} resolved`);
      onAcknowledged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Couldn’t resolve it');
      setBusy(false);   // stay open so the operator can retry
    }
  };

  return (
    <footer className="px-5 py-4 border-t border-border shrink-0">
      {mine ? (
        <Button size="lg" onClick={() => { void resolve(); }}
                disabled={busy} className="w-full">
          <CheckCircle2 aria-hidden />
          {busy ? 'Resolving…' : 'Done'}
        </Button>
      ) : (
        <div className="flex gap-2">
          <Button size="lg" onClick={() => { void claim(); }}
                  disabled={busy} className="flex-1">
            <Wrench aria-hidden />
            {busy ? 'Claiming…' : 'Work on it'}
          </Button>
          <Button size="lg" variant="outline"
                  onClick={() => { void resolve(); }}
                  disabled={busy}>
            Done
          </Button>
        </div>
      )}
      <p className="text-2xs text-muted-foreground mt-2 text-center">
        {mine
          ? 'Done resolves it for everyone — recorded under your name.'
          : 'Work on it claims the task and quiets the pager. Done resolves '
            + 'it for everyone, recorded under your name.'}
      </p>
    </footer>
  );
}


function DrawerBody({ alert }: { alert: Alert }) {
  const { t } = useTranslation();
  const { has, hasAny } = useViewPermissions();
  const description = formatAlertDescription(
    alert as Alert & { last_detail?: string; message?: string },
  );

  // Quick-link visibility maps to existing dashboard pages.  Permission
  // gates mirror the route guards on each page so operators don't see
  // dead links.
  const canScorecards = hasAny('can_view_scorecards');
  const canCoaching = has('can_manage_coaching');
  const canCamera = has('can_camera');
  const isEventAlert = alert.alert_type === 'events' || alert.alert_type === 'event';

  return (
    <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
      {description && (
        <section>
          <h3 className="text-2xs uppercase tracking-wide text-muted-foreground font-medium mb-1.5">
            {t('alerts.drillin.description_label')}
          </h3>
          <p className="text-sm text-foreground leading-relaxed">{description}</p>
        </section>
      )}

      {canCamera && isEventAlert && (
        <VideoBlock alert={alert} />
      )}

      <section>
        <h3 className="text-2xs uppercase tracking-wide text-muted-foreground font-medium mb-2">
          {t('alerts.drillin.quick_links')}
        </h3>
        <div className="grid gap-2">
          {alert.vehicle_id && (
            <DrawerLink
              icon={Truck}
              label={t('alerts.drillin.vehicle_page')}
              hint={t('alerts.drillin.vehicle_page_hint')}
              to={`/vehicles/${encodeURIComponent(alert.vehicle_id)}`}
            />
          )}
          {canScorecards && (
            <DrawerLink
              icon={BarChart3}
              label={t('alerts.drillin.scorecards')}
              hint={t('alerts.drillin.scorecards_hint')}
              to={`/scorecards${
                alert.acknowledged_by
                  ? `?driver_id=${encodeURIComponent(String(alert.acknowledged_by))}`
                  : ''
              }`}
            />
          )}
          {canCoaching && (
            <DrawerLink
              icon={GraduationCap}
              label={t('alerts.drillin.coaching')}
              hint={t('alerts.drillin.coaching_hint')}
              to="/coaching"
            />
          )}
        </div>
      </section>
    </div>
  );
}


function DrawerLink({ icon: Icon, label, hint, to }: {
  icon: typeof Truck;
  label: string;
  hint: string;
  to: string;
}) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-border hover:border-primary/40 hover:bg-muted/40 transition group"
    >
      <span className="inline-flex items-center justify-center w-8 h-8 rounded-md bg-muted text-muted-foreground group-hover:text-primary group-hover:bg-primary/10">
        <Icon className="size-4" />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        <span className="block text-2xs text-muted-foreground">{hint}</span>
      </span>
      <ExternalLink className="text-muted-foreground/60 size-3.5" />
    </Link>
  );
}


/**
 * Video preview — fetches the freshly-signed S3 url from
 * /safety/events/{id}/video so the link doesn't 403 on stale signatures.
 *
 * The video block only renders when the alert carries an event_id /
 * acknowledged_by-derived id; if the underlying endpoint 404s (the
 * Samsara event isn't ingested) we hide the block silently rather
 * than surfacing a broken-video error to the safety operator.
 */
function VideoBlock({ alert }: { alert: Alert }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string>('');
  const [loading, setLoading] = useState(false);

  // The Alert shape doesn't carry event_id directly; the AlertHistory
  // row records it inside alert_key for safety events
  // ("eventId:driverId").  Try to recover it; if absent the video
  // block can't render and we hide.
  const eventId = parseEventId(alert);

  useEffect(() => {
    if (!eventId) return;
    let cancelled = false;
    setLoading(true);
    apiJSON<{ url?: string }>(`/safety/events/${encodeURIComponent(eventId)}/video?angle=forward`)
      .then((r) => {
        if (cancelled) return;
        if (r?.url) setUrl(r.url);
        else setError(t('alerts.drillin.no_video_for_event'));
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
          setError(t('alerts.drillin.video_unavailable'));
        } else {
          setError(e instanceof Error ? e.message : t('alerts.drillin.video_request_failed'));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [eventId, t]);

  if (!eventId) return null;

  return (
    <section>
      <h3 className="text-2xs uppercase tracking-wide text-muted-foreground font-medium mb-2 inline-flex items-center gap-1.5">
        <Video className="size-3" />
        {t('alerts.drillin.forward_camera')}
      </h3>
      {loading && (
        <div className="text-xs text-muted-foreground py-4 text-center bg-muted rounded">
          {t('alerts.drillin.loading_video')}
        </div>
      )}
      {!loading && error && (
        <div className="text-2xs text-muted-foreground py-3 px-3 bg-muted/50 rounded">
          {error}
        </div>
      )}
      {!loading && url && (
        // controls only — autoplay is rude for an alert drawer
        // that may open in a crowded ops room.
        <video
          src={url}
          controls
          preload="metadata"
          className="w-full rounded border border-border bg-black"
        />
      )}
    </section>
  );
}


// Safety-event alert_key shape: "<event_id>:<driver_id>" or just
// "<event_id>".  ``parseEventId`` extracts the event_id when present;
// returns null when the alert isn't a safety event or the key shape
// doesn't match.  Defensive: events from non-Samsara sources won't
// carry an event_id and the video block stays hidden.
function parseEventId(alert: Alert): string | null {
  const t = alert.alert_type;
  if (t !== 'events' && t !== 'event') return null;
  const key = alert.alert_key || '';
  if (!key) return null;
  // alert_key is "<vehicle_id>:event:<event_id>" or
  // "<event_id>:<driver_id>" historically — be permissive.
  const parts = key.split(':');
  // Find the first long alphanumeric token that looks like a Samsara
  // event id (24+ char hex-ish UUID).  Falls through to null if none
  // of the parts match.
  for (const p of parts) {
    if (p.length >= 16 && /^[A-Za-z0-9_-]+$/.test(p)) return p;
  }
  return null;
}
