/**
 * The run sheet's COLUMNS — what each cell of the settlement table
 * shows, and what it explains on hover.
 *
 * They lived inside the page component, where 164 lines of cell
 * rendering sat between the page's state and its layout.  The page now
 * asks for them; every value they close over (the live loads, the
 * period, whether the run is still a draft, what a Days click opens)
 * arrives as a parameter, so the table's rules can be read without
 * reading the page.
 */
import { InfoTip, Tip } from '../../../../components/tooltip';
import { toneClasses, toneText } from '../../../../lib/status';
import type { AnyColumn } from '../../../../types';
import type { TFunction } from 'i18next';
import type { RunLoadsResponse, RunRow } from '../../api';
import { daysCell, daysSummary, loadedDayCount, matchedTip, zeroTip } from '../explain';
import { DaysTipContent } from '../DaysTip';
import { usd } from './format';
import { Note } from './Note';

// Archive columns — the run LIST as records (period, status, size, money).
export const ARCHIVE_COLUMNS: AnyColumn[] = [
  { key: 'period_start', label: 'Period', sortable: true,
    render: (_v, r) => (
      <span className="tabular-nums">{String(r.period_start)} – {String(r.period_end)}</span>
    ) },
  { key: 'status', label: 'Status', sortable: true, filterable: true,
    render: (v) => (
      <span className={`text-xs font-medium px-2 py-0.5 rounded-md ${
        toneClasses(v === 'finalized' ? 'ok' : 'info')}`}>
        {String(v)}
      </span>
    ) },
  { key: 'row_count', label: 'Rows', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? '—')}</span> },
  { key: 'total', label: 'Total', sortable: true, aggregable: true,
    aggFormat: (v) => usd(v),
    render: (v) => <span className="tabular-nums font-medium">{usd(v)}</span> },
  { key: 'created_at', label: 'Created', sortable: true, aggType: 'date',
    render: (v) => (
      <span className="text-xs text-muted-foreground tabular-nums">{String(v || '—').slice(0, 10)}</span>
    ) },
  { key: 'finalized_at', label: 'Finalized', sortable: true,
    render: (v) => (
      <span className="text-xs text-muted-foreground tabular-nums">{String(v || '—').slice(0, 10)}</span>
    ) },
];


export function runSheetColumns({ t, runLoads, draft, periodStart, periodEnd, onOpenLoads }: {
  t: TFunction;
  /** Live loads for this run — undefined while the query is in flight
   *  (unknown day coverage is not zero coverage). */
  runLoads: RunLoadsResponse | undefined;
  /** The run is still editable — marking and its coaching line apply. */
  draft: boolean;
  periodStart: string;
  periodEnd: string;
  /** What the Days cell's button opens: that row's loads. */
  onOpenLoads: (row: RunRow) => void;
}): AnyColumn[] {
    return [
    { key: 'dispatcher_name', label: 'Dispatcher', sortable: true, filterable: true,
      defaultPinned: 'left' },
    { key: 'company_code', label: 'Company', sortable: true, filterable: true },
    { key: 'vehicle_unit', label: 'Unit', sortable: true, defaultPinned: 'left',
      render: (v) => v
        ? <span>{String(v)}</span>
        : <span className="text-muted-foreground">{t('kpi_runs.unassigned', 'Unassigned')}</span> },
    { key: 'window_start', label: 'Window', sortable: true,
      render: (_v, r) => (
        <span className="text-xs text-muted-foreground">
          {String(r.window_start)} – {String(r.window_end)}
        </span>
      ) },
    { key: 'total_days', label: 'Days', sortable: true,
      // Period length never varies within one run — counted days is
      // the only orderable fact in the trio.
      sortKey: (r) => Number((r as unknown as RunRow).total_days)
        - Number((r as unknown as RunRow).inactive_days),
      headerRender: () => (
        <span className="inline-flex items-center gap-1">
          {t('kpi_runs.days_col', 'Days')}
          <span onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}>
            <InfoTip label={t('kpi_runs.days_info4',
              'Counted days of the period, and how many of those days were covered by a load (from pickup until delivery — in transit included). Days marked inactive on the board (home time, repair, holiday) drop out, and the target is split across the counted days. A counted day with no load is on the dispatcher.')} />
          </span>
        </span>
      ),
      // period/counted/loaded, with the inactive reason as visible
      // context — these numbers move the target, so they must be
      // readable and self-explaining.
      render: (_v, r) => {
        // The already-computed cost of the inactive days: the bar this
        // truck ISN'T being held to (weekly rate × inactive days ÷ 7).
        const cut = r.weekly_target != null && Number(r.inactive_days) > 0
          ? (Number(r.weekly_target) / 7) * Number(r.inactive_days) : null;
        return (
          // Words label the numbers inline — no notation to decode, so
          // AT reads the cell as-is.  The note leads with the DECISION
          // number (what excusing cost); reasons live in the tooltip.
          <span className="tabular-nums">
            <Tip label={<DaysTipContent row={r}
              loads={runLoads ? (runLoads.rows[String(r.id)] ?? []) : undefined}
              draft={draft} periodStart={periodStart}
              periodEnd={periodEnd} t={t} />}>
              {/* A real control: keyboard/AT reach the breakdown via
                  the name, and the click opens the row's loads — the
                  hover Tip alone was mouse-only three audits running. */}
              <button type="button"
                onClick={(e) => {
                  // The row's own click opens the edit dialog — without
                  // this, one click stacked BOTH dialogs.
                  e.stopPropagation();
                  onOpenLoads(r);
                }}
                aria-label={t('kpi_runs.days_btn_aria',
                  'Open unit {{unit}} loads — {{sum}}',
                  { unit: r.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned'),
                    sum: daysSummary(r, runLoads
                      ? loadedDayCount(runLoads.rows[String(r.id)] ?? [],
                          r.window_start, r.window_end) : null, t) })}
                className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 hover:text-foreground transition">
                {daysCell(r, runLoads
                  ? loadedDayCount(runLoads.rows[String(r.id)] ?? [],
                      r.window_start, r.window_end) : null, t)}
              </button>
            </Tip>
            {Number(r.inactive_days) > 0 && (
              <Note text={`(${cut != null
                ? `−$${Math.round(cut).toLocaleString()} · ` : ''}${
                t('kpi_runs.n_inactive', '{{n}} inactive', { n: r.inactive_days })})`} />
            )}
          </span>
        );
      } },
    { key: 'kpi_gross', label: 'Gross', sortable: true,
      aggregable: true, aggFormat: (v) => usd(v),
      render: (v, r) => (
        <span className="tabular-nums">
          {usd(v)}
          {Number(r.extras) !== 0 && (
            <Note text={`(incl. ${usd(r.extras)}${r.extras_note ? ` ${r.extras_note}` : ''})`} />
          )}
        </span>
      ) },
    { key: 'miles', label: 'Miles', sortable: true, defaultHidden: true,
      aggregable: true,
      aggFormat: (v) => Math.round(v).toLocaleString(),
      render: (v) => <span className="tabular-nums">{Number(v).toLocaleString()}</span> },
    { key: 'rpm', label: 'RPM', sortable: true,
      // Group/footer RPM is the MILES-WEIGHTED rate (Σ base gross ÷ Σ
      // miles, extras excluded — same base the row RPM uses), never an
      // average of the row rates.
      aggregable: true, aggFns: ['avg'],
      aggRatio: { num: (r) => Number(r.base_gross), den: (r) => Number(r.miles) },
      aggFormat: (v) => v.toFixed(2),
      render: (v) => <span className="tabular-nums">{v == null ? '—' : Number(v).toFixed(2)}</span> },
    { key: 'adjusted_target', label: 'Target', sortable: true,
      // NULL weekly_target = this company has no bar configured — say
      // so, never render an invented number.  A prorated bar names its
      // cause inline: "$2,285.71 · 2 of 7 days" — an unanchored low
      // target reads as an error.
      render: (v, r) => r.weekly_target == null
        ? <span className={`text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>{t('kpi_runs.no_target', 'no target')}</span>
        : <span className="tabular-nums">{usd(v)}</span> },
    { key: 'vs_target', label: 'vs Target', sortable: true,
      sortKey: (r) => (r as unknown as RunRow).weekly_target == null
        ? Number.NEGATIVE_INFINITY
        : Number((r as unknown as RunRow).kpi_gross)
          - Number((r as unknown as RunRow).adjusted_target),
      render: (_v, r) => {
        if (r.weekly_target == null) return <span className="text-muted-foreground">—</span>;
        const d = Number(r.kpi_gross) - Number(r.adjusted_target);
        return (
          <span className={`tabular-nums ${d >= 0 ? toneText('ok') : toneText('danger')}`}>
            {d >= 0 ? '+' : '−'}${Math.abs(Math.round(d)).toLocaleString()}
          </span>
        );
      } },
    { key: 'pct', label: 'KPI %', sortable: true,
      // Every zero in a money column carries its reason — the first
      // question a dispatcher asks their manager is "why is this 0?".
      // no_target is annotated on the Target column already.
      render: (v, r) => (
        <span className="tabular-nums">
          {r.matched_rule ? (
            <Tip label={matchedTip(r.matched_rule, t)}>
              <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {Number(v)}%
              </span>
            </Tip>
          ) : (
            <>{Number(v)}%</>
          )}
          {Number(v) === 0 && r.zero_reason && r.zero_reason !== 'no_target' && (
            <Tip label={zeroTip(r, t)}>
              <span tabIndex={0} className={`ml-1 text-xs font-medium cursor-help ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
                {r.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
                  : r.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                    : t('kpi_runs.zr_tier', 'no tier met')}
              </span>
            </Tip>
          )}
          {r.override_pct != null && (
            <span className={`ml-1 text-xs font-medium ${toneClasses('info')} px-2 py-0.5 rounded-md`}>
              → {Number(r.override_pct)}%
            </span>
          )}
        </span>
      ) },
    { key: 'kpi_dollars', label: 'KPI $', sortable: true, defaultHidden: true,
      aggregable: true, aggFormat: (v) => usd(v),
      render: (v) => <span className="tabular-nums">{usd(v)}</span> },
    { key: 'confirmed_dollars', label: 'Confirmed', sortable: true, aggregable: true,
      aggFormat: (v) => usd(v),
      render: (v, r) => (
        <span className="tabular-nums font-medium">
          {usd(v)}
          {r.override_reason ? <Note text={`(${String(r.override_reason)})`} /> : null}
        </span>
      ) },
  ];
}
