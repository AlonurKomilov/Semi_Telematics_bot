import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import type { CompActionRow, PastDueRow, WebhookEventRow } from '../types';

type Tab = 'comp' | 'past-due' | 'webhooks';

export default function AuditPage() {
  const [tab, setTab] = useState<Tab>('comp');

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Audit feed</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Cross-account read-only view.  All three feeds are sourced from
          the same tables the operator actions write to, so anything that
          happened on the system console (or via the API directly) shows
          up here.
        </p>
      </header>

      <div className="flex gap-1 mb-3 border-b border-slate-800">
        <TabButton current={tab} value="comp"     onClick={setTab}>Comp actions</TabButton>
        <TabButton current={tab} value="past-due" onClick={setTab}>Past-due transitions</TabButton>
        <TabButton current={tab} value="webhooks" onClick={setTab}>Webhook events</TabButton>
      </div>

      {tab === 'comp'     && <CompActionsTab />}
      {tab === 'past-due' && <PastDueTab />}
      {tab === 'webhooks' && <WebhookEventsTab />}
    </div>
  );
}

function TabButton({
  current, value, onClick, children,
}: {
  current: Tab; value: Tab; onClick: (t: Tab) => void; children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      onClick={() => onClick(value)}
      className={`px-3 py-1.5 text-xs border-b-2 -mb-px transition ${
        active
          ? 'border-accent text-slate-100 font-semibold'
          : 'border-transparent text-slate-500 hover:text-slate-300'
      }`}
    >
      {children}
    </button>
  );
}

// ── Comp actions tab ───────────────────────────────────────────

function CompActionsTab() {
  const [rows, setRows] = useState<CompActionRow[]>([]);
  const [action, setAction] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '100' });
    if (action) qs.set('action', action);
    apiJSON<{ items: CompActionRow[] }>(`/system/audit/comp-actions?${qs}`)
      .then((r) => setRows(r.items))
      .catch((e: unknown) => setErr(friendly(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [action]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <Filters>
        <select value={action} onChange={(e) => setAction(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">All actions</option>
          <option value="granted">granted</option>
          <option value="renewed">renewed</option>
          <option value="revoked">revoked</option>
          <option value="expired">expired</option>
          <option value="expiring_reminder">expiring_reminder</option>
        </select>
      </Filters>
      {err && <ErrorBox msg={err} />}
      <Table loading={loading} empty={rows.length === 0 && !loading}
             headers={['When', 'Account', 'Action', 'Expires', 'Reason', 'By (tg id)']}>
        {rows.map((r) => (
          <tr key={r.id} className="border-b border-slate-800/50">
            <td className="px-3 py-2 text-slate-400 tabular-nums">
              {r.created_at.slice(0, 19).replace('T', ' ')}
            </td>
            <td className="px-3 py-2">
              <Link to={`/accounts/${r.account_id}`} className="text-slate-100 hover:text-accent">
                {r.account_name}
              </Link>
              <span className="text-xs text-slate-500"> #{r.account_id}</span>
            </td>
            <td className="px-3 py-2">
              <ActionPill action={r.action} />
            </td>
            <td className="px-3 py-2 text-slate-400">{r.expires_at?.slice(0, 10) || '—'}</td>
            <td className="px-3 py-2 text-slate-400 italic max-w-md truncate" title={r.reason}>
              {r.reason || '—'}
            </td>
            <td className="px-3 py-2 text-slate-500 tabular-nums">{r.actor_user_id ?? '—'}</td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

function ActionPill({ action }: { action: string }) {
  const cls =
    action === 'granted'  ? 'text-ok'     :
    action === 'renewed'  ? 'text-accent' :
    action === 'revoked'  ? 'text-danger' :
    action === 'expired'  ? 'text-slate-400' :
    'text-slate-500';
  return <span className={`text-xs ${cls}`}>{action}</span>;
}

// ── Past-due tab ──────────────────────────────────────────────

function PastDueTab() {
  const [rows, setRows] = useState<PastDueRow[]>([]);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = () => {
    setLoading(true);
    setErr('');
    apiJSON<{ items: PastDueRow[] }>(`/system/audit/past-due?days=${days}`)
      .then((r) => setRows(r.items))
      .catch((e: unknown) => setErr(friendly(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [days]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <Filters>
        <label className="text-xs text-slate-400">Window:</label>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
          <option value={365}>1 year</option>
        </select>
      </Filters>
      {err && <ErrorBox msg={err} />}
      <Table loading={loading} empty={rows.length === 0 && !loading}
             headers={['Past-due since', 'Account', 'Tier', 'Status', 'Provider', 'Email']}>
        {rows.map((r) => (
          <tr key={r.account_id} className="border-b border-slate-800/50">
            <td className="px-3 py-2 text-slate-300 tabular-nums">
              {r.past_due_since.slice(0, 19).replace('T', ' ')}
            </td>
            <td className="px-3 py-2">
              <Link to={`/accounts/${r.account_id}`} className="text-slate-100 hover:text-accent">
                {r.account_name}
              </Link>
            </td>
            <td className="px-3 py-2 text-slate-400 capitalize">{r.tier}</td>
            <td className="px-3 py-2"><StatusBadge status={r.status} /></td>
            <td className="px-3 py-2 text-slate-500 text-xs">{r.provider}</td>
            <td className="px-3 py-2 text-slate-400 text-xs">{r.billing_email || '—'}</td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

// ── Webhook events tab ────────────────────────────────────────

function WebhookEventsTab() {
  const [rows, setRows] = useState<WebhookEventRow[]>([]);
  const [type, setType] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '200' });
    if (type) qs.set('event_type', type);
    apiJSON<{ items: WebhookEventRow[] }>(`/system/audit/webhook-events?${qs}`)
      .then((r) => setRows(r.items))
      .catch((e: unknown) => setErr(friendly(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <Filters>
        <select value={type} onChange={(e) => setType(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">All event types</option>
          <option value="checkout.session.completed">checkout.session.completed</option>
          <option value="customer.subscription.updated">customer.subscription.updated</option>
          <option value="customer.subscription.deleted">customer.subscription.deleted</option>
          <option value="invoice.payment_succeeded">invoice.payment_succeeded</option>
          <option value="invoice.payment_failed">invoice.payment_failed</option>
        </select>
      </Filters>
      {err && <ErrorBox msg={err} />}
      <Table loading={loading} empty={rows.length === 0 && !loading}
             headers={['Processed at', 'Account', 'Event type', 'Stripe event id']}>
        {rows.map((r) => (
          <tr key={r.event_id} className="border-b border-slate-800/50">
            <td className="px-3 py-2 text-slate-400 tabular-nums">
              {r.processed_at.slice(0, 19).replace('T', ' ')}
            </td>
            <td className="px-3 py-2">
              {r.account_id ? (
                <Link to={`/accounts/${r.account_id}`} className="text-slate-100 hover:text-accent">
                  {r.account_name || `#${r.account_id}`}
                </Link>
              ) : (
                <span className="text-slate-600 italic">unmatched</span>
              )}
            </td>
            <td className="px-3 py-2 text-slate-300 text-xs font-mono">{r.event_type}</td>
            <td className="px-3 py-2 text-slate-500 text-xs font-mono truncate max-w-xs">
              {r.event_id}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

// ── Shared building blocks ────────────────────────────────────

function Filters({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 flex flex-wrap items-center gap-2">
      {children}
    </div>
  );
}

function Table({ loading, empty, headers, children }: {
  loading: boolean; empty: boolean; headers: string[]; children: React.ReactNode;
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
          <tr>
            {headers.map((h) => <th key={h} className="text-left px-3 py-2">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {loading && <tr><td colSpan={headers.length} className="text-center text-slate-500 py-8">Loading…</td></tr>}
          {empty   && <tr><td colSpan={headers.length} className="text-center text-slate-500 py-8">No rows.</td></tr>}
          {!loading && !empty && children}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'active'    ? 'bg-ok/15 text-ok border-ok/40' :
    status === 'past_due'  ? 'bg-danger/15 text-danger border-danger/40' :
    status === 'canceled' || status === 'unpaid'
                           ? 'bg-slate-700 text-slate-300 border-slate-600' :
    'bg-slate-800 text-slate-400 border-slate-700';
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
      {status.replace('_', ' ')}
    </span>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
      {msg}
    </div>
  );
}

function friendly(e: unknown): string {
  if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
    return 'Session expired or no operator access.  Log out and back in.';
  }
  return e instanceof Error ? e.message : 'Failed to load';
}
