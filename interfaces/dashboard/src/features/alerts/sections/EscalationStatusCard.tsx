/**
 * Owner/admin oversight card — past-due + breached escalation counts.
 *
 * Pulls /alerts/escalations once on mount (refreshes when refocused);
 * one server-aggregated number per concept means no client-side
 * arithmetic and no walking the queue.  The by_persona breakdown
 * surfaces "which team is sitting on the backlog" so an owner can
 * triage by ping rather than scrolling.
 *
 * Permission-gated by ``can_alerts_all`` on the server.  Personas
 * that don't have it (driver, accounting) get 403 and we hide the
 * card silently — same pattern as SafetySummaryStrip's coaching
 * fallback.
 */
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Clock, Bell } from 'lucide-react';
import { apiJSON, ApiError } from '../../../api/client';

interface EscalationSummary {
  past_due_count:           number;
  breached_count:           number;
  by_persona:               Record<string, number>;
  reescalate_after_minutes: number;
  reescalate_max_attempts:  number;
}

const PERSONA_LABEL_KEYS: Record<string, string> = {
  owner_admin: 'alerts.escalations.persona_owner_admin',
  dispatcher:  'alerts.escalations.persona_dispatcher',
  safety:      'alerts.escalations.persona_safety',
  fleet:       'alerts.escalations.persona_fleet',
  hr:          'alerts.escalations.persona_hr',
};

function useEscalations() {
  return useQuery<EscalationSummary | null>({
    queryKey: ['admin', 'escalations'],
    queryFn: async () => {
      try {
        return await apiJSON<EscalationSummary>('/alerts/escalations');
      } catch (e) {
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
          return null;
        }
        throw e;
      }
    },
    staleTime: 30_000,
  });
}

export default function EscalationStatusCard() {
  const { t } = useTranslation();
  const { data, isLoading } = useEscalations();
  if (!isLoading && !data) return null;  // permission-hidden

  const pastDue = data?.past_due_count ?? 0;
  const breached = data?.breached_count ?? 0;
  const ageMin = data?.reescalate_after_minutes ?? 60;
  const personas = data?.by_persona ?? {};
  const personaRows = Object.entries(personas)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  return (
    <section className="mb-4 bg-card border border-border rounded-xl p-5">
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-foreground inline-flex items-center gap-2">
          <Bell size={16} className="text-muted-foreground" />
          {t('alerts.escalations.title')}
        </h2>
        <span className="text-2xs text-muted-foreground">
          {isLoading ? '…' : t('alerts.escalations.older_than_n_min', { n: ageMin })}
        </span>
      </header>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="flex items-baseline gap-2">
            <span className={`text-3xl font-bold tabular-nums ${
              pastDue > 0 ? 'text-warn' : 'text-foreground'
            }`}>
              {isLoading ? '…' : pastDue}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('alerts.escalations.past_due')}
            </span>
          </div>
          <p className="text-2xs text-muted-foreground mt-0.5">
            {t('alerts.escalations.past_due_hint', { n: ageMin })}
          </p>
        </div>
        <div>
          <div className="flex items-baseline gap-2">
            <span className={`text-3xl font-bold tabular-nums ${
              breached > 0 ? 'text-danger' : 'text-foreground'
            }`}>
              {isLoading ? '…' : breached}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('alerts.escalations.breached')}
            </span>
          </div>
          <p className="text-2xs text-muted-foreground mt-0.5 inline-flex items-center gap-1">
            <Clock size={12} />
            {t('alerts.escalations.breached_hint')}
          </p>
        </div>
      </div>

      {personaRows.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border">
          <p className="text-3xs uppercase tracking-wide text-muted-foreground mb-2">
            {t('alerts.escalations.past_due_by_persona')}
          </p>
          <div className="flex flex-wrap gap-2">
            {personaRows.map(([persona, n]) => (
              <span
                key={persona}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-muted text-xs"
              >
                <span className="text-foreground font-medium">
                  {PERSONA_LABEL_KEYS[persona] ? t(PERSONA_LABEL_KEYS[persona]) : persona}
                </span>
                <span className="text-muted-foreground tabular-nums">{n}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
