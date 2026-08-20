/**
 * One truck's card in the board's static left pane — identity, day
 * coverage, the money in, the payout, and the meter.
 *
 * It knows nothing about bars or calendars: the days pane
 * ([DayCells.tsx](DayCells.tsx)) owns those, and the two only agree
 * on the row height in [shared.ts](shared.ts).
 */
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Tip } from '../../../../components/tooltip';
import { toneClasses } from '../../../../lib/status';
import type { RunLoad, RunRow } from '../../api';
import { daysCell, daysSummary, loadedDayCount, matchedTip, zeroTip } from '../explain';
import { DaysTipContent } from '../DaysTip';
import { ROW_CONTAIN, money0, usd } from './shared';

export const UnitCard = memo(function UnitCard({ row, loads, stale, clickable, alsoUnder, periodStart, periodEnd }: {
  row: RunRow;
  loads: RunLoad[] | undefined;
  /** Live loads no longer sum to this row's snapshot. */
  stale: boolean;
  /** The run is an editable draft — marking is open. */
  clickable: boolean;
  /** OTHER dispatchers this unit also has a row under in this run —
   *  each row counts only its own dispatcher's loads. */
  alsoUnder: string[];
  periodStart: string;
  periodEnd: string;
}) {
  const { t } = useTranslation();
  const loadedDays = loadedDayCount(loads, row.window_start, row.window_end);

  // The row meter — a glance-scale answer to "how is this truck doing"
  // that the numbers alone can't give across twenty rows.  Scale: $0 →
  // the next milestone (the next tier's gross when one is coming, else
  // the larger of target and gross).  Fill tone = earning state (ok
  // pays, warn pays 0% — the same tone the "no tier met" chip wears);
  // the tick marks the target.  The text above carries the exact
  // numbers, so the bar is aria-hidden decoration with a hover
  // explanation.
  const meterGross = Number(row.kpi_gross);
  const meterTarget = Number(row.adjusted_target);
  const meterEnd = row.next_tier
    ? meterGross + Number(row.next_tier.gap)
    : Math.max(meterTarget, meterGross);
  const meter = row.weekly_target != null && meterTarget > 0 && meterEnd > 0
    ? {
        fill: Math.min(100, Math.round((meterGross / meterEnd) * 100)),
        tick: meterTarget < meterEnd
          ? Math.round((meterTarget / meterEnd) * 100) : null,
        earning: Number(row.pct) > 0,
      }
    : null;
  const meterTip = !meter ? '' : row.next_tier && meter.tick != null
    ? t('kpi_board.meter_tip',
      'Gross {{g}} — the tick is the {{tgt}} target; the bar ends at the next tier ({{end}} gross).',
      { g: money0(meterGross), tgt: money0(meterTarget), end: money0(meterEnd) })
    : meter.tick != null
      ? t('kpi_board.meter_tip2', 'Gross {{g}} — past the {{tgt}} target (the tick).',
        { g: money0(meterGross), tgt: money0(meterTarget) })
      : t('kpi_board.meter_tip3', 'Gross {{g}} of the {{tgt}} target.',
        { g: money0(meterGross), tgt: money0(meterTarget) });

  // The next-tier line's honesty kit.  The gap is GROSS dollars but the
  // payout is PAY dollars — two currencies in one sentence — and on an
  // RPM tier the gap only closes with revenue at CURRENT miles: a cheap
  // extra load adds miles and moves the tier AWAY.  The surface says
  // both briefly; this tip says them properly.
  const tierDelta = row.next_tier
    ? row.next_tier.dollars_at - row.confirmed_dollars : 0;
  // At $0.00 the gain IS the total — "(+$80.00)" next to "pays $80.00"
  // restates it, so the delta only shows when there is a now to beat.
  const showTierDelta = tierDelta > 0 && Number(row.confirmed_dollars) > 0;
  const tierChain = !row.next_tier ? '' : [
    row.next_tier.min_rpm != null
      ? t('kpi_board.next_gap2', '{{gap}} more (same miles)',
        { gap: money0(row.next_tier.gap) })
      : t('kpi_board.next_gap', '{{gap}} more gross',
        { gap: money0(row.next_tier.gap) }),
    t('kpi_board.next_goal2', '→ {{pct}}%', { pct: row.next_tier.pct }),
    showTierDelta
      ? t('kpi_board.next_pays4', '→ {{at}} (+{{d}})',
        { at: usd(row.next_tier.dollars_at), d: usd(tierDelta) })
      : t('kpi_board.next_pays3', '→ {{at}}',
        { at: usd(row.next_tier.dollars_at) }),
  ].join(' ');
  const tierHint = !row.next_tier ? null : (
    <div className="space-y-0.5 text-left tabular-nums">
      <div>{tierChain}</div>
      <div>{row.next_tier.min_rpm != null
        ? t('kpi_board.tier_rpm',
          'The tier needs RPM ≥ {{rpm}}, so the {{gap}} gap holds at your current miles: more revenue on the SAME miles lifts RPM — an extra cheap load adds miles and can move the tier further away.',
          { rpm: row.next_tier.min_rpm.toFixed(2),
            gap: money0(row.next_tier.gap) })
        : t('kpi_board.tier_any',
          '{{gap}} more gross reaches it at any miles — this tier has no RPM condition.',
          { gap: money0(row.next_tier.gap) })}</div>
    </div>
  );

    return (
      /* Truck identity + its sheet numbers.  Gross and the zero-reason
         live HERE so a $0.00 row explains itself without switching to
         the sheet.  h-36 is the row-height CONTRACT with the days pane
         — overflow-hidden guards it. */
      <div className="h-36 overflow-hidden px-3 py-2 border-b border-border last:border-b-0"
        style={ROW_CONTAIN}>
        <div className="flex items-center gap-1.5 text-sm font-medium whitespace-nowrap">
          <span className="shrink-0">{row.vehicle_unit || t('kpi_board.no_unit', 'No unit assigned')}</span>
          <span className="shrink-0 text-xs font-normal text-muted-foreground">{row.company_code}</span>
          {stale && (
            <Tip label={t('kpi_board.stale_tip', 'This row’s loads changed after the run was generated — it still pays from the snapshot.')}>
              <span tabIndex={0} className={`shrink-0 text-xs font-normal ${toneClasses('warn')} px-1.5 rounded`}>
                {t('kpi_board.stale', 'stale')}
              </span>
            </Tip>
          )}
          {alsoUnder.length > 0 && (
            /* The same truck under two dispatchers is BY DESIGN (each
               row counts only its own dispatcher's loads) — but
               unexplained it reads as duplicate data. */
            <Tip label={t('kpi_board.also_under_tip',
              'Unit {{unit}} also has a row under {{names}} in this run — each dispatcher’s row counts only the loads they dispatched on it.',
              { unit: row.vehicle_unit, names: alsoUnder.join(', ') })}>
              <span tabIndex={0}
                className="min-w-0 truncate text-xs font-normal text-muted-foreground underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {t('kpi_board.also_under', 'also under {{name}}', {
                  name: alsoUnder.length === 1
                    ? alsoUnder[0].split(' ')[0]
                    : t('kpi_board.n_others', '{{n}} others', { n: alsoUnder.length }) })}
              </span>
            </Tip>
          )}
        </div>
        {/* One fact family per line — days, then money.  The old single
            run wrapped wherever the column edge fell, splitting facts
            mid-sentence and stranding a trailing "·"; deliberate lines
            break at the same place on every row, so one fact scans
            straight down the column. */}
        <div className="text-xs text-muted-foreground tabular-nums">
          <Tip label={<DaysTipContent row={row} loads={loads}
            draft={clickable} periodStart={periodStart}
            periodEnd={periodEnd} t={t} />}>
            {/* tabIndex: every Tip on this card opens on keyboard focus
                too — the card must not be mouse-only.  The aria-label is
                the breakdown as one sentence (the visual Tip is JSX a
                reader can't see). */}
            <span tabIndex={0}
              aria-label={daysSummary(row, loadedDays, t)}
              className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
              {daysCell(row, loadedDays, t)}
            </span>
          </Tip>
        </div>
        {/* Space groups the card, not ink: 8px opens each group (what
            happened / what could happen), 2px holds a group's lines
            together — proximity separates for free where dividers
            would draw boxes inside an already-bordered row. */}
        <div className="mt-2 text-xs text-muted-foreground tabular-nums">
          <span className="whitespace-nowrap">
            ${Math.round(row.kpi_gross).toLocaleString()}
            {row.weekly_target != null && (
              <span className="text-muted-foreground/70">
                {' '}{t('kpi_board.vs_target', 'vs {{tgt}}',
                  { tgt: `$${Math.round(row.adjusted_target).toLocaleString()}` })}
              </span>
            )}
          </span>
          {row.rpm != null && (
            /* RPM is what most tiers actually test — without it, two
               adjacent rows where MORE gross pays LESS look broken. */
            <>
              {' · '}
              <span className="whitespace-nowrap">
                {t('kpi_board.rpm', 'RPM {{r}}',
                  { r: Number(row.rpm).toFixed(2) })}
              </span>
            </>
          )}
        </div>
        {/* The payout gets its OWN line on every row — a constant shape
            that lands "% → $" at the same y-offset on every card, so a
            reader compares trucks by running one line down the column.
            The WHOLE result is the answer, so the whole line carries
            the weight, not just the dollars. */}
        <div className="mt-0.5 text-sm font-semibold text-foreground tabular-nums">
          <span className="whitespace-nowrap">
            {row.matched_rule ? (
              <Tip label={matchedTip(row.matched_rule, t)}>
                <span tabIndex={0} aria-label={matchedTip(row.matched_rule, t)}
                  className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                  {Number(row.pct)}%
                </span>
              </Tip>
            ) : (
              <>{Number(row.pct)}%</>
            )}
            {' → '}
            {tierHint ? (
              /* Hover/focus the AMOUNT for the what's-next chain —
                 "$224 more (same miles) → 3.25% → $278.66 (+$49.03)".
                 On the surface there is exactly ONE money line, so the
                 current pay can never be mistaken for the hypothetical
                 (owner decision 2026-08-20). */
              <Tip label={tierHint}>
                <span tabIndex={0}
                  className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                  {usd(row.confirmed_dollars)}
                </span>
              </Tip>
            ) : (
              usd(row.confirmed_dollars)
            )}
          </span>
        </div>
        {Number(row.pct) === 0 && row.zero_reason && (
          <Tip label={zeroTip(row, t)}>
            <span tabIndex={0} className={`mt-1 inline-block text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
              {row.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
                : row.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                  : row.zero_reason === 'no_target' ? t('kpi_runs.zr_target', 'no target')
                    : t('kpi_runs.zr_tier', 'no tier met')}
            </span>
          </Tip>
        )}

        {meter && (
          <Tip label={meterTip}>
            {/* py-1 -my-1 grows the hover/focus area without moving
                layout — a bare 4px strip is unhoverable.  role="img" +
                the tip text as its name: the meter carries three values
                and must exist in the accessibility tree. */}
            <div tabIndex={0} role="img" aria-label={meterTip}
              className="mt-0.5 py-1 -my-1 cursor-help">
              <div className="relative h-1 w-full rounded bg-muted">
                <div className={`h-full rounded ${meter.earning ? 'bg-ok' : 'bg-warn'}`}
                  style={{ width: `${meter.fill}%` }} />
                {meter.tick != null && (
                  <div className="absolute -top-0.5 -bottom-0.5 w-px bg-muted-foreground"
                    style={{ left: `${meter.tick}%` }} />
                )}
              </div>
            </div>
          </Tip>
        )}

      </div>
    );
});
