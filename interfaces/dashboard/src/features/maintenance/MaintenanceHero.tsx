/**
 * MaintenanceHero — feature-scoped topbar chips shown while the
 * operator is ON a /maintenance route (see shells/heroes/ShellHero).
 *
 *   Overdue · Due soon · Pending · Completed
 *
 * Reads the SAME react-query cache entry as the Tasks page (via
 * useMaintenanceTasksQuery) and the same bucket classifier, so the
 * numbers up here always equal the chip counts on the page — no
 * second computation, no extra request (the page is fetching this
 * data anyway; whoever mounts first primes the shared cache).
 */
import { useMemo } from 'react';
import HeroChip, { HERO_STRIP } from '../../shells/heroes/HeroChip';
import { useMaintenanceTasksQuery, classifyTaskBuckets } from './useMaintenanceTasks';

export default function MaintenanceHero() {
  const { data, isLoading, isError } = useMaintenanceTasksQuery();
  const buckets = useMemo(
    () => classifyTaskBuckets(data?.tasks ?? []),
    [data],
  );
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading maintenance…</span>
      </div>
    );
  }
  return (
    <div className={HERO_STRIP}>
      <HeroChip label="Overdue" value={buckets.overdue.length} tone="critical" title="Past due on any axis — date, mileage, or engine hours" />
      <HeroChip label="Due soon" value={buckets.dueSoon.length} tone="warning" title="Within 7 days / 5,000 mi / 100 engine hours of a threshold" />
      <HeroChip label="Pending" value={buckets.pending.length} tone="info" />
      <HeroChip label="Completed" value={buckets.completed.length} tone="positive" />
    </div>
  );
}
