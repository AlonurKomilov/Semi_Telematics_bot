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
};

export function capabilityLabel(id: string): string {
  return CAPABILITY_LABELS[id] || id;
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
