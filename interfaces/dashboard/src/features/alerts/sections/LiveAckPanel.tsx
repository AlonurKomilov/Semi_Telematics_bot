/**
 * Dispatcher live-ops panel — sits above the control bar on the
 * Dispatcher persona's Alerts layout.
 *
 * Three signals an on-shift dispatcher needs at a glance:
 *   1. Sound on/off — browsers block autoplay until a user gesture,
 *      so this is a single explicit toggle (no silent prompt loop).
 *      Preference persisted in localStorage; chime plays on poll-tick
 *      whenever the unack count rises above its prior value.
 *   2. Δ since last poll — "new since you last looked" count.  We
 *      diff the current pending-count against the previous render's
 *      count so a dispatcher who's been heads-down sees how many
 *      alerts piled up while they were typing.
 *   3. Last acknowledged — relative time since the most recent
 *      ack-state transition; a stale value (>15min) hints the queue
 *      is being neglected.
 *
 * Persona-agnostic in implementation: the panel reads the same
 * useAlertsQuery hook every section uses, so it works on any persona
 * if a layout chooses to include it.  Dispatcher's layout is the
 * canonical caller (see layouts.ts).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Volume2, VolumeX, Activity, Clock } from 'lucide-react';
import { useAlertsQuery } from '../_shared/useAlertsQuery';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';
import type { AlertsResponse, VehiclesAlertsResponse } from '../../../types';
import { usePreference } from '../../../preferences';
import { Tip } from '../../../components/tooltip';

const LAST_ACK_KEY = '4truck_dispatch_last_ack_iso';

// Short, soft, non-alarming.  Browser-bundled Web Audio so we don't
// ship an MP3 — keeps the bundle lean and side-steps Telegram-style
// cache-poisoning issues with hosted audio.
function playChime() {
  try {
    const Ctx =
      (window as unknown as { AudioContext?: typeof AudioContext })
        .AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.18);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.32);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
    // Tear down so we don't leak AudioContexts across polls.
    osc.onended = () => { try { void ctx.close(); } catch { /* swallow */ } };
  } catch {
    /* audio is best-effort; never let it break the page */
  }
}

function totalPending(
  data: AlertsResponse | VehiclesAlertsResponse | undefined,
): number {
  if (!data) return 0;
  // VehicleAlerts: response.count is the vehicle count, NOT alerts.
  // Sum alert_count across vehicles to get the real pending total.
  if ('vehicles' in data && Array.isArray((data as VehiclesAlertsResponse).vehicles)) {
    return (data as VehiclesAlertsResponse).vehicles.reduce(
      (sum, v) => sum + (v.alert_count ?? 0), 0,
    );
  }
  // Flat alerts: count IS the total row count for the current filter.
  if ('alerts' in data && typeof data.count === 'number') return data.count;
  return 0;
}

// Plural-aware relative time formatter — i18next handles the
// number→string interpolation so a future locale that pluralises
// differently ("3 минуты назад") fits into the same key.
type TFn = (key: string, opts?: Record<string, unknown>) => string;
function relativeTime(iso: string | null, t: TFn): string {
  if (!iso) return t('alerts.live_ack.no_ack_yet');
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return t('alerts.live_ack.no_ack_yet');
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return t('alerts.live_ack.seconds_ago', { n: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t('alerts.live_ack.minutes_ago', { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t('alerts.live_ack.hours_ago', { n: hours });
  return t('alerts.live_ack.days_ago', { n: Math.floor(hours / 24) });
}

export default function LiveAckPanel() {
  const { t } = useTranslation();
  const { data, isLoading } = useAlertsQuery();
  const { selected, acking } = useAlertsSelection();

  // Device-scoped preference: a wall-mounted dispatch screen should chime,
  // a laptop in a shared office should not.  Storage + the legacy
  // '4truck_dispatch_sound_on' key live in the preferences registry.
  const { value: soundOn, setValue: setSoundOn } = usePreference('dispatch.soundOn');
  const [lastAck, setLastAck] = useState<string | null>(() => {
    try { return localStorage.getItem(LAST_ACK_KEY); } catch { return null; }
  });

  const pending = useMemo(() => totalPending(data), [data]);
  const prevPendingRef = useRef<number | null>(null);
  const delta = prevPendingRef.current === null
    ? 0
    : Math.max(0, pending - prevPendingRef.current);

  // Chime whenever the pending count rose since the last render AND
  // sound is on AND the page actually had a prior value (so a fresh
  // mount doesn't open with a bell).
  useEffect(() => {
    if (prevPendingRef.current !== null && pending > prevPendingRef.current && soundOn) {
      playChime();
    }
    prevPendingRef.current = pending;
  }, [pending, soundOn]);

  // Stamp last-ack each time the selection clears AFTER an ack-in-flight
  // (the bulk-ack flow in AlertsHeader sets acking=true → false + clears
  // selection on success).  We piggyback on selection size dropping
  // from non-zero to zero while acking transitioned true→false in the
  // same render to avoid stamping on a user-driven clear.
  const prevAcking = useRef(acking);
  const prevSelectedSize = useRef(selected.size);
  useEffect(() => {
    const justFinishedAck =
      prevAcking.current && !acking && prevSelectedSize.current > 0 && selected.size === 0;
    if (justFinishedAck) {
      const stamp = new Date().toISOString();
      setLastAck(stamp);
      try { localStorage.setItem(LAST_ACK_KEY, stamp); } catch { /* ignore */ }
    }
    prevAcking.current = acking;
    prevSelectedSize.current = selected.size;
  }, [acking, selected.size]);

  // Tick once a minute so "last ack" relative time stays fresh
  // without re-rendering the heavy results table.
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const toggleSound = () => {
    const next = !soundOn;
    setSoundOn(next);
    // Tiny chime on enable to confirm audio works under the current
    // browser gesture-grant rules.
    if (next) playChime();
  };

  const lastAckLabel = relativeTime(lastAck, t);
  // `now` is in deps so the chip refreshes when the interval fires.
  void now;

  return (
    <div className="mb-3 flex flex-wrap items-stretch gap-2">
      <Chip
        icon={<Activity size={14} className="text-primary" />}
        label={t('alerts.live_ack.pending')}
        value={isLoading ? '…' : String(pending)}
      />
      <Chip
        icon={<Activity size={14} className={delta > 0 ? 'text-warn' : 'text-muted-foreground'} />}
        label={t('alerts.live_ack.new_since_last_look')}
        value={delta > 0 ? `+${delta}` : '0'}
        accent={delta > 0 ? 'text-warn' : undefined}
      />
      <Chip
        icon={<Clock size={14} className="text-muted-foreground" />}
        label={t('alerts.live_ack.last_ack')}
        value={lastAckLabel}
      />
      <Tip label={t(soundOn ? 'alerts.live_ack.sound_on_title' : 'alerts.live_ack.sound_off_title')}>
      <button
        type="button"
        onClick={toggleSound}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border transition ${
          soundOn
            ? 'bg-primary/10 border-primary/40 text-primary'
            : 'bg-muted/40 border-border text-muted-foreground hover:text-foreground'
        }`}
      >
        {soundOn ? <Volume2 size={14} /> : <VolumeX size={14} />}
        {t(soundOn ? 'alerts.live_ack.sound_on' : 'alerts.live_ack.enable_sound')}
      </button>
      </Tip>
      <span className="ml-auto self-center text-2xs text-muted-foreground">
        {t('alerts.live_ack.hotkey_hint')}{' '}
        <kbd className="px-1 py-px rounded bg-muted text-foreground/80 text-3xs">
          {t('alerts.live_ack.hotkey_action')}
        </kbd>{' '}
        {t('alerts.live_ack.hotkey_tail')}
      </span>
    </div>
  );
}


function Chip({ icon, label, value, accent }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-border bg-card">
      {icon}
      <span className="text-3xs uppercase tracking-wide text-muted-foreground font-medium">
        {label}
      </span>
      <span className={`text-sm font-semibold tabular-nums ${accent ?? 'text-foreground'}`}>
        {value}
      </span>
    </div>
  );
}
