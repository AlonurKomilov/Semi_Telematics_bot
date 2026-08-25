/**
 * Shared team-members query — ONE react-query entry, so every surface
 * that counts members counts the same rows (same cache, no second
 * fetch, no drift).  Same contract as maintenance/useMaintenanceTasks.ts
 * and applications/useApplications.ts.
 *
 * Lives in hooks/ rather than features/settings/ because it stopped
 * being one feature's business: the Team page, the topbar hero and the
 * Overview onboarding checklist all ask the same question, and no
 * feature owns another feature's imports.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../api/client';
import type { AdminUser } from '../types';

export function useTeamMembersQuery(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['admin-users'],
    queryFn: () => apiJSON<{ users: AdminUser[] }>('/admin/users'),
    // Callers that only need the count for something the reader may
    // already have dismissed pass `enabled` and cost nothing.
    enabled: opts?.enabled ?? true,
  });
}

/** Derived sign-in lifecycle ("pending" = provisioned but can't sign
 *  in yet).  Same ``?? 'active'`` fallback the page's Status column
 *  and segment tabs use. */
export function memberLifecycleOf(u: AdminUser): 'active' | 'pending' | 'inactive' {
  const v = (u as AdminUser & { lifecycle?: string }).lifecycle;
  return v === 'pending' || v === 'inactive' ? v : 'active';
}
