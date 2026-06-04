import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import MetricsCard from '../components/MetricsCard';
import type { AccountListItem, SystemStats } from '../types';

function usd(cents: number): string {
  return '$' + (cents / 100).toFixed(2);
}

function statusColor(status: string): string {
  switch (status) {
    case 'active':    return 'bg-ok/15 text-ok border-ok/40';
    case 'trialing':  return 'bg-warn/15 text-warn border-warn/40';
    case 'past_due':  return 'bg-danger/15 text-danger border-danger/40';
    case 'canceled':
    case 'unpaid':    return 'bg-slate-700 text-slate-300 border-slate-600';
    default:          return 'bg-slate-800 text-slate-400 border-slate-700';
  }
}

export default function Accounts() {
  const [accounts, setAccounts] = useState<AccountListItem[]>([]);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // Filters — kept simple; the server does the heavy lifting.
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [tierFilter, setTierFilter] = useState('');
  const [compFilter, setCompFilter] = useState<'' | 'yes' | 'no'>('');

  const load = () => {
    setLoading(true);
    setError('');
    const qs = new URLSearchParams();
    if (search.trim()) qs.set('search', search.trim());
    if (statusFilter) qs.set('status', statusFilter);
    if (tierFilter)   qs.set('tier',   tierFilter);
    if (compFilter)   qs.set('is_comped', compFilter);
    Promise.all([
      apiJSON<{ items: AccountListItem[]; count: number }>(`/system/accounts?${qs.toString()}`),
      apiJSON<SystemStats>('/system/stats'),
    ])
      .then(([a, s]) => { setAccounts(a.items); setStats(s); })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setError('Session expired or no operator access.  Log out and back in.');
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      {/* Pipeline health card — first thing operators see.  Goes red
          when there are webhook errors / invalid signatures / sync
          errors so an at-risk pipeline can't be missed. */}
      <MetricsCard />

      {/* System-wide stats — keep operators oriented at a glance */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <StatCard label="Total accounts" value={stats.accounts_total} />
          <StatCard label="Active" value={stats.accounts_active} />
          <StatCard label="Past due" value={stats.past_due}
                    accent={stats.past_due > 0 ? 'text-danger' : undefined} />
          <StatCard label="Comped" value={stats.comped}
                    accent={stats.comped > 0 ? 'text-accent' : undefined} />
          <StatCard label="By tier"
                    value={Object.entries(stats.by_tier).map(([t,n])=>`${t}:${n}`).join(' · ') || '—'} />
        </div>
      )}

      {/* Search + filters */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-4 flex flex-wrap gap-2 items-center">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load(); }}
          placeholder="Search by name…"
          className="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm flex-1 min-w-[200px]"
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">Any status</option>
          <option value="active">active</option>
          <option value="trialing">trialing</option>
          <option value="past_due">past_due</option>
          <option value="canceled">canceled</option>
          <option value="unpaid">unpaid</option>
        </select>
        <select value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">Any tier</option>
          <option value="free">free</option>
          <option value="starter">starter</option>
          <option value="pro">pro</option>
          <option value="enterprise">enterprise</option>
        </select>
        <select value={compFilter} onChange={(e) => setCompFilter(e.target.value as '' | 'yes' | 'no')}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">Any comp</option>
          <option value="yes">Comped</option>
          <option value="no">Not comped</option>
        </select>
        <button onClick={load}
                className="bg-accent text-white text-xs px-3 py-1.5 rounded hover:bg-accent/90">
          Apply
        </button>
      </div>

      {error && (
        <div className="mb-4 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {error}
        </div>
      )}

      {/* Accounts table */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left px-4 py-2">ID</th>
              <th className="text-left px-4 py-2">Name</th>
              <th className="text-left px-4 py-2">Tier</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-right px-4 py-2">Vehicles</th>
              <th className="text-left px-4 py-2">Provider</th>
              <th className="text-left px-4 py-2">Flags</th>
              <th className="text-left px-4 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="text-center text-slate-500 py-8">Loading…</td></tr>
            )}
            {!loading && accounts.length === 0 && (
              <tr><td colSpan={8} className="text-center text-slate-500 py-8">No accounts match.</td></tr>
            )}
            {accounts.map((a) => (
              <tr key={a.id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                <td className="px-4 py-2 tabular-nums text-slate-400">{a.id}</td>
                <td className="px-4 py-2">
                  <Link to={`/accounts/${a.id}`} className="text-slate-100 hover:text-accent">
                    {a.name}
                  </Link>
                  <div className="text-xs text-slate-500">{a.slug}</div>
                </td>
                <td className="px-4 py-2 capitalize text-slate-300">{a.tier}</td>
                <td className="px-4 py-2">
                  <span className={`text-xs px-2 py-0.5 rounded border ${statusColor(a.subscription.status)}`}>
                    {a.subscription.status.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-300">
                  {a.subscription.vehicle_count}
                </td>
                <td className="px-4 py-2 text-xs text-slate-400">{a.subscription.provider}</td>
                <td className="px-4 py-2 text-xs">
                  {a.subscription.is_comped && (
                    <span className="text-accent mr-2" title={a.subscription.comp_expires_at ?? ''}>comp</span>
                  )}
                  {a.subscription.past_due_since && (
                    <span className="text-danger" title={a.subscription.past_due_since}>past-due</span>
                  )}
                  {!a.is_active && (
                    <span className="text-slate-500">disabled</span>
                  )}
                </td>
                <td className="px-4 py-2 text-xs text-slate-500 tabular-nums">
                  {a.created_at?.slice(0, 10) || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-3">
        {accounts.length} row{accounts.length === 1 ? '' : 's'} · server-side limit 100 ·
        {' '}<a href="#" onClick={(e) => { e.preventDefault(); load(); }} className="text-accent hover:underline">refresh</a>
      </p>
    </div>
  );
}

function StatCard({ label, value, accent }: { label: string; value: number | string; accent?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${accent ?? 'text-slate-100'}`}>{value}</p>
    </div>
  );
}

// USD helper exported for re-use in detail page.
export { usd };
