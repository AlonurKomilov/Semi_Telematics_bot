/** A price break for one account, on the operator's account page.
 *
 *  A comp makes an account free; this makes it cheaper, for a bounded
 *  time — "$100 off for 3 months", "20% off until we revoke it". The
 *  money is Stripe's to compute: each grant becomes a coupon on the
 *  account's subscription, so the reduced amount is what the card is
 *  charged and the minus line prints itself on the invoice and the PDF.
 *
 *  The card shows what the next bill actually comes to, read from the
 *  provider rather than computed here — the number the operator is
 *  about to promise someone.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import { Button } from './ui/Button';
import { Dialog } from './ui/Dialog';
import { INPUT_CLS as inputCls } from './ui/Input';

interface Discount {
  id: number;
  kind: 'amount' | 'percent';
  amount_off_cents: number;
  percent_off: number;
  months: number;
  reason: string;
  granted_by: string;
  status: 'pending' | 'active' | 'ended' | 'revoked';
  starts_at: string | null;
  ends_at: string | null;
  created_at: string;
}

interface NextInvoice { subtotal_cents: number; discount_cents: number; total_cents: number }

const usd = (c: number) => `$${(c / 100).toFixed(2)}`;
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '—');

function describe(d: Discount): string {
  const off = d.kind === 'percent' ? `${d.percent_off}% off` : `${usd(d.amount_off_cents)} off`;
  const how_long = d.months > 0 ? `for ${d.months} month${d.months === 1 ? '' : 's'}` : 'until revoked';
  return `${off} ${how_long}`;
}

export function DiscountCard({ accountId, isComped }: { accountId: number; isComped: boolean }) {
  const [live, setLive] = useState<Discount | null>(null);
  const [history, setHistory] = useState<Discount[]>([]);
  const [next, setNext] = useState<NextInvoice | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [granting, setGranting] = useState(false);

  const load = useCallback(async () => {
    setErr('');
    try {
      const r = await apiJSON<{ discount: Discount | null; history: Discount[]; next_invoice: NextInvoice | null }>(
        `/system/accounts/${accountId}/discount`);
      setLive(r.discount);
      setHistory(r.history);
      setNext(r.next_invoice);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not read the discount');
    }
  }, [accountId]);

  useEffect(() => { load(); }, [load]);

  const revoke = async () => {
    if (!window.confirm('Take the discount off?  Bills already issued keep it; the next one is full price.')) return;
    setBusy(true);
    setErr('');
    try {
      await apiJSON(`/system/accounts/${accountId}/discount`, { method: 'DELETE' });
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not revoke');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="border border-slate-800 rounded-lg">
      <header className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-800 bg-slate-900/40">
        <h2 className="text-sm font-semibold text-slate-200">Discount</h2>
        <div className="ml-auto flex gap-2">
          {live ? (
            <Button variant="danger" disabled={busy} onClick={revoke}>Revoke</Button>
          ) : (
            <Button variant="primary" disabled={isComped} onClick={() => setGranting(true)}>Grant discount</Button>
          )}
        </div>
      </header>
      <div className="px-4 py-3">
        {err && <p className="text-sm text-danger mb-2" role="alert">{err}</p>}
        {isComped && !live && (
          <p className="text-sm text-slate-400">
            This account is comped — it already pays nothing. Revoke the comp first if it should
            pay a reduced amount instead.
          </p>
        )}
        {live ? (
          <dl className="text-sm space-y-1.5">
            <Row label="Discount" value={describe(live)} accent="text-accent" />
            <Row label="Reason" value={live.reason || '—'} />
            <Row label="Status" value={live.status} />
            <Row label="Runs" value={`${day(live.starts_at)} → ${day(live.ends_at) }`} />
            <Row label="Granted by" value={live.granted_by || '—'} />
            {live.status === 'pending' && (
              <p className="text-xs text-warn pt-1">
                Waiting for this account's first checkout — the coupon rides it and starts then.
              </p>
            )}
          </dl>
        ) : !isComped && (
          <p className="text-sm text-slate-400">No discount. This account pays the full plan price.</p>
        )}
        {next && (
          <dl className="text-sm space-y-1 mt-3 pt-3 border-t border-slate-800">
            <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Next invoice, per the provider</p>
            <Row label="Amount" value={usd(next.subtotal_cents)} />
            {next.discount_cents > 0 && (
              <Row label="Promotion" value={`-${usd(next.discount_cents)}`} accent="text-accent" />
            )}
            <Row label="Total to charge" value={usd(next.total_cents)} accent="text-ok" />
          </dl>
        )}
        {history.length > 1 && (
          <details className="mt-3 pt-3 border-t border-slate-800">
            <summary className="text-xs text-slate-500 cursor-pointer">Earlier discounts ({history.length - 1})</summary>
            <ul className="mt-2 space-y-1">
              {history.filter((h) => h.id !== live?.id).map((h) => (
                <li key={h.id} className="text-xs text-slate-400">
                  {describe(h)} · {h.status} · {day(h.created_at)}{h.reason ? ` · ${h.reason}` : ''}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {granting && (
        <GrantDiscountDialog
          accountId={accountId}
          onClose={() => setGranting(false)}
          onDone={() => { setGranting(false); load(); }}
        />
      )}
    </section>
  );
}

function Row({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500 text-xs">{label}</dt>
      <dd className={`text-right ${accent ?? 'text-slate-200'}`}>{value}</dd>
    </div>
  );
}

function GrantDiscountDialog({ accountId, onClose, onDone }: {
  accountId: number; onClose: () => void; onDone: () => void;
}) {
  const [kind, setKind] = useState<'amount' | 'percent'>('amount');
  const [amount, setAmount] = useState('100');
  const [percent, setPercent] = useState('20');
  const [months, setMonths] = useState('3');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    setBusy(true);
    setErr('');
    try {
      await apiJSON(`/system/accounts/${accountId}/discount`, {
        method: 'POST',
        body: {
          account_id: accountId,
          kind,
          amount_off_cents: kind === 'amount' ? Math.round(Number(amount) * 100) : 0,
          percent_off: kind === 'percent' ? Number(percent) : 0,
          months: Number(months) || 0,
          reason: reason.trim(),
        },
      });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not grant the discount');
    } finally {
      setBusy(false);
    }
  };

  const off = kind === 'amount' ? `$${Number(amount || 0).toFixed(2)}` : `${percent}%`;
  const span = Number(months) > 0 ? `for ${months} month${months === '1' ? '' : 's'}` : 'until revoked';

  return (
    <Dialog title="Grant a discount" onClose={onClose} size="lg">
      <div className="space-y-4">
        <fieldset className="flex gap-2">
          {(['amount', 'percent'] as const).map((k) => (
            <label key={k} className={`flex-1 rounded border px-3 py-2 cursor-pointer text-sm ${
              kind === k ? 'border-accent bg-accent/10 text-slate-100' : 'border-slate-800 text-slate-400'}`}>
              <input type="radio" name="kind" className="mr-2" checked={kind === k} onChange={() => setKind(k)} />
              {k === 'amount' ? 'A fixed amount off' : 'A percentage off'}
            </label>
          ))}
        </fieldset>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">
              {kind === 'amount' ? 'Dollars off each bill' : 'Percent off each bill'}
            </span>
            {kind === 'amount'
              ? <input className={inputCls} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
              : <input className={inputCls} value={percent} onChange={(e) => setPercent(e.target.value)} inputMode="numeric" />}
          </label>
          <label className="block">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">Months (0 = until revoked)</span>
            <input className={inputCls} value={months} onChange={(e) => setMonths(e.target.value)} inputMode="numeric" />
          </label>
        </div>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wide text-slate-500">Reason</span>
          <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)}
                 placeholder="What the customer is being thanked for — they read this on their bill" />
        </label>
        <p className="text-sm text-slate-300">
          {off} off each bill, {span}.
        </p>
        <p className="text-[12px] text-slate-500">
          More than a month's bill is not carried forward — Stripe makes that invoice zero and the
          rest is not credited. The customer sees the line on their Billing page, their invoice and
          their receipt.
        </p>
        {err && <p className="text-sm text-danger" role="alert">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={busy}>
            {busy ? 'Granting…' : 'Grant'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
