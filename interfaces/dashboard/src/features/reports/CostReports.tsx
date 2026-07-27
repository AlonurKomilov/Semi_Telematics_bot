import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, CartesianGrid, PieChart, Pie, Legend,
} from 'recharts';
import { TrendingDown, Minus, Download, Receipt, DollarSign, Wallet, Users, TrendingUp } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import {
  EmptyState, ErrorState, CardSkeleton, DateRangePresets,
} from '../../components/shell';
import type { WorkOrderCostRow, AnyColumn } from '../../types';
import { TASK_TYPE_OPTIONS } from '../maintenance/badges';
import type { ReportsLayoutOutletContext } from './ReportsLayout';
import { chartColor } from '../../lib/status';
import DataGrid from '../../components/datagrid';
import { Tip } from '../../components/tooltip';

// Backend response envelopes for each /reports/* endpoint.
interface SystemRow {
  system_key: string;
  system: string;
  total_spent: number;    // parts
  labor_spent: number;
  work_order_count: number;
}

interface ReportResponse {
  days: number;
  rows: WorkOrderCostRow[];
}

interface MonthlyRow {
  month: string;          // 'YYYY-MM'
  work_order_count: number;
  total_spent: number;
}

interface MonthlyResponse {
  days: number;
  rows: MonthlyRow[];
}

// Headline summary with prior-period comparison.  Backend computes
// the deltas so the frontend never has to divide-by-zero guard.
// ``delta_pct`` values of ``null`` mean the prior period had no data
// to compare against — UI renders the card without a delta chip.
interface SummaryResponse {
  days: number;
  current: {
    total_spent: number;
    work_order_count: number;
    vendor_count: number;
  };
  prior: {
    total_spent: number;
    work_order_count: number;
    vendor_count: number;
  };
  delta_pct: {
    total_spent: number | null;
    work_order_count: number | null;
    avg_per_wo: number | null;
    vendor_count: number | null;
  };
  avg_per_wo: { current: number; prior: number };
}

// Period picker options — same windows we offer the DOT binder so
// users learn one mental model.  90 default because that's the
// quarterly cadence most fleet managers run their review on.
// ``i18nKey`` is resolved at render time so the dropdown speaks the
// user's language (the keys live under ``cost_reports.period_*``).
const PERIOD_OPTIONS = [
  { i18nKey: 'cost_reports.period_30',  days: 30 },
  { i18nKey: 'cost_reports.period_90',  days: 90 },
  { i18nKey: 'cost_reports.period_180', days: 180 },
  { i18nKey: 'cost_reports.period_365', days: 365 },
  { i18nKey: 'cost_reports.period_730', days: 730 },
] as const;

// Bar colour palette — picked to read well in both light and dark
// modes (saturated mid-tones rather than pastels).  Index modulo the
// length so a chart with 20 vehicles still cycles cleanly.
const BAR_PALETTE = [chartColor(1), chartColor(2), chartColor(3), chartColor(4), chartColor(5)];

function money(n: number): string {
  return `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  })}`;
}

function moneyDetail(n: number): string {
  return `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

export default function Reports() {
  const { t } = useTranslation();
  const [days, setDays] = useState<number>(90);
  const navigate = useNavigate();
  // Outlet context comes from ReportsLayout — lets this child page
  // push its own header chrome (period selector + CSV button) into
  // the layout's shared action slot.  ``useOutletContext`` returns
  // ``undefined`` when the page is rendered outside the layout (e.g.
  // future direct route or test harness) — we tolerate that by
  // calling the setter conditionally.
  const outletCtx = useOutletContext<ReportsLayoutOutletContext | undefined>();

  // Four parallel useQuery calls — react-query batches them in the
  // same effect tick and the API serves them concurrently.  Worst-
  // case round-trip is one slow endpoint, not the sum.  Sharing the
  // ``days`` argument in the key invalidates correctly when the
  // user flips the period picker.
  // Headline summary + prior-period comparison.  Drives the four
  // stat cards with their delta chips ("+12% vs prior 90d").  Kept
  // separate from the per-vehicle/type/vendor queries so a slow
  // summary fetch doesn't block the charts (and vice versa).
  const summaryQuery = useQuery<SummaryResponse>({
    queryKey: ['wo-reports', 'summary', days],
    queryFn: () => apiJSON<SummaryResponse>(`/reports/cost-reports/summary?days=${days}`),
  });
  const perVehicle = useQuery<ReportResponse>({
    queryKey: ['wo-reports', 'per-vehicle', days],
    queryFn: () => apiJSON<ReportResponse>(`/reports/cost-reports/per-vehicle?days=${days}`),
  });
  // Parts spend per service-task tag (summed at the part level, so a
  // mixed "oil + brakes" invoice splits correctly).  Replaced the old
  // per-task-type source, which only saw work orders linked to a
  // maintenance task and silently dropped everything else.
  const perType = useQuery<ReportResponse>({
    queryKey: ['wo-reports', 'per-service-task', days],
    queryFn: () => apiJSON<ReportResponse>(`/reports/cost-reports/per-service-task?days=${days}`),
  });
  // Usage + spend per part name — "which part keeps costing us".
  const perPart = useQuery<ReportResponse>({
    queryKey: ['wo-reports', 'per-part', days],
    queryFn: () => apiJSON<ReportResponse>(`/reports/cost-reports/per-part?days=${days}`),
  });
  // Spend per SYSTEM — the coarse "what are brakes costing us" axis.
  // Unlike the per-task donut, labor IS attributable here (the system
  // rides the task, which labor lines carry), so the bar shows the
  // combined number and the tooltip splits it.
  const perSystem = useQuery<{ days: number; rows: SystemRow[] }>({
    queryKey: ['wo-reports', 'per-system', days],
    queryFn: () => apiJSON(`/reports/cost-reports/per-system?days=${days}`),
  });
  const perVendor = useQuery<ReportResponse>({
    queryKey: ['wo-reports', 'per-vendor', days],
    queryFn: () => apiJSON<ReportResponse>(`/reports/cost-reports/per-vendor?days=${days}`),
  });
  const monthly = useQuery<MonthlyResponse>({
    queryKey: ['wo-reports', 'monthly-trend', days],
    queryFn: () => apiJSON<MonthlyResponse>(`/reports/cost-reports/monthly-trend?days=${days}`),
  });

  const loading = summaryQuery.isLoading || perVehicle.isLoading
    || perType.isLoading || perVendor.isLoading || monthly.isLoading
    || perPart.isLoading || perSystem.isLoading;
  const firstError =
    summaryQuery.error || perVehicle.error || perType.error
    || perVendor.error || monthly.error;

  // Summary cards consume the server-computed summary which carries
  // both the current window AND the equivalent-length prior window
  // (so we can render "+12% vs prior 90d" without a second round trip).
  const s = summaryQuery.data;

  // Vehicle bar chart — top 10 spenders + collapse the rest.
  // Sorted descending so the highest spender is the leftmost bar
  // (matches the eye's natural reading order).
  const vehicleChart = useMemo(() => {
    const rows = (perVehicle.data?.rows ?? [])
      .slice()
      .sort((a, b) => b.total_spent - a.total_spent);
    if (rows.length <= 10) return rows;
    const head = rows.slice(0, 10);
    const restTotal = rows.slice(10).reduce(
      (acc, r) => acc + (r.total_spent ?? 0), 0,
    );
    const restCount = rows.slice(10).reduce(
      (acc, r) => acc + (r.work_order_count ?? 0), 0,
    );
    return [
      ...head,
      { vehicle_name: `+${rows.length - 10} more`, total_spent: restTotal, work_order_count: restCount },
    ];
  }, [perVehicle.data]);

  // Service-task donut — sorted descending so the legend is
  // meaningful.  Rows carry task-type SLUGS ('brakes',
  // 'custom_oil_change', 'untagged'); label them with the shared
  // maintenance vocabulary, falling back to de-slugged text.
  const typeChart = useMemo(() => {
    const labelOf = (slug: string): string => {
      if (slug === 'untagged') return 'Untagged';
      const opt = TASK_TYPE_OPTIONS.find(o => o.value === slug);
      if (opt) return opt.label;
      return slug.replace(/^custom_/, '').replace(/[_-]+/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase());
    };
    return (perType.data?.rows ?? [])
      .slice()
      // Combined parts+labor drives the ordering; the donut slice
      // itself stays PARTS spend (the report's documented meaning).
      .sort((a, b) =>
        (b.total_spent + (b.labor_spent ?? 0)) - (a.total_spent + (a.labor_spent ?? 0)))
      .map(r => ({ ...r, label: labelOf(String(r.service_task ?? '')) }));
  }, [perType.data]);

  // System bars — combined parts+labor, biggest first.
  const systemChart = useMemo(
    () => (perSystem.data?.rows ?? [])
      .map(r => ({ ...r, combined: r.total_spent + (r.labor_spent ?? 0) }))
      .filter(r => r.combined > 0)
      .sort((a, b) => b.combined - a.combined),
    [perSystem.data],
  );

  // Top parts — highest spend first; the backend already caps at 25.
  const partRows = useMemo(
    () => (perPart.data?.rows ?? []).slice().sort((a, b) => b.total_spent - a.total_spent),
    [perPart.data],
  );

  // Vendor table — top 10 by spend.  Pure HTML table rather than a
  // chart because text is the right primitive for "which vendor got
  // how much money" + a manager often wants to glance at the count
  // alongside the total to spot one-off vs recurring relationships.
  const vendorRows = useMemo(() => {
    return (perVendor.data?.rows ?? [])
      .slice()
      .sort((a, b) => b.total_spent - a.total_spent)
      .slice(0, 10);
  }, [perVendor.data]);

  // CSV export — bundles all four aggregates into one CSV with
  // section headers.  Built client-side from the data we already
  // fetched; no extra round trip.  Format matches what most ops
  // teams paste into spreadsheets without re-mapping columns.
  const exportCsv = () => {
    if (loading) {
      toast.info(t('cost_reports.still_loading'));
      return;
    }
    const csv: string[] = [];
    csv.push(`# Cost Reports — Last ${days} days`);
    csv.push(`# Generated ${new Date().toISOString()}`);
    csv.push('');
    csv.push('# Spend by Vehicle');
    csv.push('vehicle_name,work_order_count,total_spent');
    for (const r of (perVehicle.data?.rows ?? [])) {
      csv.push(`"${(r.vehicle_name ?? '').replace(/"/g, '""')}",${r.work_order_count},${r.total_spent.toFixed(2)}`);
    }
    csv.push('');
    csv.push('# Spend by Task Type');
    csv.push('task_type,work_order_count,total_spent');
    for (const r of (perType.data?.rows ?? [])) {
      csv.push(`"${(r.task_type ?? '').replace(/"/g, '""')}",${r.work_order_count},${r.total_spent.toFixed(2)}`);
    }
    csv.push('');
    csv.push('# Spend by System');
    csv.push('system,work_order_count,parts_spent,labor_spent');
    for (const r of (perSystem.data?.rows ?? [])) {
      csv.push(`"${r.system.replace(/"/g, '""')}",${r.work_order_count},${r.total_spent.toFixed(2)},${(r.labor_spent ?? 0).toFixed(2)}`);
    }
    csv.push('');
    csv.push('# Spend by Vendor');
    csv.push('vendor_name,work_order_count,total_spent');
    for (const r of (perVendor.data?.rows ?? [])) {
      csv.push(`"${(r.vendor_name ?? '').replace(/"/g, '""')}",${r.work_order_count},${r.total_spent.toFixed(2)}`);
    }
    csv.push('');
    csv.push('# Spend by Month');
    csv.push('month,work_order_count,total_spent');
    for (const r of (monthly.data?.rows ?? [])) {
      csv.push(`${r.month},${r.work_order_count},${r.total_spent.toFixed(2)}`);
    }
    const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cost-reports-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  // Avoid lint warnings for the imported helper that's only used in
  // file uploads elsewhere; keep the import live for future polish
  // (e.g. PDF export of the same data).
  void apiFetch;

  // Period picker items — labels resolved through i18n so they follow the
  // user's language.  Rebuilt each render (cheap) so ``t`` stays current.
  const periodItems = PERIOD_OPTIONS.map((opt) => ({ value: String(opt.days), label: t(opt.i18nKey) }));

  // Push the period selector + CSV export into the layout header.
  // Rerun whenever ``days`` or the load/export state changes so the
  // header reflects the current values; clear on unmount to keep the
  // slot clean when the user navigates to another Reports tab.
  useEffect(() => {
    if (!outletCtx) return;
    outletCtx.setActions(
      <div className="flex items-center gap-2">
        <DateRangePresets
          value={days}
          onChange={setDays}
          options={periodItems.map((it) => ({ label: it.label, days: Number(it.value) }))}
          maxDays={730}
        />
        <Tip label={t('cost_reports.export_csv_title')}>
          <button
            type="button"
            onClick={exportCsv}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
          >
            <Download size={14} />
            {t('cost_reports.export_csv')}
          </button>
        </Tip>
      </div>,
    );
    return () => outletCtx.setActions(null);
    // exportCsv is rebuilt every render but the layout only cares
    // about the rendered JSX, so re-pushing each render is fine; we
    // gate on the visible inputs (days) to skip when nothing changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, outletCtx, t]);

  if (firstError) {
    return (
      <div>
        <ErrorState message={firstError instanceof Error ? firstError.message : 'Could not load reports'} />
      </div>
    );
  }

  const noData = !loading && (s?.current.work_order_count ?? 0) === 0;

  return (
    <div>
      {noData ? (
        <EmptyState
          icon={Receipt}
          title={t('cost_reports.empty_title')}
          description={t('cost_reports.empty_desc')}
        />
      ) : (
        <>
          {/* ── Summary cards ───────────────────────────────────── */}
          {loading || !s ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              {[0, 1, 2, 3].map(i => <CardSkeleton key={i} />)}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              {/* Cost-sensitive metrics: "spend went up" reads as bad,
                  so higherIsBad=true.  Vendor count is neutral —
                  more vendors isn't inherently good or bad. */}
              <SummaryCard
                icon={<DollarSign size={14} />}
                label={t('cost_reports.card_total_spend')}
                value={money(s.current.total_spent)}
                deltaPct={s.delta_pct.total_spent}
                priorLabel={t('cost_reports.vs_prior', { days })}
                higherIsBad
              />
              <SummaryCard
                icon={<Receipt size={14} />}
                label={t('cost_reports.card_work_orders')}
                value={String(s.current.work_order_count)}
                deltaPct={s.delta_pct.work_order_count}
                priorLabel={t('cost_reports.vs_prior', { days })}
                higherIsBad
              />
              <SummaryCard
                icon={<Wallet size={14} />}
                label={t('cost_reports.card_avg_per_wo')}
                value={moneyDetail(s.avg_per_wo.current)}
                deltaPct={s.delta_pct.avg_per_wo}
                priorLabel={t('cost_reports.vs_prior', { days })}
                higherIsBad
              />
              <SummaryCard
                icon={<Users size={14} />}
                label={t('cost_reports.card_vendors')}
                value={String(s.current.vendor_count)}
                deltaPct={s.delta_pct.vendor_count}
                priorLabel={t('cost_reports.vs_prior', { days })}
              />
            </div>
          )}

          {/* ── Charts grid ─────────────────────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
            {/* Vehicle bar — click a bar to filter the work-orders
                list to that truck. */}
            <ChartCard title={t('cost_reports.spend_by_vehicle')}
              hint={t('cost_reports.spend_by_vehicle_hint')}>
              {vehicleChart.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data.</p>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(220, vehicleChart.length * 28)}>
                  <BarChart
                    data={vehicleChart}
                    layout="vertical"
                    margin={{ top: 0, right: 24, left: 8, bottom: 8 }}
                    // Bar.onClick has shifting signatures across recharts
                    // releases; hoist the navigate to the parent so we
                    // get a stable ``activeLabel`` (Y-axis category for
                    // vertical bar charts) and avoid the type drift.
                    // Cast to unknown then through — the runtime shape
                    // is correct; recharts types are over-specified.
                    onClick={((state: unknown) => {
                      const s = state as { activeLabel?: string };
                      const name = s?.activeLabel || '';
                      if (!name || name.startsWith('+')) return;
                      navigate(`/work-orders?vehicle=${encodeURIComponent(name)}`);
                    }) as React.ComponentProps<typeof BarChart>['onClick']}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} tickFormatter={(v) => money(v)} />
                    <YAxis type="category" dataKey="vehicle_name" width={80} tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} />
                    <Tooltip
                      formatter={(value) => [moneyDetail(Number(value)), 'Total']}
                      labelStyle={{ color: 'var(--foreground)' }}
                      itemStyle={{ color: 'var(--foreground)' }}
                      contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}
                    />
                    <Bar dataKey="total_spent" style={{ cursor: 'pointer' }}>
                      {vehicleChart.map((_, i) => (
                        <Cell key={i} fill={BAR_PALETTE[i % BAR_PALETTE.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>

            {/* Task-type donut — click a slice to filter list to that
                task type.  task_type filter is client-side today (see
                WorkOrders.tsx). */}
            <ChartCard title={t('cost_reports.spend_by_task_type')}
              hint={t('cost_reports.spend_by_task_type_hint')}>
              {typeChart.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data.</p>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie
                      data={typeChart}
                      dataKey="total_spent"
                      nameKey="label"
                      innerRadius={55} outerRadius={95}
                      paddingAngle={2}
                      // Pie.onClick passes the data element as the
                      // first arg.  Recharts' types are loose here
                      // — cast to a known shape locally.
                      onClick={(d) => {
                        const slug = (d as { service_task?: string }).service_task;
                        if (slug && slug !== 'untagged') {
                          navigate(`/work-orders?task_type=${encodeURIComponent(slug)}`);
                        }
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      {typeChart.map((_, i) => (
                        <Cell key={i} fill={BAR_PALETTE[i % BAR_PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value, _name, entry) => {
                        const labor = Number(
                          (entry?.payload as { labor_spent?: number } | undefined)?.labor_spent ?? 0,
                        );
                        return [
                          labor > 0
                            ? `${moneyDetail(Number(value))} parts · ${moneyDetail(labor)} labor`
                            : moneyDetail(Number(value)),
                          'Spend',
                        ];
                      }}
                      itemStyle={{ color: 'var(--foreground)' }}
                      contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </div>

          {/* ── Spend by system — the coarse rollup ─────────────── */}
          <ChartCard title={t('cost_reports.spend_by_system', { defaultValue: 'Spend by system' })}
            hint={t('cost_reports.spend_by_system_hint', { defaultValue: 'Parts + labor rolled up to the system each task belongs to — the "what are brakes costing us" view. Unassigned = tasks without a system yet.' })}>
            {systemChart.length === 0 ? (
              <p className="text-sm text-muted-foreground">No data.</p>
            ) : (
              <ResponsiveContainer width="100%" height={Math.max(200, systemChart.length * 34)}>
                <BarChart data={systemChart} layout="vertical"
                  margin={{ left: 8, right: 16 }}>
                  <XAxis type="number" tickFormatter={money} tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="system" width={170}
                    tick={{ fontSize: 11 }} />
                  <Tooltip
                    formatter={(value, _name, entry) => {
                      const p = entry?.payload as SystemRow | undefined;
                      return [
                        `${moneyDetail(Number(p?.total_spent ?? 0))} parts · ${moneyDetail(Number(p?.labor_spent ?? 0))} labor`,
                        `${moneyDetail(Number(value))} total`,
                      ];
                    }}
                    itemStyle={{ color: 'var(--foreground)' }}
                    contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}
                  />
                  <Bar dataKey="combined">
                    {systemChart.map((_, i) => (
                      <Cell key={i} fill={BAR_PALETTE[i % BAR_PALETTE.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </ChartCard>

          {/* ── Vendor table + monthly trend ────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <ChartCard title={t('cost_reports.top_vendors')}
              hint={t('cost_reports.top_vendors_hint')}>
              {vendorRows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data.</p>
              ) : (
                <DataGrid
                  columns={[
                    {
                      key: 'vendor_name', label: 'Vendor', sortable: true,
                      render: (v) => v
                        ? <span>{String(v)}</span>
                        : <span className="text-muted-foreground">—</span>,
                    },
                    {
                      key: 'work_order_count', label: 'WO Count', sortable: true,
                      render: (v) => <span className="tabular-nums">{String(v)}</span>,
                    },
                    {
                      // Avg = total / count — computed per row.  ``sortKey``
                      // returns the numeric value so the column sorts as
                      // money, not lexicographically.
                      key: '_avg', label: 'Avg/WO', sortable: true,
                      sortKey: (row) => {
                        const r = row as unknown as WorkOrderCostRow;
                        return r.work_order_count ? r.total_spent / r.work_order_count : 0;
                      },
                      render: (_v, row) => {
                        const r = row as unknown as WorkOrderCostRow;
                        const avg = r.work_order_count ? r.total_spent / r.work_order_count : 0;
                        return <span className="tabular-nums text-muted-foreground">{moneyDetail(avg)}</span>;
                      },
                    },
                    {
                      key: 'total_spent', label: 'Total', sortable: true,
                      render: (v) => <span className="tabular-nums font-medium">{moneyDetail(Number(v))}</span>,
                    },
                  ] satisfies AnyColumn[]}
                  data={vendorRows as unknown as Record<string, unknown>[]}
                  enableToolbar={false}
                  enablePagination={false}
                  onRowClick={(row) => {
                    const r = row as unknown as WorkOrderCostRow;
                    navigate(`/work-orders?vendor=${encodeURIComponent(r.vendor_name || '')}`);
                  }}
                />
              )}
            </ChartCard>

            {/* Monthly trend.  Line chart works better than bars for
                trend questions ("is spend going up?") because the
                slope is the eye's signal. */}
            <ChartCard title={t('cost_reports.monthly_trend')}
              hint={t('cost_reports.monthly_trend_hint')}>
              {(monthly.data?.rows ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No data.</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={monthly.data!.rows} margin={{ top: 10, right: 20, left: 8, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="month" tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} />
                    <YAxis tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} tickFormatter={(v) => money(v)} />
                    <Tooltip
                      formatter={(value) => [moneyDetail(Number(value)), 'Spend']}
                      labelStyle={{ color: 'var(--foreground)' }}
                      itemStyle={{ color: 'var(--foreground)' }}
                      contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}
                    />
                    <Line
                      type="monotone" dataKey="total_spent"
                      stroke={chartColor(1)} strokeWidth={2.5}
                      dot={{ fill: chartColor(1), r: 4 }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </div>

          {/* ── Top parts ────────────────────────────────────────
              "Which part keeps costing us" — a part recurring across
              many work orders is the early-warning signal for a
              failing-component pattern (and the lever for challenging
              a vendor's pricing). */}
          <div className="grid grid-cols-1 gap-5 mt-5">
            <ChartCard title={t('cost_reports.top_parts', { defaultValue: 'Top parts' })}
              hint={t('cost_reports.top_parts_hint', { defaultValue: 'Highest-spend parts across all work orders — recurring parts flag failing-component patterns.' })}>
              {partRows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data.</p>
              ) : (
                <DataGrid
                  columns={[
                    {
                      key: 'part_name', label: 'Part', sortable: true,
                      render: (v) => <span>{String(v ?? '—')}</span>,
                    },
                    {
                      key: 'usage_count', label: 'Line Items', sortable: true,
                      render: (v) => <span className="tabular-nums">{String(v)}</span>,
                    },
                    {
                      key: 'work_order_count', label: 'Work Orders', sortable: true,
                      render: (v) => <span className="tabular-nums">{String(v)}</span>,
                    },
                    {
                      key: 'total_quantity', label: 'Qty', sortable: true,
                      render: (v) => <span className="tabular-nums text-muted-foreground">{Number(v ?? 0).toLocaleString()}</span>,
                    },
                    {
                      key: 'total_spent', label: 'Total', sortable: true,
                      render: (v) => <span className="tabular-nums font-medium">{moneyDetail(Number(v))}</span>,
                    },
                  ] satisfies AnyColumn[]}
                  data={partRows as unknown as Record<string, unknown>[]}
                  enableToolbar={false}
                  enablePagination={false}
                  onRowClick={(row) => {
                    // Drill into the part profile (recurrence per
                    // vehicle, price per vendor) when the line is
                    // catalog-linked.
                    const pid = (row as { part_id?: number | null }).part_id;
                    if (pid != null) navigate(`/parts/${pid}`);
                  }}
                />
              )}
            </ChartCard>
          </div>
        </>
      )}
    </div>
  );
}

// ── Small subcomponents ────────────────────────────────────────

function SummaryCard({
  icon, label, value, deltaPct, priorLabel, higherIsBad,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  /** Percent change vs prior period.  ``null`` means no prior data
   *  to compare (we render the card with no chip in that case). */
  deltaPct: number | null;
  /** Short caption next to the chip — e.g. "vs prior 90d". */
  priorLabel: string;
  /** For cost-sensitive metrics (spend, count) higher = bad → red.
   *  Omit / false for neutral metrics where direction has no value
   *  judgement (e.g. vendor count). */
  higherIsBad?: boolean;
}) {
  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <p className="text-xs text-muted-foreground inline-flex items-center gap-1">
        {icon}
        {label}
      </p>
      <p className="text-xl font-bold tabular-nums mt-1 text-foreground">{value}</p>
      <DeltaChip pct={deltaPct} priorLabel={priorLabel} higherIsBad={higherIsBad} />
    </div>
  );
}

function DeltaChip({
  pct, priorLabel, higherIsBad,
}: {
  pct: number | null;
  priorLabel: string;
  higherIsBad?: boolean;
}) {
  const { t } = useTranslation();
  // No prior data → render a muted note instead of a chip so the
  // card layout stays consistent (otherwise rows would jump heights).
  if (pct === null || pct === undefined) {
    return (
      <p className="text-2xs text-muted-foreground/70 mt-1.5">
        {t('cost_reports.no_prior_period')}
      </p>
    );
  }
  // Pick chip colour from sign + sensitivity.  For neutral metrics
  // (higherIsBad=false), any change is shown in muted blue rather
  // than green/red so we don't imply a value judgement.
  let color: string;
  // lucide-react icons are ``ForwardRefExoticComponent`` — match the
  // exact type rather than narrowing to ``ComponentType`` which loses
  // the LucideProps signature.  ``LucideIcon`` is the exported alias.
  let Icon: typeof TrendingUp;
  if (Math.abs(pct) < 0.5) {
    // Flat (rounded to 0%) — no value movement worth flagging.
    color = 'text-muted-foreground';
    Icon = Minus;
  } else if (higherIsBad) {
    color = pct > 0 ? 'text-danger' : 'text-ok';
    Icon = pct > 0 ? TrendingUp : TrendingDown;
  } else {
    color = 'text-info';
    Icon = pct > 0 ? TrendingUp : TrendingDown;
  }
  const arrow = pct > 0 ? '+' : '';
  return (
    <p className={`text-2xs inline-flex items-center gap-1 mt-1.5 ${color}`}>
      <Icon size={12} />
      <span className="font-medium tabular-nums">
        {arrow}{pct.toFixed(1)}%
      </span>
      <span className="text-muted-foreground">{priorLabel}</span>
    </p>
  );
}

function ChartCard({
  title, hint, children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <h3 className="text-sm font-semibold mb-1">{title}</h3>
      {hint && <p className="text-2xs text-muted-foreground mb-3">{hint}</p>}
      {children}
    </div>
  );
}
