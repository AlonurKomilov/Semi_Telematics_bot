/** Customers who asked about a plan that is not sold self-serve.
 *
 *  A plan offered at no price means "talk to us" in this product —
 *  Enterprise is the case it exists for. The customer presses a button,
 *  writes what they need, and gets a case number; this is where that
 *  lands, so the request is something an operator can find later rather
 *  than a Telegram message that scrolled away.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import { Button } from '../components/ui/Button';
import { OfferPlanDialog } from '../components/OfferPlanDialog';

interface PlanRequest {
  id: number;
  account_id: number;
  account_name: string;
  tier: string;
  case_number: string;
  contact_email: string;
  note: string;
  status: 'open' | 'contacted' | 'closed';
  handled_by: string;
  created_at: string;
}

const TONE: Record<PlanRequest['status'], string> = {
  open:      'bg-warn/15 text-warn',
  contacted: 'bg-accent/15 text-accent',
  closed:    'bg-slate-500/15 text-slate-400',
};

const when = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
};

export default function PlanRequests() {
  const [items, setItems] = useState<PlanRequest[]>([]);
  const [openCount, setOpenCount] = useState(0);
  const [filter, setFilter] = useState<'' | PlanRequest['status']>('open');
  const [busy, setBusy] = useState<number | null>(null);
  // the case being answered with a plan of its own
  const [offerFor, setOfferFor] = useState<PlanRequest | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const r = await apiJSON<{ items: PlanRequest[]; open: number }>(
        `/system/plan-requests${filter ? `?status=${filter}` : ''}`);
      setItems(r.items);
      setOpenCount(r.open);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not load the queue');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const move = async (r: PlanRequest, status: PlanRequest['status']) => {
    setBusy(r.id);
    setErr('');
    try {
      await apiJSON(`/system/plan-requests/${r.id}/status`, { method: 'POST', body: { status } });
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not move that request');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Plan requests</h1>
      <p className="text-sm text-slate-500 mb-5">
        A customer pressed "Talk to sales" on a plan that carries no price. Closing a
        request frees that plan for them to ask about again later — it does not tell
        them anything, so reply by email first.
      </p>

      {err && <div className="mb-4 text-sm text-danger border border-danger/30 bg-danger/10 rounded px-3 py-2">{err}</div>}

      <div className="flex items-center gap-2 mb-4">
        {(['open', 'contacted', 'closed', ''] as const).map((f) => (
          <button
            key={f || 'all'}
            onClick={() => setFilter(f)}
            className={`text-xs px-2 py-0.5 rounded border transition ${
              filter === f ? 'bg-accent/15 text-accent border-accent/40'
                           : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}
          >{f || 'all'}{f === 'open' && openCount > 0 ? ` (${openCount})` : ''}</button>
        ))}
      </div>

      {loading && <p className="text-slate-500 text-sm">Loading…</p>}
      {!loading && items.length === 0 && (
        <p className="text-sm text-slate-500">
          {filter === 'open'
            ? 'Nobody is waiting. A request arrives here the moment a customer sends one.'
            : `No ${filter || ''} requests.`}
        </p>
      )}

      <div className="space-y-3">
        {items.map((r) => (
          <section key={r.id} className="border border-slate-800 rounded-lg overflow-hidden">
            <header className="flex items-center gap-3 px-3 py-2 bg-slate-900/40 border-b border-slate-800">
              <span className="text-sm font-semibold text-slate-200">{r.account_name}</span>
              <code className="text-[11px] text-slate-500">{r.case_number}</code>
              <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${TONE[r.status]}`}>
                {r.status}
              </span>
              <span className="text-[11px] text-slate-500 ml-auto">{when(r.created_at)}</span>
            </header>
            <div className="px-3 py-2 space-y-1.5">
              <p className="text-sm text-slate-300">
                Asked about <span className="text-slate-100 font-medium">{r.tier}</span>
                {r.contact_email && <> · reply to <a href={`mailto:${r.contact_email}?subject=${encodeURIComponent(`[${r.case_number}] your ${r.tier} enquiry`)}`} className="text-accent hover:underline">{r.contact_email}</a></>}
              </p>
              <p className="text-sm text-slate-400 whitespace-pre-wrap">{r.note || '(no message)'}</p>
              {r.handled_by && <p className="text-[11px] text-slate-500">last moved by {r.handled_by}</p>}
            </div>
            <div className="flex gap-2 px-3 py-2 border-t border-slate-800">
              {/* The offer is what a case is waiting for, so it carries the
                  weight; "contacted" is the bookkeeping beside it. */}
              {r.status !== 'closed' && (
                <Button variant="primary" disabled={busy === r.id}
                        onClick={() => setOfferFor(r)}>Offer a plan…</Button>
              )}
              {r.status !== 'contacted' && (
                <Button disabled={busy === r.id}
                        onClick={() => move(r, 'contacted')}>Mark contacted</Button>
              )}
              {r.status !== 'closed' && (
                <Button disabled={busy === r.id} onClick={() => move(r, 'closed')}>Close</Button>
              )}
              {r.status === 'closed' && (
                <Button disabled={busy === r.id} onClick={() => move(r, 'open')}>Reopen</Button>
              )}
            </div>
          </section>
        ))}
      </div>
      {offerFor && (
        <OfferPlanDialog
          account={{ id: offerFor.account_id, name: offerFor.account_name,
                     request_id: offerFor.id, case_number: offerFor.case_number }}
          onClose={() => setOfferFor(null)}
          onDone={load}
        />
      )}
    </div>
  );
}
