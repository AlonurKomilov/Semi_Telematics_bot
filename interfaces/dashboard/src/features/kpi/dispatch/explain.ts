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
import type { RunRow } from '../api';

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
