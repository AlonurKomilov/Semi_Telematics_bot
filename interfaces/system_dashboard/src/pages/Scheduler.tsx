import { useEffect, useState, useCallback } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type { SchedulerJobsResponse, SchedulerJob } from '../types';

// "cron[hour='2', minute='0']" -> "cron · hour=2, minute=0"
// "interval[0:01:00]"          -> "interval · 0:01:00"
function formatTrigger(t: string): string {
  const m = t.match(/^(\w+)\[(.*)\]$/);
  if (!m) return t;
  const body = m[2].replace(/'/g, '');
  return body ? `${m[1]} · ${body}` : m[1];
}

function formatWhen(iso: string | null): string {
  if (!iso) return '—';
  return iso.slice(0, 16).replace('T', ' ');
}

export default function SchedulerPage() {
  const [jobs, setJobs] = useState<SchedulerJob[] | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<SchedulerJobsResponse>('/system/scheduler')
      .then((d) => setJobs(d.jobs))
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
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const snapshotAt = jobs && jobs.length > 0 ? jobs[0].updated_at : null;

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Scheduled jobs</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          The bot scheduler's registered cron / interval jobs. The bot snapshots these to the
          database (the API process can't read its in-memory scheduler); the snapshot refreshes
          every 15 minutes, so next-run times are approximate.
          {snapshotAt && <> Snapshot as of {snapshotAt.slice(0, 16).replace('T', ' ')}.</>}
        </p>
      </header>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      {loading && !jobs && (
        <div className="text-slate-500 text-sm py-8 text-center">Loading…</div>
      )}

      {jobs && jobs.length === 0 && !loading && (
        <div className="text-slate-500 text-sm py-8 text-center">
          No snapshot yet — the bot records its jobs on startup.
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                <th className="px-4 py-2 font-medium">Job</th>
                <th className="px-4 py-2 font-medium">Schedule</th>
                <th className="px-4 py-2 font-medium">Next run</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id} className="border-b border-slate-800/60 last:border-0 align-top">
                  <td className="px-4 py-3">
                    <div className="font-mono text-slate-200">{j.job_id}</div>
                    {j.description && (
                      <div className="text-xs text-slate-500 mt-0.5">{j.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-300">
                    {formatTrigger(j.trigger)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-xs text-slate-400">
                    {formatWhen(j.next_run_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-slate-600 mt-4">
        Read-only. Schedules are defined in code (the bot's scheduler); change them via code review,
        not here. Next-run reflects the last snapshot, not live scheduler state.
      </p>
    </div>
  );
}
