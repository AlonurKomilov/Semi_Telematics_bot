import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import { usd } from './Accounts';
import type { InvoiceWithAccount } from '../types';

// Cross-account invoice search.  Mirrors Stripe's own invoice search
// in the Stripe Dashboard but constrained to our locally-mirrored rows
// (the ``billing_invoices`` table).  Useful for:
//   - "find this Stripe invoice id" (search by partial id via account name)
//   - "show me all open invoices right now" (status=open)
//   - "everything paid in May" (date filters)

export default function InvoicesPage() {
  const [rows, setRows] = useState<InvoiceWithAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  // Filters — all optional; an empty query returns the 100 newest.
  const [status, setStatus] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [accountSearch, setAccountSearch] = useState('');

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '200' });
    if (status) qs.set('status', status);
    if (from)   qs.set('from_date', from);
    if (to)     qs.set('to_date',   to);
    if (accountSearch.trim()) qs.set('account_search', accountSearch.trim());
    apiJSON<{ items: InvoiceWithAccount[] }>(`/system/invoices?${qs}`)
      .then((r) => setRows(r.items))
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Aggregate footers — at-a-glance totals over the loaded set.
  const totalPaid     = rows.reduce((a, r) => a + (r.amount_paid_cents || 0), 0);
  const totalOpenDue  = rows.filter((r) => r.status === 'open')
                            .reduce((a, r) => a + (r.amount_due_cents - r.amount_paid_cents), 0);

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Invoices</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Cross-account view of locally-mirrored Stripe invoices.  Receipts
          link to Stripe's hosted invoice page.  Refresh by re-applying any
          filter or hitting "Apply".
        </p>
      </header>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 flex flex-wrap items-center gap-2">
        <input
          value={accountSearch}
          onChange={(e) => setAccountSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load(); }}
          placeholder="Account name contains…"
          className="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm flex-1 min-w-[200px]"
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">Any status</option>
          <option value="paid">paid</option>
          <option value="open">open</option>
          <option value="uncollectible">uncollectible</option>
          <option value="void">void</option>
        </select>
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
               className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm"
               title="Created on or after" />
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
               className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm"
               title="Created on or before" />
        <button onClick={load}
                className="bg-accent text-white text-xs px-3 py-1.5 rounded hover:bg-accent/90">
          Apply
        </button>
      </div>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left px-3 py-2">Created</th>
              <th className="text-left px-3 py-2">Account</th>
              <th className="text-left px-3 py-2">Status</th>
              <th className="text-left px-3 py-2">Period</th>
              <th className="text-right px-3 py-2">Due</th>
              <th className="text-right px-3 py-2">Paid</th>
              <th className="text-left px-3 py-2">Stripe id</th>
              <th className="text-right px-3 py-2">Receipt</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="text-center text-slate-500 py-8">Loading…</td></tr>
            )}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={8} className="text-center text-slate-500 py-8">No invoices match.</td></tr>
            )}
            {rows.map((inv) => (
              <tr key={inv.id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                <td className="px-3 py-2 text-slate-400 tabular-nums">
                  {inv.created_at.slice(0, 10)}
                </td>
                <td className="px-3 py-2">
                  <Link to={`/accounts/${inv.account_id}`} className="text-slate-100 hover:text-accent">
                    {inv.account_name}
                  </Link>
                </td>
                <td className="px-3 py-2"><InvoiceStatus status={inv.status} /></td>
                <td className="px-3 py-2 text-xs text-slate-500">
                  {inv.period_start?.slice(0, 10) || '—'}
                  {inv.period_end && <> → {inv.period_end.slice(0, 10)}</>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-200">
                  {usd(inv.amount_due_cents)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-300">
                  {inv.amount_paid_cents > 0 ? usd(inv.amount_paid_cents) : '—'}
                </td>
                <td className="px-3 py-2 text-xs font-mono text-slate-500 truncate max-w-[180px]"
                    title={inv.provider_invoice_id}>
                  {inv.provider_invoice_id || '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  {inv.hosted_invoice_url ? (
                    <a href={inv.hosted_invoice_url} target="_blank" rel="noopener noreferrer"
                       className="text-accent hover:underline text-xs">view</a>
                  ) : <span className="text-slate-600 text-xs">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500">
          <span>{rows.length} invoice{rows.length === 1 ? '' : 's'}</span>
          <span>Total paid: <span className="text-slate-300 tabular-nums">{usd(totalPaid)}</span></span>
          {totalOpenDue > 0 && (
            <span>Open + unpaid: <span className="text-warn tabular-nums">{usd(totalOpenDue)}</span></span>
          )}
        </div>
      )}
    </div>
  );
}

function InvoiceStatus({ status }: { status: string }) {
  const cls = status === 'paid' ? 'text-ok' :
              status === 'open' ? 'text-warn' :
              (status === 'uncollectible' || status === 'void') ? 'text-danger' :
              'text-slate-500';
  return <span className={`text-xs ${cls}`}>{status || '—'}</span>;
}
