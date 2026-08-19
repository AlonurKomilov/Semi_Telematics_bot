/**
 * The Days cell's hover breakdown — period days, inactive, counted,
 * loaded — as a LIST that reads as the calculation.  Sheet and board
 * both render this, so the two views can never tell different day
 * stories.  Sibling helpers (the cell phrase, the loaded-day count):
 * [explain.ts](explain.ts).
 *
 * One vocabulary everywhere: noun "inactive", verb pair "mark
 * inactive" ↔ "make it count" — a second name for one state on one
 * screen is a comprehension tax.
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
  /** The make-it-count coaching line only makes sense while marking is open. */
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
  const loaded = loadedDayCount(loads, row.window_start, row.window_end);
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
          {t('kpi_runs.dt_off4', '− {{n}} inactive · {{why}}', {
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
            /* Natural phrasing, valid for 2, 7 and 14 counted days —
               "÷ 7 ×" read as arithmetic homework. */
            <> {t('kpi_runs.dt_target4', '→ target {{tgt}} ({{a}} days at the {{wk}}/week rate)', {
              tgt: usd2(row.adjusted_target), a: active,
              wk: usd2(row.weekly_target),
            })}</>
          ) : (
            /* 7 counted → the target IS the weekly number. */
            <> {t('kpi_runs.dt_target3', '→ target {{tgt}}', {
              tgt: usd2(row.adjusted_target) })}</>
          )
        )}
      </div>
      {loaded != null && (
        <div>{loaded === 1
          ? t('kpi_runs.dt_loaded_one2', '• 1 day covered by a load (pickup → delivery)')
          : t('kpi_runs.dt_loaded2', '• {{n}} days covered by loads (pickup → delivery)', { n: loaded })}</div>
      )}
      {draft && off > 0 && (
        /* The bubble is the INVERTED surface (bg-foreground /
           text-background), so "muted" here is background at reduced
           alpha — text-muted-foreground would sink into the fill. */
        <div className="pt-0.5 text-background/70">
          {row.weekly_target != null
            ? t('kpi_runs.dt_unmark4',
              'If the truck was available, click its inactive day on the board to make it count again (target +{{d}} per day).',
              { d: `$${Math.round(row.weekly_target / 7).toLocaleString()}` })
            : t('kpi_runs.dt_unmark5',
              'If the truck was available, click its inactive day on the board to make it count again.')}
        </div>
      )}
    </div>
  );
}
