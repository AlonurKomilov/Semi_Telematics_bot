/**
 * Driver settlement statement — the itemized document behind a payroll run
 * item.  Lists every counted load (date, load #, route, rate, this load's
 * pay), then each addition (layover / TONU / detention), then the totals —
 * the verifiable "settlement" a trucking accountant hands a driver.
 *
 * The lines are a FROZEN snapshot taken at run time (statement_json), so a
 * later load edit never rewrites a finalized statement.  Print → the
 * browser's print dialog (→ PDF); CSV → a spreadsheet download.
 */

import { X, Printer, Download } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type {
  Statement, StatementLoadLine, StatementAddition, StatementDeduction,
} from './Payroll';

const money = (c: number | null | undefined) =>
  c == null ? '$0.00'
    : `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function toCsv(
  driver: string, period: string, st: Statement,
): string {
  const rows: string[][] = [
    [`Settlement — ${driver} — ${period}`],
    [],
    ['LOADS'],
    ['Date', 'Load #', 'Route', 'Rate', 'Pay'],
    ...(st.loads ?? []).map((l) => [
      l.date, l.load_number, l.route,
      money(l.rate_cents), money(l.pay_cents),
    ]),
    [],
    ['ADDITIONS'],
    ['Date', 'Type', 'Note', 'Load #', 'Amount'],
    ...(st.additions ?? []).map((a) => [
      a.date, a.kind, a.notes, a.load_number, money(a.amount_cents),
    ]),
    [],
    ['DEDUCTIONS'],
    ['Date', 'Type', 'Note', 'Amount'],
    ...(st.deductions ?? []).map((d) => [
      d.date, d.kind, d.notes, `-${money(d.amount_cents)}`,
    ]),
    [],
    ['Base pay', money(st.base_pay_cents)],
    ['Load earnings', money(st.load_earnings_cents)],
    ['Additions', money(st.extras_cents)],
    ['Bonuses', money(st.bonus_total_cents)],
    ['Gross', money(st.total_cents)],
    ['Deductions', `-${money(st.deductions_cents)}`],
    ['NET PAY', money(st.net_cents)],
  ];
  return rows
    .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(','))
    .join('\n');
}

export default function StatementDrawer({
  driver, period, statement, onClose,
}: {
  driver: string;
  period: string;
  statement: Statement;
  onClose: () => void;
}) {
  const loads: StatementLoadLine[] = statement.loads ?? [];
  const additions: StatementAddition[] = statement.additions ?? [];
  const deductions: StatementDeduction[] = statement.deductions ?? [];

  const downloadCsv = () => {
    const blob = new Blob([toCsv(driver, period, statement)], {
      type: 'text/csv;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `settlement-${driver.replace(/\s+/g, '_')}-${period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-y-auto bg-card border-l border-border print:max-w-none print:border-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3 print:hidden">
          <span className="text-base font-semibold text-foreground">Settlement statement</span>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="xs" onClick={() => window.print()}>
              <Printer size={14} className="mr-1" /> Print
            </Button>
            <Button type="button" variant="outline" size="xs" onClick={downloadCsv}>
              <Download size={14} className="mr-1" /> CSV
            </Button>
            <button type="button" onClick={onClose} aria-label="Close" className="p-1 text-muted-foreground hover:text-foreground">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="px-5 py-4 text-sm">
          <div className="mb-4">
            <div className="text-lg font-semibold text-foreground">{driver}</div>
            <div className="text-xs text-muted-foreground">Period {period}</div>
          </div>

          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Loads</div>
          {loads.length === 0 ? (
            <p className="mb-4 text-sm text-muted-foreground">No loads in this period.</p>
          ) : (
            <table className="mb-4 w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-1 text-left font-medium">Date</th>
                  <th className="py-1 text-left font-medium">Load #</th>
                  <th className="py-1 text-left font-medium">Route</th>
                  <th className="py-1 text-right font-medium">Rate</th>
                  <th className="py-1 text-right font-medium">Pay</th>
                </tr>
              </thead>
              <tbody>
                {loads.map((l, i) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="py-1 text-foreground">{l.date || '—'}</td>
                    <td className="py-1 font-mono text-foreground">{l.load_number || '—'}</td>
                    <td className="py-1 text-muted-foreground">{l.route || '—'}</td>
                    <td className="py-1 text-right tabular-nums text-muted-foreground">{money(l.rate_cents)}</td>
                    <td className="py-1 text-right tabular-nums font-medium text-foreground">{money(l.pay_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {additions.length > 0 && (
            <>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Additions</div>
              <table className="mb-4 w-full text-xs">
                <tbody>
                  {additions.map((a, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-1 text-foreground">{a.date || '—'}</td>
                      <td className="py-1 capitalize text-foreground">{a.kind}</td>
                      <td className="py-1 text-muted-foreground">
                        {a.notes || (a.load_number ? `Load ${a.load_number}` : '')}
                      </td>
                      <td className="py-1 text-right tabular-nums font-medium text-foreground">{money(a.amount_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {deductions.length > 0 && (
            <>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Deductions</div>
              <table className="mb-4 w-full text-xs">
                <tbody>
                  {deductions.map((d, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-1 text-foreground">{d.date || (d.recurring ? 'recurring' : '—')}</td>
                      <td className="py-1 capitalize text-foreground">{d.kind.replace('_', ' ')}</td>
                      <td className="py-1 text-muted-foreground">{d.notes}</td>
                      <td className="py-1 text-right tabular-nums font-medium text-danger">−{money(d.amount_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <dl className="mt-4 space-y-1 border-t border-border pt-3 text-sm">
            <Row label="Base pay" value={money(statement.base_pay_cents)} />
            <Row label="Load earnings" value={money(statement.load_earnings_cents)} />
            <Row label="Additions" value={money(statement.extras_cents)} />
            <Row label="Bonuses" value={money(statement.bonus_total_cents)} />
            <div className="flex justify-between pt-1">
              <dt className="text-muted-foreground">Gross</dt>
              <dd className="tabular-nums text-foreground">{money(statement.total_cents)}</dd>
            </div>
            {(statement.deductions_cents ?? 0) > 0 && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Deductions</dt>
                <dd className="tabular-nums text-danger">−{money(statement.deductions_cents)}</dd>
              </div>
            )}
            <div className="flex justify-between border-t border-border pt-2 text-base font-semibold text-foreground">
              <dt>Net pay</dt>
              <dd className="tabular-nums">{money(statement.net_cents ?? statement.total_cents)}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
