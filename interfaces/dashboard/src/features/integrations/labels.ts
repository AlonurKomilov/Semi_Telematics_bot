/**
 * Human-readable labels for the backend's stable capability ids.
 *
 * Kept here (not in i18n) because they describe a vendor-facing
 * concept that's the same regardless of the dashboard language —
 * "Vehicle State Sync" is what fleet ops everywhere call it.
 * Future i18n keys can wrap these without changing the call sites.
 */

import { formatDay, formatTime } from '../../utils/datetime';

export const CAPABILITY_LABELS: Record<string, string> = {
  vehicle_state:            'Live vehicle state',
  safety_events:            'Safety events',
  vehicle_health:           'Vehicle health',
  vehicle_faults:           'Fault codes',
  driver_efficiency:        'Driver efficiency (daily)',
  // Deprecated alias: the wire value before the warehouse schema move
  // (2026-08) — kept so any cached/stored payload still labels.
  driver_efficiency_daily:  'Driver efficiency (daily)',
  fleet_weather:            'Weather overlay',
  fleet_efficiency:         'Fleet efficiency',
  geofence_definitions:     'Geofence definitions',
  // NB: no snapshot/hourly/daily tiers here — they're the warehouse's
  // provider-agnostic roll-up cascade (operator console), not Samsara feeds.
  // history_prune retired — retention now lives in the Retention hub.
  history_backfill:         'One-time history backfill',
  // TMS capabilities (Datatruck) — dispatch-shaped sync, not telemetry.
  tms_drivers_sync:         'Drivers sync',
  tms_trucks_sync:          'Trucks sync',
  tms_trailers_sync:        'Trailers sync',
  tms_orders_sync:          'Loads / orders sync',
  tms_work_orders_sync:     'Work orders sync',
};

/** Resource ids of the Datatruck sync engine → row labels for the
 *  "Synced data" panel.  Order here is render order. */
export const DATATRUCK_RESOURCE_LABELS: [string, string][] = [
  ['drivers',     'Drivers'],
  ['trucks',      'Trucks'],
  ['trailers',    'Trailers'],
  ['orders',      'Loads / orders'],
  ['work_orders', 'Work orders'],
];

export function capabilityLabel(id: string): string {
  return CAPABILITY_LABELS[id] || id;
}

/** Format an ISO timestamp into the relative + absolute form the cards
 *  show ("2 hr ago · Jun 7, 2:22 PM").  Empty/invalid input → empty
 *  output.  Shared by the Samsara backfill line and the Datatruck
 *  synced-data rows so the same stamp renders identically.
 *
 *  Both the date AND the clock render in the **account timezone** (the
 *  SSOT) via the shared datetime helpers — no hardcoded UTC.  ``tz``
 *  absent → browser locale fallback.
 *
 *  Naive warehouse stamps (e.g. ``"2026-06-19T07:00:00"``) carry no tz
 *  designator; they are UTC, so we tag them before parsing — otherwise
 *  ``new Date`` reads them as browser-local, skewing the relative time
 *  (and reading slightly-ahead aggregate rows as the future).  Future
 *  ages are clamped to "just now". */
export function formatSyncTimestamp(
  iso: string | null | undefined,
  tz?: string,
): string {
  if (!iso) return '';
  const hasTz = /[zZ]|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasTz ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return '';
  const ageMin = Math.max(0, Math.round((Date.now() - d.getTime()) / 60_000));
  const ageHr = Math.round(ageMin / 60);
  const ageDays = Math.round(ageHr / 24);
  const rel =
    ageMin < 1 ? 'just now'
    : ageMin < 60 ? `${ageMin} min ago`
    : ageHr < 48 ? `${ageHr} hr ago`
    : `${ageDays} days ago`;
  const abs = `${formatDay(d, {
    timeZone: tz,
    intl: { year: undefined, month: 'short', day: 'numeric' },
  })} · ${formatTime(d, { timeZone: tz })}`;
  return `${rel} · ${abs}`;
}

/**
 * Progress fragment for an in-flight history backfill.
 *
 * Days-only progress ("0/20") sits still for the whole first day —
 * one day is 3 throttled, paginated Samsara fetches and can take
 * minutes on a large fleet — which reads as a hang.  When the status
 * carries batch counters (one unit per stat-batch fetch) the label
 * adds a percentage that moves from the first minute:
 * "3/20 days · 17%".  Falls back to days-only for older payloads.
 */
export function backfillProgressLabel(
  s: {
    days_done?: number;
    days_total?: number;
    days_requested?: number;
    batches_done?: number;
    batches_total?: number;
  },
  fallbackTotal?: number,
): string {
  const days =
    `${s.days_done ?? 0}/${s.days_total ?? s.days_requested ?? fallbackTotal ?? 0} days`;
  if (s.batches_total && s.batches_total > 0) {
    const pct = Math.min(
      100, Math.round(((s.batches_done ?? 0) / s.batches_total) * 100),
    );
    return `${days} · ${pct}%`;
  }
  return days;
}

/**
 * Format a feature toggle's cadence into a human string.
 * Returns empty string when no interval is configured (e.g. the
 * history_backfill capability which is one-shot, not periodic).
 */
export function formatCadence(toggle: {
  interval_sec?: number;
  interval_min?: number;
  interval_hour?: number;
  cron?: string;
}): string {
  if (toggle.interval_sec) {
    if (toggle.interval_sec < 60) return `every ${toggle.interval_sec} sec`;
    return `every ${Math.round(toggle.interval_sec / 60)} min`;
  }
  if (toggle.interval_min) return `every ${toggle.interval_min} min`;
  if (toggle.interval_hour) {
    return toggle.interval_hour === 1 ? 'every hour' : `every ${toggle.interval_hour} hr`;
  }
  if (toggle.cron) return toggle.cron;
  return '';
}
