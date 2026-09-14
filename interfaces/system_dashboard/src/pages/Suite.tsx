/** The test board — what the suite did, and who was holding it.
 *
 *  Not a green light. "The suite is red" was never the useful sentence
 *  on this platform: three sessions and a person write to one tree, so
 *  the suite is usually red about something and the question is always
 *  WHOSE and SINCE WHEN. Every run is kept, so a failing test carries a
 *  bracket — the last commit that passed it and the first that did not —
 *  and that bracket is what a reader can act on.
 *
 *  Two labels do most of the work here. A run with a `scope` asked for
 *  part of the suite, so its green says nothing about the rest; a run on
 *  a `dirty` tree was measuring uncommitted edits, so its red accuses
 *  nobody. A board that rendered those like a clean full run would be
 *  worse than no board.
 */
import { useCallback, useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';

interface SuiteFailure {
  id: number;
  nodeid: string;
  file: string;
  message: string;
  first_seen_run: number | null;
  first_seen_sha: string | null;
  first_seen_at: string | null;
  first_seen_actor: string | null;
  last_green_sha: string | null;
}

interface SuiteRun {
  id: number;
  started_at: string;
  finished_at: string;
  source: 'ci' | 'local';
  actor: string;
  git_sha: string;
  git_branch: string;
  dirty: boolean | number;
  scope: string;
  passed: number;
  failed: number;
  skipped: number;
  errors: number;
  duration_s: number | null;
  ok: boolean;
  partial: boolean;
  failures?: SuiteFailure[];
}

function dur(seconds: number | null): string {
  if (!seconds && seconds !== 0) return '—';
  if (seconds < 90) return `${seconds.toFixed(0)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function when(iso: string): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace('T', ' ');
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** The verdict, said in the strongest form the run actually supports. */
function Verdict({ run }: { run: SuiteRun }) {
  const failed = run.failed + run.errors;
  if (failed > 0) {
    return (
      <span className="text-danger font-medium">
        {failed} failed
      </span>
    );
  }
  if (run.partial) {
    return <span className="text-slate-300">passed (part)</span>;
  }
  return <span className="text-ok font-medium">green</span>;
}

export default function SuitePage() {
  const [runs, setRuns] = useState<SuiteRun[]>([]);
  const [open, setOpen] = useState<SuiteRun | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    apiJSON<{ items: SuiteRun[] }>('/system/suite/runs?limit=40')
      .then((d) => { setRuns(d.items); setErr(''); })
      .catch((e) => setErr(e instanceof ApiError && e.status === 401
        ? 'Session expired or no operator access.'
        : e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const openRun = (id: number) => {
    apiJSON<SuiteRun>(`/system/suite/runs/${id}`)
      .then(setOpen)
      .catch(() => { /* the row already says the counts; a detail that
                        will not load is not worth an error banner */ });
  };

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-slate-100">Test board</h1>
      <p className="text-sm text-slate-400 mt-1 max-w-3xl">
        Every pytest run that reported in — here and in CI. A run that
        asked for part of the suite says <em>part</em>; a run on a tree
        with uncommitted edits says <em>dirty</em>, because its red
        accuses nobody. Open a red run to see which test, and the two
        commits the break lies between.
      </p>

      {err && (
        <div className="mt-4 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      {!loading && !err && runs.length === 0 && (
        <div className="mt-6 border border-slate-800 rounded p-6 text-sm text-slate-400">
          <p className="text-slate-300 font-medium">No runs reported yet.</p>
          <p className="mt-2">
            A run reports when <code className="text-slate-300">SUITE_REPORT_URL</code>
            {' '}and <code className="text-slate-300">SUITE_REPORT_TOKEN</code> are set
            in the environment pytest runs in. Unset, the reporter collects
            nothing and sends nothing — which is why this page is empty
            rather than wrong.
          </p>
        </div>
      )}

      {runs.length > 0 && (
        <table className="mt-5 w-full text-sm">
          <thead className="text-slate-400 border-b border-slate-800">
            <tr>
              <th className="text-left px-3 py-2 font-normal">When</th>
              <th className="text-left px-3 py-2 font-normal">Verdict</th>
              <th className="text-left px-3 py-2 font-normal">Who</th>
              <th className="text-left px-3 py-2 font-normal">Commit</th>
              <th className="text-left px-3 py-2 font-normal">Scope</th>
              <th className="text-right px-3 py-2 font-normal">Passed</th>
              <th className="text-right px-3 py-2 font-normal">Took</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.id}
                onClick={() => (r.failed + r.errors > 0 ? openRun(r.id) : undefined)}
                className={`border-b border-slate-900 ${
                  r.failed + r.errors > 0 ? 'cursor-pointer hover:bg-slate-900/60' : ''
                }`}
              >
                <td className="px-3 py-2 text-slate-300">{when(r.finished_at)}</td>
                <td className="px-3 py-2"><Verdict run={r} /></td>
                <td className="px-3 py-2 text-slate-400">
                  {r.actor || '—'}
                  <span className="ml-1.5 text-[10px] uppercase tracking-wide text-slate-500">
                    {r.source}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-400">
                  {r.git_sha || '—'}
                  {r.dirty ? (
                    <span
                      title="uncommitted edits in the tree when this ran — a red run here accuses nobody"
                      className="ml-1.5 text-warn"
                    >
                      dirty
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-slate-500 text-xs max-w-[22rem] truncate"
                    title={r.scope || 'the whole suite'}>
                  {r.scope || <span className="text-slate-400">whole suite</span>}
                </td>
                <td className="px-3 py-2 text-right text-slate-400 tabular-nums">{r.passed}</td>
                <td className="px-3 py-2 text-right text-slate-500 tabular-nums">{dur(r.duration_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {open && (
        <div className="fixed inset-0 bg-black/60 flex items-start justify-center p-8 z-50"
             onClick={() => setOpen(null)}>
          <div className="bg-slate-950 border border-slate-800 rounded-lg max-w-4xl w-full max-h-[80vh] overflow-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-slate-800 flex items-start justify-between">
              <div>
                <h2 className="text-slate-100 font-medium">
                  Run {open.id} — {open.failed + open.errors} failed
                </h2>
                <p className="text-xs text-slate-500 mt-1">
                  {open.actor || 'unknown'} · {open.git_sha || '—'}
                  {open.dirty ? ' · dirty tree' : ''}
                  {open.scope ? ` · ${open.scope}` : ' · whole suite'}
                </p>
              </div>
              <button onClick={() => setOpen(null)}
                      className="text-slate-500 hover:text-slate-300 px-2">×</button>
            </div>
            <div className="p-5 space-y-4">
              {(open.failures ?? []).map((f) => (
                <div key={f.id} className="border border-slate-800 rounded p-3">
                  <div className="font-mono text-xs text-slate-200 break-all">{f.nodeid}</div>
                  <div className="text-xs text-danger mt-1.5">{f.message || '—'}</div>
                  {/* The bracket: what a reader can actually act on. */}
                  <div className="text-xs text-slate-500 mt-2">
                    {f.last_green_sha ? (
                      <>
                        went red between{' '}
                        <span className="font-mono text-slate-400">{f.last_green_sha}</span>
                        {' '}and{' '}
                        <span className="font-mono text-slate-400">{f.first_seen_sha}</span>
                      </>
                    ) : f.first_seen_at ? (
                      <>
                        red since {when(f.first_seen_at)}
                        {f.first_seen_actor ? ` (${f.first_seen_actor}'s run)` : ''}
                        {' '}— no green run before it to narrow the range
                      </>
                    ) : (
                      'first run on this board — nothing before it to compare'
                    )}
                  </div>
                </div>
              ))}
              {(open.failures ?? []).length === 0 && (
                <p className="text-sm text-slate-400">
                  No failure rows stored for this run.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
