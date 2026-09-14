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

interface SuitePackage {
  package: string;
  passed: number;
  failed: number;
  skipped: number;
  run_id: number;
  finished_at: string;
  actor: string;
  source: 'ci' | 'local';
  git_sha: string;
  dirty: boolean | number;
  scope: string;
}

/** features/loads → Features · loads.  The layer is the grouping a
 *  reader already has in their head; the tail is the name they use. */
function split(pkg: string): { layer: string; name: string } {
  const parts = pkg.split('/');
  if (parts.length === 1) return { layer: 'Repo-wide', name: parts[0] };
  const LAYER: Record<string, string> = {
    features: 'Features', capabilities: 'Capabilities', system: 'System services',
    adapters: 'Adapters', interfaces: 'Interfaces', infra: 'Infra',
  };
  return { layer: LAYER[parts[0]] ?? parts[0], name: parts.slice(1).join('/') };
}

const LAYER_ORDER = ['Features', 'System services', 'Capabilities',
                     'Adapters', 'Interfaces', 'Infra', 'Repo-wide'];

/** One feature or service, answering from the last run that ran it. */
function PackageRows({ rows, onOpen }: {
  rows: SuitePackage[];
  onOpen: (pkg: string) => void;
}) {
  const groups = new Map<string, SuitePackage[]>();
  for (const r of rows) {
    const { layer } = split(r.package);
    if (!groups.has(layer)) groups.set(layer, []);
    groups.get(layer)!.push(r);
  }
  const ordered = [...groups.entries()].sort(
    (a, b) => (LAYER_ORDER.indexOf(a[0]) + 99) % 100 - (LAYER_ORDER.indexOf(b[0]) + 99) % 100,
  );

  return (
    <div className="mt-5 space-y-6">
      {ordered.map(([layer, items]) => (
        <div key={layer}>
          <h2 className="text-xs uppercase tracking-wide text-slate-500 mb-2">
            {layer} <span className="text-slate-600">· {items.length}</span>
          </h2>
          <table className="w-full text-sm">
            <thead className="text-slate-500 border-b border-slate-800">
              <tr>
                <th className="text-left px-3 py-1.5 font-normal">Name</th>
                <th className="text-left px-3 py-1.5 font-normal">Verdict</th>
                <th className="text-left px-3 py-1.5 font-normal">Last run</th>
                <th className="text-left px-3 py-1.5 font-normal">Who</th>
                <th className="text-left px-3 py-1.5 font-normal">Commit</th>
                <th className="text-right px-3 py-1.5 font-normal">Passed</th>
              </tr>
            </thead>
            <tbody>
              {items.sort((a, b) => a.package.localeCompare(b.package)).map((r) => (
                <tr key={r.package}
                    onClick={() => onOpen(r.package)}
                    className="border-b border-slate-900 cursor-pointer hover:bg-slate-900/60">
                  <td className="px-3 py-2 text-slate-200">{split(r.package).name}</td>
                  <td className="px-3 py-2">
                    {r.failed > 0
                      ? <span className="text-danger font-medium">{r.failed} failed</span>
                      : <span className="text-ok">passed</span>}
                  </td>
                  <td className="px-3 py-2 text-slate-400">{when(r.finished_at)}</td>
                  <td className="px-3 py-2 text-slate-400">
                    {r.actor || '—'}
                    <span className="ml-1.5 text-[10px] uppercase tracking-wide text-slate-500">
                      {r.source}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400">
                    {r.git_sha || '—'}
                    {r.dirty ? <span className="ml-1.5 text-warn">dirty</span> : null}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400 tabular-nums">{r.passed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

interface PackageDetail {
  package: string;
  history: SuitePackage[];
  failures: SuiteFailure[];
}

export default function SuitePage() {
  /** Packages first: the repo's own shape is the one a reader already
   *  holds, and "which feature is red" is the question that gets asked.
   *  Runs stay one click away for "what happened at 07:21". */
  const [view, setView] = useState<'packages' | 'runs'>('packages');
  const [runs, setRuns] = useState<SuiteRun[]>([]);
  const [packages, setPackages] = useState<SuitePackage[]>([]);
  const [open, setOpen] = useState<SuiteRun | null>(null);
  const [pkg, setPkg] = useState<PackageDetail | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      apiJSON<{ items: SuiteRun[] }>('/system/suite/runs?limit=40'),
      apiJSON<{ items: SuitePackage[] }>('/system/suite/packages'),
    ])
      .then(([r, p]) => { setRuns(r.items); setPackages(p.items); setErr(''); })
      .catch((e) => setErr(e instanceof ApiError && e.status === 401
        ? 'Session expired or no operator access.'
        : e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const openPackage = (name: string) => {
    apiJSON<PackageDetail>(`/system/suite/packages/${name}`)
      .then(setPkg)
      .catch(() => { /* the row already carries the verdict */ });
  };

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

      {(packages.length > 0 || runs.length > 0) && (
        <div className="mt-4 flex gap-1 text-sm">
          {(['packages', 'runs'] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
                    className={`px-3 py-1 rounded border ${
                      view === v
                        ? 'border-accent/50 bg-accent/10 text-accent'
                        : 'border-slate-800 text-slate-400 hover:text-slate-200'}`}>
              {v === 'packages'
                ? `Features & services (${packages.length})`
                : `Runs (${runs.length})`}
            </button>
          ))}
        </div>
      )}

      {view === 'packages' && packages.length > 0 && (
        <PackageRows rows={packages} onOpen={openPackage} />
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

      {view === 'runs' && runs.length > 0 && (
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

      {pkg && (
        <div className="fixed inset-0 bg-black/60 flex items-start justify-center p-8 z-50"
             onClick={() => setPkg(null)}>
          <div className="bg-slate-950 border border-slate-800 rounded-lg max-w-4xl w-full max-h-[80vh] overflow-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-slate-800 flex items-start justify-between">
              <div>
                <h2 className="text-slate-100 font-medium font-mono text-sm">{pkg.package}</h2>
                <p className="text-xs text-slate-500 mt-1">
                  {pkg.history.length} run{pkg.history.length === 1 ? '' : 's'} on record
                </p>
              </div>
              <button onClick={() => setPkg(null)}
                      className="text-slate-500 hover:text-slate-300 px-2">×</button>
            </div>

            {pkg.failures.length > 0 && (
              <div className="p-5 space-y-3 border-b border-slate-800">
                <h3 className="text-xs uppercase tracking-wide text-slate-500">
                  Failing in the newest run that ran it
                </h3>
                {pkg.failures.map((f) => (
                  <div key={f.nodeid} className="border border-slate-800 rounded p-3">
                    <div className="font-mono text-xs text-slate-200 break-all">{f.nodeid}</div>
                    <div className="text-xs text-danger mt-1.5">{f.message || '—'}</div>
                    {f.last_green_sha && (
                      <div className="text-xs text-slate-500 mt-2">
                        went red between{' '}
                        <span className="font-mono text-slate-400">{f.last_green_sha}</span>
                        {' '}and{' '}
                        <span className="font-mono text-slate-400">{f.first_seen_sha}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div className="p-5">
              <h3 className="text-xs uppercase tracking-wide text-slate-500 mb-2">
                Runs that exercised it
              </h3>
              <table className="w-full text-sm">
                <tbody>
                  {pkg.history.map((h) => (
                    <tr key={h.run_id} className="border-b border-slate-900">
                      <td className="py-1.5 pr-3 text-slate-400">{when(h.finished_at)}</td>
                      <td className="py-1.5 pr-3">
                        {h.failed > 0
                          ? <span className="text-danger">{h.failed} failed</span>
                          : <span className="text-ok">passed</span>}
                      </td>
                      <td className="py-1.5 pr-3 text-slate-500">{h.actor}</td>
                      <td className="py-1.5 pr-3 font-mono text-xs text-slate-500">
                        {h.git_sha}{h.dirty ? <span className="ml-1 text-warn">dirty</span> : null}
                      </td>
                      <td className="py-1.5 text-right text-slate-500 tabular-nums">{h.passed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
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
