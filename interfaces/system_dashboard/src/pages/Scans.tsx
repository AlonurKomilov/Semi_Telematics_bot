import { useEffect, useState, useCallback, useRef } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { ScansOverview, RescanJob } from '../types';

/** Compact timestamp for the recent-activity table — same format the
 *  Retention page uses for "Last run" so columns line up visually. */
function fmtTs(iso: string): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—';
}

function fmtBytes(n: number | null | undefined): string {
  if (!n || n <= 0) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function VerdictPill({ verdict }: { verdict: string }) {
  // Per dashboard rules the system console uses a Slate-scale palette
  // with single-accent semantics — no full design-system tone tokens.
  // Keep the per-verdict bg/text combos hand-rolled (matching the
  // Retention page convention) but use the same Slate ramps so the
  // page stays in the console's visual language.
  const cls =
    verdict === 'clean'
      ? 'bg-emerald-900/40 text-emerald-300 border border-emerald-800/60'
      : verdict === 'infected'
      ? 'bg-rose-900/40 text-rose-300 border border-rose-800/60'
      : 'bg-amber-900/40 text-amber-300 border border-amber-800/60';
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${cls}`}>
      {verdict}
    </span>
  );
}

export default function ScansPage() {
  const [data, setData] = useState<ScansOverview | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  // Action-button busy flags — the buttons disable themselves so the
  // operator can't double-click into an inconsistent state.
  const [busy, setBusy] = useState<string | null>(null);
  // Polling timer ref so we can clean it up on unmount + restart when
  // an active job appears / disappears.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<ScansOverview>('/system/scans')
      .then(setData)
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-poll while a rescan is running so the progress card advances
  // without the operator hitting refresh.  3s feels responsive without
  // hammering the /scans endpoint.  Stops as soon as active_job goes
  // null (terminal state reached).
  useEffect(() => {
    const isRunning = data?.active_job?.status === 'running';
    if (isRunning && pollRef.current === null) {
      pollRef.current = setInterval(() => load(), 3000);
    } else if (!isRunning && pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [data?.active_job?.status, load]);

  // Action handlers — each posts to its endpoint, then reloads the
  // page to show the new state.  busy flag prevents double-clicks.
  const runRescan = async () => {
    setBusy('rescan');
    setErr('');
    try {
      await apiJSON('/system/scans/rescan', { method: 'POST' });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to start rescan');
    } finally { setBusy(null); }
  };
  const cancelRescan = async (jobId: number) => {
    setBusy(`cancel-${jobId}`);
    setErr('');
    try {
      await apiJSON(`/system/scans/rescan/${jobId}/cancel`, { method: 'POST' });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to cancel');
    } finally { setBusy(null); }
  };
  const restoreArticle = async (articleId: number) => {
    setBusy(`restore-${articleId}`);
    setErr('');
    try {
      await apiJSON(`/system/scans/quarantine/${articleId}/restore`, { method: 'POST' });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to restore');
    } finally { setBusy(null); }
  };
  const deleteArticle = async (articleId: number) => {
    if (!confirm('Delete this quarantined article and its stored file?  This cannot be undone.')) return;
    setBusy(`delete-${articleId}`);
    setErr('');
    try {
      await apiJSON(`/system/scans/quarantine/${articleId}`, { method: 'DELETE' });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to delete');
    } finally { setBusy(null); }
  };

  const h = data?.health;
  const s = data?.stats;
  const active = data?.active_job;
  const quarantine = data?.quarantine ?? [];
  const recentJobs = data?.recent_jobs ?? [];

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">File scans</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Per-upload virus scan via ClamAV — applies to KB attachments
          and the driver application intake. Read-only observability:
          scanner behaviour is tuned via <span className="font-mono">CLAMAV_*</span> env vars,
          not from this surface.
        </p>
      </header>

      {/* Status banner — compact one-liner when enabled, full-card
          callout when disabled (since the disabled state is the
          "needs action" case operators land on). */}
      {h?.enabled && (
        <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 text-xs text-slate-400 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-slate-200 font-medium">scanner</span>
          <span className="text-emerald-400">● enabled</span>
          <span>· clamd @ <span className="font-mono">{h.host}:{h.port}</span></span>
          <span>· max <span className="text-slate-200">{h.max_concurrent}</span> concurrent</span>
          <span>· rate <span className="text-slate-200">{h.rate_per_sec}/s</span></span>
          <span>· timeout <span className="text-slate-200">{h.timeout_sec}s</span></span>
          <span>
            · scanning{' '}
            <span className="text-slate-200">
              {h.scan_types === 'all' ? 'all whitelisted types' : (h.scan_types as string[]).join(', ')}
            </span>
          </span>
        </div>
      )}

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      {loading && !data && (
        <div className="text-slate-500 text-sm py-8 text-center">Loading…</div>
      )}

      {/* Disabled-state callout — when CLAMAV_HOST is unset everything
          downstream is zeros / no-ops, so we hide the empty stats panels
          and show a single focused "what to do" card.  Three setup
          steps, the actual env-var names operators copy, and the
          security implications of leaving it off. */}
      {data && h && !h.enabled && (
        <section className="mb-6">
          <div className="bg-amber-900/20 border border-amber-800/60 rounded-lg p-5">
            <div className="flex items-start gap-3 mb-3">
              <span className="text-amber-400 text-lg leading-none">○</span>
              <div className="flex-1">
                <h2 className="text-base font-semibold text-amber-200">
                  Scanner is off — uploads are stored without virus check
                </h2>
                <p className="text-xs text-amber-300/80 mt-1">
                  Every other defence layer (magic-byte verification, rate
                  limits, role gates) is still active. ClamAV adds the
                  "real-format with malicious payload" check on top —
                  worth enabling for production accounts that allow
                  cross-account public KB articles.
                </p>
              </div>
            </div>

            <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-3 text-xs">
              <div className="text-slate-400 mb-2 font-medium">To enable scanning:</div>
              <ol className="list-decimal list-inside space-y-1.5 text-slate-300">
                <li>
                  Run a <span className="font-mono">clamd</span> daemon
                  reachable from the API (Docker:{' '}
                  <span className="font-mono">clamav/clamav:stable</span>).
                </li>
                <li>
                  Set <span className="font-mono">CLAMAV_HOST=&lt;host&gt;</span>{' '}
                  (and <span className="font-mono">CLAMAV_PORT=3310</span> if not default).
                </li>
                <li>Restart the API.  The scanner picks up the env on next request — no code redeploy needed.</li>
              </ol>
            </div>

            <p className="text-[11px] text-slate-500 mt-3">
              Optional knobs: <span className="font-mono">CLAMAV_MAX_CONCURRENT</span>{' '}
              (default 4), <span className="font-mono">CLAMAV_RATE_PER_SEC</span>{' '}
              (10), <span className="font-mono">CLAMAV_TIMEOUT_SEC</span> (10),{' '}
              <span className="font-mono">CLAMAV_SCAN_TYPES</span>{' '}
              (comma list, default = all whitelisted types).
            </p>
          </div>
        </section>
      )}

      {/* Aggregate panel — the 7-day rollup card.  Hidden when the
          scanner is disabled (all stats are always 0 in that state,
          and the disabled-state card above explains why). */}
      {s && h?.enabled && (
        <section className="mb-6">
          <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
            Last {s.window_days} days
          </p>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Total scans</div>
              <div className="text-lg font-semibold text-slate-100">{s.total.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Clean</div>
              <div className="text-lg font-semibold text-emerald-400">{s.clean.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Blocked (infected)</div>
              <div className="text-lg font-semibold text-rose-400">
                {s.infected.toLocaleString()}
                {s.total > 0 && (
                  <span className="text-xs text-slate-500 ml-1 font-normal">
                    ({((s.infected / s.total) * 100).toFixed(2)}%)
                  </span>
                )}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Scan errors</div>
              <div className="text-lg font-semibold text-amber-400">
                {s.unavailable.toLocaleString()}
              </div>
            </div>
            <div className="col-span-2 md:col-span-2 border-t border-slate-800 pt-3 md:border-t-0 md:pt-0 md:border-l md:pl-4">
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Latency (ms)</div>
              <div className="text-sm text-slate-300">
                p50 <span className="text-slate-100 font-medium">{s.latency_p50}</span>
                <span className="text-slate-600 mx-2">·</span>
                p95 <span className="text-slate-100 font-medium">{s.latency_p95}</span>
                <span className="text-slate-600 mx-2">·</span>
                max <span className="text-slate-100 font-medium">{s.latency_max}</span>
              </div>
            </div>
            <div className="col-span-2 md:col-span-2 border-t border-slate-800 pt-3 md:border-t-0 md:pt-0 md:border-l md:pl-4">
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Top signatures</div>
              {s.top_signatures.length === 0 ? (
                <div className="text-xs text-slate-600 italic">none in window</div>
              ) : (
                <div className="space-y-0.5 text-xs">
                  {s.top_signatures.map((sig) => (
                    <div key={sig.signature} className="flex items-baseline gap-2">
                      <span className="font-mono text-slate-200">{sig.signature}</span>
                      <span className="text-slate-500">× {sig.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Recent activity — last 50 events.  Same gate as the stats
          panel — meaningless when scanner is off. */}
      {data && h?.enabled && (
        <section className="mb-6">
          <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
            Recent activity ({data.recent.length})
          </p>
          <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-4 py-2 font-medium">Surface</th>
                  <th className="px-4 py-2 font-medium">Account</th>
                  <th className="px-4 py-2 font-medium">Size</th>
                  <th className="px-4 py-2 font-medium">Type</th>
                  <th className="px-4 py-2 font-medium">Verdict</th>
                  <th className="px-4 py-2 font-medium">Signature</th>
                  <th className="px-4 py-2 font-medium">Latency</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-6 text-center text-slate-600 text-xs italic">
                      No scans yet — every KB / driver-app upload will appear here.
                    </td>
                  </tr>
                )}
                {data.recent.map((ev) => (
                  <tr key={ev.id} className="border-b border-slate-800/60 last:border-0 align-top">
                    <td className="px-4 py-2 whitespace-nowrap font-mono text-xs text-slate-300">
                      {fmtTs(ev.created_at)}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-400">{ev.surface}</td>
                    <td className="px-4 py-2 text-xs text-slate-400 font-mono">
                      {ev.account_id ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-400 whitespace-nowrap">
                      {fmtBytes(ev.file_size)}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500 font-mono">
                      {ev.content_type ?? '—'}
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      <VerdictPill verdict={ev.verdict} />
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-300 font-mono">
                      {ev.signature ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-400 whitespace-nowrap">
                      {ev.latency_ms != null ? `${ev.latency_ms} ms` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Ad-hoc rescan controls + live progress ───────────────
          Hidden when scanner disabled — rescan would be a no-op +
          the disabled-state card above already explains the path. */}
      {data && h?.enabled && (
        <section className="mb-6">
          <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
            Batch rescan
          </p>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            {/* Active job — live progress card */}
            {active ? (
              <div>
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <div className="text-sm text-slate-200 font-medium">
                      Rescan in progress · job #{active.id}
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      Started {fmtTs(active.started_at)}
                      {active.started_by && (
                        <span className="ml-1">by user #{active.started_by}</span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => cancelRescan(active.id)}
                    disabled={busy === `cancel-${active.id}`}
                    className="px-3 py-1.5 rounded bg-rose-900/40 border border-rose-800/60 text-rose-300 text-xs font-medium hover:bg-rose-900/60 disabled:opacity-50"
                  >
                    {busy === `cancel-${active.id}` ? 'Cancelling…' : 'Cancel rescan'}
                  </button>
                </div>
                <ProgressBar
                  scanned={active.scanned_files}
                  total={active.total_files}
                />
                <div className="grid grid-cols-4 gap-3 mt-3 text-xs">
                  <Stat label="Scanned"  value={active.scanned_files}  total={active.total_files} />
                  <Stat label="Clean"    value={active.clean_count}    color="text-emerald-400" />
                  <Stat label="Infected" value={active.infected_count} color="text-rose-400" />
                  <Stat label="Errors"   value={active.error_count}    color="text-amber-400" />
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-slate-200 font-medium">
                    Re-check every stored KB attachment
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    Catches files uploaded before today's signature DB update.  Infected hits are quarantined automatically — operator decides restore vs delete below.
                  </div>
                </div>
                <button
                  onClick={runRescan}
                  disabled={busy === 'rescan' || !h?.enabled}
                  title={!h?.enabled ? 'Enable CLAMAV_HOST first' : ''}
                  className="px-4 py-2 rounded bg-emerald-900/40 border border-emerald-800/60 text-emerald-300 text-xs font-medium hover:bg-emerald-900/60 disabled:opacity-50"
                >
                  {busy === 'rescan' ? 'Starting…' : 'Run rescan'}
                </button>
              </div>
            )}
          </div>

          {/* Recent jobs history table (last 10 finished jobs) */}
          {recentJobs.length > 0 && (
            <div className="mt-3 bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                    <th className="px-4 py-2 font-medium">Job</th>
                    <th className="px-4 py-2 font-medium">Started</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Scanned</th>
                    <th className="px-4 py-2 font-medium">Clean</th>
                    <th className="px-4 py-2 font-medium">Infected</th>
                    <th className="px-4 py-2 font-medium">Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {recentJobs.map((j) => (
                    <tr key={j.id} className="border-b border-slate-800/60 last:border-0">
                      <td className="px-4 py-2 font-mono text-xs text-slate-300">#{j.id}</td>
                      <td className="px-4 py-2 font-mono text-xs text-slate-300">{fmtTs(j.started_at)}</td>
                      <td className="px-4 py-2"><JobStatusPill status={j.status} /></td>
                      <td className="px-4 py-2 text-xs text-slate-300">
                        {j.scanned_files} / {j.total_files}
                      </td>
                      <td className="px-4 py-2 text-xs text-emerald-400">{j.clean_count}</td>
                      <td className="px-4 py-2 text-xs text-rose-400">{j.infected_count}</td>
                      <td className="px-4 py-2 text-xs text-amber-400">{j.error_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* ── Quarantine list ─────────────────────────────────────── */}
      {data && quarantine.length > 0 && (
        <section className="mb-6">
          <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
            Quarantined articles ({quarantine.length})
          </p>
          <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                  <th className="px-4 py-2 font-medium">Article</th>
                  <th className="px-4 py-2 font-medium">Account</th>
                  <th className="px-4 py-2 font-medium">Quarantined</th>
                  <th className="px-4 py-2 font-medium">Reason</th>
                  <th className="px-4 py-2 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {quarantine.map((q) => (
                  <tr key={q.id} className="border-b border-slate-800/60 last:border-0 align-top">
                    <td className="px-4 py-2">
                      <div className="text-slate-200">{q.title}</div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {q.category}
                        {q.creator_name && <span> · by {q.creator_name}</span>}
                      </div>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-400">{q.account_id}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-300 whitespace-nowrap">
                      {fmtTs(q.quarantined_at)}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-400 font-mono">
                      {q.quarantined_reason ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      <button
                        onClick={() => restoreArticle(q.id)}
                        disabled={busy === `restore-${q.id}`}
                        className="px-2 py-1 rounded text-xs text-slate-300 hover:text-emerald-300 border border-slate-700 hover:border-emerald-800/60 disabled:opacity-50 mr-2"
                      >
                        {busy === `restore-${q.id}` ? 'Restoring…' : 'Restore'}
                      </button>
                      <button
                        onClick={() => deleteArticle(q.id)}
                        disabled={busy === `delete-${q.id}`}
                        className="px-2 py-1 rounded text-xs text-rose-300 hover:text-rose-200 border border-rose-900/60 hover:bg-rose-900/40 disabled:opacity-50"
                      >
                        {busy === `delete-${q.id}` ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Footer note — show retention info only when there's
          activity to retain.  When scanner is disabled, the disabled-
          state card above already covers the env-var contract. */}
      {h?.enabled && (
        <p className="text-xs text-slate-600 mt-2">
          Scan events retained for <span className="text-slate-400">90 days</span> via the{' '}
          <span className="text-slate-400">platform.scan_log</span> retention target;
          old rows pruned by the nightly <span className="font-mono">data_retention</span> job.
        </p>
      )}
    </div>
  );
}

// ── Small presentational helpers (scoped to this page) ──────────

function ProgressBar({ scanned, total }: { scanned: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs text-slate-400 mb-1">
        <span>{pct}%</span>
        <span>{scanned} of {total} files</span>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-emerald-500/70 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Stat({
  label, value, color, total,
}: { label: string; value: number; color?: string; total?: number }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-600">{label}</div>
      <div className={`text-sm font-medium ${color ?? 'text-slate-200'}`}>
        {value.toLocaleString()}
        {total != null && <span className="text-slate-600 ml-1">/ {total.toLocaleString()}</span>}
      </div>
    </div>
  );
}

function JobStatusPill({ status }: { status: RescanJob['status'] }) {
  const cls =
    status === 'completed' ? 'bg-emerald-900/40 text-emerald-300 border-emerald-800/60' :
    status === 'cancelled' ? 'bg-slate-800 text-slate-400 border-slate-700' :
    status === 'failed'    ? 'bg-rose-900/40 text-rose-300 border-rose-800/60' :
                              'bg-amber-900/40 text-amber-300 border-amber-800/60';
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium border ${cls}`}>
      {status}
    </span>
  );
}
