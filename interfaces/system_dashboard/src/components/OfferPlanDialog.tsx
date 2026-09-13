/** Open a hidden plan to one account — the terms agreed after a
 *  Contact-Sales conversation.
 *
 *  One dialog, reached from two places, because it is one act: from a
 *  plan's column on the Plans page (the plan is fixed, pick the account)
 *  and from a case on the Plan requests page (the account is fixed, pick
 *  the plan).  Whichever way in, the same request goes out and the same
 *  answer comes back — what was sent to whom.
 */

import { useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import { Button } from './ui/Button';
import { Dialog } from './ui/Dialog';
import { Input } from './ui/Input';

export interface OfferablePlan {
  tier: string;
  label: string;
  public: boolean;
  price_monthly_cents: number;
  base_vehicles: number;
  extra_vehicle_cents: number;
}

interface AccountLite { id: number; name: string; tier: string }

interface OfferResult {
  created: boolean;
  case_number?: string;
  emailed?: boolean;
  telegram?: number;
  message?: string;
}

interface Props {
  /** Fixed when opened from the plan's column. */
  plan?: OfferablePlan;
  /** Fixed when opened from a case; the case travels with it. */
  account?: { id: number; name: string; request_id?: number; case_number?: string };
  onClose: () => void;
  /** After a successful offer — the caller reloads what it shows. */
  onDone: () => void;
}

const money = (cents: number) => (cents % 100 === 0 ? `$${cents / 100}` : `$${(cents / 100).toFixed(2)}`);

function terms(p: OfferablePlan): string {
  const parts = [`${money(p.price_monthly_cents)}/mo`];
  if (p.base_vehicles) parts.push(`${p.base_vehicles} truck${p.base_vehicles === 1 ? '' : 's'} included`);
  if (p.extra_vehicle_cents) parts.push(`${money(p.extra_vehicle_cents)} per extra truck`);
  return parts.join(' · ');
}

export function OfferPlanDialog({ plan, account, onClose, onDone }: Props) {
  const [pickedPlan, setPickedPlan] = useState<OfferablePlan | null>(plan ?? null);
  const [pickedAccount, setPickedAccount] = useState<AccountLite | null>(
    account ? { id: account.id, name: account.name, tier: '' } : null,
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState<OfferResult | null>(null);

  const submit = async () => {
    if (!pickedPlan || !pickedAccount) return;
    setBusy(true);
    setErr('');
    try {
      const r = await apiJSON<OfferResult>(`/system/plans/${pickedPlan.tier}/offers`, {
        method: 'POST',
        body: { account_id: pickedAccount.id, request_id: account?.request_id ?? null },
      });
      setResult(r);
      if (r.created) onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not make the offer');
    } finally {
      setBusy(false);
    }
  };

  const title = plan ? `Offer ${plan.label} to an account` : `Offer a plan to ${account?.name ?? 'this account'}`;

  return (
    <Dialog title={title} onClose={onClose} size="lg">
      {result ? (
        <div className="space-y-3">
          {result.created ? (
            <>
              <p className="text-sm text-slate-200">
                <span className="font-semibold">{pickedPlan?.label}</span> is now on{' '}
                <span className="font-semibold">{pickedAccount?.name}</span>'s Billing page, and only theirs.
                They pay for it there.
              </p>
              <ul className="text-sm text-slate-400 space-y-1">
                <li>{result.emailed ? `Emailed the terms to the address on the case${result.case_number ? ` (${result.case_number})` : ''}.` : 'No email sent — there is no address on file for this offer. Tell them yourself.'}</li>
                <li>{result.telegram ? `Told ${result.telegram} of their billing admins on Telegram.` : 'Nobody reached on Telegram — their account has no billing admin on the bot.'}</li>
                {result.case_number && <li>The case is now marked contacted.</li>}
              </ul>
            </>
          ) : (
            <p className="text-sm text-slate-200">{result.message ?? 'This offer already stands.'}</p>
          )}
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {plan ? (
            <p className="text-sm text-slate-400">
              <span className="text-slate-200 font-medium">{plan.label}</span> · {terms(plan)}
            </p>
          ) : (
            <PlanPicker picked={pickedPlan} onPick={setPickedPlan} />
          )}
          {account ? (
            <p className="text-sm text-slate-400">
              To <span className="text-slate-200 font-medium">{account.name}</span>
              {account.case_number && <> · answers case <code className="text-[11px]">{account.case_number}</code></>}
            </p>
          ) : (
            <AccountPicker picked={pickedAccount} onPick={setPickedAccount} />
          )}
          <p className="text-[12px] text-slate-500">
            The plan appears on that account's Billing page as "Prepared for your account"; nothing changes
            until they press Upgrade and pay. Everyone else still cannot see it.
          </p>
          {err && <p className="text-sm text-danger" role="alert">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={busy || !pickedPlan || !pickedAccount}>
              {busy ? 'Offering…' : pickedPlan && pickedAccount ? `Offer ${pickedPlan.label} to ${pickedAccount.name}` : 'Offer'}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

/** The hidden plans — the only ones an offer makes sense for. */
function PlanPicker({ picked, onPick }: { picked: OfferablePlan | null; onPick: (p: OfferablePlan) => void }) {
  const [plans, setPlans] = useState<OfferablePlan[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let alive = true;
    apiJSON<{ plans: OfferablePlan[] }>('/system/plans')
      .then((r) => { if (alive) setPlans(r.plans.filter((p) => !p.public)); })
      .catch((e) => { if (alive) setErr(e instanceof ApiError ? e.message : 'Could not load plans'); });
    return () => { alive = false; };
  }, []);
  if (err) return <p className="text-sm text-danger" role="alert">{err}</p>;
  if (plans === null) return <p className="text-sm text-slate-500">Loading plans…</p>;
  if (plans.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No hidden plan to offer yet. Make one on the <a href="/plans" className="text-accent hover:underline">Plans page</a> with
        the agreed price, trucks and features, keep it hidden from customers, then come back here.
      </p>
    );
  }
  return (
    <fieldset className="space-y-1">
      <legend className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Which plan</legend>
      {plans.map((p) => (
        <label key={p.tier} className={`flex items-center gap-3 rounded border px-3 py-2 cursor-pointer ${picked?.tier === p.tier ? 'border-accent bg-accent/10' : 'border-slate-800 hover:bg-slate-900/40'}`}>
          <input type="radio" name="offer-plan" checked={picked?.tier === p.tier} onChange={() => onPick(p)} />
          <span className="text-sm text-slate-200 font-medium">{p.label}</span>
          <span className="text-[12px] text-slate-400">{terms(p)}</span>
        </label>
      ))}
    </fieldset>
  );
}

/** Find the account by name — the same search the Accounts page uses. */
function AccountPicker({ picked, onPick }: { picked: AccountLite | null; onPick: (a: AccountLite) => void }) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<AccountLite[]>([]);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (q.trim().length < 2) { setRows([]); return; }
    let alive = true;
    apiJSON<{ items: AccountLite[] }>(`/system/accounts?search=${encodeURIComponent(q.trim())}&limit=8`)
      .then((r) => { if (alive) setRows(r.items); })
      .catch((e) => { if (alive) setErr(e instanceof ApiError ? e.message : 'Could not search accounts'); });
    return () => { alive = false; };
  }, [q]);
  return (
    <div className="space-y-2">
      <label className="block">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">Which account</span>
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Start typing the account name"
          autoFocus
        />
      </label>
      {err && <p className="text-sm text-danger" role="alert">{err}</p>}
      {picked && (
        <p className="text-sm text-slate-200">
          Chosen: <span className="font-medium">{picked.name}</span> <span className="text-slate-500">#{picked.id}{picked.tier && ` · on ${picked.tier}`}</span>
        </p>
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-slate-800 rounded border border-slate-800">
          {rows.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => onPick(a)}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-slate-900/40 ${picked?.id === a.id ? 'bg-accent/10 text-slate-100' : 'text-slate-300'}`}
              >
                {a.name} <span className="text-slate-500">#{a.id} · on {a.tier}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {q.trim().length >= 2 && rows.length === 0 && !err && (
        <p className="text-sm text-slate-500">No account matches "{q.trim()}".</p>
      )}
    </div>
  );
}
