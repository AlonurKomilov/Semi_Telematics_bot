import { useEffect, useState, useCallback } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { RetentionContract, RetentionTarget } from '../types';

// Render a keep-window in the friendliest unit (years when it divides
// evenly, else days) so "1095" reads as "3 years".
function formatWindow(days: number): string {
  if (days % 365 === 0) {
    const y = days / 365;
    return `${y} year${y === 1 ? '' : 's'}`;
  }
  if (days % 30 === 0 && days >= 60) {
    return `${days / 30} months`;
  }
  return `${days} day${days === 1 ? '' : 's'}`;
}

const SCOPE_ORDER: RetentionTarget['scope'][] = ['tenant', 'platform'];
const SCOPE_LABEL: Record<RetentionTarget['scope'], string> = {
  tenant: 'Per-account (tenant)',
  platform: 'Global (platform)',
};

export default function RetentionPage() {
  const [data, setData] = useState<RetentionContract | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<RetentionContract>('/system/retention')
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

  const groups = SCOPE_ORDER.map((scope) => ({
    scope,
    targets: (data?.targets ?? []).filter((t) => t.scope === scope),
  })).filter((g) => g.targets.length > 0);

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Data retention</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          The live retention contract — how long each kind of data is kept before the
          nightly prune deletes it. Read-only: windows are code-owned (some are
          compliance numbers), so this is an observability view, not a control panel.
        </p>
      </header>

      {data && (
        <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 text-xs text-slate-400">
          <span className="text-slate-200 font-medium">{data.job.id}</span>
          {' · '}runs {data.job.schedule}
          {' · '}{data.job.scope}
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

      {groups.map((group) => (
        <section key={group.scope} className="mb-6">
          <p className="px-1 py-1 text-[10px] uppercase tracking-wider text-slate-600">
            {SCOPE_LABEL[group.scope]}
          </p>
          <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                  <th className="px-4 py-2 font-medium">Target</th>
                  <th className="px-4 py-2 font-medium">Keep window</th>
                  <th className="px-4 py-2 font-medium">Needed by</th>
                </tr>
              </thead>
              <tbody>
                {group.targets.map((t) => (
                  <tr key={t.key} className="border-b border-slate-800/60 last:border-0 align-top">
                    <td className="px-4 py-3">
                      <div className="font-mono text-slate-200">{t.key}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{t.label}</div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="text-slate-100 font-medium">{formatWindow(t.keep_days)}</span>
                      <span className="text-slate-600 text-xs ml-1">({t.keep_days}d)</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        {t.needs.map((n) => (
                          <div key={n.feature} className="flex items-baseline gap-2">
                            <span className="inline-block px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-medium">
                              {n.feature}
                            </span>
                            <span className="text-xs text-slate-500">
                              {n.reason}
                              <span className="text-slate-600"> · {formatWindow(n.keep_days)}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      <p className="text-xs text-slate-600 mt-2">
        Each target's window is the <span className="text-slate-400">maximum</span> across the
        features that declare a need (so a table read by several features is kept as long as the
        hungriest consumer requires). Prune runs only on our own storage — Postgres and
        server-local files; a customer's external cloud (their Google Drive) is never touched.
      </p>
    </div>
  );
}
