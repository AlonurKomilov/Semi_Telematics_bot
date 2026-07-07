/**
 * Shared team-members query — ONE react-query entry used by both the
 * Team Management page and the topbar TeamHero, so the hero's counts
 * always equal the page's rows (same cache, no second fetch, no
 * drift).  Same contract as maintenance/useMaintenanceTasks.ts and
 * applications/useApplications.ts.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import type { AdminUser } from '../../types';

export function useTeamMembersQuery() {
  return useQuery({
    queryKey: ['admin-users'],
    queryFn: () => apiJSON<{ users: AdminUser[] }>('/admin/users'),
  });
}

/** Derived sign-in lifecycle ("pending" = provisioned but can't sign
 *  in yet).  Same ``?? 'active'`` fallback the page's Status column
 *  and segment tabs use. */
export function memberLifecycleOf(u: AdminUser): 'active' | 'pending' | 'inactive' {
  const v = (u as AdminUser & { lifecycle?: string }).lifecycle;
  return v === 'pending' || v === 'inactive' ? v : 'active';
}
