import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import type {
  AccountKind, MonitoredAccountRow, SecurityBoard, SecurityCandidate, SecurityEndpointRow,
  SecurityRequestRow, SecurityRule, SecuritySeverity, SecuritySummary, SecurityWatching,
} from '../types';

/** Windows the ledger is read over.  Hours, because that is what the API
 *  takes; the labels are what an operator thinks in. */
const WINDOWS: { hours: number; label: string }[] = [
  { hours: 1, label: 'Last hour' },
  { hours: 24, label: 'Last 24h' },
  { hours: 24 * 7, label: 'Last 7 days' },
];

/** The detector always looks a week back: a probe spread over days only
 *  reads as one story at that range, and the page's own window is for
 *  reading the ledger, not for deciding about people. */
const DETECTOR_HOURS = 24 * 7;

type StatusClass = 'all' | 'denied' | 'broke' | 'ok';
/** The question the activity tables answer, as one control.  "Denied"
 *  here is exactly what the Refused tile counts plus throttles — one
 *  word, one status set, so the filter can never return rows the tile
 *  did not. */
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

/** Severity of a FINDING — how strongly the rules implicate a subject.
 *  Unrelated to the status palette above: a high finding is red because
 *  it wants the operator's eye, not because anything broke. */
const SEVERITY_CHIP: Record<SecuritySeverity, string> = {
  high: 'text-danger border-danger/40 bg-danger/10',
  med: 'text-warn border-warn/40 bg-warn/10',
  low: 'text-slate-400 border-slate-700',
};

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

/** A candidate row's identity — stable across reloads so React keeps the
 *  expanded/promoting state on the right row. */
function rowKey(c: SecurityCandidate): string {
  if (c.group === 'burst') return `burst:${c.ip}`;
  if (c.account_id != null) return `acct:${c.account_id}`;
  if (c.subject) return `subj:${c.subject}`;
  return `ip:${c.ip ?? '?'}`;
}

const looksLikeEndpoint = (s: string | null) => !!s && /^(GET|POST|PUT|PATCH|DELETE) \//.test(s);

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
  const [decide, setDecide] = useState<SecurityCandidate[]>([]);
  const [watching, setWatching] = useState<SecurityWatching[]>([]);
  const [rules, setRules] = useState<Record<string, SecurityRule>>({});
  const [map, setMap] = useState<SecurityEndpointRow[]>([]);
  const [rows, setRows] = useState<SecurityRequestRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const [promoting, setPromoting] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const endpointsRef = useRef<HTMLElement>(null);

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
      apiJSON<SecurityBoard>(`/system/security/candidates?hours=${DETECTOR_HOURS}`),
      apiJSON<{ items: SecurityRule[] }>('/system/security/rules'),
    ])
      .then(([s, m, e, r, b, ru]) => {
        setSummary(s);
        setMonitored(m.items);
        setMap(e.items);
        setRows(r.items);
        setDecide(b.new);
        setWatching(b.watching);
        setRules(Object.fromEntries(ru.items.map((x) => [x.id, x])));
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

  const watchingById = useMemo(() => new Map(watching.map((w) => [w.account_id, w])), [watching]);

  /** An empty table must say what emptied it — the window, the account,
   *  the class — and how to widen.  "Nothing here" is false when one
   *  filter away there is plenty. */
  const emptyCopy = () => {
    const w = WINDOWS.find((x) => x.hours === hours)?.label.toLowerCase() ?? `${hours}h`;
    const who = account === '' ? 'anyone' : nameOf(account);
    const what = cls === 'all' ? '' : ` (${STATUS_CLASSES.find((c) => c.value === cls)?.label.toLowerCase()})`;
    return `Nothing from ${who} in the ${w}${what}. Widen the window or change the filter.`;
  };

  /** Promote one or many accounts to `monitored`.  The same endpoint the
   *  account page uses, so the change is audited there; nothing about the
   *  account's behaviour changes, which is why this is one click and not
   *  a confirmation dialog. */
  const promote = async (key: string, ids: number[]) => {
    setPromoting(key);
    setErr('');
    // allSettled, not a loop that throws: a bulk promote that dies on the
    // third account has already changed two, and one generic error leaves
    // the operator unable to tell which.
    const results = await Promise.allSettled(ids.map((id) =>
      apiJSON(`/system/accounts/${id}/type`, {
        method: 'PATCH', body: { type: 'monitored' as AccountKind },
      })));
    const failed = results.filter((r) => r.status === 'rejected').length;
    if (failed) {
      const first = results.find((r) => r.status === 'rejected') as PromiseRejectedResult | undefined;
      const why = first?.reason instanceof Error ? first.reason.message : 'the server refused';
      setErr(ids.length === 1
        ? `Could not set the account kind — ${why}`
        : `${ids.length - failed} of ${ids.length} set to monitored; ${failed} failed — ${why}. Try again for the rest.`);
    }
    setPromoting(null);
    load();
  };

  /** Stop watching — the way back out, on the row that shows the
   *  watching. Confirmed because it silently stops recording; what has
   *  already been recorded stays, and the operator should know that
   *  before deciding. */
  const stopWatching = async (id: number, name: string, recorded: number) => {
    const ok = window.confirm(
      `Stop watching ${name}?\n\nNew requests will no longer be recorded. ` +
      `The ${recorded.toLocaleString()} already in the ledger stay, and the rules can surface it again.`);
    if (!ok) return;
    setPromoting(`stop:${id}`);
    setErr('');
    try {
      await apiJSON(`/system/accounts/${id}/type`, { method: 'PATCH', body: { type: 'real' as AccountKind } });
      if (account === id) setAccount('');
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Could not set the account kind');
    } finally {
      setPromoting(null);
      load();
    }
  };

  /** A finding about an endpoint has no account to monitor; what the
   *  operator can do is look at that endpoint's traffic.  Widen to the
   *  detector's week, drop the account filter, and bring the row into
   *  view. */
  const showEndpoint = (subject: string) => {
    setFocusPath(subject);
    setAccount('');
    setHours(DETECTOR_HOURS);
    setTimeout(() => endpointsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  };

  const toggle = (key: string) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const nameOf = (id: number | null) => {
    if (id == null) return '—';
    const m = monitored.find((a) => a.account_id === id);
    return m ? m.name : String(id);
  };

  const RuleChip = ({ id }: { id: string }) => {
    const r = rules[id];
    return (
      <span title={r ? `${r.means} Seen: ${r.seen}` : id}
            className="text-[10px] px-1.5 py-0.5 rounded border border-slate-700 text-slate-300 cursor-help">
        {r?.label ?? id}
      </span>
    );
  };

  const focusInMap = focusPath ? map.some((e) => `${e.method} ${e.path}` === focusPath) : true;

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Security</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Who needs a decision, who is being watched, and what the ledger recorded — every refusal
          from anyone, everything from an account you watch. A refusal is the wall holding; a 5xx is
          something they found.
        </p>
      </header>

      {/* Always rendered: a region that mounts when data arrives shoves the
          whole page down on first paint.  Placeholders hold the height. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Tile label={`Refused · ${WINDOWS.find((w) => w.hours === hours)?.label.toLowerCase() ?? `${hours}h`}`} value={summary?.refused} hint="401 + 403 — held" tone="text-ok" />
        <Tile label="Throttled" value={summary?.throttled} hint="429" tone="text-warn" />
        <Tile label="Broke" value={summary?.broke} hint="5xx — look here" tone={(summary?.broke ?? 0) > 0 ? 'text-danger' : undefined} />
        <Tile label="Watching" value={summary?.monitored_accounts} hint="accounts · kind = monitored" tone="text-accent" />
      </div>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">{err}</div>
      )}

      {/* ── 1. Needs a decision ──────────────────────────────── */}
      <section className="mb-5">
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-1">
          Needs a decision · last 7 days
        </h2>
        <p className="text-xs text-slate-500 mb-2">
          Subjects the rules implicate that you are not already watching. An argument, not a verdict:
          Monitor records everything the account does and restricts nothing, so a wrong guess costs a
          row in a list.
        </p>
        <details className="mb-2 text-xs">
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200 select-none">What the rules mean</summary>
          <ul className="mt-2 grid gap-1.5 md:grid-cols-2">
            {Object.values(rules).map((r) => (
              <li key={r.id} className="bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5">
                <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded border mr-2 ${SEVERITY_CHIP[r.severity]}`}>{r.severity}</span>
                <span className="text-slate-200">{r.label}</span>
                <span className="text-slate-500"> — {r.means}</span>
              </li>
            ))}
          </ul>
        </details>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="text-left px-3 py-2">Subject</th>
                <th className="text-left px-3 py-2">Why</th>
                <th className="text-left px-3 py-2">Severity</th>
                <th className="text-right px-3 py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading && decide.length === 0 && (
                <tr><td colSpan={4} className="text-center text-slate-500 py-6">Loading…</td></tr>
              )}
              {!loading && decide.length === 0 && (
                <tr><td colSpan={4} className="text-center text-slate-500 py-6">
                  No rule fired on anyone you are not already watching in the last 7 days.
                </td></tr>
              )}
              {decide.map((c) => {
                const key = rowKey(c);
                const busy = promoting === key;
                const isBurst = c.group === 'burst' && c.members;
                const open = expanded.has(key);
                return (
                  <tr key={key} className="border-b border-slate-800/50 align-top">
                    <td className="px-3 py-2">
                      {isBurst ? (
                        <>
                          <div className="text-slate-100">{c.members!.length} accounts from one address</div>
                          <div className="font-mono text-xs text-slate-500">{c.ip}</div>
                          {/* Opens in place — so it must not wear a link's
                              shape, which on this page means navigation. */}
                          <button type="button" onClick={() => toggle(key)} aria-expanded={open}
                                  className="mt-1 px-1 -ml-1 py-0.5 text-xs text-slate-400 hover:text-slate-200 rounded">
                            <span aria-hidden className="inline-block w-3">{open ? '▾' : '▸'}</span>
                            {open ? 'Hide accounts' : `${c.members!.length} accounts`}
                          </button>
                          {open && (
                            <ul className="mt-1.5 space-y-0.5 text-xs">
                              {c.members!.map((m) => (
                                <li key={m.account_id} className="flex flex-wrap items-center gap-1.5">
                                  <Link to={`/accounts/${m.account_id}`} className="text-slate-200 hover:text-accent">{m.name ?? m.account_id}</Link>
                                  <span className="text-slate-600">{m.account_id}</span>
                                  {m.rules.map((r) => <RuleChip key={r} id={r} />)}
                                </li>
                              ))}
                            </ul>
                          )}
                        </>
                      ) : c.account_id != null ? (
                        <>
                          <Link to={`/accounts/${c.account_id}`} className="text-slate-100 hover:text-accent">{c.name ?? c.account_id}</Link>
                          <div className="text-xs text-slate-500">
                            {c.account_id}{c.ip ? ` · ${c.ip}` : ''}
                            {c.kind && c.kind !== 'real' ? <> · <span className="text-accent">{c.kind}</span></> : null}
                          </div>
                        </>
                      ) : (
                        <>
                          <span className="font-mono text-xs text-slate-200">{c.subject ?? c.ip ?? 'unattributed'}</span>
                          {c.subject && c.ip && <div className="text-xs text-slate-500">{c.ip}</div>}
                        </>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1 mb-1">
                        {c.rules.map((r) => <RuleChip key={r} id={r} />)}
                      </div>
                      <ul className="text-xs text-slate-500 space-y-0.5">
                        {c.signals.slice(0, 3).map((s, i) => <li key={i}>{s.evidence}</li>)}
                        {c.signals.length > 3 && <li className="text-slate-600">+{c.signals.length - 3} more</li>}
                      </ul>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded border ${SEVERITY_CHIP[c.severity]}`}>{c.severity}</span>
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {isBurst ? (
                        <button type="button" disabled={busy}
                                onClick={() => promote(key, c.members!.map((m) => m.account_id))}
                                className="text-xs px-2 py-1 rounded border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50">
                          {busy ? 'Setting…' : `Monitor all ${c.members!.length}`}
                        </button>
                      ) : c.account_id != null ? (
                        <button type="button" disabled={busy}
                                onClick={() => promote(key, [c.account_id!])}
                                className="text-xs px-2 py-1 rounded border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50">
                          {busy ? 'Setting…' : 'Monitor'}
                        </button>
                      ) : looksLikeEndpoint(c.subject) ? (
                        <button type="button" onClick={() => showEndpoint(c.subject!)}
                                className="text-xs px-2 py-1 rounded border border-slate-700 text-slate-300 hover:text-slate-100 hover:border-slate-500">
                          Show endpoint
                        </button>
                      ) : (
                        <span className="text-xs text-slate-600" title="Nothing to monitor yet: this finding names no account.">no account yet</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── 2. Watching ──────────────────────────────────────── */}
      <section className="mb-5">
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-1">Watching</h2>
        <p className="text-xs text-slate-500 mb-2">
          Accounts marked monitored. Every request they make is recorded; "still firing" is what the
          rules say about them this week. Filter narrows the activity tables below to one account.
        </p>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="text-left px-3 py-2">Account</th>
                <th className="text-left px-3 py-2">Still firing</th>
                <th className="text-right px-3 py-2">Requests</th>
                <th className="text-right px-3 py-2">Refused</th>
                <th className="text-right px-3 py-2">Broke</th>
                <th className="text-left px-3 py-2">Last seen</th>
                <th className="text-right px-3 py-2">Activity</th>
              </tr>
            </thead>
            <tbody>
              {!loading && monitored.length === 0 && (
                <tr><td colSpan={7} className="text-center text-slate-500 py-6">
                  No account is marked <span className="text-accent">monitored</span>. Use Monitor above, or set it from an account page.
                </td></tr>
              )}
              {loading && monitored.length === 0 && (
                <tr><td colSpan={7} className="text-center text-slate-500 py-6">Loading…</td></tr>
              )}
              {monitored.map((a) => {
                const selected = account === a.account_id;
                const w = watchingById.get(a.account_id);
                return (
                <tr key={a.account_id}
                    className={`border-b border-slate-800/50 hover:bg-slate-800/40 ${selected ? 'border-l-2 border-l-accent bg-slate-800/40' : 'border-l-2 border-l-transparent'}`}>
                  <td className="px-3 py-2">
                    <Link to={`/accounts/${a.account_id}`} className="text-slate-100 hover:text-accent">{a.name}</Link>
                    <div className="text-xs text-slate-500">{a.account_id}</div>
                  </td>
                  <td className="px-3 py-2">
                    {w ? (
                      <div className="flex flex-wrap gap-1">{w.rules.map((r) => <RuleChip key={r} id={r} />)}</div>
                    ) : (
                      <span className="text-xs text-slate-600">quiet</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-300">{a.requests}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-ok">{a.refused}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${a.broke > 0 ? 'text-danger' : 'text-slate-500'}`}>{a.broke}</td>
                  <td className="px-3 py-2 text-xs text-slate-500 whitespace-nowrap">{when(a.last_seen)}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <button type="button"
                            aria-pressed={selected}
                            onClick={() => setAccount(selected ? '' : a.account_id)}
                            className={`text-xs px-2 py-0.5 rounded border ${selected ? 'bg-accent/15 text-accent border-accent/40' : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}>
                      {selected ? 'Filtering' : 'Filter'}
                    </button>
                    <button type="button"
                            disabled={promoting === `stop:${a.account_id}`}
                            onClick={() => stopWatching(a.account_id, a.name, a.requests)}
                            className="ml-2 text-xs px-2 py-0.5 rounded border border-slate-700 text-slate-500 hover:text-slate-200 disabled:opacity-50">
                      {promoting === `stop:${a.account_id}` ? 'Stopping…' : 'Stop'}
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── 3. Activity: the ledger, filtered ────────────────── */}
      <section>
        <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-1">Activity</h2>
        <p className="text-xs text-slate-500 mb-2">
          What the ledger recorded. The window sets the tiles above and both tables below; account and
          status narrow the tables only.
        </p>
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-4 flex flex-wrap gap-2 items-center">
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

        {/* Endpoint map */}
        {/* Subordinate to the Activity zone: a child heading that wears the
            parent's exact typography reads as a fourth top-level zone, so
            these step down in colour and case, and the left rule marks
            what belongs inside. */}
        <section ref={endpointsRef} className="mb-4 scroll-mt-4 border-l border-slate-800 pl-3">
          <h3 className="text-xs font-semibold text-slate-500 mb-1">
            Endpoints {account !== '' ? `· ${nameOf(account)}` : '· all refusals'}
          </h3>
          <p className="text-xs text-slate-500 mb-2">
            Per endpoint: refused is the wall holding, invalid is input the endpoint rejected, broke is a
            bug they reached, ok on an admin or system path is the row to open. Sorted broke-first.
          </p>
          {focusPath && (
            <p className="text-xs mb-2 flex flex-wrap items-center gap-x-2">
              <span className="text-slate-400">
                Focused on <span className="font-mono text-slate-200">{focusPath}</span> · window widened to 7 days · account filter cleared
              </span>
              <button type="button" onClick={() => setFocusPath(null)}
                      className="px-1.5 py-0.5 rounded border border-slate-700 text-slate-400 hover:text-slate-200">
                Clear focus
              </button>
              {!loading && !focusInMap && (
                <span className="w-full text-warn mt-1">
                  No ledger rows for it in this window — that finding came from captured errors, before request recording started.
                </span>
              )}
            </p>
          )}
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
                  const focused = focusPath === `${e.method} ${e.path}`;
                  return (
                    <tr key={`${e.method} ${e.path}`}
                        className={`border-b border-slate-800/50 ${focused ? 'border-l-2 border-l-accent bg-slate-800/40' : 'border-l-2 border-l-transparent'}`}>
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

        {/* Timeline */}
        <section className="border-l border-slate-800 pl-3">
          <h3 className="text-xs font-semibold text-slate-500 mb-1">
            Timeline {cls !== 'all' ? `· ${STATUS_CLASSES.find((c) => c.value === cls)?.label.toLowerCase()}` : ''}
          </h3>
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
