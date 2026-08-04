/**
 * Driver settlement statement — the itemized document behind a driver-pay
 * run item.  Lists every counted load (date, load #, route, rate, this
 * load's pay), then additions, then deductions, then Gross → NET — the
 * verifiable "settlement" a trucking accountant hands a driver.
 *
 * The lines are a snapshot taken at run time (statement_json).  On a DRAFT
 * run the accountant can add an addition/deduction INLINE (for something the
 * dispatcher forgot) without leaving the page: it writes the same load
 * line-item the load form writes, then recomputes the draft in place.  A
 * FINALIZED statement is locked — a settled document never silently changes.
 * Print → the browser dialog (→ PDF); CSV → a spreadsheet download.
 */

import { useState } from 'react';
import { X, Printer, Download, Plus, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../components/ui/button';
import { apiFetch } from '../../api/client';
import { createLineItem, LINE_ITEM_BUCKET } from '../loads/api';
import type {
  Statement, StatementLoadLine, StatementAddition, StatementDeduction,
} from './DriverPay';

const money = (c: number | null | undefined) =>
  c == null ? '$0.00'
    : `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Kinds offered in the inline editor, grouped by which side of net they hit.
const ADD_KINDS = ['bonus', 'tonu', 'detention', 'layover'];
const DEDUCT_KINDS = ['advance', 'escrow', 'insurance', 'fuel_card', 'charge'];

function toCsv(driver: string, period: string, st: Statement): string {
  const rows: string[][] = [
    [`Settlement — ${driver} — ${period}`],
    [],
    ['LOADS'],
    ['Date', 'Load #', 'Route', 'Rate', 'Pay'],
    ...(st.loads ?? []).map((l) => [
      l.date, l.load_number, l.route, money(l.rate_cents), money(l.pay_cents),
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
  driver, period, statement, runId, runStatus, onChanged, onClose,
}: {
  driver: string;
  period: string;
  statement: Statement;
  runId?: number;
  runStatus?: 'draft' | 'finalized' | 'cancelled';
  onChanged?: () => void | Promise<void>;
  onClose: () => void;
}) {
  const loads: StatementLoadLine[] = statement.loads ?? [];
  const additions: StatementAddition[] = statement.additions ?? [];
  const deductions: StatementDeduction[] = statement.deductions ?? [];
  const zeroPay = statement.zero_pay_loads ?? 0;
  const driverUserId = statement.driver_user_id ?? null;

  // Inline editing is offered only on a DRAFT run and only when we know the
  // driver's user id (needed to attach the line item).
  const editable = runStatus === 'draft' && driverUserId != null && runId != null;

  const [kind, setKind] = useState('bonus');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  // The statement period is "start → end"; attach a manual entry to the
  // period end so it lands inside the window on recompute.
  const periodEnd = period.split('→').pop()?.trim() || '';

  const addEntry = async () => {
    if (busy || driverUserId == null || runId == null) return;
    const amt = Number(amount);
    if (!amt || amt <= 0) return;
    setBusy(true);
    try {
      await createLineItem({
        kind,
        amount: amt,
        driver_user_id: driverUserId,
        item_date: periodEnd,
        notes: note,
      });
      // Recompute the draft so the statement reflects the new line.
      await apiFetch(`/driver-pay/runs/${runId}/recompute`, { method: 'POST' });
      setAmount('');
      setNote('');
      toast.success(LINE_ITEM_BUCKET[kind] === 'deduction' ? 'Deduction added' : 'Addition added');
      await onChanged?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add the entry');
    } finally {
      setBusy(false);
    }
  };

  const downloadCsv = () => {
    const blob = new Blob([toCsv(driver, period, statement)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `settlement-${driver.replace(/\s+/g, '_')}-${period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const selectCls = 'bg-muted border border-input rounded-lg px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring';

  return (
    // ⚠️ Hand-rolled backdrop, and ESLint says so.  NOT an oversight and
    // not yet fixable: <Sheet> PORTALS its content to the end of <body>,
    // and this drawer's whole purpose is ``window.print()``.  Nothing in
    // the app hides the page BEHIND the drawer when printing (there is no
    // global ``@media print`` block — only per-element ``print:*``
    // utilities), so moving the content into a portal changes what lands
    // on paper in a way no test here can catch.  Converting this one needs
    // a real print check first, and probably a global print stylesheet.
    // eslint-disable-next-line no-restricted-syntax -- see above; blocked on print verification
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
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
          {/* Header — self-identifies on paper: draft vs final + generated date. */}
          <div className="mb-4">
            <div className="text-lg font-semibold text-foreground">{driver}</div>
            <div className="text-xs text-muted-foreground">Period {period}</div>
            <div className="mt-0.5 text-2xs">
              {runStatus === 'finalized'
                ? <span className="text-ok">Final settlement</span>
                : <span className="text-warn">Draft — not yet finalized</span>}
              <span className="text-muted-foreground"> · generated {new Date().toISOString().slice(0, 10)}</span>
            </div>
          </div>

          {/* Zero-pay warning — a delivered load resolving to $0 underpays silently. */}
          {zeroPay > 0 && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-warn-bd bg-warn-bg px-3 py-2 text-xs text-warn print:border">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                {zeroPay} delivered load{zeroPay === 1 ? '' : 's'} resolved to $0 pay —
                set this driver&apos;s pay model or enter pay on the load, or they&apos;ll be underpaid.
              </span>
            </div>
          )}

          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Loads</div>
          {loads.length === 0 ? (
            <p className="mb-4 text-sm text-muted-foreground">No loads in this period.</p>
          ) : (
            /* Raw <table>, deliberately — one of the four documented
               exceptions to "Tables = DataGrid" (datagrid/CLAUDE.md):
               this is a printable DOCUMENT, not a record list.  A grid
               would bring a toolbar, search, pagination and column menus
               to something whose target is paper, and every one of them
               would need ``print:hidden``.  The same reasoning covers the
               two sibling tables below. */
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
                    <td className={`py-1 text-right tabular-nums font-medium ${l.pay_cents === 0 ? 'text-warn' : 'text-foreground'}`}>{money(l.pay_cents)}</td>
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
                      <td className="py-1 text-muted-foreground">{a.notes || (a.load_number ? `Load ${a.load_number}` : '')}</td>
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
                      <td className="py-1 text-foreground">{d.date || '—'}</td>
                      <td className="py-1 capitalize text-foreground">{d.kind.replace('_', ' ')}</td>
                      <td className="py-1 text-muted-foreground">{d.notes}</td>
                      <td className="py-1 text-right tabular-nums font-medium text-danger">−{money(d.amount_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {/* Inline editor — draft only.  Fix a forgotten entry without leaving. */}
          {editable && (
            <div className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2 print:hidden">
              <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">Add to this statement</div>
              <div className="flex flex-wrap items-center gap-2">
                <select className={selectCls} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Entry type">
                  <optgroup label="Addition">
                    {ADD_KINDS.map((k) => <option key={k} value={k}>+ {k}</option>)}
                  </optgroup>
                  <optgroup label="Deduction">
                    {DEDUCT_KINDS.map((k) => <option key={k} value={k}>− {k.replace('_', ' ')}</option>)}
                  </optgroup>
                </select>
                <input className={`${selectCls} w-24`} type="number" min="0" placeholder="Amount $" value={amount} onChange={(e) => setAmount(e.target.value)} aria-label="Amount" />
                <input className={`${selectCls} flex-1 min-w-[6rem]`} placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
                <Button type="button" variant="outline" size="xs" disabled={busy || !Number(amount)} onClick={() => { void addEntry(); }}>
                  <Plus size={12} className="mr-1" /> Add
                </Button>
              </div>
            </div>
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
