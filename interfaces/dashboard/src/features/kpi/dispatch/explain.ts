/**
 * The paid-row explanation — one sentence for "why does this row pay
 * THIS percent", shared by the sheet's KPI % column and the board's
 * row header so the two views can never tell different stories.
 *
 * The zeroed rows explain themselves via ``zero_reason``; the server
 * sends ``matched_rule`` (the snapshot rule that priced the row) for
 * the paid ones — this file only puts words on it.
 */
import type { TFunction } from 'i18next';
import type { RunLoad, RunRow } from '../api';

const usd0 = (v: number) => `$${Math.round(v).toLocaleString()}`;

export function matchedTip(rule: NonNullable<RunRow['matched_rule']>, t: TFunction): string {
  if (rule.model === 'ladder') {
    const conds: string[] = [];
    if (rule.requires_target) {
      conds.push(t('kpi_runs.mr_target', 'period target met'));
    }
    if (rule.min_weekly_gross != null) {
      conds.push(t('kpi_runs.mr_gross', '≥ {{g}}/wk gross',
        { g: usd0(rule.min_weekly_gross) }));
    }
    if (rule.min_rpm != null) {
      conds.push(t('kpi_runs.mr_rpm', '≥ {{r}} RPM',
        { r: rule.min_rpm.toFixed(2) }));
    }
    if (conds.length === 0) {
      return t('kpi_runs.mr_base', 'Pays {{p}}% — the base tier (no conditions).',
        { p: rule.pct });
    }
    return t('kpi_runs.mr_ladder', 'Pays {{p}}% — matched the tier: {{conds}}.',
      { p: rule.pct, conds: conds.join(' · ') });
  }
  if (rule.model === 'hybrid') {
    return t('kpi_runs.mr_hybrid',
      'Pays {{p}}% — in the range {{ga}}–{{gb}}/wk gross at {{ra}}–{{rb}} RPM.',
      { p: rule.pct,
        ga: usd0(rule.gross_min), gb: usd0(rule.gross_max),
        ra: rule.rpm_min.toFixed(2), rb: rule.rpm_max.toFixed(2) });
  }
  const how = rule.combine_rule === 'lower'
    ? t('kpi_runs.mr_lower', 'the lower wins')
    : rule.combine_rule === 'higher'
      ? t('kpi_runs.mr_higher', 'the higher wins')
      : t('kpi_runs.mr_add', 'they add');
  return t('kpi_runs.mr_fixed',
    'Pays {{p}}% — RPM ladder {{r}}% and gross ladder {{g}}%, {{how}}.',
    { p: rule.pct, r: rule.rpm_pct, g: rule.gross_pct, how });
}

/** Distinct pickup days — the "loads getted days" of the P/A/L trio.
 *  null while the loads query is still in flight (unknown ≠ zero). */
export function loadedDayCount(loads: RunLoad[] | undefined): number | null {
  if (loads === undefined) return null;
  return new Set(loads.map((l) => l.pickup_date.slice(0, 10))).size;
}

/** The Days cell's three numbers: period / counted / loaded. */
export function daysCell(row: RunRow, loaded: number | null): string {
  const total = Number(row.total_days);
  const active = Math.max(0, total - Number(row.inactive_days));
  return loaded == null
    ? `${total}/${active}`
    : `${total}/${active}/${loaded}`;
}

/** Generation's auto-excuse reasons (runs.py stamps them; edge days
 *  outside the truck's load span).  Everything else is a HUMAN mark. */
export const AUTO_EXCUSE_REASONS: readonly string[] = [
  'before first load', 'after last load',
];

/** Days a PERSON marked inactive — the auto edge-excuses don't count
 *  as someone's decision.  Legacy rows (typed number, no day list)
 *  count only when a human fingerprint (adjusted_by) exists. */
export function handMarkedDays(r: RunRow): number {
  if (r.inactive_dates.length > 0) {
    return r.inactive_dates.filter(
      (m) => !AUTO_EXCUSE_REASONS.includes(m.reason)).length;
  }
  return r.adjusted_by != null ? r.inactive_days : 0;
}

/** A row a person actually touched — generation leaves no fingerprint
 *  (adjusted_by NULL), so auto-excused rows are NOT adjustments. */
export function isHandAdjusted(r: RunRow): boolean {
  return r.adjusted_by != null || r.override_pct != null
    || Number(r.extras) !== 0;
}
