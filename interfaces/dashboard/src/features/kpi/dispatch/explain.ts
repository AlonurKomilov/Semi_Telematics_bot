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

export const nextDay = (iso: string) =>
  new Date(Date.parse(`${iso}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);

/** Days COVERED by a load — pickup through delivery, clamped to the
 *  window.  Counting only pickup days made a Friday long-haul's
 *  weekend transit look like dispatcher idle ("1 loaded" on a truck
 *  that rolled all week).  null while the loads query is in flight
 *  (unknown ≠ zero). */
export function loadedDayCount(
  loads: RunLoad[] | undefined, winStart: string, winEnd: string,
): number | null {
  if (loads === undefined) return null;
  return coveredDays(loads, winStart, winEnd).size;
}

/** The set version — the board shades covered-but-no-pickup days as
 *  "in transit" so nobody marks a rolling day inactive. */
export function coveredDays(
  loads: RunLoad[], winStart: string, winEnd: string,
): Set<string> {
  const lo = winStart.slice(0, 10);
  const hi = winEnd.slice(0, 10);
  const days = new Set<string>();
  for (const l of loads) {
    const a = (l.pickup_date || '').slice(0, 10);
    if (!a) continue;
    const b = (l.delivery_date || '').slice(0, 10);
    const stopRaw = b && b > a ? b : a;
    const stop = stopRaw > hi ? hi : stopRaw;
    for (let d = a < lo ? lo : a; d <= stop; d = nextDay(d)) days.add(d);
  }
  return days;
}

/** The Days cell phrase — words label the numbers inline ("2 of 7
 *  days · 1 loaded"), because the slash triple pattern-matched as a
 *  DATE on a grid full of them, and even a careful reader misread the
 *  third number with the legend on screen. */
export function daysCell(row: RunRow, loaded: number | null, t: TFunction): string {
  const total = Number(row.total_days);
  const active = Math.max(0, total - Number(row.inactive_days));
  const head = t('kpi_runs.days_phrase', '{{a}} of {{p}} days', { a: active, p: total });
  if (loaded == null) return head;
  return `${head} · ${loaded === 1
    ? t('kpi_runs.days_covered_one', '1 covered')
    : t('kpi_runs.days_covered_n', '{{n}} covered', { n: loaded })}`;
}

/** The cell's breakdown as ONE plain sentence — the accessible name
 *  for the Days control (the visual list rides the hover Tip). */
export function daysSummary(row: RunRow, loaded: number | null, t: TFunction): string {
  const total = Number(row.total_days);
  const off = Number(row.inactive_days);
  const active = Math.max(0, total - off);
  const parts = [
    t('kpi_runs.ds_total', '{{n}} period days', { n: total }),
  ];
  if (off > 0) {
    parts.push(t('kpi_runs.ds_off', '{{n}} marked inactive', { n: off }));
  }
  parts.push(t('kpi_runs.ds_counted', '{{n}} counted', { n: active }));
  if (row.weekly_target != null) {
    parts.push(t('kpi_runs.ds_target', 'target ${{v}}',
      { v: Math.round(row.adjusted_target).toLocaleString() }));
  }
  if (loaded != null) {
    parts.push(t('kpi_runs.ds_covered', '{{n}} covered by loads', { n: loaded }));
  }
  return parts.join(', ');
}


/** A row a person actually touched — generation leaves no fingerprint
 *  (adjusted_by NULL), so auto-excused rows are NOT adjustments. */
export function isHandAdjusted(r: RunRow): boolean {
  return r.adjusted_by != null || r.override_pct != null
    || Number(r.extras) !== 0;
}
