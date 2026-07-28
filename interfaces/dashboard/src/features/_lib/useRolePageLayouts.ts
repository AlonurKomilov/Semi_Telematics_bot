/**
 * The account's role-default page layouts (tier two) — one bulk read,
 * shared through react-query so every configurable page and the gear's
 * manager block see the same rows without extra requests.
 *
 * Readable by anyone: each person needs their own role's default to
 * render their page, and the rows hold nothing but section ids.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';

interface PageLayoutsResponse {
  layouts: Record<string, Record<string, string[]>>;
}

const KEY = ['page-layouts'] as const;

export function useRolePageLayouts(): {
  /** role → feature → sections, or undefined while loading. */
  layouts: PageLayoutsResponse['layouts'] | undefined;
} {
  const q = useQuery<PageLayoutsResponse>({
    queryKey: KEY,
    queryFn: () => apiJSON<PageLayoutsResponse>('/page-layouts'),
    // Team defaults change rarely (a manager action), so a stale minute
    // is fine — and every configurable page shares this one entry.
    staleTime: 60_000,
  });
  return { layouts: q.data?.layouts };
}

/** The gear's manager block: save / clear a role's team default. */
export function useRoleLayoutMutations(role: string, feature: string): {
  save: (sections: string[]) => void;
  clear: () => void;
  busy: boolean;
} {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (sections: string[]) => apiJSON(
      `/page-layouts/${encodeURIComponent(role)}/${encodeURIComponent(feature)}`,
      { method: 'PUT', body: { sections } },
    ),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
  const clear = useMutation({
    mutationFn: () => apiJSON(
      `/page-layouts/${encodeURIComponent(role)}/${encodeURIComponent(feature)}`,
      { method: 'DELETE' },
    ),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
  return {
    save: (sections) => save.mutate(sections),
    clear: () => clear.mutate(),
    busy: save.isPending || clear.isPending,
  };
}
