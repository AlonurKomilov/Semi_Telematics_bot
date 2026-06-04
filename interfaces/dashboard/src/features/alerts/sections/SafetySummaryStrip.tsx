/**
 * Safety hero strip — events-today/week count + open-coaching backlog.
 *
 * Two independent queries (deduped by TanStack via stable keys):
 *   • /safety/events/summary?days=7 → {today, week, by_severity}
 *   • /coaching/assignments/count?status=open → {count}
 *
 * Both endpoints are dedicated count-only paths so the strip lands
 * before the heavier Alerts queue paints.  Clicking the open-coaching
 * card routes the user to /coaching (the assignments admin view).
 *
 * Permission-gated client-side: if coaching is disabled for the
 * account the /coaching/assignments/count endpoint 403s — we catch it
 * and hide the chip rather than surfacing an error banner.
 */
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, GraduationCap } from 'lucide-react';
import { KpiCard } from '../../../components/shell';
import { apiJSON, ApiError } from '../../../api/client';

interface SafetySummary {
  today: number;
  week: number;
  days: number;
  by_severity: Record<string, number>;
  by_type: Record<string, number>;
  timezone: string;
}

interface CountResponse {
  count: number;
  status: string;
}

function useSafetySummary() {
  return useQuery<SafetySummary>({
    queryKey: ['safety', 'events', 'summary', 7],
    queryFn: () => apiJSON<SafetySummary>('/safety/events/summary?days=7'),
    staleTime: 60_000,
  });
}

function useCoachingOpenCount() {
  return useQuery<CountResponse | null>({
    queryKey: ['coaching', 'assignments', 'count', 'open'],
    queryFn: async () => {
      try {
        return await apiJSON<CountResponse>(
          '/coaching/assignments/count?status=open',
        );
      } catch (e) {
        // 403 → coaching feature disabled for this account; hide the
        // chip silently rather than blocking the safety summary on a
        // feature flag the safety operator can't change.
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
          return null;
        }
        throw e;
      }
    },
    staleTime: 60_000,
  });
}

export default function SafetySummaryStrip() {
  const { t } = useTranslation();
  const events = useSafetySummary();
  const coaching = useCoachingOpenCount();
  const navigate = useNavigate();

  const todayCritical =
    (events.data?.by_severity?.high ?? 0) + (events.data?.by_severity?.critical ?? 0);
  const todayHint =
    todayCritical > 0
      ? t('alerts.safety_summary.high_severity_count', { n: todayCritical })
      : (events.data?.today ?? 0) > 0
        ? t('alerts.safety_summary.no_high_severity_today')
        : '—';

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      <KpiCard
        label={t('alerts.safety_summary.events_today')}
        value={events.isLoading ? '…' : events.data?.today ?? 0}
        tone={(events.data?.today ?? 0) > 0 ? 'warning' : 'default'}
        icon={ShieldAlert}
        hint={todayHint}
      />
      <KpiCard
        label={t('alerts.safety_summary.events_week')}
        value={events.isLoading ? '…' : events.data?.week ?? 0}
        tone={(events.data?.week ?? 0) > 0 ? 'info' : 'default'}
        icon={ShieldAlert}
        hint={t('alerts.safety_summary.last_n_days', { n: events.data?.days ?? 7 })}
      />
      {coaching.data && (
        <KpiCard
          label={t('alerts.safety_summary.open_coaching')}
          value={coaching.isLoading ? '…' : coaching.data.count}
          tone={(coaching.data.count ?? 0) > 0 ? 'warning' : 'default'}
          icon={GraduationCap}
          onClick={() => navigate('/coaching')}
          hint={
            (coaching.data.count ?? 0) > 0
              ? t('alerts.safety_summary.click_to_review_backlog')
              : t('alerts.safety_summary.no_open_assignments')
          }
        />
      )}
    </div>
  );
}
