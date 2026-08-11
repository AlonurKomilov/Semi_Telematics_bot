/**
 * The KPI section map — SSOT for the per-role split.
 *
 * One section per graded domain, each its own PAGE + backend package
 * (features/kpi/<section>/), so one role's KPI can never take effect on
 * another's.  Naming follows the persona rule: paths and keys use the
 * DOMAIN noun (vehicles, not fleet); role-flavored labels are what the
 * role's view renders, not what the identifier says.
 *
 * `viewSection` maps the active role view to its home section — the
 * same "view dashboard as" mechanism the shells use, applied to KPI.
 */

export interface KpiSection {
  key: string;
  label: string;
  path: string;
  ready: boolean;
}

export const KPI_SECTIONS: KpiSection[] = [
  { key: 'dispatch', label: 'Dispatch', path: '/kpi/dispatch', ready: true },
  { key: 'vehicles', label: 'Vehicles', path: '/kpi/vehicles', ready: false },
  { key: 'safety', label: 'Safety', path: '/kpi/safety', ready: false },
  { key: 'drivers', label: 'Drivers', path: '/kpi/drivers', ready: false },
];

const VIEW_TO_SECTION: Record<string, string> = {
  dispatch: 'dispatch',
  fleet: 'vehicles',
  safety: 'safety',
  hr: 'drivers',
};

/** The section a role view lands on — first READY match, else dispatch
 *  (the only built section; the switcher shows what's coming). */
export function viewSection(activeView: string): KpiSection {
  const key = VIEW_TO_SECTION[activeView];
  const hit = KPI_SECTIONS.find((s) => s.key === key && s.ready);
  return hit ?? KPI_SECTIONS[0];
}
