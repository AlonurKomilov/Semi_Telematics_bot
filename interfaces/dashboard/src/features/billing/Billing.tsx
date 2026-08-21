// NAMING: billing = the platform charging family (our charge to the customer
// account, via Stripe) — displayed to customers as "Billing".  Never carrier
// invoicing (future features/invoicing), never driver pay (Driver Pay).
// SSOT: docs/FEATURES.md "Money domains".
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CreditCard, ExternalLink, FileText, AlertTriangle, Gift } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { PageHeader, CardSkeleton } from '../../components/shell';
import { toneClasses } from '../../lib/status';
import { rollupByDisplayLabel } from '../../features/ai/helpers';
import DataGrid from '../../components/datagrid';
import type { AnyColumn } from '../../types';

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

interface LineItem {
  label: string;
  amount_cents: number;  // negative for discount lines
}

interface InactiveVehicleSample {
  vehicle_id: string;
  vehicle_name: string;
  captured_at: string;
}

interface BillingSummary {
  tier: string;
  status: string;
  // Raw Samsara fleet count (informational; not what we bill)
  vehicle_count: number;
  // Activity-driven counts — these drive the math
  active_vehicles: number;
  inactive_vehicles: number;
  inactive_sample: InactiveVehicleSample[];
  base_vehicles: number;
  monthly_base_cents: number;
  extra_vehicle_cents: number;
  extra_vehicles: number;
  // New cost breakdown
  subtotal_cents: number;
  discount_cents: number;
  amount_due_cents: number;
  line_items: LineItem[];
  // Comp account fields
  is_comped: boolean;
  comp_expires_at: string | null;
  comp_reason: string;
  // Set by the webhook handler when an invoice payment fails;
  // cleared on recovery.  Drives the past-due banner + the
  // enforcement-middleware grace-period check.
  past_due_since: string | null;
  billing_email: string | null;
  trial_ends_at: string | null;
  current_period_start: string | null;
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
  active_vehicles?: number;
  inactive_vehicles?: number;
  extra_vehicles: number;
  amount_due_cents: number;
  ai_queries: number;
  user_count: number;
}

interface Invoice {
  id: number;
  provider_invoice_id: string;
  status: string;
  amount_due_cents: number;
  amount_paid_cents: number;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  hosted_invoice_url: string;
  invoice_pdf_url: string;
  paid_at: string | null;
  created_at: string;
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
  if (tier === 'pro') return 'text-purple-600 dark:text-purple-400';
  if (tier === 'enterprise') return 'text-yellow-600 dark:text-yellow-400';
  if (tier === 'starter') return 'text-primary';
  return 'text-muted-foreground';
}

function tierBadge(tier: string): string {
  if (tier === 'pro') return 'bg-purple-500/15 text-purple-700 dark:text-purple-300 border-purple-500/40';
  if (tier === 'enterprise') return 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-300 border-yellow-500/40';
  if (tier === 'starter') return 'bg-primary/15 text-primary border-primary/30';
  return 'bg-muted text-muted-foreground border-border';
}

// ── Stat tile ─────────────────────────────────────────────────────

function Stat({ label, value, accent, sub }: { label: string; value: string; accent?: string; sub?: string }) {
  return (
    <div className="bg-muted rounded-lg p-3">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className={`text-xl font-bold ${accent ?? 'text-foreground'}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Past-due banner ───────────────────────────────────────────────

function PastDueBanner({ since }: { since: string }) {
  // Days since the first failed payment.  Mirrors the backend grace
  // computation; the API itself still gates blocking via
  // BILLING_GRACE_PERIOD_DAYS so the banner copy just communicates
  // urgency without claiming a specific cutoff.
  const days = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 86_400_000));
  return (
    <div className="mb-4 bg-destructive/10 border border-destructive/40 rounded-lg px-4 py-3 flex items-start gap-3">
      <AlertTriangle className="text-destructive shrink-0 mt-0.5" size={18} />
      <div className="text-sm">
        <p className="font-semibold text-destructive">Payment past due ({days} day{days === 1 ? '' : 's'})</p>
        <p className="text-muted-foreground mt-0.5">
          Your latest invoice couldn't be charged.  Service is still active during the grace
          window — update your payment method or pay the open invoice to keep it that way.
        </p>
      </div>
    </div>
  );
}

// ── Comp account banner ───────────────────────────────────────────

function CompBanner({ summary }: { summary: BillingSummary }) {
  const tz = useTimezone();
  if (!summary.is_comped) return null;
  const exp = summary.comp_expires_at ? new Date(summary.comp_expires_at) : null;
  const daysLeft = exp ? Math.max(0, Math.ceil((exp.getTime() - Date.now()) / 86_400_000)) : null;
  const wouldBe = summary.subtotal_cents;
  return (
    <div className="mb-4 bg-primary/5 border border-primary/30 rounded-lg px-4 py-3 flex items-start gap-3">
      <Gift className="text-primary shrink-0 mt-0.5" size={18} />
      <div className="text-sm">
        <p className="font-semibold text-foreground">Complimentary Account</p>
        <p className="text-muted-foreground mt-0.5">
          4truck is covering <span className="font-semibold text-foreground">{usd(wouldBe)}/month</span> for you.
          {exp && daysLeft != null && (
            <>
              {' '}Expires <span className="font-medium text-foreground">{formatDay(exp, { timeZone: tz, intl: { month: 'short', day: 'numeric', year: 'numeric' } })}</span>
              {' '}<span className="text-xs">({daysLeft} day{daysLeft === 1 ? '' : 's'} remaining)</span>
            </>
          )}
        </p>
        {summary.comp_reason && (
          <p className="text-xs text-muted-foreground mt-1 italic">"{summary.comp_reason}"</p>
        )}
      </div>
    </div>
  );
}

// ── Inactive vehicles footer ──────────────────────────────────────

function InactiveVehiclesFooter({ summary }: { summary: BillingSummary }) {
  if (summary.inactive_vehicles === 0) return null;
  const sampleNames = summary.inactive_sample.map((v) => v.vehicle_name || v.vehicle_id).filter(Boolean);
  const previewCount = Math.min(3, sampleNames.length);
  const preview = sampleNames.slice(0, previewCount).join(', ');
  const extra = summary.inactive_vehicles - previewCount;
  return (
    <div className="mt-3 text-xs text-muted-foreground border-t border-border pt-3 flex items-start gap-2">
      <span className="shrink-0">ⓘ</span>
      <p>
        <span className="font-medium text-foreground/80">
          {summary.inactive_vehicles} vehicle{summary.inactive_vehicles === 1 ? '' : 's'} inactive for 3+ days
        </span>
        {' '}— not billed{preview && (
          <> (e.g. {preview}{extra > 0 ? `, +${extra} more` : ''})</>
        )}.
      </p>
    </div>
  );
}

// ── Plan summary card ─────────────────────────────────────────────

function SummaryCard({ summary }: { summary: BillingSummary }) {
  const tz = useTimezone();
  const isOverLimit = summary.extra_vehicles > 0;
  return (
    <div className="bg-card border border-border rounded-xl p-6 mb-4">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{summary.account_name || 'Current Plan'}</h2>
          {summary.billing_email && (
            <p className="text-xs text-muted-foreground mt-0.5">Billing contact: {summary.billing_email}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium border ${
            summary.status === 'active' ? toneClasses('ok') :
            summary.status === 'trialing' ? toneClasses('warn') :
            summary.status === 'past_due' ? toneClasses('danger') :
            summary.status === 'canceled' || summary.status === 'unpaid' ? toneClasses('danger') :
            toneClasses('neutral')
          }`}>{summary.status.replace('_', ' ')}</span>
          <span className={`px-3 py-1 rounded-md text-xs font-semibold border ${tierBadge(summary.tier)}`}>
            {summary.tier.toUpperCase()}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Stat label="Active Vehicles" value={String(summary.active_vehicles)}
              sub={summary.inactive_vehicles > 0 ? `${summary.inactive_vehicles} parked` : 'last 3 days'} />
        <Stat label="Included" value={String(summary.base_vehicles)} sub="per plan" />
        <Stat label="Extra Trucks" value={String(summary.extra_vehicles)}
              accent={isOverLimit ? 'text-warn' : 'text-foreground'} />
        <Stat
          label={summary.is_comped ? 'Total Due' : 'Est. Monthly'}
          value={usd(summary.amount_due_cents)}
          accent={summary.is_comped ? 'text-foreground' : 'text-ok'}
        />
      </div>

      {/* Itemized breakdown — uses the server's line_items if present,
          falls back to the old computed pair so a backend that hasn't
          shipped the new fields yet still renders something. */}
      <div className="border-t border-border pt-4 text-sm text-muted-foreground space-y-1.5">
        {summary.line_items && summary.line_items.length > 0 ? (
          summary.line_items.map((item, idx) => (
            <div key={`${item.label}-${idx}`} className="flex justify-between">
              <span>{item.label}</span>
              <span className={item.amount_cents < 0
                ? 'text-primary font-medium'
                : 'text-foreground font-medium'}
              >
                {item.amount_cents < 0 ? '−' : ''}{usd(Math.abs(item.amount_cents))}
              </span>
            </div>
          ))
        ) : (
          <>
            <div className="flex justify-between">
              <span>Base plan — {summary.base_vehicles} trucks included</span>
              <span className="text-foreground font-medium">{usd(summary.monthly_base_cents)}/mo</span>
            </div>
            {isOverLimit && (
              <div className="flex justify-between">
                <span>{summary.extra_vehicles} extra truck{summary.extra_vehicles !== 1 ? 's' : ''} × {usd(summary.extra_vehicle_cents)} ea</span>
                <span className="text-warn">+{usd(summary.extra_vehicles * summary.extra_vehicle_cents)}/mo</span>
              </div>
            )}
          </>
        )}
        {summary.is_comped && summary.subtotal_cents > 0 && (
          <div className="flex justify-between text-xs text-muted-foreground border-t border-border/50 pt-1.5 mt-1.5">
            <span>Subtotal (covered by 4truck)</span>
            <span>{usd(summary.subtotal_cents)}</span>
          </div>
        )}
        <div className="flex justify-between font-semibold border-t border-border pt-2 mt-1">
          <span className="text-foreground">Total Due</span>
          <span className={summary.is_comped ? 'text-foreground' : tierColor(summary.tier)}>
            {usd(summary.amount_due_cents)}/mo
          </span>
        </div>
        <InactiveVehiclesFooter summary={summary} />
      </div>

      <div className="flex flex-wrap gap-4 mt-4 pt-4 border-t border-border text-xs text-muted-foreground">
        {summary.user_count > 0 && (
          <span>👥 {summary.user_count} team member{summary.user_count !== 1 ? 's' : ''}</span>
        )}
        {summary.current_period_end && (
          <span>📅 Period ends {formatDay(summary.current_period_end, { timeZone: tz })}</span>
        )}
        {summary.trial_ends_at && (
          <span className="text-warn">⚠️ Trial ends {formatDay(summary.trial_ends_at, { timeZone: tz })}</span>
        )}
        {summary.provider === 'stub' && (
          <span className="text-primary">🧪 Test mode — no real charges</span>
        )}
      </div>
    </div>
  );
}

// ── AI Usage card ─────────────────────────────────────────────────
//
// Friendly action-label mapping + rollup live in src/lib/aiUsage.ts
// so both this Billing panel and the admin Settings panel render the
// same display names without diverging.

function AiUsageCard({ ai }: { ai: AiUsage }) {
  const typeRows = rollupByDisplayLabel(ai.by_type);
  return (
    <div className="bg-card border border-border rounded-xl p-6 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-foreground">AI Usage</h2>
        <span className="text-xs text-muted-foreground">Last {ai.days} days</span>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-5">
        <Stat label="Total Requests" value={fmtNum(ai.total_requests)} accent="text-primary" />
        <Stat label="Total Tokens" value={fmtNum(ai.total_tokens)} accent="text-indigo-400"
              sub="included in plan" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {typeRows.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">By Type</p>
            <div className="space-y-1.5">
              {typeRows.map(([label, s]) => (
                <div key={label} className="flex justify-between text-sm">
                  <span className="text-foreground/80">{label}</span>
                  <span className="text-muted-foreground tabular-nums text-xs">
                    {fmtNum(s.requests)} req · {fmtNum(s.tokens)} tok
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {Object.keys(ai.by_model).length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">By Model</p>
            <div className="space-y-1.5">
              {Object.entries(ai.by_model)
                .sort((a, b) => b[1].tokens - a[1].tokens)
                .map(([model, s]) => (
                  <div key={model} className="flex justify-between text-sm">
                    <span className="text-foreground/80 font-mono text-xs">{model}</span>
                    <span className="text-muted-foreground tabular-nums text-xs">
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
    <div className={`bg-card border rounded-xl p-5 flex flex-col ${
      current ? 'border-primary ring-1 ring-primary/30' : 'border-border'
    }`}>
      {current && (
        <span className="text-xs bg-primary/15 text-primary border border-primary/30 rounded-md px-2 py-0.5 self-start mb-2">
          Current Plan
        </span>
      )}
      <h3 className="text-base font-semibold text-foreground mb-1 capitalize">{name}</h3>
      <p className="text-2xl font-bold text-ok mb-1">
        {price}<span className="text-sm text-muted-foreground font-normal">/mo</span>
      </p>
      <p className="text-xs text-muted-foreground mb-3">{included} trucks included &middot; {extraPer}/extra truck</p>
      <ul className="text-sm text-foreground/80 space-y-1.5 mb-5 flex-1">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-1.5">
            <span className="text-ok mt-0.5 shrink-0">✓</span> {f}
          </li>
        ))}
      </ul>
      <button
        onClick={onUpgrade}
        disabled={current || loading}
        className={`w-full py-2 rounded-lg text-sm font-semibold transition ${
          current ? 'bg-muted text-muted-foreground cursor-not-allowed' : 'bg-primary hover:bg-primary/90 text-primary-foreground'
        }`}
      >
        {loading ? 'Redirecting…' : current ? 'Current Plan' : `Switch to ${name}`}
      </button>
    </div>
  );
}

// ── Usage history ─────────────────────────────────────────────────

// Column config lives at module scope — stable identity keeps
// DataGrid's ``columns`` memo cheap and prevents column-order state
// churn.
const USAGE_COLUMNS: AnyColumn[] = [
  {
    key: 'period_start', label: 'Period', sortable: true,
    render: (v) => (
      <span className="tabular-nums text-foreground/80">
        {String(v).slice(0, 7)}
      </span>
    ),
  },
  {
    key: 'active_vehicles', label: 'Active', sortable: true,
    // ``active_vehicles`` is the post-Day-5 column.  Pre-migration
    // rows have only ``vehicle_count`` (raw fleet); show that as a
    // fallback rather than ``—`` so old months stay legible.
    render: (_v, row) => {
      const r = row as unknown as UsageSnapshot;
      const billable = r.active_vehicles ?? r.vehicle_count;
      return (
        <span>
          {billable}
          {r.inactive_vehicles ? (
            <span className="text-xs text-muted-foreground ml-1.5">(+{r.inactive_vehicles} idle)</span>
          ) : null}
        </span>
      );
    },
  },
  {
    key: 'extra_vehicles', label: 'Extra', sortable: true,
    render: (v) => Number(v) > 0
      ? <span className="text-warn">+{String(v)}</span>
      : <span className="text-muted-foreground">—</span>,
  },
  { key: 'user_count', label: 'Users', sortable: true,
    render: (v) => <span className="text-muted-foreground">{v == null ? '—' : String(v)}</span> },
  { key: 'ai_queries', label: 'AI Queries', sortable: true,
    render: (v) => <span className="text-muted-foreground">{fmtNum(Number(v))}</span> },
  { key: 'amount_due_cents', label: 'Amount', sortable: true,
    render: (v) => <span className="font-semibold text-ok tabular-nums">{usd(Number(v))}</span> },
];

function UsageTable({ items }: { items: UsageSnapshot[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">
        No billing history yet — snapshots are recorded at the end of each billing period.
      </p>
    );
  }
  return (
    <DataGrid
      columns={USAGE_COLUMNS}
      data={items as unknown as Record<string, unknown>[]}
      enableToolbar={false}
      enablePagination={false}
    />
  );
}

// ── Invoices table ────────────────────────────────────────────────

function InvoicesTable({ items }: { items: Invoice[] }) {
  const tz = useTimezone();
  // Column config depends on ``tz`` so it lives in the render — the
  // formatters need the account's effective timezone from the hook.
  const columns: AnyColumn[] = [
    {
      key: 'created_at', label: 'Date', sortable: true,
      render: (v) => (
        <span className="tabular-nums text-foreground/80">
          {formatDay(String(v), { timeZone: tz })}
        </span>
      ),
    },
    {
      key: 'status', label: 'Status', sortable: true,
      render: (v) => {
        const s = String(v || '');
        const tone =
          s === 'paid' ? toneClasses('ok') :
          s === 'open' ? toneClasses('warn') :
          s === 'uncollectible' || s === 'void' ? toneClasses('danger') :
          toneClasses('neutral');
        return (
          <span className={`px-2 py-0.5 rounded text-xs font-medium border ${tone}`}>
            {s || '—'}
          </span>
        );
      },
    },
    {
      key: 'period_start', label: 'Period', sortable: true,
      render: (_v, row) => {
        const inv = row as unknown as Invoice;
        return (
          <span className="text-muted-foreground text-xs">
            {inv.period_start ? formatDay(inv.period_start, { timeZone: tz }) : '—'}
            {inv.period_end && (
              <> → {formatDay(inv.period_end, { timeZone: tz })}</>
            )}
          </span>
        );
      },
    },
    {
      key: 'amount_paid_cents', label: 'Amount', sortable: true,
      render: (_v, row) => {
        const inv = row as unknown as Invoice;
        return (
          <span className="font-semibold text-foreground tabular-nums">
            {usd(inv.amount_paid_cents || inv.amount_due_cents)}
          </span>
        );
      },
    },
    {
      key: 'hosted_invoice_url', label: 'Receipt', sortable: false,
      render: (v) => v ? (
        <a href={String(v)} target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-1 text-primary text-xs hover:underline">
          <FileText size={12} /> View
        </a>
      ) : <span className="text-muted-foreground text-xs">—</span>,
    },
  ];
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">
        No invoices yet — your first invoice arrives at the end of the current billing period.
      </p>
    );
  }
  return (
    <DataGrid
      columns={columns}
      data={items as unknown as Record<string, unknown>[]}
      enableToolbar={false}
      enablePagination={false}
    />
  );
}

// NOTE: A billing-email edit control lived here briefly and was
// removed.  Customers update billing details (including the receipt
// email) via the Stripe Customer Portal — the "Manage payment" button
// in the page header opens that.  Doing system-side writes from the
// user dashboard mixes operator and customer surfaces, which we
// explicitly want to avoid.  The ``PATCH /billing/email`` API endpoint
// stays — it's used by the operator-only system.4truck.us console.

// ── Main Page ─────────────────────────────────────────────────────

export default function Billing() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [usage, setUsage] = useState<UsageSnapshot[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
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
      apiJSON<{ items: Invoice[] }>('/billing/invoices?limit=24'),
    ])
      .then(([s, u, i]) => {
        setSummary(s);
        setUsage(u.items ?? []);
        setInvoices(i.items ?? []);
      })
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
      <div className="p-6 max-w-4xl mx-auto">
        <PageHeader
          icon={CreditCard}
          title={t('pages.billing_title')}
          description={t('pages.billing_desc_short')}
        />
        <div className="space-y-3">
          <CardSkeleton height="h-32" />
          <CardSkeleton height="h-48" />
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <PageHeader
        icon={CreditCard}
        title={t('pages.billing_title')}
        description={t('pages.billing_desc_long')}
        actions={
          summary?.provider === 'stripe' ? (
            <button
              onClick={handlePortal}
              disabled={portalLoading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-background border border-border rounded-md text-xs font-medium hover:bg-muted transition disabled:opacity-60 min-h-tap"
            >
              <ExternalLink size={12} />
              {portalLoading ? 'Opening…' : 'Manage payment'}
            </button>
          ) : undefined
        }
      />

      {error && (
        <div className="mb-4 bg-destructive/10 border border-destructive/40 text-destructive rounded-lg px-4 py-3 text-sm">
          ⚠ {error}
        </div>
      )}

      {/* Banners — past-due first (urgent), then comp (informational).
          Comp wins the visual hierarchy when both are somehow active
          (shouldn't happen — enforcement bypasses comped accounts —
          but defensive UI ordering). */}
      {summary?.past_due_since && !summary.is_comped && <PastDueBanner since={summary.past_due_since} />}
      {summary && <CompBanner summary={summary} />}

      {summary && <SummaryCard summary={summary} />}

      {summary?.ai_usage && summary.ai_usage.total_requests > 0 && (
        <AiUsageCard ai={summary.ai_usage} />
      )}

      {/* Plans */}
      <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3 mt-6">
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
      <div className="bg-card border border-border rounded-xl p-4 mb-6 text-sm text-muted-foreground">
        <p className="font-medium text-foreground/80 mb-1.5">💡 How pricing works</p>
        <ul className="space-y-1 list-disc list-inside text-xs">
          <li>Each plan includes 10 trucks. Additional <em>active</em> trucks: $2.99/truck/month.</li>
          <li>A truck is "active" if it sent any telemetry signal in the last 3 days — parked trucks are automatically excluded.</li>
          <li>AI usage (tokens) is included — no per-query fees on any plan.</li>
          <li>Invoices generated at the end of each billing period, with mid-cycle vehicle changes pro-rated automatically.</li>
        </ul>
      </div>

      {/* Invoices — Stripe-issued bills.  Hidden when empty AND on
          stub provider so test installs don't show a dead section. */}
      {(invoices.length > 0 || summary?.provider === 'stripe') && (
        <div className="bg-card border border-border rounded-xl p-6 mb-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground">Invoices</h2>
            <span className="text-xs text-muted-foreground">Last 24</span>
          </div>
          <InvoicesTable items={invoices} />
        </div>
      )}

      {/* Billing history — internal monthly usage snapshots, separate
          from the Stripe invoices above.  Useful even on the stub
          provider where invoices don't exist. */}
      <div className="bg-card border border-border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-foreground">Usage History</h2>
          <span className="text-xs text-muted-foreground">Last 12 periods</span>
        </div>
        <UsageTable items={usage} />
      </div>

      <p className="text-xs text-muted-foreground mt-6 text-center">
        Questions?{' '}
        <a href="mailto:billing@4truck.us" className="underline hover:text-muted-foreground">
          billing@4truck.us
        </a>
      </p>
    </div>
  );
}
