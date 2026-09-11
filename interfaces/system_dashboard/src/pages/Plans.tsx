import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';

// ── Plans: what each plan includes, as data ─────────────────────
//
// One column per plan, one row per sellable feature or service.  A tick
// = included.  "Everything" on a plan means every row, today's and
// tomorrow's — the seed every plan ships with, and the state in which
// this page changes nothing for anyone.  Saving a column reaches every
// account on that plan: the API's own worker at once, its siblings
// within about two minutes.  Nothing here is undoable by "cancel" once
// saved, which is why the confirm names the accounts and the rows.

interface CatalogEntry {
  id: string;
  kind: 'feature' | 'service';
  tier: string | null;
  parent: string | null;
  label: string;
  flags: string[];
}

interface Plan {
  tier: string;
  label: string;
  included: string[];
  everything: boolean;
  quotas: Record<string, number>;
  quota_defaults: Record<string, number>;
  accounts: number;
  updated_at: string;
  updated_by: string;
  // the price catalog — what the customer's Billing page shows and checkout charges
  price_monthly_cents: number;
  base_vehicles: number;
  extra_vehicle_cents: number;
  stripe_price_id: string;
  public: boolean;
  sort: number;
  trial_default: boolean;
}

/** The catalog fields the operator edits per column, as strings while typing. */
interface CatalogDraft { price: string; base: string; extra: string; stripe: string; pub: boolean; sort: string; trial: boolean }
const CATALOG_ROWS: { key: keyof CatalogDraft; label: string; hint: string }[] = [
  { key: 'price', label: 'Price / month ($)', hint: '0 = free' },
  { key: 'base', label: 'Trucks included', hint: '' },
  { key: 'extra', label: 'Extra truck ($/month)', hint: '' },
  { key: 'stripe', label: 'Stripe price id', hint: 'blank = STRIPE_PRICE_<TIER> env' },
  { key: 'sort', label: 'Order on the page', hint: 'lowest first' },
];

interface PlansResponse {
  plans: Plan[];
  catalog: CatalogEntry[];
  quota_keys: string[];
  plan_key_pattern: string;
  accounts_without_plan: Record<string, number>;
}

interface Draft {
  label: string;
  everything: boolean;
  included: Set<string>;
  quotas: Record<string, string>; // '' = use the default
  cat: CatalogDraft;
}

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';
const btnCls =
  'px-2.5 py-1 rounded text-xs font-medium border transition disabled:opacity-50';

const QUOTA_LABEL: Record<string, string> = {
  max_users: 'Users',
  max_companies: 'Companies',
};

const dollars = (cents: number) => (cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2));
const cents = (s: string) => Math.round(Number(s) * 100);

function draftOf(p: Plan): Draft {
  return {
    label: p.label,
    everything: p.everything,
    included: new Set(p.everything ? [] : p.included),
    quotas: Object.fromEntries(
      Object.entries(p.quotas).map(([k, v]) => [k, String(v)]),
    ),
    cat: {
      price: dollars(p.price_monthly_cents), base: String(p.base_vehicles),
      extra: dollars(p.extra_vehicle_cents), stripe: p.stripe_price_id, pub: p.public, sort: String(p.sort),
      trial: p.trial_default,
    },
  };
}

/** The catalog as the API wants it; ``null`` when a number does not parse. */
function catalogOf(d: Draft): {
  price_monthly_cents: number; base_vehicles: number; extra_vehicle_cents: number;
  stripe_price_id: string; public: boolean; sort: number; trial_default: boolean;
} | null {
  const price = cents(d.cat.price || '0'), extra = cents(d.cat.extra || '0');
  const base = Number(d.cat.base || '0'), sort = Number(d.cat.sort || '0');
  if (![price, extra, base, sort].every((n) => Number.isInteger(n) && n >= 0)) return null;
  return { price_monthly_cents: price, base_vehicles: base, extra_vehicle_cents: extra,
    stripe_price_id: d.cat.stripe.trim(), public: d.cat.pub, sort, trial_default: d.cat.trial };
}

function includedOf(d: Draft, catalog: CatalogEntry[]): string[] {
  if (d.everything) return ['*'];
  return catalog.filter((c) => d.included.has(c.id)).map((c) => c.id);
}

function quotasOf(d: Draft): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(d.quotas)) {
    if (v.trim() === '') continue;
    out[k] = Number(v);
  }
  return out;
}

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x));
}

function isDirty(p: Plan, d: Draft, catalog: CatalogEntry[]): boolean {
  if (d.label.trim() !== p.label) return true;
  if (!sameSet(includedOf(d, catalog), p.included)) return true;
  const q = quotasOf(d);
  const keys = new Set([...Object.keys(q), ...Object.keys(p.quotas)]);
  for (const k of keys) if (q[k] !== p.quotas[k]) return true;
  const c = catalogOf(d);
  if (!c) return true;
  return c.price_monthly_cents !== p.price_monthly_cents || c.base_vehicles !== p.base_vehicles
    || c.extra_vehicle_cents !== p.extra_vehicle_cents || c.stripe_price_id !== p.stripe_price_id
    || c.public !== p.public || c.sort !== p.sort || c.trial_default !== p.trial_default;
}

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Rows: services first, then features by tier, a child indented under
// its parent — the registry's own order inside each band.
function groupRows(catalog: CatalogEntry[]): { title: string; rows: CatalogEntry[] }[] {
  const services = catalog.filter((c) => c.kind === 'service');
  const features = catalog.filter((c) => c.kind !== 'service');
  const ordered: CatalogEntry[] = [];
  for (const f of features) {
    if (f.parent && features.some((x) => x.id === f.parent)) continue;
    ordered.push(f);
    for (const child of features) if (child.parent === f.id) ordered.push(child);
  }
  const shared = ordered.filter((c) => c.tier === 'shared');
  const role = ordered.filter((c) => c.tier !== 'shared');
  return [
    { title: 'Services', rows: services },
    { title: 'Shared features', rows: shared },
    { title: 'Role features', rows: role },
  ].filter((g) => g.rows.length > 0);
}

export default function PlansPage() {
  const [data, setData] = useState<PlansResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [saved, setSaved] = useState('');
  const [newPlan, setNewPlan] = useState({ key: '', label: '' });

  // What the page last loaded and what the operator has typed since —
  // read through refs so a reload can tell an unsaved edit from a stale
  // draft without depending on render-time state.
  const dataRef = useRef<PlansResponse | null>(null);
  const draftsRef = useRef<Record<string, Draft>>({});
  dataRef.current = data;
  draftsRef.current = drafts;

  // A reload replaces every draft EXCEPT an unsaved edit to another
  // plan: saving Free must not throw away half-done work on Pro.
  const load = useCallback(async (savedTier?: string) => {
    setLoading(true);
    setErr('');
    try {
      const res = await apiJSON<PlansResponse>('/system/plans');
      const old = dataRef.current;
      const oldDrafts = draftsRef.current;
      const next: Record<string, Draft> = {};
      for (const p of res.plans) {
        const prev = old?.plans.find((x) => x.tier === p.tier);
        const d = oldDrafts[p.tier];
        const keep = prev && d && p.tier !== savedTier && isDirty(prev, d, old?.catalog ?? []);
        next[p.tier] = keep ? d : draftOf(p);
      }
      setData(res);
      setDrafts(next);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not load plans');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const groups = useMemo(() => (data ? groupRows(data.catalog) : []), [data]);
  const catalog = data?.catalog ?? [];
  const plans = data?.plans ?? [];

  function setDraft(tier: string, patch: (d: Draft) => Draft) {
    setDrafts((ds) => ({ ...ds, [tier]: patch(ds[tier]) }));
  }

  function toggle(tier: string, id: string) {
    setDraft(tier, (d) => {
      const next = new Set(d.included);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...d, included: next };
    });
  }

  function toggleEverything(tier: string, p: Plan) {
    setDraft(tier, (d) => {
      if (d.everything) {
        // leaving "everything": start from every row ticked, so the
        // operator unticks what the plan leaves out
        return { ...d, everything: false, included: new Set(catalog.map((c) => c.id)) };
      }
      return { ...d, everything: true, included: new Set(p.everything ? [] : p.included) };
    });
  }

  async function save(p: Plan) {
    const d = drafts[p.tier];
    if (!d) return;
    const included = includedOf(d, catalog);
    const before = p.everything ? catalog.map((c) => c.id) : p.included;
    const after = d.everything ? catalog.map((c) => c.id) : included;
    const label = (id: string) => catalog.find((c) => c.id === id)?.label ?? id;
    const excluded = before.filter((id) => !after.includes(id)).map(label);
    const restored = after.filter((id) => !before.includes(id)).map(label);
    const quotas = quotasOf(d);
    for (const [k, v] of Object.entries(quotas)) {
      if (!Number.isInteger(v) || v < 0) {
        setErr(`${QUOTA_LABEL[k] ?? k}: 0 (unlimited) or a whole number`);
        return;
      }
    }
    const cat = catalogOf(d);
    if (!cat) {
      setErr('Price, trucks, extra-truck price and order are numbers, 0 or more');
      return;
    }
    const lines = [
      `Save "${d.label.trim()}"?  ${p.accounts} account${p.accounts === 1 ? '' : 's'} on this plan will follow at once.`,
      excluded.length ? `\nTaken away: ${excluded.join(', ')}` : '',
      restored.length ? `\nGiven: ${restored.join(', ')}` : '',
      d.everything && !p.everything ? '\nBack to everything included.' : '',
      cat.public !== p.public ? (cat.public ? '\nShown on the customer Billing page from now on.' : '\nHidden from the customer Billing page (accounts already on it keep it).') : '',
      cat.trial_default && !p.trial_default ? '\nNew self-serve signups start their trial on this plan from now on.' : '',
      cat.price_monthly_cents !== p.price_monthly_cents ? `\nPrice: $${dollars(p.price_monthly_cents)} → $${dollars(cat.price_monthly_cents)} per month (new checkouts only; Stripe is the bill).` : '',
    ];
    if (!window.confirm(lines.join(''))) return;
    setBusy(p.tier);
    setErr('');
    try {
      await apiJSON(`/system/plans/${p.tier}`, {
        method: 'PUT',
        body: { label: d.label.trim(), included, quotas, ...cat },
      });
      setSaved(p.tier);
      await load(p.tier);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Save failed');
    } finally {
      setBusy('');
    }
  }

  async function create() {
    const key = newPlan.key.trim();
    const label = newPlan.label.trim() || key;
    const pattern = new RegExp(data?.plan_key_pattern ?? '^[a-z][a-z0-9_]{1,31}$');
    if (!pattern.test(key)) {
      setErr('Plan key: a-z, 0-9, _ — 2 to 32 chars, starting with a letter');
      return;
    }
    // Creating is its own door (POST) and the server refuses a taken
    // key with 409; the check here only spares the operator the round
    // trip and a confirm that would otherwise promise an empty plan.
    if (plans.some((p) => p.tier === key)) {
      setErr(`A plan named "${key}" already exists — edit it in the grid above`);
      return;
    }
    const orphaned = data?.accounts_without_plan[key];
    const who = orphaned
      ? `${orphaned} account${orphaned === 1 ? '' : 's'} already on "${key}" will hold everything from now on.`
      : 'No account is on it until one is moved there.';
    if (!window.confirm(`Create plan "${label}" (${key}) with everything included?  ${who}`)) return;
    setBusy('new');
    setErr('');
    try {
      await apiJSON('/system/plans', {
        method: 'POST',
        body: { tier: key, label },
      });
      setNewPlan({ key: '', label: '' });
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Create failed');
    } finally {
      setBusy('');
    }
  }

  const orphans = Object.entries(data?.accounts_without_plan ?? {});

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Plans</h1>
      <p className="text-sm text-slate-500 mb-5">
        What each plan includes. A tick is included; an unticked row is
        <span className="text-slate-300"> taken away from every account on that plan</span> —
        the page, the API, the bot and the AI all close through the permission.
        Billing, Overview and the administration pages are never for sale, so they are not listed.
        Saving reaches this server at once and the others within about two minutes.
      </p>

      {err && <div className="mb-4 text-sm text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-3 py-2">{err}</div>}
      {loading && <p className="text-slate-500 text-sm">Loading…</p>}

      {orphans.length > 0 && (
        <div className="mb-4 text-sm text-amber-300 border border-amber-500/30 bg-amber-500/10 rounded px-3 py-2">
          Accounts on a plan that has no row — they hold <span className="font-medium">nothing sellable</span> until it exists:{' '}
          {orphans.map(([t, n]) => `${t} (${n})`).join(', ')}.
          Create the plan below with that exact key.
        </div>
      )}

      {data && (
        <div className="border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/60 text-slate-400">
              <tr>
                <th className="text-left px-3 py-2 font-medium w-64">Plan</th>
                {plans.map((p) => (
                  <th key={p.tier} className="px-3 py-2 font-medium text-center min-w-[9rem] align-top">
                    <input
                      className={`${inputCls} text-center`}
                      value={drafts[p.tier]?.label ?? p.label}
                      onChange={(e) => setDraft(p.tier, (d) => ({ ...d, label: e.target.value }))}
                      aria-label={`Label of plan ${p.tier}`}
                    />
                    <div className="mt-1 text-[11px] font-normal text-slate-500">
                      <code>{p.tier}</code> · {p.accounts} account{p.accounts === 1 ? '' : 's'}
                    </div>
                  </th>
                ))}
              </tr>
              <tr className="border-t border-slate-800/70">
                <th className="text-left px-3 py-2 font-medium text-slate-300">Everything included</th>
                {plans.map((p) => (
                  <th key={p.tier} className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={drafts[p.tier]?.everything ?? p.everything}
                      onChange={() => toggleEverything(p.tier, p)}
                      aria-label={`Everything included on ${p.label}`}
                      title="On: every row, today's and tomorrow's. Off: every row starts ticked — untick what this plan leaves out."
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <GroupRows key={g.title} title={g.title} rows={g.rows} plans={plans} drafts={drafts} onToggle={toggle} />
              ))}
              <tr className="border-t border-slate-800 bg-slate-900/40">
                <td className="px-3 py-1.5 text-xs uppercase tracking-wide text-slate-500" colSpan={plans.length + 1}>
                  Quotas — blank uses the default shown; 0 = unlimited
                </td>
              </tr>
              {data.quota_keys.map((k) => (
                <tr key={k} className="border-t border-slate-800/70">
                  <td className="px-3 py-1.5 text-slate-300">{QUOTA_LABEL[k] ?? k}</td>
                  {plans.map((p) => (
                    <td key={p.tier} className="px-3 py-1.5">
                      <input
                        className={`${inputCls} text-center tabular-nums`}
                        inputMode="numeric"
                        placeholder={`default ${p.quota_defaults[k] === 0 ? '∞' : p.quota_defaults[k]}`}
                        value={drafts[p.tier]?.quotas[k] ?? ''}
                        onChange={(e) =>
                          setDraft(p.tier, (d) => ({ ...d, quotas: { ...d.quotas, [k]: e.target.value } }))
                        }
                        aria-label={`${QUOTA_LABEL[k] ?? k} on ${p.label}`}
                      />
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="border-t border-slate-800 bg-slate-900/40">
                <td className="px-3 py-1.5 text-xs uppercase tracking-wide text-slate-500" colSpan={plans.length + 1}>
                  Customer Billing page — price, what is included per truck, and whether the plan is offered
                </td>
              </tr>
              <tr className="border-t border-slate-800/70">
                <td className="px-3 py-1.5 text-slate-300">Offered to customers</td>
                {plans.map((p) => (
                  <td key={p.tier} className="px-3 py-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={drafts[p.tier]?.cat.pub ?? p.public}
                      onChange={(e) => setDraft(p.tier, (d) => ({ ...d, cat: { ...d.cat, pub: e.target.checked } }))}
                      aria-label={`Offer ${p.label} on the customer Billing page`}
                    />
                  </td>
                ))}
              </tr>
              <tr className="border-t border-slate-800/70">
                <td className="px-3 py-1.5 text-slate-300">
                  <label className="inline-flex items-center gap-2">
                    <input
                      type="radio"
                      name="trial_default"
                      checked={!plans.some((p) => drafts[p.tier]?.cat.trial ?? p.trial_default)}
                      onChange={() => setDrafts((ds) => Object.fromEntries(Object.entries(ds).map(([t, d]) =>
                        [t, { ...d, cat: { ...d.cat, trial: false } }])))}
                      aria-label="No trial for new signups"
                    />
                    <span>Trial plan for new signups</span>
                  </label>
                  <span className="ml-2 text-[11px] text-slate-500">this radio = no trial; else one plan</span>
                </td>
                {plans.map((p) => (
                  <td key={p.tier} className="px-3 py-1.5 text-center">
                    <input
                      type="radio"
                      name="trial_default"
                      checked={drafts[p.tier]?.cat.trial ?? p.trial_default}
                      onChange={() => setDrafts((ds) => Object.fromEntries(Object.entries(ds).map(([t, d]) =>
                        [t, { ...d, cat: { ...d.cat, trial: t === p.tier } }])))}
                      aria-label={`Trials start on ${p.label}`}
                    />
                  </td>
                ))}
              </tr>
              {CATALOG_ROWS.map((row) => (
                <tr key={row.key} className="border-t border-slate-800/70">
                  <td className="px-3 py-1.5 text-slate-300">
                    {row.label}
                    {row.hint && <span className="ml-2 text-[11px] text-slate-500">{row.hint}</span>}
                  </td>
                  {plans.map((p) => (
                    <td key={p.tier} className="px-3 py-1.5">
                      <input
                        className={`${inputCls} text-center tabular-nums`}
                        inputMode={row.key === 'stripe' ? 'text' : 'decimal'}
                        value={String(drafts[p.tier]?.cat[row.key] ?? '')}
                        onChange={(e) =>
                          setDraft(p.tier, (d) => ({ ...d, cat: { ...d.cat, [row.key]: e.target.value } }))
                        }
                        aria-label={`${row.label} on ${p.label}`}
                      />
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="border-t border-slate-800">
                <td className="px-3 py-2 text-xs text-slate-500">Last change</td>
                {plans.map((p) => {
                  const d = drafts[p.tier];
                  const dirty = d ? isDirty(p, d, catalog) : false;
                  return (
                    <td key={p.tier} className="px-3 py-2 text-center align-top">
                      <button
                        className={`${btnCls} ${dirty ? 'border-accent text-accent hover:bg-accent/10' : 'border-slate-800 text-slate-500'}`}
                        disabled={!dirty || busy === p.tier}
                        onClick={() => save(p)}
                      >
                        {busy === p.tier ? 'Saving…' : dirty ? 'Save' : saved === p.tier ? 'Saved' : 'No changes'}
                      </button>
                      <div className="mt-1 text-[11px] text-slate-500">
                        {p.updated_by || '—'}
                        <br />
                        {p.updated_at ? when(p.updated_at) : ''}
                      </div>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <div className="mt-6 border border-slate-800 rounded-lg p-3">
          <div className="text-sm font-medium text-slate-300 mb-2">New plan</div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input
              className={inputCls}
              placeholder="key (e.g. gold) — becomes the account's tier"
              value={newPlan.key}
              onChange={(e) => setNewPlan({ ...newPlan, key: e.target.value })}
              aria-label="New plan key"
            />
            <input
              className={inputCls}
              placeholder="Label (e.g. Gold)"
              value={newPlan.label}
              onChange={(e) => setNewPlan({ ...newPlan, label: e.target.value })}
              aria-label="New plan label"
            />
            <button
              className={`${btnCls} border-accent text-accent hover:bg-accent/10`}
              disabled={busy === 'new' || !newPlan.key.trim()}
              onClick={create}
            >
              {busy === 'new' ? 'Creating…' : 'Create with everything included'}
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            A new plan starts with everything included, no price, and hidden from customers.
            Set its price and tick "Offered to customers" above when it is ready; Stripe stays the bill.
          </p>
        </div>
      )}
    </div>
  );
}

function GroupRows({
  title, rows, plans, drafts, onToggle,
}: {
  title: string;
  rows: CatalogEntry[];
  plans: Plan[];
  drafts: Record<string, Draft>;
  onToggle: (tier: string, id: string) => void;
}) {
  return (
    <>
      <tr className="border-t border-slate-800 bg-slate-900/40">
        <td className="px-3 py-1.5 text-xs uppercase tracking-wide text-slate-500" colSpan={plans.length + 1}>
          {title}
        </td>
      </tr>
      {rows.map((c) => (
        <tr key={c.id} className="border-t border-slate-800/70">
          <td className={`px-3 py-1.5 text-slate-200 ${c.parent ? 'pl-8 text-slate-400' : ''}`} title={c.flags.join(', ')}>
            {c.label}
          </td>
          {plans.map((p) => {
            const d = drafts[p.tier];
            const everything = d?.everything ?? p.everything;
            const on = everything || (d ? d.included.has(c.id) : p.included.includes(c.id));
            return (
              <td key={p.tier} className="px-3 py-1.5 text-center">
                <input
                  type="checkbox"
                  checked={on}
                  disabled={everything}
                  className={everything ? 'opacity-40 cursor-not-allowed' : ''}
                  title={everything ? 'Included via "Everything included" — turn that off to pick rows' : undefined}
                  onChange={() => onToggle(p.tier, c.id)}
                  aria-label={`${c.label} on ${p.label}`}
                />
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}
