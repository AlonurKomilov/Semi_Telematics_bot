/**
 * Shared stats hook for shell hero strips.
 *
 * All three per-role hero strips (FleetHero / DispatchHero /
 * SafetyHero) draw from the same ``/overview/stats`` endpoint —
 * each hero just emphasizes different fields.  Using one shared hook
 * means the React Query cache is hit once per dashboard session
 * regardless of which persona is active; switching persona doesn't
 * trigger a refetch.
 *
 * The endpoint already exists and is role-aware on the server side —
 * it returns ``can_faults`` / ``can_fuel`` / ``can_maintenance_*``
 * gated fields based on the caller's permissions.  So a Safety user
 * who can't see fuel data won't get ``low_fuel`` in the response, and
 * the hero just hides that chip.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import type { DashboardStats } from '../../types';

export function useShellStats(enabled = true) {
  return useQuery<DashboardStats>({
    queryKey: ['shell', 'overview-stats'],
    queryFn: () => apiJSON<DashboardStats>('/overview/stats'),
    // Callers with no use for the stats (e.g. the Notifications bell for a
    // role with no alerts access) pass enabled=false so they don't trigger
    // the /overview/stats fetch just to compute a badge forced to 0.
    enabled,
    // 60s stale matches Overview.tsx's policy.  The hero is persistent
    // background context, not a tab the user actively watches, so a
    // minute of staleness is fine; eyes-on-the-data live in the page
    // proper (Live Map, Alerts, etc.) which has its own polling.
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}
