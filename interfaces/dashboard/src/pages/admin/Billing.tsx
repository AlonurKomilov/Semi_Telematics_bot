import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';

// ── Types ─────────────────────────────────────────────────────────

interface AiUsageByKey {
  requests: number;
  tokens: number;
}

interface AiUsage {
  total_requests: number;
  total_tokens: number;
  by_type: Record<string, AiUsageByKey>;
  by_model: Record<string, AiUsageByKey>;
  days: number;
}

interface BillingSummary {
  tier: string;
  status: string;
  vehicle_count: number;
  base_vehicles: number;
  monthly_base_cents: number;
  extra_vehicle_cents: number;
  billing_email: string | null;
  amount_due_cents: number;
  extra_vehicles: number;
  trial_ends_at: string | null;
  current_period_end: string | null;
  provider: string;
  account_name: string;
  user_count: number;
  ai_usage: AiUsage | null;
}

interface UsageSnapshot {
  period_start: string;
  period_end: string;
  vehicle_count: number;
  extra_vehicles: number;
  amount_due_cents: number;
  ai_queries: number;
  user_count: number;
}

// ── Helpers ───────────────────────────────────────────────────────

function usd(cents: number): string {
  return '$' + (cents / 100).toFixed(2);
}

function fmtNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function tierColor(tier: string): string {
  if (tier === 'pro') return 'text-purple-400';
  if (tier === 'enterprise') return 'text-yellow-400';
  if (tier === 'starter') return 'text-blue-400';
  return 'text-gray-400';
}

function tierBadge(tier: string): string {
  if (tier === 'pro') return 'bg-purple-900/40 text-purple-300 border-purple-700';
  if (tier === 'enterprise') return 'bg-yellow-900/40 text-yellow-300 border-yellow-700';
  if (tier === 'starter') return 'bg-blue-900/40 text-blue-300 border-blue-700';
  return 'bg-gray-800 text-gray-400 border-gray-600';
}

// ── Stat tile ─────────────────────────────────────────────────────

function Stat({ label, value, accent, sub }: { label: string; value: string; accent?: string; sub?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-bold ${accent ?? 'text-white'}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Plan summary card ─────────────────────────────────────────────

function SummaryCard({ summary }: { summary: BillingSummary }) {
  const isOverLimit = summary.extra_vehicles > 0;
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-4">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-white">{summary.account_name || 'Current Plan'}</h2>
          {summary.billing_email && (
            <p className="text-xs text-gray-500 mt-0.5">Billing contact: {summary.billing_email}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium border ${
            summary.status === 'active' ? 'bg-green-900/30 text-green-300 border-green-700' :
            summary.status === 'trialing' ? 'bg-yellow-900/30 text-yellow-300 border-yellow-700' :
            'bg-gray-800 text-gray-400 border-gray-600'
          }`}>{summary.status}</span>
          <span className={`px-3 py-1 rounded-full text-xs font-semibold border ${tierBadge(summary.tier)}`}>
            {summary.tier.toUpperCase()}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Stat label="Vehicles Active" value={String(summary.vehicle_count)} />
        <Stat label="Included" value={String(summary.base_vehicles)} sub="per plan" />
        <Stat label="Extra Trucks" value={String(summary.extra_vehicles)}
              accent={isOverLimit ? 'text-yellow-400' : 'text-white'} />
        <Stat label="Est. Monthly" value={usd(summary.amount_due_cents)} accent="text-green-400" />
      </div>

      <div className="border-t border-gray-800 pt-4 text-sm text-gray-400 space-y-1.5">
        <div className="flex justify-between">
          <span>Base plan — {summary.base_vehicles} trucks included</span>
          <span className="text-white font-medium">{usd(summary.monthly_base_cents)}/mo</span>
        </div>
        {isOverLimit && (
          <div className="flex justify-between">
            <span>{summary.extra_vehicles} extra truck{summary.extra_vehicles !== 1 ? 's' : ''} x {usd(summary.extra_vehicle_cents)} ea</span>
            <span className="text-yellow-400">+{usd(summary.extra_vehicles * summary.extra_vehicle_cents)}/mo</span>
          </div>
        )}
        <div className="flex justify-between font-semibold border-t border-gray-700 pt-2 mt-1">
          <span className="text-white">Total Estimate</span>
          <span className={tierColor(summary.tier)}>{usd(summary.amount_due_cents)}/mo</span>
        </div>
      </div>

      <div className="flex flex-wrap gap-4 mt-4 pt-4 border-t border-gray-800 text-xs text-gray-500">
        {summary.user_count > 0 && (
          <span>👥 {summary.user_count} team member{summary.user_count !== 1 ? 's' : ''}</span>
        )}
        {summary.current_period_end && (
          <span>📅 Period ends {new Date(summary.current_period_end).toLocaleDateString()}</span>
        )}
        {summary.trial_ends_at && (
          <span className="text-yellow-500">⚠️ Trial ends {new Date(summary.trial_ends_at).toLocaleDateString()}</span>
        )}
        {summary.provider === 'stub' && (
          <span className="text-blue-400">🧪 Test mode — no real charges</span>
        )}
      </div>
    </div>
  );
}

// ── AI Usage card ─────────────────────────────────────────────────

function AiUsageCard({ ai }: { ai: AiUsage }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white">AI Usage</h2>
        <span className="text-xs text-gray-500">Last {ai.days} days</span>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-5">
        <Stat label="Total Requests" value={fmtNum(ai.total_requests)} accent="text-blue-400" />
        <Stat label="Total Tokens" value={fmtNum(ai.total_tokens)} accent="text-indigo-400"
              sub="included in plan" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {Object.keys(ai.by_type).length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">By Type</p>
            <div className="space-y-1.5">
              {Object.entries(ai.by_type)
                .sort((a, b) => b[1].requests - a[1].requests)
                .map(([type, s]) => (
                  <div key={type} className="flex justify-between text-sm">
                    <span className="text-gray-300 capitalize">{type.replace('_', ' ')}</span>
                    <span className="text-gray-400 tabular-nums text-xs">
                      {fmtNum(s.requests)} req · {fmtNum(s.tokens)} tok
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
        {Object.keys(ai.by_model).length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">By Model</p>
            <div className="space-y-1.5">
              {Object.entries(ai.by_model)
                .sort((a, b) => b[1].tokens - a[1].tokens)
                .map(([model, s]) => (
                  <div key={model} className="flex justify-between text-sm">
                    <span className="text-gray-300 font-mono text-xs">{model}</span>
                    <span className="text-gray-400 tabular-nums text-xs">
                      {fmtNum(s.requests)} req · {fmtNum(s.tokens)} tok
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Plan cards ────────────────────────────────────────────────────

interface PlanCardProps {
  name: string; price: string; included: number; extraPer: string;
  features: string[]; current: boolean; onUpgrade: () => void; loading: boolean;
}

function PlanCard({ name, price, included, extraPer, features, current, onUpgrade, loading }: PlanCardProps) {
  return (
    <div className={`bg-gray-900 border rounded-xl p-5 flex flex-col ${
      current ? 'border-blue-600 ring-1 ring-blue-600/30' : 'border-gray-700'
    }`}>
      {current && (
        <span className="text-xs bg-blue-900/40 text-blue-300 border border-blue-700 rounded-full px-2 py-0.5 self-start mb-2">
          Current Plan
        </span>
      )}
      <h3 className="text-lg font-bold text-white mb-1 capitalize">{name}</h3>
      <p className="text-2xl font-bold text-green-400 mb-1">
        {price}<span className="text-sm text-gray-400 font-normal">/mo</span>
      </p>
      <p className="text-xs text-gray-500 mb-3">{included} trucks included &middot; {extraPer}/extra truck</p>
      <ul className="text-sm text-gray-300 space-y-1.5 mb-5 flex-1">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-1.5">
            <span className="text-green-400 mt-0.5 shrink-0">✓</span> {f}
          </li>
        ))}
      </ul>
      <button
        onClick={onUpgrade}
        disabled={current || loading}
        className={`w-full py-2 rounded-lg text-sm font-semibold transition ${
          current ? 'bg-gray-700 text-gray-500 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-500 text-white'
        }`}
      >
        {loading ? 'Redirecting…' : current ? 'Current Plan' : `Switch to ${name}`}
      </button>
    </div>
  );
}

// ── Usage history ─────────────────────────────────────────────────

function UsageTable({ items }: { items: UsageSnapshot[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-gray-500 text-center py-8">
        No billing history yet — snapshots are recorded at the end of each billing period.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b border-gray-800 text-xs uppercase tracking-wider">
            <th className="pb-2 pr-4">Period</th>
            <th className="pb-2 pr-4">Vehicles</th>
            <th className="pb-2 pr-4">Extra</th>
            <th className="pb-2 pr-4">Users</th>
            <th className="pb-2 pr-4">AI Queries</th>
            <th className="pb-2 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.period_start} className="border-b border-gray-800/50 hover:bg-gray-800/30">
              <td className="py-2.5 pr-4 text-gray-300 tabular-nums">{row.period_start.slice(0, 7)}</td>
              <td className="py-2.5 pr-4 text-white">{row.vehicle_count}</td>
              <td className="py-2.5 pr-4">
                {row.extra_vehicles > 0
                  ? <span className="text-yellow-400">+{row.extra_vehicles}</span>
                  : <span className="text-gray-600">—</span>}
              </td>
              <td className="py-2.5 pr-4 text-gray-400">{row.user_count ?? '—'}</td>
              <td className="py-2.5 pr-4 text-gray-400">{fmtNum(row.ai_queries)}</td>
              <td className="py-2.5 text-right font-semibold text-green-400">{usd(row.amount_due_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────

export default function Billing() {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [usage, setUsage] = useState<UsageSnapshot[]>([]);
  const [loadingMain, setLoadingMain] = useState(true);
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoadingMain(true);
    setError(null);
    Promise.all([
      apiJSON<BillingSummary>('/billing/summary'),
      apiJSON<{ items: UsageSnapshot[] }>('/billing/usage?limit=12'),
    ])
      .then(([s, u]) => { setSummary(s); setUsage(u.items ?? []); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoadingMain(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCheckout = async (tier: string) => {
    setCheckoutLoading(tier);
    setError(null);
    try {
      const res = await apiJSON<{ url?: string }>(
        '/billing/checkout',
        { method: 'POST', body: { tier } },
      );
      if (res.url) {
        window.location.href = res.url;
      } else {
        load();
        setCheckoutLoading(null);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Checkout failed');
      setCheckoutLoading(null);
    }
  };

  const handlePortal = async () => {
    setPortalLoading(true);
    setError(null);
    try {
      const res = await apiJSON<{ url?: string }>('/billing/portal', { method: 'POST', body: {} });
      if (res.url) window.location.href = res.url;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Portal failed');
    } finally {
      setPortalLoading(false);
    }
  };

  if (loadingMain) {
    return (
      <div className="p-6 text-gray-400 flex items-center gap-2">
        <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        Loading billing info…
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Billing &amp; Subscription</h1>
          <p className="text-sm text-gray-400 mt-1">
            Manage your plan, monitor AI usage, and review billing history.
          </p>
        </div>
        {summary?.provider === 'stripe' && (
          <button
            onClick={handlePortal}
            disabled={portalLoading}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition"
          >
            {portalLoading ? 'Opening…' : '🔗 Manage Payment'}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 bg-red-900/30 border border-red-700 text-red-300 rounded-lg px-4 py-3 text-sm">
          ⚠ {error}
        </div>
      )}

      {summary && <SummaryCard summary={summary} />}

      {summary?.ai_usage && summary.ai_usage.total_requests > 0 && (
        <AiUsageCard ai={summary.ai_usage} />
      )}

      {/* Plans */}
      <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 mt-6">
        Available Plans
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <PlanCard
          name="Starter" price="$49" included={10} extraPer="$2.99"
          features={[
            '10 trucks included',
            'All real-time telematics',
            'Fault & health alerts',
            'AI assistant (all models)',
            '1 Samsara organization',
          ]}
          current={summary?.tier === 'starter'}
          onUpgrade={() => handleCheckout('starter')}
          loading={checkoutLoading === 'starter'}
        />
        <PlanCard
          name="Pro" price="$99" included={10} extraPer="$2.99"
          features={[
            '10 trucks included',
            'Everything in Starter',
            'Unlimited Samsara orgs',
            'Advanced AI reports & vision',
            'Priority support',
            'Custom knowledge base',
          ]}
          current={summary?.tier === 'pro'}
          onUpgrade={() => handleCheckout('pro')}
          loading={checkoutLoading === 'pro'}
        />
      </div>

      {/* Pricing info */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 text-sm text-gray-400">
        <p className="font-medium text-gray-300 mb-1.5">💡 How pricing works</p>
        <ul className="space-y-1 list-disc list-inside text-xs">
          <li>Each plan includes 10 trucks. Additional trucks: $2.99/truck/month.</li>
          <li>Vehicle count syncs automatically from Samsara each day.</li>
          <li>AI usage (tokens) is included — no per-query fees on any plan.</li>
          <li>Invoices generated at the end of each billing period.</li>
        </ul>
      </div>

      {/* Billing history */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Billing History</h2>
          <span className="text-xs text-gray-500">Last 12 periods</span>
        </div>
        <UsageTable items={usage} />
      </div>

      <p className="text-xs text-gray-600 mt-6 text-center">
        Questions?{' '}
        <a href="mailto:billing@4truck.us" className="underline hover:text-gray-400">
          billing@4truck.us
        </a>
      </p>
    </div>
  );
}
