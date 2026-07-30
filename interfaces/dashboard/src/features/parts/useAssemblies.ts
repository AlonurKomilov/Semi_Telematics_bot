/**
 * The assembly vocabulary (level 2 of System → Assembly → Part), in
 * the one shape every picker needs: grouped under its parent system.
 *
 * Fetched, never hardcoded — a second copy of a vocabulary in a
 * frontend is exactly how the task list drifted into three disagreeing
 * versions. Lives in the parts feature because `parts_catalog` owns
 * the column; Service Tasks imports it the same way Work Orders
 * imports the task picker.
 *
 * The GROUPING is the point: a flat list of 112 assemblies teaches
 * nothing, while `Cooling System → Radiator / Water Pump / Thermostat`
 * shows the tree at the moment someone is choosing from it.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import { SYSTEMS_KEY, fetchTaskSystems } from '../service-tasks/api';

export interface Assembly { key: string; label: string; system_key: string }
export interface AssemblyGroup { systemKey: string; systemLabel: string; items: Assembly[] }

export const ASSEMBLIES_KEY = ['part-assemblies'] as const;

export function useAssemblies(): {
  all: Assembly[];
  /** key → label, for rendering a stored value. */
  labelOf: Map<string, string>;
  /** Systems in the server's order, each with its assemblies. */
  groups: AssemblyGroup[];
  /** Just one system's assemblies — for a picker already bound to it. */
  forSystem: (systemKey: string) => Assembly[];
} {
  const { data } = useQuery<{ assemblies: Assembly[] }>({
    queryKey: ASSEMBLIES_KEY,
    queryFn: () => apiJSON('/parts/assemblies'),
    staleTime: 5 * 60_000,
  });
  // System LABELS come from the task feature's endpoint — the same one
  // source both levels already read, so the two can't disagree.
  const { data: sysData } = useQuery({
    queryKey: SYSTEMS_KEY, queryFn: fetchTaskSystems, staleTime: 5 * 60_000,
  });

  const all = useMemo(() => data?.assemblies ?? [], [data]);

  const labelOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of all) m.set(a.key, a.label);
    return m;
  }, [all]);

  const groups = useMemo(() => {
    const bySystem = new Map<string, Assembly[]>();
    for (const a of all) {
      const list = bySystem.get(a.system_key) ?? [];
      list.push(a);
      bySystem.set(a.system_key, list);
    }
    // Server order for systems (the reporting axis's own order), and
    // any system the list doesn't name falls to the end under its key
    // rather than vanishing.
    const out: AssemblyGroup[] = [];
    for (const sy of sysData?.systems ?? []) {
      const items = bySystem.get(sy.key);
      if (items?.length) {
        out.push({ systemKey: sy.key, systemLabel: sy.label, items });
        bySystem.delete(sy.key);
      }
    }
    for (const [key, items] of bySystem) {
      out.push({ systemKey: key, systemLabel: key, items });
    }
    return out;
  }, [all, sysData]);

  const forSystem = useMemo(() => {
    const bySystem = new Map<string, Assembly[]>();
    for (const a of all) {
      const list = bySystem.get(a.system_key) ?? [];
      list.push(a);
      bySystem.set(a.system_key, list);
    }
    return (systemKey: string) => bySystem.get(systemKey) ?? [];
  }, [all]);

  return { all, labelOf, groups, forSystem };
}
