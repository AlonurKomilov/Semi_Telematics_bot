/**
 * What a MONTH pays — finalized runs only, with the open drafts named
 * beside it so nobody reads the total as the whole story.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../../../components/ui/select';
import { TableSkeleton } from '../../../../components/shell';
import { getMonthlyPayouts, type RunSummary } from '../../api';
import { usd } from './format';
import { Card } from '@/components/ui/card';

/** The last 12 months, newest first, as YYYY-MM + a human label. */
function monthOptions(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  const d = new Date();
  for (let i = 0; i < 12; i += 1) {
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    out.push({
      value,
      label: d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }),
    });
    d.setMonth(d.getMonth() - 1);
  }
  return out;
}

export function MonthlyPayoutsPanel({ allRuns, onSelectRun }: {
  allRuns: RunSummary[];
  onSelectRun: (id: number) => void;
}) {
  const { t } = useTranslation();
  const months = monthOptions();
  const [month, setMonth] = useState(months[0].value);
  // Drafts whose period ends in the viewed month: the concrete money
  // that finalizing would add — the honest version of "no data yet".
  const pendingDrafts = allRuns.filter(
    (r) => r.status === 'draft' && r.period_end.slice(0, 7) === month);
  const q = useQuery({
    queryKey: ['kpi-monthly-payouts', month],
    queryFn: () => getMonthlyPayouts(month),
  });
  const payouts = Object.entries(q.data?.payouts ?? {})
    .sort((a, b) => b[1] - a[1]);

  return (
    <Card className="mt-8 space-y-3" render={<section />}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">
            {t('kpi_runs.monthly_title', 'Monthly payouts')}
          </h2>
          <p className="text-xs text-muted-foreground max-w-prose">
            {t('kpi_runs.monthly_desc',
              'Finalized runs only, summed per dispatcher — a run counts in the month its period ends. For dispatchers paid monthly, this is the number payroll uses.')}
          </p>
        </div>
        <Select value={month} onValueChange={setMonth}
          items={months.map((m) => ({ value: m.value, label: m.label }))}>
          <SelectTrigger className="w-44" aria-label={t('kpi_runs.month', 'Month')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {months.map((m) => (
              <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {q.isLoading && <TableSkeleton />}
      {!q.isLoading && payouts.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.monthly_empty',
            'No finalized runs end in this month yet — drafts do not count toward a payout total.')}
        </p>
      )}
      {pendingDrafts.length > 0 && (
        <div className="space-y-1 text-xs text-muted-foreground">
          {pendingDrafts.map((d) => (
            <p key={d.id}>
              {t('kpi_runs.monthly_pending2', 'Finalizing the draft ')}
              <button type="button" onClick={() => onSelectRun(d.id)}
                className="tabular-nums underline underline-offset-4 hover:text-foreground transition">
                {d.period_start} – {d.period_end}
              </button>
              {t('kpi_runs.monthly_pending3', ' would add {{total}} to this month.',
                { total: usd(d.total) })}
            </p>
          ))}
        </div>
      )}
      {payouts.length > 0 && q.data && (
        <>
          <ul className="divide-y divide-border border-t border-border">
            {payouts.map(([name, total]) => (
              <li key={name} className="flex items-center justify-between py-2 text-sm">
                <span>{name}</span>
                <span className="font-medium tabular-nums">{usd(total)}</span>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              {t('kpi_runs.monthly_runs', '{{n}} finalized runs', { n: q.data.runs.length })}
              {': '}
              <span className="tabular-nums">
                {q.data.runs.map((r) => `${r.period_start} – ${r.period_end}`).join(', ')}
              </span>
            </span>
            <span className="text-base font-semibold tabular-nums">{usd(q.data.total)}</span>
          </div>
        </>
      )}
    </Card>
  );
}
