/**
 * Owner/admin account-wide alert volume histogram.
 *
 * Pulls /alerts/aggregate?days=7 — a server-aggregated count by
 * alert_type — and renders a compact horizontal bar chart.  Owner
 * sees "which alert categories drove this week" without scrolling
 * the queue.  Bars normalise against the largest bucket so a tiny
 * fleet's chart still shows shape.
 *
 * Clicking a bar narrows the queue to that alert type (writes
 * typeFilter to the URL via useAlertsFilters).
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BarChart3 } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import { useAlertsFilters } from '../_shared/useAlertsFilters';
import type { AlertType } from '../personaConfig';

interface AggregateResponse {
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
  total: number;
  days: number;
}

// Map server alert_type tokens to the URL-filter union.  Most are
// 1:1 except the singular/plural fault forms; we collapse them onto
// the URL token 'fault' (the filter chip's canonical value).
const SERVER_TO_URL: Record<string, AlertType> = {
  fault: 'fault', faults: 'fault',
  health: 'health',
  fuel: 'fuel',
  events: 'events', event: 'events',
  parking: 'parking',
};

// Display order: most operationally-noisy first so the bars render
// in a stable layout regardless of which bucket happens to be tallest
// this week.
const DISPLAY_ORDER = ['fault', 'health', 'fuel', 'events', 'parking', 'camera', 'scorecard', 'maintenance'];

const LABEL_KEY: Record<string, string> = {
  fault: 'alerts.volume_summary.type_faults',
  faults: 'alerts.volume_summary.type_faults',
  health: 'alerts.volume_summary.type_health',
  fuel: 'alerts.volume_summary.type_fuel',
  events: 'alerts.volume_summary.type_events',
  event: 'alerts.volume_summary.type_events',
  parking: 'alerts.volume_summary.type_parking',
  camera: 'alerts.volume_summary.type_camera',
  scorecard: 'alerts.volume_summary.type_scorecard',
  maintenance: 'alerts.volume_summary.type_maintenance',
};

function useAggregate(days: number) {
  return useQuery<AggregateResponse>({
    queryKey: ['alerts', 'aggregate', days],
    queryFn: () => apiJSON<AggregateResponse>(`/alerts/aggregate?days=${days}`),
    staleTime: 60_000,
  });
}

export default function AccountAlertSummary() {
  const { t } = useTranslation();
  const { data, isLoading } = useAggregate(7);
  const { setTypeFilter } = useAlertsFilters();

  const buckets = useMemo(() => {
    if (!data) return [];
    const seen = new Set<string>();
    const out: { key: string; label: string; count: number; urlValue?: AlertType }[] = [];
    const labelFor = (k: string) => (LABEL_KEY[k] ? t(LABEL_KEY[k]) : k);
    // Display-order first so the layout doesn't shuffle frame-to-frame.
    for (const k of DISPLAY_ORDER) {
      const count = data.by_type[k];
      if (count !== undefined && !seen.has(k)) {
        seen.add(k);
        out.push({
          key: k,
          label: labelFor(k),
          count,
          urlValue: SERVER_TO_URL[k],
        });
      }
    }
    // Tail: anything the server returned that wasn't in DISPLAY_ORDER.
    for (const [k, count] of Object.entries(data.by_type)) {
      if (!seen.has(k)) {
        out.push({
          key: k,
          label: labelFor(k),
          count,
          urlValue: SERVER_TO_URL[k],
        });
      }
    }
    return out;
  }, [data, t]);

  const max = buckets.reduce((m, b) => Math.max(m, b.count), 0);
  const days = data?.days ?? 7;

  return (
    <section className="mb-4 bg-card border border-border rounded-xl p-5">
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-foreground inline-flex items-center gap-2">
          <BarChart3 size={16} className="text-muted-foreground" />
          {t('alerts.volume_summary.title', { n: days })}
        </h2>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {isLoading ? '…' : t('alerts.volume_summary.total_count', { n: data?.total ?? 0 })}
        </span>
      </header>

      {isLoading ? (
        <div className="text-xs text-muted-foreground py-4">…</div>
      ) : buckets.length === 0 ? (
        <div className="text-xs text-muted-foreground py-4">
          {t('alerts.volume_summary.no_alerts_window', { n: days })}
        </div>
      ) : (
        <ul className="space-y-1.5">
          {buckets.map(b => (
            <li key={b.key}>
              <button
                type="button"
                onClick={() => b.urlValue && setTypeFilter(b.urlValue)}
                disabled={!b.urlValue}
                className={`w-full text-left grid grid-cols-[8rem_1fr_3rem] gap-3 items-center text-xs px-2 py-1.5 rounded transition ${
                  b.urlValue ? 'hover:bg-muted cursor-pointer' : 'cursor-default'
                }`}
              >
                <span className="text-muted-foreground truncate">{b.label}</span>
                <div className="h-2 bg-muted rounded overflow-hidden">
                  <div
                    className="h-full bg-primary/70 rounded"
                    style={{ width: max > 0 ? `${(b.count / max) * 100}%` : '0%' }}
                  />
                </div>
                <span className="text-foreground font-medium tabular-nums text-right">
                  {b.count}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
