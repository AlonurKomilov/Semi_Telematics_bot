/**
 * The KPI section map — SSOT for the per-role split.
 *
 * THE FUNDAMENT (owner decision, 2026-08-11): KPI grades EMPLOYEES,
 * never assets.  Every row in every section is a PERSON — what that
 * worker produced against the target the company gave them.  A fleet
 * manager's grade may be COMPUTED THROUGH asset numbers (utilization,
 * downtime response), but the row is the manager, the target is
 * theirs, the grade is theirs.  Vehicle/asset analytics as a subject
 * belong to Reports and the warehouse surfaces — never under KPI.
 *
 * One section per employee GROUP, each its own page + backend package
 * (features/kpi/<section>/), so one section's changes can never take
 * effect on another's.  Section keys are role words ON PURPOSE: a
 * section whose subject is the role's employees is a genuinely
 * per-role artifact, which is exactly where the persona rule allows
 * them (/kpi/dispatch set the precedent).
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
  { key: 'fleet', label: 'Fleet', path: '/kpi/fleet', ready: false },
  { key: 'safety', label: 'Safety', path: '/kpi/safety', ready: false },
  { key: 'hr', label: 'HR', path: '/kpi/hr', ready: false },
  { key: 'drivers', label: 'Drivers', path: '/kpi/drivers', ready: false },
];

const VIEW_TO_SECTION: Record<string, string> = {
  dispatch: 'dispatch',
  fleet: 'fleet',
  safety: 'safety',
  hr: 'hr',
};

/** The section a role view lands on — first READY match, else dispatch
 *  (the only built section; the switcher shows what's coming). */
export function viewSection(activeView: string): KpiSection {
  const key = VIEW_TO_SECTION[activeView];
  const hit = KPI_SECTIONS.find((s) => s.key === key && s.ready);
  return hit ?? KPI_SECTIONS[0];
}
