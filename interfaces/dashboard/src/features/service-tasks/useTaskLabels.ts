/**
 * One label source for task slugs everywhere OUTSIDE the Service
 * Tasks pages — report rows, vendor spend breakdowns, the maintenance
 * Type column.
 *
 * Those surfaces carry the task VALUE (canonical_key for a standard,
 * name for a custom, plus legacy free-typed slugs on old rows).  The
 * display name lives in the account's service_tasks list — the SSOT —
 * so labels are LOOKED UP, never hardcoded.  The hardcoded copies this
 * hook replaced (TASK_TYPE_OPTIONS / TYPE_LABELS in maintenance
 * badges) had already drifted: they said "Brake Inspection" for a
 * task really named "Brake Service", and knew nothing newer.
 *
 * Archived tasks are included on purpose — history keeps its label.
 * Shares the Service Tasks grid's query key, so it's a cache hit on
 * any page the user reached from there.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SERVICE_TASKS_KEY, fetchServiceTasks, taskValue } from './api';

export function useTaskLabels(): {
  /** value → display name, for direct map access. */
  byValue: Record<string, string>;
  /** Resolves any slug; de-slugs unknowns instead of showing raw keys. */
  labelOf: (slug: string) => string;
} {
  const { data } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, 'all'],
    queryFn: () => fetchServiceTasks(true),
    staleTime: 60_000,
  });

  const byValue = useMemo(() => {
    const m: Record<string, string> = {};
    for (const t of data?.service_tasks ?? []) m[taskValue(t)] = t.name;
    return m;
  }, [data]);

  const labelOf = useMemo(() => (slug: string): string => {
    if (!slug || slug === 'untagged') return 'Untagged';
    const hit = byValue[slug];
    if (hit) return hit;
    return slug.replace(/^custom_/, '').replace(/[_-]+/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }, [byValue]);

  return { byValue, labelOf };
}
