import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import type {
  MonitoredAccountRow, SecurityEndpointRow, SecurityRequestRow, SecuritySummary,
} from '../types';

/** Windows the ledger is read over.  Hours, because that is what the API
 *  takes; the labels are what an operator thinks in. */
const WINDOWS: { hours: number; label: string }[] = [
  { hours: 1, label: 'Last hour' },
  { hours: 24, label: 'Last 24h' },
  { hours: 24 * 7, label: 'Last 7 days' },
];

type StatusClass = 'all' | 'denied' | 'broke' | 'ok';
/** The question the page answers, as one control.  "Denied" here is
 *  exactly what the Refused tile counts plus throttles — one word, one
 *  status set, so the filter can never return rows the tile did not. */
const STATUS_CLASSES: { value: StatusClass; label: string }[] = [
  { value: 'all', label: 'Everything' },
  { value: 'denied', label: 'Denied · 401 403 429' },
  { value: 'broke', label: 'Broke · 5xx' },
  { value: 'ok', label: 'Got through · 2xx' },
];

/** Status → tone.  Refusals read as GOOD here — they are the wall
 *  holding — which is the opposite of what a status colour usually says,
 *  and the reason this page has its own mapping rather than reusing an
 *  HTTP-error palette. */
function statusTone(status: number): string {
  if (status === 401 || status === 403) return 'text-ok';
  if (status === 429) return 'text-warn';
  if (status >= 500) return 'text-danger';
  if (status >= 400) return 'text-slate-400';
  return 'text-slate-200';
}

function safeGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k: string, v: string): void {
  try { localStorage.setItem(k, v); } catch { /* private mode etc. */ }
}

function when(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function SecurityPage() {
  // Remembered per browser: an operator returns to the same window and
  // account they were reading — small, and the console has no
  // preferences service to reach for.
  const [hours, setHours] = useState<number>(() => Number(safeGet('sec.hours')) || 24);
  const [account, setAccount] = useState<number | ''>(() => { const v = safeGet('sec.account'); return v ? Number(v) : ''; });
  useEffect(() => { safeSet('sec.hours', String(hours)); }, [hours]);
  useEffect(() => { safeSet('sec.account', account === '' ? '' : String(account)); }, [account]);
  const [cls, setCls] = useState<StatusClass>('all');
  const [summary, setSummary] = useState<SecuritySummary | null>(null);
  const [monitored, setMonitored] = useState<MonitoredAccountRow[]>([]);
  const [map, setMap] = useState<SecurityEndpointRow[]>([]);
  const [rows, setRows] = useState<SecurityRequestRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = () => {
    setLoading(true);
    setErr('');
    const base = new URLSearchParams({ hours: String(hours) });
    const scoped = new URLSearchParams(base);
    if (account !== '') scoped.set('account_id', String(account));
    const reqQs = new URLSearchParams(scoped);
    reqQs.set('limit', '200');
    if (cls !== 'all') reqQs.set('cls', cls);
    Promise.all([
      apiJSON<SecuritySummary>(`/system/security/summary?${base}`),
      apiJSON<{ items: MonitoredAccountRow[] }>(`/system/security/monitored?${base}`),
      apiJSON<{ items: SecurityEndpointRow[] }>(`/system/security/map?${scoped}`),
      apiJSON<{ items: SecurityRequestRow[] }>(`/system/security/requests?${reqQs}`),
    ])
      .then(([s, m, e, r]) => {
        setSummary(s);
        setMonitored(m.items);
        setMap(e.items);
        setRows(r.items);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [hours, account, cls]); // eslint-disable-line react-hooks/exhaustive-deps

  /** An empty table must say what emptied it — the window, the account,
   *  the class — and how to widen.  "Nothing here" is false when one
   *  filter away there is plenty. */
  const emptyCopy = () => {
    const w = WINDOWS.find((x) => x.hours === hours)?.label.toLowerCase() ?? `${hours}h`;
    const who = account === '' ? 'anyone' : nameOf(account);
    const what = cls === 'all' ? '' : ` (${STATUS_CLASSES.find((c) => c.value === cls)?.label.toLowerCase()})`;
    return `Nothing from ${who} in the ${w}${what}. Widen the window or change the filter.`;
  };

  const nameOf = (id: number | null) => {
    if (id == null) return '—';
    const m = monitored.find((a) => a.account_id === id);
    return m ? m.name : String(id);
  };

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Security</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Every refusal the platform issued, from anyone — and every request from an account
          you are watching. A refusal is the wall holding; a 5xx is something they found.
        </p>
      </header>

      {/* Always rendered: a region that mounts when data arrives shoves the
          whole page down on first paint.  Placeholders hold the height. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Tile label={`Refused · ${hours}h`} value={summary?.refused} hint="401 + 403 — held" tone="text-ok" />
        <Tile label="Throttled" value={summary?.throttled} hint="429" tone="text-warn" />
        <Tile label="Broke" value={summary?.broke} hint="5xx — look here" tone={(summary?.broke ?? 0) > 0 ? 'text-danger' : undefined} />
        <Tile label="Monitored accounts" value={summary?.monitored_accounts} hint="kind = monitored" tone="text-accent" />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 flex flex-wrap gap-2 items-center">
        <label className="text-xs text-slate-400">Window:</label>
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
        </select>
        <label className="text-xs text-slate-400 ml-2">Account:</label>
        <select value={account} onChange={(e) => setAccount(e.target.value === '' ? '' : Number(e.target.value))}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          <option value="">All (refusals from anyone)</option>
          {monitored.map((a) => <option key={a.account_id} value={a.account_id}>{a.name}</option>)}
        </select>
        <label className="text-xs text-slate-400 ml-2">Show:</label>
        <select value={cls} onChange={(e) => setCls(e.target.value as StatusClass)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-sm">
          {STATUS_CLASSES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
        <button onClick={load}
                className="bg-accent text-white text-xs px-3 py-1.5 rounded hover:bg-accent/90 ml-auto">
          Refresh
        </button>
      </div>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">{err}</div>
      )}

      {/* ── Monitored accounts ───────────────────────────────── */}
      <section className="mb-4">
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-2">Monitored accounts</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="text-left px-3 py-2">Account</th>
                <th className="text-right px-3 py-2">Requests</th>
                <th className="text-right px-3 py-2">Refused</th>
                <th className="text-right px-3 py-2">Broke</th>
                <th className="text-left px-3 py-2">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {!loading && monitored.length === 0 && (
                <tr><td colSpan={5} className="text-center text-slate-500 py-6">
                  No account is marked <span className="text-accent">monitored</span>. Set one from its account page.
                </td></tr>
              )}
              {loading && monitored.length === 0 && (
                <tr><td colSpan={5} className="text-center text-slate-500 py-6">Loading…</td></tr>
              )}
              {monitored.map((a) => {
                const selected = account === a.account_id;
                return (
                <tr key={a.account_id}
                    className={`border-b border-slate-800/50 hover:bg-slate-800/40 ${selected ? 'border-l-2 border-l-accent bg-slate-800/40' : 'border-l-2 border-l-transparent'}`}>
                  <td className="px-3 py-2">
                    <Link to={`/accounts/${a.account_id}`} className="text-slate-100 hover:text-accent">{a.name}</Link>
                    <div className="text-xs text-slate-500">{a.account_id}</div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-300">{a.requests}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-ok">{a.refused}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${a.broke > 0 ? 'text-danger' : 'text-slate-500'}`}>{a.broke}</td>
                  <td className="px-3 py-2 text-xs text-slate-500 whitespace-nowrap">
                    {when(a.last_seen)}
                    <button type="button"
                            aria-pressed={selected}
                            onClick={() => setAccount(selected ? '' : a.account_id)}
                            className={`ml-3 text-xs px-2 py-0.5 rounded border ${selected ? 'bg-accent/15 text-accent border-accent/40' : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}>
                      {selected ? 'Filtering' : 'Filter'}
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Endpoint map ─────────────────────────────────────── */}
      <section className="mb-4">
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-2">
          Endpoints {account !== '' ? `· ${nameOf(account)}` : '· all refusals'}
        </h2>
        <p className="text-xs text-slate-500 mb-2">
          Per endpoint: refused is the wall holding, invalid is input the endpoint rejected, broke is a
          bug they reached, ok on an admin or system path is the row to open. Sorted broke-first.
        </p>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="text-left px-3 py-2">Method</th>
                <th className="text-left px-3 py-2">Path</th>
                <th className="text-right px-3 py-2">Refused</th>
                <th className="text-right px-3 py-2">Throttled</th>
                <th className="text-right px-3 py-2">Invalid</th>
                <th className="text-right px-3 py-2">Broke</th>
                <th className="text-right px-3 py-2">OK</th>
                <th className="text-right px-3 py-2">Total</th>
              </tr>
            </thead>
            <tbody>
              {loading && map.length === 0 && (
                <tr><td colSpan={8} className="text-center text-slate-500 py-6">Loading…</td></tr>
              )}
              {!loading && map.length === 0 && (
                <tr><td colSpan={8} className="text-center text-slate-500 py-6">{emptyCopy()}</td></tr>
              )}
              {map.map((e) => {
                const sensitive = /^\/api\/(system|admin)\//.test(e.path);
                return (
                  <tr key={`${e.method} ${e.path}`} className="border-b border-slate-800/50">
                    <td className="px-3 py-2 text-xs text-slate-400">{e.method}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-200">{e.path}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ok">{e.refused || ''}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-warn">{e.throttled || ''}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-400">{e.rejected || ''}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${e.broke > 0 ? 'text-danger font-semibold' : 'text-slate-600'}`}>{e.broke || ''}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${sensitive && e.ok > 0 ? 'text-warn font-semibold' : 'text-slate-300'}`}>{e.ok || ''}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{e.total}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Timeline ─────────────────────────────────────────── */}
      <section>
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-2">
          Timeline {cls !== 'all' ? `· ${STATUS_CLASSES.find((c) => c.value === cls)?.label.toLowerCase()}` : ''}
        </h2>
        <p className="text-xs text-slate-500 mb-2">
          Status colours read the other way round here: <span className="text-ok">401 / 403</span> is the wall holding,
          <span className="text-warn"> 429</span> is throttling, <span className="text-danger">5xx</span> is something they reached.
        </p>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="text-left px-3 py-2">When</th>
                <th className="text-left px-3 py-2">Request</th>
                <th className="text-right px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Account</th>
                <th className="text-left px-3 py-2">Who</th>
                <th className="text-left px-3 py-2">IP</th>
                <th className="text-right px-3 py-2">ms</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={7} className="text-center text-slate-500 py-6">Loading…</td></tr>}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={7} className="text-center text-slate-500 py-6">{emptyCopy()}</td></tr>
              )}
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/40">
                  <td className="px-3 py-2 text-xs text-slate-500 whitespace-nowrap">{when(r.created_at)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-200">
                    <span className="text-slate-400">{r.method}</span> {r.path}
                    {r.query && <span className="text-slate-600">?{r.query}</span>}
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums font-semibold ${statusTone(r.status)}`}>{r.status}</td>
                  <td className="px-3 py-2 text-xs text-slate-300">
                    {r.account_id != null ? <Link to={`/accounts/${r.account_id}`} className="hover:text-accent">{nameOf(r.account_id)}</Link> : <span className="text-slate-600">anonymous</span>}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-400">
                    {r.user_id == null ? '—' : (r.user_name ?? `#${r.user_id}`)}{r.role ? <span className="text-slate-600"> · {r.role}</span> : null}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400">{r.ip ?? '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs text-slate-500">{r.duration_ms ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-slate-500 mt-2">{rows.length} row{rows.length === 1 ? '' : 's'} · newest first · server-side limit 200</p>
      </section>
    </div>
  );
}

function Tile({ label, value, hint, tone }: { label: string; value: number | undefined; hint: string; tone?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${value === undefined ? 'text-slate-600' : (tone ?? 'text-slate-100')}`}>{value === undefined ? '—' : value.toLocaleString()}</p>
      <p className="text-[10px] text-slate-600 uppercase tracking-wider">{hint}</p>
    </div>
  );
}
