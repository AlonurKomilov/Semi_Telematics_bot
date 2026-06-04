import { useEffect, useState } from 'react';
import { apiJSON } from '../api/client';
import type { MetricsSnapshot } from '../types';

// Operator landing card — pulls cumulative billing counters from the
// in-process Prometheus registry via /system/metrics-snapshot.  Numbers
// shown are absolute totals since the API process started, NOT a rate;
// for rates use the actual Grafana dashboards.  This card is a
// "everything green right now?" check, not a replacement for time-series
// observability.

function sum(map: Record<string, number>): number {
  return Object.values(map).reduce((a, b) => a + b, 0);
}

// Filter the webhook events map by ``result=`` label.  Keys look like
// ``"checkout.session.completed|processed"``; we split on ``|`` and
// match the second segment.
function webhookByResult(map: Record<string, number>, result: string): number {
  return Object.entries(map)
    .filter(([k]) => k.split('|')[1] === result)
    .reduce((a, [, v]) => a + v, 0);
}

// Same idea for sync_quantity which is single-label (``result`` only).
function syncByResult(map: Record<string, number>, result: string): number {
  return map[result] ?? 0;
}

function compSweepByAction(map: Record<string, number>, action: string): number {
  return map[action] ?? 0;
}

export default function MetricsCard() {
  const [data, setData] = useState<MetricsSnapshot | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiJSON<MetricsSnapshot>('/system/metrics-snapshot')
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'failed'));
  }, []);

  if (err) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 text-xs text-slate-500">
        Metrics unavailable: {err}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 text-xs text-slate-500">
        Loading metrics…
      </div>
    );
  }

  const webhookErrors  = webhookByResult(data.webhook_events, 'error');
  const webhookInvalid = webhookByResult(data.webhook_events, 'invalid_signature');
  const webhookDup     = webhookByResult(data.webhook_events, 'duplicate');
  const webhookOk      = webhookByResult(data.webhook_events, 'processed');
  const syncPatched    = syncByResult(data.sync_quantity, 'patched');
  const syncStripeErr  = syncByResult(data.sync_quantity, 'stripe_error');
  const notificationsTotal = sum(data.notifications);
  const compExpired    = compSweepByAction(data.comp_sweep, 'expired');
  const compReminded   = compSweepByAction(data.comp_sweep, 'reminder_sent');

  // Anything red?  This is the "wake-up signal" for the operator —
  // most of the time everything is zero and the card is just info.
  const hasAlerts = webhookErrors > 0 || webhookInvalid > 0 || syncStripeErr > 0;

  return (
    <div className={`bg-slate-900 border rounded-lg p-3 mb-3 ${
      hasAlerts ? 'border-danger/60' : 'border-slate-800'
    }`}>
      <header className="flex items-center justify-between mb-2">
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
          Billing pipeline · since process start
        </h2>
        {hasAlerts && (
          <span className="text-xs text-danger">⚠ Investigate</span>
        )}
      </header>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 text-xs">
        <Metric label="Webhook OK"      value={webhookOk}      muted />
        <Metric label="Webhook dup"     value={webhookDup}     muted />
        <Metric label="Webhook errors"  value={webhookErrors}  alert={webhookErrors > 0} />
        <Metric label="Invalid sig"     value={webhookInvalid} alert={webhookInvalid > 0} />
        <Metric label="Stripe synced"   value={syncPatched}    muted />
        <Metric label="Sync errors"     value={syncStripeErr}  alert={syncStripeErr > 0} />
        <Metric label="TG notifications" value={notificationsTotal} muted />
        <Metric label="Comps expired"   value={compExpired}    muted />
        <Metric label="Comps reminded"  value={compReminded}   muted />
      </div>
    </div>
  );
}

function Metric({ label, value, alert, muted }: {
  label: string; value: number; alert?: boolean; muted?: boolean;
}) {
  const cls = alert ? 'text-danger' : muted ? 'text-slate-400' : 'text-slate-100';
  return (
    <div>
      <p className="text-[10px] text-slate-500 uppercase tracking-wider truncate">{label}</p>
      <p className={`text-sm font-semibold tabular-nums ${cls}`}>{value.toLocaleString()}</p>
    </div>
  );
}
