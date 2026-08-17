/**
 * My payouts — a dispatcher's own finalized incentive payouts.
 *
 * SELF-SCOPED by design: the endpoint (/kpi/dispatch/me) only ever
 * returns the caller's own rows, so the route carries no can_* flag —
 * ``can_kpi`` is for the people who RUN the settlement, not the
 * people it pays.  Finalized runs only: a draft number that
 * could still drop is never shown as "your payout".
 */

import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BadgeDollarSign } from 'lucide-react';
import DataGrid from '../../../components/datagrid';
import {
  EmptyState, ErrorState, PageHeader, TableSkeleton,
} from '../../../components/shell';
import type { AnyColumn } from '../../../types';
import { getMyPayouts } from '../api';

function usd(v: unknown): string {
  if (v == null) return '—';
  return `$${Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

const COLUMNS: AnyColumn[] = [
  { key: 'vehicle_unit', label: 'Unit', sortable: true },
  { key: 'company_code', label: 'Company', sortable: true },
  { key: 'total_days', label: 'Days',
    render: (_v, r) => (
      <span className="tabular-nums">
        {Number(r.total_days) - Number(r.inactive_days)}/{String(r.total_days)}
      </span>
    ) },
  { key: 'kpi_gross', label: 'Gross', sortable: true,
    render: (v) => <span className="tabular-nums">{usd(v)}</span> },
  { key: 'miles', label: 'Miles', sortable: true,
    render: (v) => <span className="tabular-nums">{Number(v).toLocaleString()}</span> },
  { key: 'rpm', label: 'RPM',
    render: (v) => <span className="tabular-nums">{v == null ? '—' : Number(v).toFixed(2)}</span> },
  { key: 'pct', label: 'KPI %',
    render: (v) => <span className="tabular-nums">{Number(v)}%</span> },
  { key: 'confirmed_dollars', label: 'Payout', sortable: true,
    render: (v) => <span className="tabular-nums font-medium">{usd(v)}</span> },
];

export default function MyPayouts() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ['kpi-my-payouts'], queryFn: getMyPayouts });

  const runs = q.data?.runs ?? [];
  const grand = runs.reduce((a, r) => a + r.total, 0);

  return (
    <div>
      <PageHeader
        icon={BadgeDollarSign}
        title={t('my_payouts.title', 'My payouts')}
        description={t(
          'my_payouts.desc',
          'Your incentive payouts, per truck per period — finalized periods only. Questions about a number go to your manager.',
        )}
      />

      {q.isLoading && <TableSkeleton />}
      {!q.isLoading && q.error != null && (
        <ErrorState message={q.error instanceof Error ? q.error.message : 'Failed to load'} />
      )}
      {!q.isLoading && q.error == null && runs.length === 0 && (
        <EmptyState
          icon={BadgeDollarSign}
          title={t('my_payouts.empty_title', 'No finalized payouts yet')}
          description={t(
            'my_payouts.empty_desc',
            'Payouts appear here after your manager finalizes a period that includes your trucks.',
          )}
        />
      )}

      {runs.length > 0 && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t('my_payouts.summary', '{{n}} finalized periods', { n: runs.length })}
            {' · '}
            <span className="font-medium text-foreground tabular-nums">{usd(grand)}</span>
          </p>
          {runs.map((run) => (
            <section key={run.run_id} className="space-y-2">
              <div className="flex items-baseline justify-between">
                <h2 className="text-base font-semibold tabular-nums">
                  {run.period_start} – {run.period_end}
                </h2>
                <span className="text-base font-semibold tabular-nums">{usd(run.total)}</span>
              </div>
              <DataGrid
                tableId={`kpi-my-payouts-${run.run_id}`}
                columns={COLUMNS}
                data={run.rows as unknown as Record<string, unknown>[]}
                enableToolbar={false}
                enablePagination={false}
              />
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
