/**
 * One truck's week in the board's scrolling right pane — a cell per
 * day carrying its loads' chips and bars, and (on a draft) the
 * mark-inactive menu.
 *
 * Which cell renders which piece of which bar is decided by
 * [geometry.ts](geometry.ts), never here; this file only puts pixels
 * on the answer.  The unit pane ([UnitCard.tsx](UnitCard.tsx)) owns
 * the numbers, and the two agree on the row height in
 * [shared.ts](shared.ts).
 */
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { CalendarOff } from 'lucide-react';
import { Tip } from '../../../../components/tooltip';
import { ActionMenu } from '../../../../components/ui/context-menu';
import { toneClasses, toneText } from '../../../../lib/status';
import type { DaySuggestion, RunLoad, RunRow } from '../../api';
import { nextDay } from '../explain';
import { boardGeometry, prevDay } from './geometry';
import { CV_ROW, dayLabel, place, usd } from './shared';

const REASONS = ['home time', 'repair', 'holiday'];

export const DayCells = memo(function DayCells({ row, days, loads, suggestions, clickable, onMark }: {
  row: RunRow;
  days: string[];
  loads: RunLoad[] | undefined;
  /** Maintenance-suggested inactive days (human confirms by click). */
  suggestions: DaySuggestion[];
  clickable: boolean;
  onMark: (row: RunRow, day: string, reason: string | null) => void;
}) {
  const { t } = useTranslation();
  const marks = new Map((row.inactive_dates ?? []).map((m) => [m.date, m.reason]));
  const suggested = new Map(suggestions.map((sug) => [sug.date, sug]));
  // Which day renders which piece of which load's bar — the rules live
  // in board/geometry.ts, where a test can argue with them.
  const {
    byDay, transitInfo, inWindow, stripLoadAt, stripRun, deliveryEnd, laneOf,
  } = boardGeometry({
    loads, windowStart: row.window_start, windowEnd: row.window_end,
    marks, suggested,
  });

  return (
    <div className="flex h-36 border-b border-border last:border-b-0" style={CV_ROW}>
      {days.map((d) => {
        const dayLoads = byDay.get(d) ?? [];
        const reason = marks.get(d);
        const inside = inWindow(d);
        // The INNER cell fills its wrapper; sizing lives on the wrapper
        // below — the wrapper (or the ActionMenu trigger button) is the
        // real flex item, so putting basis/grow here was dead code and
        // let a wide chip widen ONE row's column off the header grid.
        const cell = (
          <div
            className={`group relative h-full min-h-14 px-1.5 py-1.5 space-y-1 ${
              !inside ? 'bg-muted/60'
                : reason != null ? 'bg-warn-bg' : ''
            } ${clickable && inside ? 'cursor-pointer hover:bg-muted hover:ring-1 hover:ring-inset hover:ring-border' : ''}`}
          >
            {dayLoads.slice(0, 2).map((l, i) => {
              const runDays = i <= 1 ? stripRun(l, d) : 0;
              const cont = runDays > 0;
              // Label runway = this cell + every following STRIP cell.
              // Columns are flex-equal, so the runway is a PERCENTAGE
              // of this cell (100% per covered day) — slightly
              // conservative (each cell's padding is counted once),
              // which only ends the text a few px before the bar.
              const runway = cont
                ? `calc(${(runDays + 1) * 100}% - 12px)` : undefined;
              const loadAria = t('kpi_board.load_aria',
                'Load {{rate}} — {{from}} to {{to}}, {{mi}} mi, delivers {{del}}', {
                  rate: usd(l.total_rate),
                  from: place(l.pickup_location) || '—',
                  to: place(l.delivery_location) || l.load_number,
                  mi: Math.round(l.miles).toLocaleString(),
                  del: dayLabel(deliveryEnd(l)),
                });
              return cont ? (
                /* DAY-ALIGNED fill + floating label.  The fill ends at
                   the cell edge (uniform bg-ok-bg, single alpha layer,
                   day-aligned like the strips and the hover regions);
                   the label floats above the whole bar,
                   pointer-events-none so hovering day 2 hits day 2's
                   transit tip, and names the delivery day on the
                   surface. */
                <div key={i} className="relative">
                  <Tip label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                    <div className="block h-6 rounded-l rounded-r-none -mr-1.5 bg-ok-bg"
                      role="img" aria-label={loadAria} />
                  </Tip>
                  <span
                    className="pointer-events-none absolute left-1.5 top-0 z-10 flex h-6 leading-6 text-xs tabular-nums text-ok"
                    style={{ maxWidth: runway }} aria-hidden>
                    {/* The date must SURVIVE truncation — "→ Thu 8/"
                        is misinformation; the city gives way first. */}
                    <span className="min-w-0 truncate">
                      ${Math.round(l.total_rate).toLocaleString()} · {place(l.delivery_location) || l.load_number}
                    </span>
                    <span className="shrink-0 whitespace-pre">
                      {' → '}{dayLabel(deliveryEnd(l))}
                    </span>
                  </span>
                </div>
              ) : (
                <Tip key={i}
                  label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                  <div
                    /* h-6 + leading-6 (not flex): truncate's ellipsis
                       needs a block formatting context. */
                    className={`block h-6 leading-6 rounded text-xs tabular-nums px-1.5 truncate ${toneClasses('ok')}`}
                    role="img" aria-label={loadAria}
                  >
                    ${Math.round(l.total_rate).toLocaleString()} · {place(l.delivery_location) || l.load_number}
                  </div>
                </Tip>
              );
            })}
            {dayLoads.length > 2 && (
              <div className="px-1.5 text-xs text-muted-foreground">
                +{dayLoads.length - 2} {t('kpi_board.more', 'more')}
              </div>
            )}
            {reason != null && (
              clickable ? (
                <Tip label={t('kpi_board.unmark_tip3',
                  '{{why}} — click to make {{day}} count again{{stake}}.', {
                    why: reason || t('kpi_board.inactive', 'inactive'),
                    day: dayLabel(d),
                    stake: row.weekly_target != null
                      ? t('kpi_board.stake_up', ', target +{{v}}',
                          { v: `$${Math.round(row.weekly_target / 7).toLocaleString()}` })
                      : '',
                  })}>
                  <div className={`rounded px-1.5 py-0.5 text-xs font-medium ${toneClasses('warn')} uppercase tracking-wide truncate`}>
                    {t('kpi_board.inactive', 'inactive')}{reason ? ` · ${reason}` : ''}
                  </div>
                </Tip>
              ) : (
                <div className={`rounded px-1.5 py-0.5 text-xs font-medium ${toneClasses('warn')} uppercase tracking-wide truncate`}>
                  {t('kpi_board.inactive', 'inactive')}{reason ? ` · ${reason}` : ''}
                </div>
              )
            )}
            {clickable && inside && reason == null && suggested.has(d) && (
              /* Phase 4b stepping stone: a work order on this truck's
                 service day SUGGESTS the mark; the manager confirms via
                 the menu.  Dashed = proposal, filled warn = decided. */
              <Tip label={`${suggested.get(d)!.source} — ${t('kpi_board.suggest_tip', 'click to confirm as inactive')}`}>
                <span className={`block rounded border border-dashed border-warn-bd px-1.5 py-0.5 text-xs uppercase tracking-wide truncate bg-transparent ${toneText('warn')}`}>
                  {suggested.get(d)!.reason}?
                </span>
              </Tip>
            )}
            {clickable && inside && dayLoads.length > 0 && reason == null && (
              /* Loaded cells hide their gesture behind a bare cursor
                 change — on hover a corner glyph names the action
                 without reflowing the cell (overlay, not in-flow). */
              <span className="absolute bottom-1 right-1 hidden group-hover:inline-flex group-focus-within:inline-flex items-center justify-center size-5 rounded border border-dashed border-border bg-card"
                aria-hidden>
                <CalendarOff size={12} className="text-muted-foreground" />
              </span>
            )}
            {inside && dayLoads.length === 0 && reason == null
              && !suggested.has(d) && transitInfo.has(d) && (() => {
              /* A rolling day is a PIECE of its load's bar — same tone
                 as the pickup chip, lighter fill, connected through
                 the cell edges; the delivery day carries the right
                 cap.  An anonymous "in transit" couldn't say WHICH
                 load the truck was rolling for. */
              const info = transitInfo.get(d)!;
              const continues = stripLoadAt(nextDay(d)) === info.load;
              // The strip lives on ITS load's lane (the chip's index
              // on the pickup day); lanes beyond the rendered two get
              // no strip.
              const lane = laneOf(info.load);
              if (lane < 0 || lane > 1) return null;
              // Connected LEFT when yesterday rendered this load's
              // head (its pickup) or its strip; an interrupted span
              // (another chip in between) restarts with a left cap.
              const leftConnected =
                stripLoadAt(prevDay(d)) === info.load
                || (byDay.get(prevDay(d)) ?? [])[0] === info.load;
              return (
                <Tip label={t('kpi_board.transit_tip2',
                  '{{rate}} to {{place}} — in transit, day {{i}} of {{n}}; delivers {{del}}. A working day; it counts.', {
                    rate: `$${Math.round(info.load.total_rate).toLocaleString()}`,
                    place: place(info.load.delivery_location) || info.load.load_number,
                    i: info.dayNo, n: info.total,
                    del: dayLabel(deliveryEnd(info.load)),
                  })}>
                  {/* FLAT — the pickup chip already carries the text
                      once; repeating it per piece breaks the one-bar
                      reading.  h-6 = the chip's exact height, so the
                      bar runs level; AT still hears the story. */}
                  {/* Full-strength token: --ok-bg is ALREADY a 15%
                      tint (color-mix), so an alpha modifier on top
                      painted a ~9% wash — invisible on white.  Same
                      fill as the chip = one unbroken bar. */}
                  <span className={`block h-6 bg-ok-bg ${
                    lane === 1 ? 'mt-7' : ''} ${
                    leftConnected ? '-ml-1.5 rounded-l-none' : 'rounded-l'} ${
                    continues ? 'rounded-r-none -mr-1.5' : 'rounded-r'}`}>
                    <span className="sr-only">
                      {t('kpi_board.transit_sr', 'in transit — {{rate}} to {{place}}', {
                        rate: `$${Math.round(info.load.total_rate).toLocaleString()}`,
                        place: place(info.load.delivery_location) || info.load.load_number,
                      })}
                    </span>
                  </span>
                </Tip>
              );
            })()}
            {clickable && inside && dayLoads.length === 0 && reason == null
              && !suggested.has(d) && !transitInfo.has(d) && (
              /* A persistent dashed WELL with a calendar-off glyph — a
                 "+" promised ADDING something; the gesture REMOVES a
                 day from the target.  Tip names day and action. */
              <Tip label={row.weekly_target != null
                ? t('kpi_board.mark_tip2', 'Mark {{day}} inactive → target −{{v}}',
                    { day: dayLabel(d),
                      v: `$${Math.round(row.weekly_target / 7).toLocaleString()}` })
                : t('kpi_board.mark_tip', 'Mark {{day}} inactive', { day: dayLabel(d) })}>
                <span className="flex h-6 items-center justify-center rounded border border-dashed border-border">
                  <CalendarOff size={12} className="text-muted-foreground/60" aria-hidden />
                </span>
              </Tip>
            )}
          </div>
        );
        // The bar erases the grid line it crosses — only where it
        // VISUALLY crosses: the next day renders load L's strip AND
        // this day renders L's head or L's strip.  A span interrupted
        // by another chip keeps its grid lines.
        const nextStripLoad = stripLoadAt(nextDay(d));
        const seamless = nextStripLoad != null
          && (stripLoadAt(d) === nextStripLoad
            || (dayLoads.length > 0 && dayLoads[0] === nextStripLoad));
        const wrapCls = `flex-1 min-w-0 ${
          seamless ? '' : 'border-r'} border-border last:border-r-0`;
        if (!clickable || !inside) return <div key={d} className={wrapCls}>{cell}</div>;
        return (
          <ActionMenu
            key={d}
            items={[
              {
                key: 'heading',
                label: `${row.vehicle_unit || t('kpi_board.no_unit', 'No unit assigned')} · ${dayLabel(d)}`,
                disabled: true,
                onSelect: () => {},
              },
              // The consequence AT the moment of choosing — it already
              // lived in the cell's accessible name, invisible to the
              // sighted user with the menu open.
              ...(row.weekly_target != null ? [{
                key: 'consequence',
                label: reason == null
                  ? t('kpi_board.menu_target_down', 'target {{a}} → {{b}}', {
                      a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
                      b: `$${Math.round(Math.max(0, row.adjusted_target - row.weekly_target / 7)).toLocaleString()}`,
                    })
                  : t('kpi_board.menu_target_up', 'count it again: target {{a}} → {{b}}', {
                      a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
                      b: `$${Math.round(row.adjusted_target + row.weekly_target / 7).toLocaleString()}`,
                    }),
                disabled: true,
                onSelect: () => {},
              }] : []),
              ...(reason == null && suggested.has(d) ? [{
                key: 'confirm-suggest',
                label: t('kpi_board.confirm_suggest',
                  'Confirm {{reason}} — {{source}}',
                  { reason: suggested.get(d)!.reason,
                    source: suggested.get(d)!.source }),
                separatorBefore: true,
                onSelect: () => onMark(row, d, suggested.get(d)!.reason),
              }] : []),
              ...(reason != null ? [{
                key: 'clear',
                label: t('kpi_board.clear', 'Active day (clear mark)'),
                separatorBefore: true,
                onSelect: () => onMark(row, d, null),
              }] : []),
              ...REASONS.map((r, i) => ({
                key: r,
                label: t(`kpi_board.reason_${r.replace(' ', '_')}`,
                  r.charAt(0).toUpperCase() + r.slice(1)),
                disabled: reason === r,
                separatorBefore: i === 0 && reason == null,
                onSelect: () => onMark(row, d, r),
              })),
              {
                key: 'cancel',
                label: t('common.cancel', 'Cancel'),
                separatorBefore: true,
                onSelect: () => {},
              },
            ]}
          >
            <button type="button" className={`${wrapCls} text-left`}
              aria-label={(reason != null
                ? t('kpi_board.day_aria_unmark',
                    'Make {{day}} count again for unit {{unit}}{{stake}}',
                    { day: dayLabel(d), unit: row.vehicle_unit,
                      stake: row.weekly_target != null
                        ? ` — ${t('kpi_board.aria_up', 'target +${{v}}',
                            { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' })
                : suggested.has(d)
                  /* The suggestion state must reach the screen reader —
                     without this prefix a REPAIR? cell announced exactly
                     like a plain empty day. */
                  ? t('kpi_board.day_aria_suggest',
                      '{{reason}} suggested — confirm {{day}} inactive for unit {{unit}}{{stake}}',
                      { reason: suggested.get(d)!.reason,
                        day: dayLabel(d), unit: row.vehicle_unit,
                        stake: row.weekly_target != null
                          ? ` — ${t('kpi_board.aria_down', 'target −${{v}}',
                              { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' })
                  : t('kpi_board.day_aria_mark',
                    'Mark {{day}} inactive for unit {{unit}}{{stake}}',
                    { day: dayLabel(d), unit: row.vehicle_unit,
                      stake: row.weekly_target != null
                        ? ` — ${t('kpi_board.aria_down', 'target −${{v}}',
                            { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' }))}>
              {cell}
            </button>
          </ActionMenu>
        );
      })}
    </div>
  );
});
