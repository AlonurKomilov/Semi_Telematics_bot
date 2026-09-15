// NAMING: billing = the platform charging family (our charge to the customer
// account, via Stripe) — displayed to customers as "Billing".  Never carrier
// invoicing (future features/invoicing), never driver pay (Driver Pay).
// SSOT: docs/FEATURES.md "Money domains".
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CalendarDays, Check, CreditCard, ExternalLink, FileText, FlaskConical, Gift, Lightbulb, Users, Download } from '../../lib/icons';
import { apiFetch, apiJSON } from '../../api/client';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { CardSkeleton, PageHeader, SectionHeader } from '../../components/shell';
import { toneClasses } from '../../lib/status';
import { rollupByDisplayLabel } from '../../features/ai/helpers';
import DataGrid from '../../components/datagrid';
import type { AnyColumn } from '../../types';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { FEATURE_CATALOG } from '../../config/featureCatalog';
import { featureLines, money, onOffer, overQuotaLines, plansIncluding, type CustomerPlan } from './planCards';
import { cardVariants } from '@/components/ui/card';

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
  archived_at: string;
}

interface BillingSummary {
  tier: string;
  status: string;
  // Fleet size on the subscription row (informational; the same number)
  vehicle_count: number;
  // Registry counts — these drive the math: every truck in the
  // Vehicles list that is not archived is billed; archived trucks and
  // trailers are not.
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
  /** '' when there is none — a granted price break, in the customer's words */
  promotion_label: string;
  promotion_until: string;
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
  company_count: number;
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
  if (tier === 'pro') return 'text-chart-1';
  if (tier === 'enterprise') return 'text-chart-4';
  if (tier === 'starter') return 'text-primary';
  return 'text-muted-foreground';
}

function tierBadge(tier: string): string {
  if (tier === 'pro') return 'bg-chart-1/15 text-chart-1 border-chart-1/40';
  if (tier === 'enterprise') return 'bg-chart-4/15 text-chart-4 border-chart-4/40';
  if (tier === 'starter') return 'bg-primary/15 text-foreground border-primary';
  return 'bg-muted text-muted-foreground border-border';
}

// ── Stat tile ─────────────────────────────────────────────────────

function Stat({ label, value, accent, sub }: { label: string; value: string; accent?: string; sub?: string }) {
  return (
    // `truncate` on all three lines: "$341.19" painted 59px outside its
    // tile and 35px outside the card, onto the page background —
    // `overflow: visible`, and nothing to stop it. The grid that let the
    // tile get that narrow is fixed separately (grid-cols-fit-36); this
    // is the backstop that means a number can never escape again.
    <div className="bg-muted rounded-lg p-3">
      <p className="text-xs text-muted-foreground mb-1 truncate">{label}</p>
      <p className={`text-xl font-bold truncate ${accent ?? 'text-foreground'}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5 truncate">{sub}</p>}
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
      <AlertTriangle className="text-destructive shrink-0 mt-0.5 size-4.5" />
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
      <Gift className="text-primary shrink-0 mt-0.5 size-4.5" />
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
          {summary.inactive_vehicles} archived truck{summary.inactive_vehicles === 1 ? '' : 's'}
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
    <Card className="mb-4">
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

      <div className="grid grid-cols-fit-36 gap-3 mb-5">
        <Stat label="Billed Trucks" value={String(summary.active_vehicles)}
              sub={summary.inactive_vehicles > 0 ? `${summary.inactive_vehicles} archived` : 'in your Vehicles list'} />
        <Stat label="Included" value={String(summary.base_vehicles)} sub="per plan" />
        <Stat label="Extra Trucks" value={String(summary.extra_vehicles)}
              accent={isOverLimit ? 'text-warn' : 'text-foreground'} />
        <Stat
          label={summary.is_comped ? 'Total due' : 'Estimated monthly'}
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
        {/* A granted price break: the customer sees what came off, not
            only the smaller number — otherwise the gift is invisible
            and the bill just looks like a different price. */}
        {!summary.is_comped && summary.discount_cents > 0 && (
          <>
            <div className="flex justify-between text-xs text-muted-foreground border-t border-border/50 pt-1.5 mt-1.5">
              <span>Amount</span>
              <span>{usd(summary.subtotal_cents)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-primary">
                {summary.promotion_label || 'Promotion'}
                {summary.promotion_until && (
                  <span className="text-muted-foreground"> · until {summary.promotion_until.slice(0, 10)}</span>
                )}
              </span>
              <span className="text-primary">-{usd(summary.discount_cents)}</span>
            </div>
          </>
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
          <span className="inline-flex items-center gap-1.5"><Users className="size-3.5" aria-hidden />{summary.user_count} team member{summary.user_count !== 1 ? 's' : ''}</span>
        )}
        {summary.current_period_end && (
          <span className="inline-flex items-center gap-1.5"><CalendarDays className="size-3.5" aria-hidden />Period ends {formatDay(summary.current_period_end, { timeZone: tz })}</span>
        )}
        {summary.trial_ends_at && (
          <span className="inline-flex items-center gap-1.5 text-warn"><AlertTriangle className="size-3.5" aria-hidden />Trial ends {formatDay(summary.trial_ends_at, { timeZone: tz })}</span>
        )}
        {summary.provider === 'stub' && (
          <Badge tone="warn"><FlaskConical className="size-3" aria-hidden />No charges — payments are not switched on for this account yet</Badge>
        )}
      </div>
    </Card>
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
    <Card className="mb-4">
      <div className="flex items-center justify-between mb-4">
        <SectionHeader>AI Usage</SectionHeader>
        <span className="text-xs text-muted-foreground">Last {ai.days} days</span>
      </div>
      <div className="grid grid-cols-fit-36 gap-3 mb-5">
        <Stat label="Total Requests" value={fmtNum(ai.total_requests)} accent="text-primary" />
        <Stat label="Total Tokens" value={fmtNum(ai.total_tokens)} accent="text-chart-1"
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
    </Card>
  );
}

// ── Plan cards ────────────────────────────────────────────────────

interface PlanCardProps {
  name: string; price: string;
  features: string[]; current: boolean; highlighted: boolean; buyable: boolean; offered?: boolean;
  /** What this plan would not hold, from what the account has today. */
  warnings?: string[];
  /** A plan offered at no price is not for sale — it is a conversation.
   *  The case number of one already asked for, when there is one. */
  openCase?: string;
  /** What the server said about this request — "we have it" reads
   *  differently from "you already asked", and only the server knows
   *  which happened. */
  caseMessage?: string;
  onUpgrade: () => void;
  onAsk?: (note: string, email: string) => Promise<void>;
  defaultEmail?: string;
  loading: boolean;
}

function PlanCard({
  name, price, features, current, highlighted, buyable, offered = false, warnings = [],
  openCase, caseMessage, onUpgrade, onAsk, defaultEmail = '', loading,
}: PlanCardProps) {
  // A plan with no price cannot be bought; asking about it is the whole
  // interaction, so the form lives in the card rather than behind a
  // dialog — the reader is already looking at what they are asking for.
  const askable = !current && !buyable && !!onAsk;
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const [email, setEmail] = useState(defaultEmail);
  const [sending, setSending] = useState(false);
  return (
    <div className={cn(cardVariants({ padding: 'default' }), 'flex flex-col', (current || highlighted || offered) && 'border-primary ring-1 ring-primary/30')}>
      {current && (
        <span className="text-xs bg-primary/15 text-foreground border border-primary rounded-md px-2 py-0.5 self-start mb-2">
          Current Plan
        </span>
      )}
      {!current && offered && (
        <span className="text-xs bg-primary/15 text-foreground border border-primary rounded-md px-2 py-0.5 self-start mb-2">
          Prepared for your account
        </span>
      )}
      {!current && !offered && highlighted && (
        <span className="text-xs bg-primary/15 text-foreground border border-primary rounded-md px-2 py-0.5 self-start mb-2">
          Includes what you asked for
        </span>
      )}
      <h3 className="text-base font-semibold text-foreground mb-1">{name}</h3>
      <p className="text-2xl font-bold text-ok mb-3">
        {price}<span className="text-sm text-muted-foreground font-normal">/mo</span>
      </p>
      <ul className="text-sm text-foreground/80 space-y-1.5 mb-5 flex-1">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-1.5">
            <Check className="size-3.5 text-ok mt-0.5 shrink-0" aria-hidden /> {f}
          </li>
        ))}
      </ul>
      {warnings.length > 0 && (
        /* Above the button, because it is what the button is about: a
           quota bites when something is CREATED, so a smaller plan
           freezes what is already over the line rather than deleting
           it — fair, and an unfair surprise if it arrives after the
           payment. */
        <ul className="text-xs text-warn/90 space-y-1 mb-3">
          {warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5">
              <AlertTriangle className="size-3.5 mt-0.5 shrink-0" aria-hidden /> {w}
            </li>
          ))}
        </ul>
      )}
      {openCase ? (
        /* Already asked: the case number is the answer, and offering the
           button again would only produce a second conversation about
           the same thing. */
        <div className="rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
          <p className="font-medium text-foreground">Request {openCase}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {caseMessage ?? 'We have it — someone will be in touch by email.'}
          </p>
        </div>
      ) : asking ? (
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!onAsk) return;
            setSending(true);
            try { await onAsk(note, email); } finally { setSending(false); }
          }}
        >
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            maxLength={2000}
            required
            placeholder="How many trucks, how many companies, and what you need it to do"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm
                       placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="Where should we reply?"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm
                       placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={sending}
              className="flex-1 py-2 min-h-tap rounded-lg text-sm font-semibold transition
                         bg-primary hover:bg-primary-hover text-primary-foreground disabled:opacity-60"
            >{sending ? 'Sending…' : 'Send request'}</button>
            <button
              type="button"
              onClick={() => setAsking(false)}
              className="px-3 py-2 min-h-tap rounded-lg text-sm text-muted-foreground hover:text-foreground"
            >Cancel</button>
          </div>
        </form>
      ) : (
        <button
          onClick={askable ? () => setAsking(true) : onUpgrade}
          disabled={current || loading || (!buyable && !askable)}
          className={`w-full py-2 min-h-tap rounded-lg text-sm font-semibold transition ${
            current || (!buyable && !askable) ? 'bg-muted text-muted-foreground cursor-not-allowed' : 'bg-primary hover:bg-primary-hover text-primary-foreground'
          }`}
        >
          {loading ? 'Opening Stripe…'
            : current ? 'Current Plan'
            : !buyable ? 'Contact Sales'
            : 'Upgrade'}
        </button>
      )}
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

/** The PDF of an invoice 4truck wrote itself.
 *
 *  Fetched rather than linked: the endpoint is behind the API's auth
 *  and a browser sends no Authorization header on an <a href>. The blob
 *  is opened in a new tab and revoked when the tab has taken it. */
function InvoicePdfButton({ number }: { number: string }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const open = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const res = await apiFetch(`/billing/invoices/${encodeURIComponent(number)}/pdf`);
      if (!res.ok) { setFailed(true); return; }
      const url = URL.createObjectURL(await res.blob());
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      // A bill that will not open and says nothing is the same as a
      // bill that is not there — the reason has to reach the person.
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button onClick={open} disabled={busy}
            className="inline-flex items-center gap-1 text-primary text-xs hover:underline min-h-tap disabled:opacity-50">
      <Download className="size-3" />
      {busy ? 'Opening…' : failed ? "Couldn't open — retry" : 'PDF'}
    </button>
  );
}

function UsageTable({ items }: { items: UsageSnapshot[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">
        No billing history yet — a summary is recorded at the end of each billing period.
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
      // Both links come from Stripe and both are stored; only the hosted
      // one was ever offered, so a customer who wanted the PDF for their
      // bookkeeping had to open the receipt and hunt for it there.
      key: 'hosted_invoice_url', label: 'Receipt', sortable: false,
      render: (_v, row) => {
        const hosted = String(row.hosted_invoice_url || '');
        const pdf = String(row.invoice_pdf_url || '');
        const number = String(row.provider_invoice_id || '');
        // An invoice 4truck wrote has no Stripe page and no hosted PDF;
        // its file is built on demand from the row, behind the API's
        // own auth — so it is fetched, not linked (the browser sends no
        // Authorization header on an <a href>).
        const isLocal = !hosted && !pdf && !!number;
        if (!hosted && !pdf && !isLocal) return <span className="text-muted-foreground text-xs">—</span>;
        return (
          <span className="inline-flex items-center gap-3">
            {hosted && (
              <a href={hosted} target="_blank" rel="noopener noreferrer"
                 className="inline-flex items-center gap-1 text-primary text-xs hover:underline min-h-tap">
                <FileText className="size-3" /> View
              </a>
            )}
            {pdf && (
              <a href={pdf} target="_blank" rel="noopener noreferrer"
                 className="inline-flex items-center gap-1 text-primary text-xs hover:underline min-h-tap">
                <Download className="size-3" /> PDF
              </a>
            )}
            {isLocal && <InvoicePdfButton number={number} />}
          </span>
        );
      },
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
  const [plans, setPlans] = useState<CustomerPlan[]>([]);
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
      apiJSON<{ plans: CustomerPlan[] }>('/billing/plans'),
    ])
      .then(([s, u, i, p]) => {
        setSummary(s);
        setUsage(u.items ?? []);
        setInvoices(i.items ?? []);
        setPlans(p.plans ?? []);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoadingMain(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // "?upgrade=<feature>": the lock in the sidebar, the matrix or a closed
  // route brought the owner here for ONE feature — name it, and mark
  // the plans that include it.
  const [params] = useSearchParams();
  const upgradeFor = params.get('upgrade') ?? '';
  const upgradeFeature = useMemo(() => FEATURE_CATALOG.find((f) => f.id === upgradeFor), [upgradeFor]);
  const upgradePlans = useMemo(() => (upgradeFor ? plansIncluding(upgradeFor, plans) : []), [upgradeFor, plans]);
  const labelOf = (id: string) => {
    const f = FEATURE_CATALOG.find((c) => c.id === id);
    return f ? t(f.labelKey) : id.replace(/[_-]/g, ' ');
  };
  const words = {
    everything: 'Every feature and service',
    unlimitedUsers: 'Unlimited users',
    users: (n: number) => `Up to ${n} user${n === 1 ? '' : 's'}`,
    unlimitedCompanies: 'Unlimited companies',
    companies: (n: number) => `Up to ${n} compan${n === 1 ? 'y' : 'ies'}`,
    trucks: (n: number) => `${n} trucks included`, extra: (p: string) => `${p}/month per extra active truck`,
  };

  // Said in what the customer HAS, not in what the plan allows: "up to 3
  // companies" is a fact about the plan; "you have 5" is the reason it
  // matters to them.
  const quotaWarnings = {
    users: (allowed: number, have: number) =>
      `You have ${have} team members; this plan allows ${allowed}. No one is removed — you cannot add more until you are under ${allowed}.`,
    companies: (allowed: number, have: number) =>
      `You have ${have} companies; this plan allows ${allowed}. None is deleted — you cannot add more until you are under ${allowed}.`,
  };

  // Which plans this account has already asked about, so a card shows
  // its case number instead of offering the button a second time.
  const [openCases, setOpenCases] = useState<Record<string, string>>({});
  useEffect(() => {
    apiJSON<{ items: { tier: string; case_number: string }[] }>('/billing/plan-requests')
      .then((r) => setOpenCases(
        Object.fromEntries(r.items.map((i) => [i.tier, i.case_number]))))
      .catch(() => { /* a missing case number costs a duplicate, not a page */ });
  }, []);

  const [askResult, setAskResult] = useState<Record<string, string>>({});
  const handleAsk = async (tier: string, note: string, email: string) => {
    setError(null);
    try {
      const res = await apiJSON<{ case_number: string; joined: boolean; message: string }>(
        '/billing/plan-request',
        { method: 'POST', body: { tier, note, contact_email: email } },
      );
      setOpenCases((c) => ({ ...c, [tier]: res.case_number }));
      // The server's sentence, not one assembled here: asking twice is a
      // different answer from asking once, and the difference is the
      // whole point of saying anything.
      setAskResult((r) => ({ ...r, [tier]: res.message }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'We could not record that request. Please try again.');
    }
  };

  const handleCheckout = async (tier: string) => {
    setCheckoutLoading(tier);
    setError(null);
    // Opened NOW, inside the click, because a browser only allows a new
    // tab while it can still see the gesture that asked for one — open
    // it after the round trip and it is a popup, and blocked. The tab
    // waits on about:blank until the session URL arrives.
    //
    // NOT with 'noopener': that feature makes window.open return null by
    // definition, so the handle needed to point the tab at Stripe never
    // arrives — the blank tab sits there and the customer's own page
    // navigates away instead, which is what this was supposed to stop.
    // The opener reference is cut below, once the tab has its URL.
    const tab = window.open('', '_blank');
    try {
      const res = await apiJSON<{ url?: string }>(
        '/billing/checkout',
        { method: 'POST', body: { tier } },
      );
      if (res.url) {
        if (tab) {
          tab.location.href = res.url;
          // Cut the back-reference now that the tab is on its way: the
          // page we just opened has no business reaching into this one.
          try { tab.opener = null; } catch { /* cross-origin by then, which is the point */ }
          tab.focus();
          // The billing page stays where it was, so the customer comes
          // back to their own account rather than to whatever Stripe
          // decided to return them to.
          setCheckoutLoading(null);
        } else {
          // Popups blocked — going there in place still beats a dead
          // button.
          window.location.href = res.url;
        }
      } else {
        tab?.close();
        load();
        setCheckoutLoading(null);
      }
    } catch (e: unknown) {
      tab?.close();
      setError(e instanceof Error ? e.message : 'We could not start the checkout. Please try again.');
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
      setError(e instanceof Error ? e.message : 'We could not open the payment portal. Please try again.');
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
              <ExternalLink className="size-3" />
              {portalLoading ? 'Opening Stripe…' : 'Manage payment'}
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

      {/* Plans — from the plan table the operator edits; the cards say
          what each plan includes in the reader's language. */}
      <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3 mt-6">
        Available Plans
      </h2>
      {upgradeFor && (
        <Card className="mb-4 text-sm">
          {upgradePlans.length > 0 ? (
            <p>
              <span className="font-medium text-foreground">{upgradeFeature ? t(upgradeFeature.labelKey) : labelOf(upgradeFor)}</span>
              {' '}is included in {upgradePlans.map((p) => p.label).join(' and ')} — pick one below.
            </p>
          ) : (
            <p>
              <span className="font-medium text-foreground">{upgradeFeature ? t(upgradeFeature.labelKey) : labelOf(upgradeFor)}</span>
              {' '}is not in any plan you can pick here. Contact support to add it.
            </p>
          )}
        </Card>
      )}
      {plans.length === 0 ? (
        <Card className="mb-6 text-sm text-muted-foreground">No plans are offered right now.</Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          {plans.map((p) => (
            <PlanCard
              key={p.tier}
              name={p.label}
              price={money(p.price_monthly_cents)}
              features={featureLines(p, labelOf, words)}
              current={p.current}
              highlighted={upgradePlans.some((u) => u.tier === p.tier)}
              onUpgrade={() => handleCheckout(p.tier)}
              loading={checkoutLoading === p.tier}
              buyable={onOffer(p) && p.price_monthly_cents > 0}
              offered={!!p.offered}
              warnings={overQuotaLines(
                p,
                { users: summary?.user_count ?? 0, companies: summary?.company_count ?? 0 },
                quotaWarnings,
              )}
              openCase={openCases[p.tier]}
              caseMessage={askResult[p.tier]}
              onAsk={(note, email) => handleAsk(p.tier, note, email)}
              defaultEmail={summary?.billing_email ?? ''}
            />
          ))}
        </div>
      )}

      {/* Pricing info */}
      <Card className="mb-6 text-sm text-muted-foreground">
        <p className="inline-flex items-center gap-1.5 font-medium text-foreground/80 mb-1.5"><Lightbulb className="size-3.5" aria-hidden />How pricing works</p>
        <ul className="space-y-1 list-disc list-inside text-xs">
          {summary && summary.base_vehicles > 0 && (
            <li>Your plan includes {summary.base_vehicles} trucks. Each additional truck: {money(summary.extra_vehicle_cents)}/truck/month.</li>
          )}
          <li>Every truck in your Vehicles list is billed — whether it came from your telematics provider or you added it yourself. Archive a truck to stop billing it. Archived trucks and trailers are never billed.</li>
          <li>AI usage (tokens) is included — no per-query fees on any plan.</li>
          <li>Invoices are generated at the end of each billing period, and mid-cycle vehicle changes are pro-rated automatically.</li>
        </ul>
      </Card>

      {/* Invoices — Stripe-issued bills.  Hidden when empty AND on
          stub provider so test installs don't show a dead section. */}
      {(invoices.length > 0 || summary?.provider === 'stripe') && (
        <Card className="mb-4">
          <div className="flex items-center justify-between mb-4">
            <SectionHeader>Invoices</SectionHeader>
            <span className="text-xs text-muted-foreground">Last 24</span>
          </div>
          <InvoicesTable items={invoices} />
        </Card>
      )}

      {/* Billing history — internal monthly usage snapshots, separate
          from the Stripe invoices above.  Useful even on the stub
          provider where invoices don't exist. */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <SectionHeader>Usage History</SectionHeader>
          <span className="text-xs text-muted-foreground">Last 12 periods</span>
        </div>
        <UsageTable items={usage} />
      </Card>

      <p className="text-xs text-muted-foreground mt-6 text-center">
        Questions?{' '}
        <a href="mailto:billing@4truck.us" className="underline hover:text-muted-foreground">
          billing@4truck.us
        </a>
      </p>
    </div>
  );
}
