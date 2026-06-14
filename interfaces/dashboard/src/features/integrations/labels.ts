/**
 * Human-readable labels for the backend's stable capability ids.
 *
 * Kept here (not in i18n) because they describe a vendor-facing
 * concept that's the same regardless of the dashboard language —
 * "Vehicle State Sync" is what fleet ops everywhere call it.
 * Future i18n keys can wrap these without changing the call sites.
 */

export const CAPABILITY_LABELS: Record<string, string> = {
  vehicle_state:            'Live vehicle state',
  safety_events:            'Safety events',
  vehicle_health:           'Vehicle health',
  vehicle_faults:           'Fault codes',
  driver_efficiency_daily:  'Driver efficiency (daily)',
  fleet_weather:            'Weather overlay',
  fleet_efficiency:         'Fleet efficiency',
  geofence_definitions:     'Geofence definitions',
  state_snapshot_history:   'Vehicle state history (5-min)',
  telemetry_hourly:         'Hourly roll-up',
  metrics_daily:            'Daily roll-up',
  history_prune:            'History retention prune',
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

/** Format an ISO timestamp like ``"2026-06-07T14:22:35+00:00"`` into
 *  the relative + absolute form the cards show ("2 hr ago · Jun 7,
 *  14:22 UTC").  Empty/invalid input → empty output.  Shared by the
 *  Samsara backfill line and the Datatruck synced-data rows so the
 *  same stamp renders identically across providers. */
export function formatSyncTimestamp(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const ageMin = Math.round((Date.now() - d.getTime()) / 60_000);
  const ageHr = Math.round(ageMin / 60);
  const ageDays = Math.round(ageHr / 24);
  const rel =
    ageMin < 60 ? `${ageMin} min ago`
    : ageHr < 48 ? `${ageHr} hr ago`
    : `${ageDays} days ago`;
  const abs = d.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric',
  }) + ' · ' + d.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
  }) + ' UTC';
  return `${rel} · ${abs}`;
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
