import { useEffect, useState, useCallback } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { SystemHealth, HealthComponent } from '../types';

// Friendly labels + ordering for the known components.  Unknown keys
// still render (future-proof) but after these.
const ORDER: { key: string; label: string }[] = [
  { key: 'database', label: 'Database' },
  { key: 'redis',    label: 'Redis' },
  { key: 'ingest',   label: 'Samsara ingest' },
  { key: 'accounts', label: 'Accounts' },
  { key: 'errors',   label: 'Error rate' },
];

function dot(status: string): string {
  return status === 'ok' ? 'bg-ok' : status === 'warn' ? 'bg-warn' : 'bg-danger';
}
function textColor(status: string): string {
  return status === 'ok' ? 'text-ok' : status === 'warn' ? 'text-warn' : 'text-danger';
}

export default function HealthPage() {
  const [data, setData] = useState<SystemHealth | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<SystemHealth>('/system/health')
      .then((d) => { setData(d); setLastChecked(new Date()); })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  }, []);

  // Auto-refresh every 30s so the page is a live status board.
  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const ordered = data
    ? [
        ...ORDER.filter((o) => o.key in data.components),
        ...Object.keys(data.components)
          .filter((k) => !ORDER.some((o) => o.key === k))
          .map((k) => ({ key: k, label: k })),
      ]
    : [];

  return (
    <div>
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">System health</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Live platform status — auto-refreshes every 30s.
            {lastChecked && <> Last checked {lastChecked.toLocaleTimeString()}.</>}
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${dot(data.overall)}`} />
            <span className={`text-sm font-semibold uppercase ${textColor(data.overall)}`}>
              {data.overall}
            </span>
          </div>
        )}
      </header>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {loading && !data && (
          <div className="text-slate-500 text-sm py-8 col-span-2 text-center">Checking…</div>
        )}
        {ordered.map(({ key, label }) => {
          const c: HealthComponent = data!.components[key];
          return (
            <div key={key} className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-slate-200">{label}</span>
                <span className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${dot(c.status)}`} />
                  <span className={`text-xs uppercase ${textColor(c.status)}`}>{c.status}</span>
                </span>
              </div>
              <p className="text-xs text-slate-500">{c.detail}</p>
            </div>
          );
        })}
      </div>

      <p className="text-xs text-slate-600 mt-4">
        Note: bot-daemon liveness isn't shown here — the API process can't see the bot
        process's in-memory registry.  "Accounts" reports configured per-account bots from
        the DB; "Samsara ingest" infers freshness from the newest vehicle signal.
      </p>
    </div>
  );
}
