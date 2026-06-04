import { useEffect, useState, Fragment } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { ErrorRow } from '../types';

// Error log viewer — recent rows from the error_log table (scheduler /
// bot / API / system_bot exceptions).  Lets the operator triage
// without SSHing into the box.  Traceback is collapsed by default.

const SOURCES = ['', 'api', 'bot', 'system_bot', 'scheduler', 'task', 'startup'];

function sourceColor(s: string): string {
  switch (s) {
    case 'api':        return 'text-accent';
    case 'bot':        return 'text-warn';
    case 'system_bot': return 'text-warn';
    case 'scheduler':  return 'text-ok';
    default:           return 'text-slate-400';
  }
}

export default function ErrorsPage() {
  const [rows, setRows] = useState<ErrorRow[]>([]);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '100' });
    if (source) qs.set('source', source);
    apiJSON<{ items: ErrorRow[] }>(`/system/errors?${qs}`)
      .then((r) => setRows(r.items))
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Error log</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Recent exceptions captured by the built-in error reporter, newest first.
          Click a row to expand its traceback.
        </p>
      </header>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 flex flex-wrap gap-2 items-center">
        <label className="text-xs text-slate-400">Source:</label>
        <select value={source} onChange={(e) => setSource(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          {SOURCES.map((s) => (
            <option key={s || 'all'} value={s}>{s || 'All sources'}</option>
          ))}
        </select>
        <button onClick={load}
                className="bg-accent text-white text-xs px-3 py-1.5 rounded hover:bg-accent/90 ml-auto">
          Refresh
        </button>
      </div>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left px-3 py-2">When</th>
              <th className="text-left px-3 py-2">Source</th>
              <th className="text-left px-3 py-2">Type</th>
              <th className="text-left px-3 py-2">Message</th>
              <th className="text-left px-3 py-2">Acct</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={5} className="text-center text-slate-500 py-8">Loading…</td></tr>}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={5} className="text-center text-slate-500 py-8">No errors logged 🎉</td></tr>
            )}
            {rows.map((e) => (
              <Fragment key={e.id}>
                <tr
                  onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                  className="border-b border-slate-800/50 hover:bg-slate-800/50 cursor-pointer"
                >
                  <td className="px-3 py-2 text-slate-400 tabular-nums whitespace-nowrap">
                    {e.created_at.slice(0, 19).replace('T', ' ')}
                  </td>
                  <td className={`px-3 py-2 text-xs ${sourceColor(e.source)}`}>
                    {e.source}{e.job_name ? `·${e.job_name}` : ''}
                  </td>
                  <td className="px-3 py-2 text-slate-300 font-mono text-xs">{e.error_type}</td>
                  <td className="px-3 py-2 text-slate-400 max-w-md truncate" title={e.error_msg}>
                    {e.error_msg}
                  </td>
                  <td className="px-3 py-2 text-slate-500 text-xs tabular-nums">{e.account_id ?? '—'}</td>
                </tr>
                {expanded === e.id && (
                  <tr className="border-b border-slate-800/50 bg-slate-950">
                    <td colSpan={5} className="px-3 py-2">
                      <p className="text-xs text-slate-300 mb-1">{e.error_msg}</p>
                      {e.traceback ? (
                        <pre className="text-[11px] text-slate-500 whitespace-pre-wrap overflow-x-auto max-h-80 overflow-y-auto bg-black/40 rounded p-2">
                          {e.traceback}
                        </pre>
                      ) : (
                        <p className="text-xs text-slate-600 italic">No traceback captured.</p>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-3">{rows.length} row{rows.length === 1 ? '' : 's'} · server-side limit 100</p>
    </div>
  );
}
