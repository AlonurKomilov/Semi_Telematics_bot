/**
 * The Days cell's legend — the P/A/L breakdown (period / counted /
 * loaded) as a LIST, one row per number in the same order the cell
 * prints them.  Sheet and board both render this, so the two views
 * can never tell different day stories.  Sibling helpers (the cell
 * string, the loaded-day count): [explain.ts](explain.ts).
 *
 * Vocabulary is the BOARD's ("marked inactive", "clear the mark") —
 * a tooltip that says "excused" while every other surface says
 * "inactive" would teach a second name for one concept.
 */
import type { ReactNode } from 'react';
import type { TFunction } from 'i18next';
import type { RunLoad, RunRow } from '../api';
import { loadedDayCount } from './explain';

// Cents — the Target column shows cents, and one value rendered with
// two roundings on one screen reads as two different numbers.
const usd2 = (v: number) => `$${Number(v).toLocaleString(undefined, {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})}`;

export function DaysTipContent({ row, loads, draft, periodStart, periodEnd, t }: {
  row: RunRow;
  loads: RunLoad[] | undefined;
  /** The clear-mark coaching line only makes sense while marking is open. */
  draft: boolean;
  /** The RUN's period — old rows (and API-edited ones) can carry a
   *  window SHORTER than the period, and calling those days "period
   *  days" would be false. */
  periodStart: string;
  periodEnd: string;
  t: TFunction;
}): ReactNode {
  const total = Number(row.total_days);
  const off = Number(row.inactive_days);
  const active = Math.max(0, total - off);
  const loaded = loadedDayCount(loads);
  const isFullPeriod = row.window_start.slice(0, 10) === periodStart.slice(0, 10)
    && row.window_end.slice(0, 10) === periodEnd.slice(0, 10);
  return (
    <div className="space-y-0.5 text-left tabular-nums">
      <div>
        {isFullPeriod
          ? t('kpi_runs.dt_total', '{{n}} period days ({{a}} – {{b}})', {
              n: total,
              a: row.window_start.slice(0, 10), b: row.window_end.slice(0, 10),
            })
          : t('kpi_runs.dt_total_w', '{{n}} window days ({{a}} – {{b}} — an older run; new runs count the whole period)', {
              n: total,
              a: row.window_start.slice(0, 10), b: row.window_end.slice(0, 10),
            })}
      </div>
      {off > 0 && (
        <div>
          {t('kpi_runs.dt_off3', '− {{n}} marked inactive — {{why}}', {
            n: off,
            why: row.inactive_reason || t('kpi_board.inactive', 'inactive'),
          })}
        </div>
      )}
      <div>
        {/* The '=' names a subtraction — with nothing subtracted it
            would be an orphan equals-sign. */}
        {off > 0
          ? t('kpi_runs.dt_counted', '= {{n}} counted', { n: active })
          : t('kpi_runs.dt_counted2', '{{n}} counted', { n: active })}
        {row.weekly_target != null && (
          active !== 7 ? (
            <> {t('kpi_runs.dt_target2', '→ target {{tgt}} (weekly {{wk}} ÷ 7 × {{a}})', {
              tgt: usd2(row.adjusted_target), a: active,
              wk: usd2(row.weekly_target),
            })}</>
          ) : (
            /* 7 counted → target IS the weekly number; the formula
               would be "(÷ 7 × 7)" noise.  (A 14-day run with zero
               marks still shows "÷ 7 × 14" — that one informs.) */
            <> {t('kpi_runs.dt_target3', '→ target {{tgt}}', {
              tgt: usd2(row.adjusted_target) })}</>
          )
        )}
      </div>
      {loaded != null && (
        <div>{loaded === 1
          ? t('kpi_runs.dt_loaded_one', '• 1 day with loads')
          : t('kpi_runs.dt_loaded', '• {{n}} days with loads', { n: loaded })}</div>
      )}
      {draft && off > 0 && (
        /* The bubble is the INVERTED surface (bg-foreground /
           text-background), so "muted" here is background at reduced
           alpha — text-muted-foreground would sink into the fill. */
        <div className="pt-0.5 text-background/70">
          {row.weekly_target != null
            ? t('kpi_runs.dt_unmark3',
              'Click a marked day on the board and clear the mark if the truck was available — the day counts again (target +{{d}} per day).',
              { d: usd2(row.weekly_target / 7) })
            : t('kpi_runs.dt_unmark2',
              'Click a marked day on the board and clear the mark if the truck was available — the day counts again.')}
        </div>
      )}
    </div>
  );
}
