import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import { usd } from './Accounts';
import AlertRoutingCard from '../components/AlertRoutingCard';
import type {
  AccountDetail, CompHistoryRow,
  SyncQuantityResult, RefreshVehiclesResult, BillingEmailResult,
  OrphanReport, OrphanPurgeResult,
} from '../types';

export default function AccountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const accountId = Number(id);
  const [data, setData] = useState<AccountDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showGrant, setShowGrant] = useState(false);
  const [showRenew, setShowRenew] = useState(false);

  const load = () => {
    if (!Number.isFinite(accountId)) {
      setError('Invalid account id');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    apiJSON<AccountDetail>(`/system/accounts/${accountId}`)
      .then(setData)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404) {
          setError('Account not found');
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [accountId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <div className="text-slate-500 py-12 text-center text-sm">Loading…</div>;
  if (error || !data) {
    return (
      <div>
        <Link to="/accounts" className="text-accent text-sm hover:underline">← Accounts</Link>
        <div className="mt-4 bg-danger/10 border border-danger/40 text-danger rounded px-3 py-2 text-sm">
          {error || 'No data'}
        </div>
      </div>
    );
  }

  const sub = data.subscription;
  const billing = data.billing;
  const isComped = !!sub?.is_comped;

  return (
    <div>
      <Link to="/accounts" className="text-accent text-sm hover:underline">← Accounts</Link>

      <header className="mt-3 mb-6">
        <h1 className="text-xl font-semibold text-slate-100">{data.account.name}</h1>
        <div className="text-xs text-slate-500 mt-0.5 flex flex-wrap gap-x-4 gap-y-1">
          <span>id: {data.account.id}</span>
          <span>slug: {data.account.slug}</span>
          <span>tz: {data.account.timezone}</span>
          {data.account.bot_username && <span>bot: @{data.account.bot_username}</span>}
          <span>{data.user_count} users</span>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Subscription card */}
        <Card title="Subscription">
          {!sub ? (
            <p className="text-sm text-slate-400">No subscription row.</p>
          ) : (
            <dl className="text-sm space-y-1.5">
              <Row label="Tier" value={sub.tier} />
              <Row label="Status" value={sub.status.replace('_', ' ')} />
              <Row label="Provider" value={sub.provider} />
              <Row label="Billing email" value={sub.billing_email || '—'} />
              <Row label="Current period"
                   value={sub.current_period_start || sub.current_period_end
                     ? `${(sub.current_period_start ?? '?').slice(0,10)} → ${(sub.current_period_end ?? '?').slice(0,10)}`
                     : '—'} />
              {sub.past_due_since && (
                <Row label="Past due since" value={sub.past_due_since.slice(0, 19).replace('T', ' ')}
                     accent="text-danger" />
              )}
              <Row label="Stripe customer" value={sub.provider_customer_id || '—'} mono />
              <Row label="Stripe sub" value={sub.provider_subscription_id || '—'} mono />
              <Row label="Base item" value={sub.provider_base_item_id || '—'} mono />
              <Row label="Extras item" value={sub.provider_extra_item_id || '—'} mono />
            </dl>
          )}
        </Card>

        {/* Live billing computation */}
        <Card title="Billing right now">
          {!billing ? (
            <p className="text-sm text-slate-400">Not available.</p>
          ) : (
            <dl className="text-sm space-y-1.5">
              <Row label="Active vehicles" value={String(billing.active_vehicles)} />
              <Row label="Inactive (3+ days)" value={String(billing.inactive_vehicles)} />
              <Row label="Included" value={String(billing.included)} />
              <Row label="Extras" value={String(billing.extras)} />
              <Row label="Base" value={usd(billing.base_cents)} />
              <Row label="Extras cost" value={usd(billing.extras_cents)} />
              <div className="border-t border-slate-800 pt-1.5 mt-1.5">
                <Row label="Subtotal" value={usd(billing.subtotal_cents)} accent="text-slate-100 font-semibold" />
              </div>
              {isComped && (
                <Row label="Comp covers" value={usd(billing.subtotal_cents)} accent="text-accent" />
              )}
            </dl>
          )}
        </Card>

        {/* Suspend / unsuspend + deletion-grace visibility.  Lives
            above the debug actions because it's the operator's
            biggest lever on a tenant. */}
        <AccountStateCard accountId={accountId} />

        {/* Manual triggers that mirror the scheduler jobs but run
            RIGHT NOW for debugging.  Each action shows its server
            response inline so the operator can confirm the change
            took effect without refreshing the page. */}
        <OperatorActionsCard
          accountId={accountId}
          currentEmail={sub?.billing_email ?? ''}
          onMutated={load}
        />

        {/* Server-local orphaned-file audit (report-only) */}
        <StorageOrphansCard accountId={accountId} />

        {/* Telemetry warehouse cascade (our processing, not a provider feed) */}
        <TelemetryCascadeCard accountId={accountId} />

        {/* Comp control + history */}
        <Card title="Complimentary access"
              actions={
                <div className="flex gap-2">
                  {isComped ? (
                    <>
                      <button
                        onClick={() => setShowRenew(true)}
                        className="text-xs px-3 py-1 bg-accent/15 text-accent border border-accent/40 rounded hover:bg-accent/25"
                      >Renew</button>
                      <RevokeButton accountId={accountId} onDone={load} />
                    </>
                  ) : (
                    <button
                      onClick={() => setShowGrant(true)}
                      className="text-xs px-3 py-1 bg-accent text-white rounded hover:bg-accent/90"
                    >Grant comp</button>
                  )}
                </div>
              }
        >
          {isComped && sub ? (
            <dl className="text-sm space-y-1.5 mb-4">
              <Row label="Expires" value={sub.comp_expires_at?.slice(0, 10) || '—'} accent="text-accent" />
              <Row label="Reason" value={sub.comp_reason || '—'} />
              <Row label="Granted at"
                   value={sub.comp_granted_at?.slice(0, 19).replace('T', ' ') || '—'} />
              <Row label="Granted by tg_id" value={String(sub.comp_granted_by ?? '—')} />
            </dl>
          ) : (
            <p className="text-sm text-slate-400 mb-4">Not comped.</p>
          )}
          <CompHistory rows={data.comp_history} />
        </Card>

        {/* Recent invoices */}
        <Card title="Recent invoices (Stripe-mirrored)">
          {data.invoices.length === 0 ? (
            <p className="text-sm text-slate-400">No invoices recorded.</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-slate-500 border-b border-slate-800">
                <tr>
                  <th className="text-left py-1.5">Date</th>
                  <th className="text-left py-1.5">Status</th>
                  <th className="text-right py-1.5">Amount</th>
                  <th className="text-right py-1.5">Link</th>
                </tr>
              </thead>
              <tbody>
                {data.invoices.map((inv) => (
                  <tr key={inv.id} className="border-b border-slate-800/50">
                    <td className="py-1.5 text-slate-300">{inv.created_at.slice(0, 10)}</td>
                    <td className="py-1.5"><InvoiceStatus status={inv.status} /></td>
                    <td className="py-1.5 text-right tabular-nums text-slate-200">
                      {usd(inv.amount_paid_cents || inv.amount_due_cents)}
                    </td>
                    <td className="py-1.5 text-right">
                      {inv.hosted_invoice_url ? (
                        <a href={inv.hosted_invoice_url} target="_blank" rel="noopener noreferrer"
                           className="text-accent hover:underline">view</a>
                      ) : <span className="text-slate-600">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {/* Alert routing — mode flip + persona group bindings */}
        <AlertRoutingCard accountId={accountId} />
      </div>

      {showGrant && (
        <CompForm
          mode="grant"
          accountId={accountId}
          onClose={() => setShowGrant(false)}
          onDone={() => { setShowGrant(false); load(); }}
        />
      )}
      {showRenew && (
        <CompForm
          mode="renew"
          accountId={accountId}
          currentExpiry={sub?.comp_expires_at ?? null}
          onClose={() => setShowRenew(false)}
          onDone={() => { setShowRenew(false); load(); }}
        />
      )}
    </div>
  );
}

// ── Pieces ─────────────────────────────────────────────────────

function Card({ title, actions, children }: {
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}

function Row({ label, value, accent, mono }: {
  label: string; value: string; accent?: string; mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500 text-xs">{label}</dt>
      <dd className={`text-right ${mono ? 'font-mono text-xs' : ''} ${accent ?? 'text-slate-200'}`}>
        {value}
      </dd>
    </div>
  );
}

function InvoiceStatus({ status }: { status: string }) {
  const cls = status === 'paid' ? 'text-ok' :
              status === 'open' ? 'text-warn' :
              (status === 'uncollectible' || status === 'void') ? 'text-danger' :
              'text-slate-500';
  return <span className={`${cls} text-xs`}>{status || '—'}</span>;
}

function CompHistory({ rows }: { rows: CompHistoryRow[] }) {
  if (rows.length === 0) {
    return <p className="text-xs text-slate-500">No history.</p>;
  }
  return (
    <details>
      <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-200">
        Audit log ({rows.length})
      </summary>
      <ul className="mt-2 space-y-1 text-xs">
        {rows.map((r) => (
          <li key={r.id} className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-400">
            <span className="text-slate-500 tabular-nums">{r.created_at.slice(0, 19).replace('T', ' ')}</span>
            <span className={
              r.action === 'granted' ? 'text-ok' :
              r.action === 'renewed' ? 'text-accent' :
              r.action === 'revoked' ? 'text-danger' :
              r.action === 'expired' ? 'text-slate-400' :
              'text-slate-500'
            }>{r.action}</span>
            {r.expires_at && <span>→ {r.expires_at.slice(0, 10)}</span>}
            {r.reason && <span className="italic">"{r.reason}"</span>}
            {r.actor_user_id != null && <span className="text-slate-600">by tg {r.actor_user_id}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function RevokeButton({ accountId, onDone }: { accountId: number; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const handle = async () => {
    const reason = window.prompt('Revoke reason (optional, for the audit log):') ?? '';
    if (reason === null) return;  // user cancelled
    if (!window.confirm('Revoke comp now?  The account will start billing normally.')) return;
    setBusy(true);
    try {
      await apiJSON('/billing/comp/revoke', {
        method: 'POST',
        body: { account_id: accountId, reason },
      });
      onDone();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Revoke failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      onClick={handle}
      disabled={busy}
      className="text-xs px-3 py-1 bg-danger/15 text-danger border border-danger/40 rounded hover:bg-danger/25 disabled:opacity-50"
    >
      {busy ? '…' : 'Revoke'}
    </button>
  );
}

function CompForm({ mode, accountId, currentExpiry, onClose, onDone }: {
  mode: 'grant' | 'renew';
  accountId: number;
  currentExpiry?: string | null;
  onClose: () => void;
  onDone: () => void;
}) {
  // Default to +90 days from today for a grant; +90 days from current
  // expiry for a renewal.  Operators can override to any future date.
  const defaultDate = (() => {
    const base = mode === 'renew' && currentExpiry ? new Date(currentExpiry) : new Date();
    base.setDate(base.getDate() + 90);
    return base.toISOString().slice(0, 10);
  })();
  const [date, setDate] = useState(defaultDate);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      // Backend expects ISO-8601 UTC datetime — append T23:59:59 so the
      // comp lasts through end-of-day in the operator's mental model.
      const expires_at = `${date}T23:59:59+00:00`;
      const path  = mode === 'grant' ? '/billing/comp/grant' : '/billing/comp/renew';
      const body  = mode === 'grant'
        ? { account_id: accountId, expires_at, reason }
        : { account_id: accountId, new_expires_at: expires_at, reason };
      await apiJSON(path, { method: 'POST', body });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <form
        onSubmit={submit}
        className="bg-slate-900 border border-slate-700 rounded-lg p-5 w-full max-w-md"
      >
        <h3 className="text-sm font-semibold text-slate-100 mb-4">
          {mode === 'grant' ? 'Grant complimentary access' : 'Renew complimentary access'}
        </h3>
        <label className="text-xs text-slate-400 block mb-1">Expires on (UTC)</label>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          required
          min={new Date().toISOString().slice(0, 10)}
          className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm w-full mb-3"
        />
        <label className="text-xs text-slate-400 block mb-1">Reason (audit log)</label>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          placeholder="VIP partner · 6-month pilot · etc."
          className="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm w-full mb-4"
        />
        {err && (
          <p className="text-xs text-danger mb-3 bg-danger/10 border border-danger/40 rounded px-3 py-2">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button type="button" onClick={onClose} disabled={busy}
                  className="text-xs px-3 py-1.5 text-slate-400 hover:text-slate-200">
            Cancel
          </button>
          <button type="submit" disabled={busy}
                  className="text-xs px-3 py-1.5 bg-accent text-white rounded hover:bg-accent/90 disabled:opacity-50">
            {busy ? '…' : (mode === 'grant' ? 'Grant' : 'Renew')}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── Storage: orphaned-file audit (report-only) ─────────────────

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

// The telemetry warehouse cascade (live → 5-min → hourly → daily).  Our own
// downsampling pipeline — provider-agnostic plumbing, not a Samsara feed —
// so its health lives operator-side here, not on the customer's integration
// card.  Self-loads on mount; freshness reflects ingest time per tier.
function TelemetryCascadeCard({ accountId }: { accountId: number }) {
  interface Tier {
    label: string;
    table: string;
    note: string;
    count: number;
    last_ingest_at: string | null;
  }
  const [tiers, setTiers] = useState<Tier[] | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiJSON<{ tiers: Tier[] }>(`/system/accounts/${accountId}/telemetry`)
      .then((d) => setTiers(d.tiers))
      .catch(() => setErr('Failed to load telemetry cascade'));
  }, [accountId]);

  return (
    <Card title="Telemetry pipeline">
      <p className="text-xs text-slate-500 mb-3">
        Our downsampling cascade (live → 5-min → hourly → daily). Provider-agnostic
        warehouse plumbing — our processing, not a provider feed.
      </p>
      {err && <p className="text-xs text-danger">{err}</p>}
      {!tiers && !err && <p className="text-sm text-slate-500">Loading…</p>}
      {tiers && (
        <dl className="text-sm space-y-2">
          {tiers.map((t) => (
            <div key={t.table} className="flex items-baseline justify-between gap-3">
              <dt className="min-w-0">
                <span className="text-slate-200">{t.label}</span>
                <span className="block text-[11px] text-slate-600">{t.note}</span>
              </dt>
              <dd className="text-right shrink-0">
                <span className="text-slate-300 tabular-nums">{t.count.toLocaleString()} rows</span>
                <span className="block text-[11px] text-slate-500">
                  {t.last_ingest_at ? t.last_ingest_at.slice(0, 16).replace('T', ' ') : '—'}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}

// On-demand (not auto-load): the scan walks the account's disk subtree, so
// the operator triggers it explicitly.  Report-only — never deletes, never
// touches the customer's external cloud.
function StorageOrphansCard({ accountId }: { accountId: number }) {
  const [report, setReport] = useState<OrphanReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [purging, setPurging] = useState(false);
  const [purgeMsg, setPurgeMsg] = useState('');

  const scan = async () => {
    setBusy(true);
    setErr('');
    try {
      setReport(await apiJSON<OrphanReport>(`/system/retention/orphans/${accountId}`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setBusy(false);
    }
  };

  const purge = async () => {
    if (!report || report.candidate_count === 0) return;
    if (!window.confirm(
      `Delete ${report.candidate_count} orphaned file(s) (${fmtBytes(report.candidate_bytes)}) `
      + `from this account's server-local storage?\n\n`
      + `It re-scans and removes only files still unreferenced. The customer's Google `
      + `Drive is never touched. This cannot be undone.`,
    )) return;
    setPurging(true);
    setPurgeMsg('');
    try {
      const r = await apiJSON<OrphanPurgeResult>(
        `/system/retention/orphans/${accountId}/purge?confirm=true`,
        { method: 'POST' },
      );
      setPurgeMsg(`Deleted ${r.deleted} file(s), freed ${fmtBytes(r.deleted_bytes)}.`);
      await scan(); // refresh the report so the count drops to 0
    } catch (e) {
      setPurgeMsg(`Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setPurging(false);
    }
  };

  return (
    <Card
      title="Storage · orphaned files"
      actions={
        <button
          onClick={scan}
          disabled={busy}
          className="text-xs px-3 py-1.5 bg-slate-800 border border-slate-700 rounded hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? 'Scanning…' : report ? 'Re-scan' : 'Scan for orphans'}
        </button>
      }
    >
      <p className="text-xs text-slate-500 mb-3">
        Report only — finds files on <span className="text-slate-400">our server's</span> disk that
        no record references (leaked blobs from deleted parents). Deletes nothing, and never touches
        the customer's Google Drive.
      </p>

      {err && (
        <p className="text-xs text-danger bg-danger/10 border border-danger/40 rounded px-2 py-1">{err}</p>
      )}
      {!report && !err && (
        <p className="text-sm text-slate-500">Run a scan to see candidates.</p>
      )}

      {report && (
        <>
          <dl className="text-sm space-y-1.5">
            <Row label="Files scanned" value={String(report.scanned_files)} />
            <Row label="Referenced by a record" value={String(report.referenced)} />
            <Row
              label={`Orphan candidates (older than ${report.grace_days}d)`}
              value={`${report.candidate_count} · ${fmtBytes(report.candidate_bytes)}`}
              accent={report.candidate_count ? 'text-warn' : 'text-ok'}
            />
          </dl>

          {report.sample.length > 0 && (
            <details className="mt-3">
              <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-200">
                Sample ({report.sample.length} of {report.candidate_count}, largest first)
              </summary>
              <ul className="mt-2 space-y-0.5 text-xs font-mono text-slate-500">
                {report.sample.map((p) => (
                  <li key={p} className="truncate" title={p}>{p}</li>
                ))}
              </ul>
            </details>
          )}

          {report.candidate_count > 0 && (
            <div className="mt-3 pt-3 border-t border-slate-800">
              <button
                onClick={purge}
                disabled={purging || busy}
                className="text-xs px-3 py-1.5 bg-danger/15 text-danger border border-danger/40 rounded hover:bg-danger/25 disabled:opacity-50"
              >
                {purging ? 'Deleting…' : `Delete ${report.candidate_count} orphaned file(s)`}
              </button>
              <p className="text-[11px] text-slate-600 mt-2">
                Review the sample first. Deletion re-scans and removes only server-local files
                still unreferenced; the customer's Google Drive is never touched.
              </p>
              {purgeMsg && (
                <p className="text-xs mt-2 text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
                  {purgeMsg}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

// ── Operator actions ───────────────────────────────────────────

function OperatorActionsCard({
  accountId, currentEmail, onMutated,
}: {
  accountId: number;
  currentEmail: string;
  onMutated: () => void;
}) {
  // Inline status messages per-action.  Kept local (not toast) so the
  // operator sees the response right where they clicked it — important
  // when the result is a structured dict like sync_billing_quantity's.
  const [syncStatus, setSyncStatus] = useState<string>('');
  const [syncBusy, setSyncBusy] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState<string>('');
  const [refreshBusy, setRefreshBusy] = useState(false);

  const doSyncQuantity = async () => {
    setSyncBusy(true);
    setSyncStatus('');
    try {
      const r = await apiJSON<SyncQuantityResult>(
        `/system/accounts/${accountId}/sync-quantity`, { method: 'POST' },
      );
      // skipped===null is the patched path (or noop if before===after).
      // Provider returns ``skipped: "noop"`` when qty already matched.
      if (r.skipped === 'noop') {
        setSyncStatus(`Already in sync (qty=${r.after ?? r.before ?? '?'}).`);
      } else if (r.skipped) {
        setSyncStatus(`Skipped: ${r.skipped}.`);
      } else {
        setSyncStatus(`Patched: ${r.before ?? '?'} → ${r.after ?? '?'}.`);
      }
    } catch (e) {
      setSyncStatus(`Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setSyncBusy(false);
    }
  };

  const doRefreshVehicles = async () => {
    setRefreshBusy(true);
    setRefreshStatus('');
    try {
      const r = await apiJSON<RefreshVehiclesResult>(
        `/system/accounts/${accountId}/refresh-vehicles`, { method: 'POST' },
      );
      setRefreshStatus(
        `Persisted ${r.persisted}; active ${r.active_vehicles}, inactive ${r.inactive_vehicles}.`,
      );
      // The billing card uses the freshly-recomputed numbers — refresh
      // the parent's view so the operator sees the new state.
      onMutated();
    } catch (e) {
      setRefreshStatus(`Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setRefreshBusy(false);
    }
  };

  return (
    <Card title="Operator actions">
      <p className="text-xs text-slate-500 mb-3">
        Manual triggers for jobs the scheduler runs automatically.  Use these for
        debugging or when a customer reports a discrepancy and you want the
        change to land before the next 60-second tick.
      </p>

      <div className="space-y-3 text-sm">
        {/* Force Stripe extras-qty sync */}
        <div>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-slate-200">Force Stripe extras sync</p>
              <p className="text-xs text-slate-500">
                Runs <code className="text-slate-400">sync_billing_quantity</code>;
                only PATCHes Stripe when the target qty changed.
              </p>
            </div>
            <button
              onClick={doSyncQuantity}
              disabled={syncBusy}
              className="text-xs px-3 py-1.5 bg-slate-800 border border-slate-700 rounded hover:bg-slate-700 disabled:opacity-50"
            >
              {syncBusy ? 'Running…' : 'Force sync'}
            </button>
          </div>
          {syncStatus && (
            <p className="text-xs mt-1.5 text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
              {syncStatus}
            </p>
          )}
        </div>

        {/* Force Samsara ingest */}
        <div className="border-t border-slate-800 pt-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-slate-200">Refresh vehicles from Samsara</p>
              <p className="text-xs text-slate-500">
                Re-pulls the fleet overview and re-runs the active-count math.
                Inline — typically 3–5 s.
              </p>
            </div>
            <button
              onClick={doRefreshVehicles}
              disabled={refreshBusy}
              className="text-xs px-3 py-1.5 bg-slate-800 border border-slate-700 rounded hover:bg-slate-700 disabled:opacity-50"
            >
              {refreshBusy ? 'Pulling…' : 'Refresh'}
            </button>
          </div>
          {refreshStatus && (
            <p className="text-xs mt-1.5 text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
              {refreshStatus}
            </p>
          )}
        </div>

        {/* Billing email override */}
        <div className="border-t border-slate-800 pt-3">
          <p className="text-slate-200 mb-1">Billing email (operator override)</p>
          <p className="text-xs text-slate-500 mb-2">
            Routes Stripe receipts + dunning.  The customer dashboard intentionally
            cannot edit this — operator action only.
          </p>
          <BillingEmailEditor
            accountId={accountId}
            currentEmail={currentEmail}
            onSaved={onMutated}
          />
        </div>
      </div>
    </Card>
  );
}

function BillingEmailEditor({
  accountId, currentEmail, onSaved,
}: { accountId: number; currentEmail: string; onSaved: () => void }) {
  const [value, setValue] = useState(currentEmail);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>('');
  // Track the most recently saved value separately so the "Save"
  // button can stay disabled when nothing changed since last save.
  const [lastSaved, setLastSaved] = useState(currentEmail);

  const save = async () => {
    const email = value.trim();
    if (!email.includes('@') || !email.includes('.')) {
      setStatus('Invalid email');
      return;
    }
    setBusy(true);
    setStatus('');
    try {
      const r = await apiJSON<BillingEmailResult>(
        `/system/accounts/${accountId}/billing-email`,
        { method: 'PATCH', body: { email } },
      );
      setLastSaved(email);
      setStatus(
        r.synced_to_provider
          ? 'Saved + pushed to Stripe.'
          : `Saved locally only (${r.reason ?? 'no Stripe customer yet'}).`,
      );
      onSaved();
    } catch (e) {
      setStatus(`Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setBusy(false);
    }
  };

  const dirty = value.trim() !== lastSaved.trim();
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          type="email"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="finance@example.com"
          disabled={busy}
          className="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm flex-1 font-mono"
          onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save(); }}
        />
        <button
          onClick={save}
          disabled={busy || !dirty}
          className="text-xs px-3 py-1.5 bg-accent text-white rounded hover:bg-accent/90 disabled:opacity-50"
        >
          {busy ? '…' : 'Save'}
        </button>
      </div>
      {status && (
        <p className="text-xs mt-1.5 text-slate-400 bg-slate-950 border border-slate-800 rounded px-2 py-1">
          {status}
        </p>
      )}
    </div>
  );
}

// Suspend / unsuspend + deletion-grace state.  Self-loading: fetches
// /system/accounts/:id/lifecycle on mount and after every action, so
// it never goes stale relative to the parent's billing-centric load().
function AccountStateCard({ accountId }: { accountId: number }) {
  interface Lifecycle {
    is_active: boolean;
    suspended_at: string | null;
    suspended_reason: string | null;
    suspended_by: number | null;
    deleted_at: string | null;
    purge_at: string | null;
  }
  const [lc, setLc] = useState<Lifecycle | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const load = () => {
    apiJSON<Lifecycle>(`/system/accounts/${accountId}/lifecycle`)
      .then(setLc)
      .catch(() => setErr('Failed to load lifecycle state'));
  };
  useEffect(() => { load(); }, [accountId]); // eslint-disable-line react-hooks/exhaustive-deps

  const suspend = async () => {
    const reason = prompt(
      'Suspend this account?\n\nEvery login is blocked and all active '
      + 'sessions are revoked immediately. Enter a reason (required, '
      + 'lands in the audit log + customer email):',
    );
    if (!reason || reason.trim().length < 3) return;
    setBusy(true);
    setErr('');
    try {
      await apiJSON(`/system/accounts/${accountId}/suspend`, {
        method: 'POST',
        body: { reason: reason.trim() },
      });
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Suspend failed');
    } finally {
      setBusy(false);
    }
  };

  const unsuspend = async () => {
    if (!confirm('Reactivate this account? Logins are re-enabled immediately.')) return;
    setBusy(true);
    setErr('');
    try {
      await apiJSON(`/system/accounts/${accountId}/unsuspend`, { method: 'POST' });
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Unsuspend failed');
    } finally {
      setBusy(false);
    }
  };

  const suspended = !!lc?.suspended_at;
  const deletionPending = !!lc?.deleted_at;

  return (
    <Card
      title="Account state"
      actions={
        lc && !deletionPending ? (
          suspended ? (
            <button
              onClick={unsuspend}
              disabled={busy}
              className="text-xs px-3 py-1 bg-ok/15 text-ok border border-ok/40 rounded hover:bg-ok/25 disabled:opacity-50"
            >
              {busy ? '…' : 'Unsuspend'}
            </button>
          ) : (
            <button
              onClick={suspend}
              disabled={busy}
              className="text-xs px-3 py-1 bg-danger/15 text-danger border border-danger/40 rounded hover:bg-danger/25 disabled:opacity-50"
            >
              {busy ? '…' : 'Suspend'}
            </button>
          )
        ) : undefined
      }
    >
      {!lc && !err && <p className="text-sm text-slate-500">Loading…</p>}
      {err && <p className="text-xs text-danger">{err}</p>}
      {lc && (
        <dl className="text-sm space-y-1.5">
          <Row
            label="Status"
            value={
              deletionPending ? 'deletion pending'
              : suspended ? 'suspended'
              : lc.is_active ? 'active' : 'inactive'
            }
            accent={
              deletionPending ? 'text-danger'
              : suspended ? 'text-warn'
              : 'text-ok'
            }
          />
          {suspended && (
            <>
              <Row label="Suspended at" value={lc.suspended_at?.slice(0, 19).replace('T', ' ') || '—'} />
              <Row label="Reason" value={lc.suspended_reason || '—'} />
              <Row label="By operator" value={String(lc.suspended_by ?? '—')} />
            </>
          )}
          {deletionPending && (
            <>
              <Row label="Deletion requested" value={lc.deleted_at?.slice(0, 10) || '—'} />
              <Row label="Hard purge on" value={lc.purge_at?.slice(0, 10) || '—'} accent="text-danger" />
            </>
          )}
        </dl>
      )}
      {deletionPending && (
        <p className="text-xs text-slate-500 mt-3">
          Owner-driven deletion is in its retention window (FMCSA
          recordkeeping). Only the owner can cancel it from their
          dashboard — unsuspend won't restore access.
        </p>
      )}
    </Card>
  );
}
