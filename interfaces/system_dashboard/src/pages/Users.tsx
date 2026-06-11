import { useEffect, useState, Fragment } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON, ApiError } from '../api/client';
import type { SystemUser, UserSession } from '../types';

// Cross-account user search.  Support tool: "customer X's driver can't
// log in" → search the name/telegram-id, see which account + role.
// Read-only — no editing of tenant users from here.

function roleColor(role: string): string {
  switch (role) {
    case 'owner':      return 'text-accent';
    case 'admin':      return 'text-warn';
    case 'driver':     return 'text-slate-400';
    default:           return 'text-slate-300';
  }
}

// Relative "last seen" formatter — friendlier than raw ISO for a support
// tool.  Beyond ~30 days we fall back to YYYY-MM-DD because by then the
// operator wants the actual date, not "47 days ago".
function formatLastSeen(iso: string | null): { label: string; tone: string; title: string } {
  if (!iso) return { label: 'never', tone: 'text-slate-600', title: 'No API activity recorded' };
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { label: 'never', tone: 'text-slate-600', title: iso };
  const title = iso.replace('T', ' ').slice(0, 19) + ' UTC';
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60)   return { label: 'just now',       tone: 'text-ok',       title };
  const min = Math.floor(sec / 60);
  if (min < 60)   return { label: `${min}m ago`,    tone: 'text-ok',       title };
  const hr = Math.floor(min / 60);
  if (hr < 24)    return { label: `${hr}h ago`,     tone: 'text-slate-300', title };
  const days = Math.floor(hr / 24);
  if (days === 1) return { label: 'yesterday',      tone: 'text-slate-400', title };
  if (days < 30)  return { label: `${days}d ago`,   tone: 'text-slate-400', title };
  return { label: iso.slice(0, 10), tone: 'text-slate-500', title };
}

export default function UsersPage() {
  const [rows, setRows] = useState<SystemUser[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  // Which row's sessions panel is open.  Sessions are lazy-loaded on
  // expand (operator usually only inspects one user at a time, no need
  // to fan out 200 /sessions calls on initial page load).
  const [expanded, setExpanded] = useState<number | null>(null);
  const [sessionsByUser, setSessionsByUser] = useState<Record<number, UserSession[] | 'loading' | 'error'>>({});

  const loadSessions = (uid: number) => {
    setSessionsByUser((m) => ({ ...m, [uid]: 'loading' }));
    apiJSON<{ items: UserSession[] }>(`/system/users/${uid}/sessions`)
      .then((r) => setSessionsByUser((m) => ({ ...m, [uid]: r.items })))
      .catch(() => setSessionsByUser((m) => ({ ...m, [uid]: 'error' })));
  };

  const toggleRow = (uid: number) => {
    if (expanded === uid) {
      setExpanded(null);
      return;
    }
    setExpanded(uid);
    if (!sessionsByUser[uid]) loadSessions(uid);
  };

  const revokeSession = async (uid: number, sessionId: number, label: string) => {
    if (!confirm(`Revoke ${label || 'this session'}? The user will be signed out at their next request.`)) return;
    try {
      await apiJSON(`/system/sessions/${sessionId}`, { method: 'DELETE' });
      // Optimistic: drop the row from local state rather than refetch.
      setSessionsByUser((m) => {
        const cur = m[uid];
        if (!Array.isArray(cur)) return m;
        return { ...m, [uid]: cur.filter((s) => s.id !== sessionId) };
      });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Revoke failed');
    }
  };

  const load = () => {
    setLoading(true);
    setErr('');
    const qs = new URLSearchParams({ limit: '200' });
    if (search.trim()) qs.set('search', search.trim());
    apiJSON<{ items: SystemUser[] }>(`/system/users?${qs}`)
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

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Users</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Every user across every account.  Search by name, email, or exact Telegram id.
          Read-only — tenant user management stays in the customer dashboard.
        </p>
      </header>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-3 flex flex-wrap gap-2 items-center">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load(); }}
          placeholder="Name, email, or telegram id…"
          className="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm flex-1 min-w-[220px]"
        />
        <button onClick={load}
                className="bg-accent text-white text-xs px-3 py-1.5 rounded hover:bg-accent/90">
          Search
        </button>
      </div>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left px-3 py-2">#</th>
              <th className="text-left px-3 py-2">Name</th>
              <th className="text-left px-3 py-2">Telegram id</th>
              <th className="text-left px-3 py-2">Account</th>
              <th className="text-left px-3 py-2">Role</th>
              <th className="text-left px-3 py-2">Email</th>
              <th className="text-left px-3 py-2">Active</th>
              <th className="text-left px-3 py-2">Last seen</th>
              <th className="text-left px-3 py-2">Joined</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} className="text-center text-slate-500 py-8">Loading…</td></tr>}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={9} className="text-center text-slate-500 py-8">No users match.</td></tr>
            )}
            {rows.map((u) => {
              const ls = formatLastSeen(u.last_seen);
              const isOpen = expanded === u.id;
              const sess = sessionsByUser[u.id];
              return (
                <Fragment key={u.id}>
                  <tr
                    onClick={() => toggleRow(u.id)}
                    className={`border-b border-slate-800/50 cursor-pointer ${isOpen ? 'bg-slate-800/50' : 'hover:bg-slate-800/50'}`}
                  >
                    <td className="px-3 py-2 text-slate-500 font-mono text-xs tabular-nums">{u.id}</td>
                    <td className="px-3 py-2 text-slate-100">{u.display_name || <span className="text-slate-600 italic">—</span>}</td>
                    <td className="px-3 py-2 text-slate-400 font-mono text-xs tabular-nums">{u.telegram_id}</td>
                    <td className="px-3 py-2">
                      <Link to={`/accounts/${u.account_id}`} onClick={(e) => e.stopPropagation()} className="text-slate-300 hover:text-accent">
                        {u.account_name}
                      </Link>
                    </td>
                    <td className={`px-3 py-2 capitalize ${roleColor(u.role)}`}>{u.role}</td>
                    <td className="px-3 py-2 text-slate-400 text-xs">{u.email || '—'}</td>
                    <td className="px-3 py-2">
                      {u.is_active
                        ? <span className="text-ok text-xs">yes</span>
                        : <span className="text-slate-600 text-xs">no</span>}
                    </td>
                    <td className={`px-3 py-2 text-xs tabular-nums ${ls.tone}`} title={ls.title}>{ls.label}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs tabular-nums">{u.created_at?.slice(0, 10) || '—'}</td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-slate-800/50 bg-slate-950">
                      <td colSpan={9} className="px-4 py-3">
                        <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">
                          Active sessions
                        </div>
                        {sess === 'loading' && <p className="text-xs text-slate-500">Loading…</p>}
                        {sess === 'error' && <p className="text-xs text-danger">Failed to load sessions.</p>}
                        {Array.isArray(sess) && sess.length === 0 && (
                          <p className="text-xs text-slate-600 italic">No active sessions.</p>
                        )}
                        {Array.isArray(sess) && sess.length > 0 && (
                          <table className="w-full text-xs">
                            <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                              <tr>
                                <th className="text-left pr-3 py-1">Device</th>
                                <th className="text-left pr-3 py-1">IP</th>
                                <th className="text-left pr-3 py-1">Signed in</th>
                                <th className="text-left pr-3 py-1">Last active</th>
                                <th className="text-left pr-3 py-1">Expires</th>
                                <th className="text-right pr-3 py-1 w-8"></th>
                              </tr>
                            </thead>
                            <tbody>
                              {sess.map((s) => {
                                const la = formatLastSeen(s.last_seen);
                                return (
                                  <tr key={s.id} className="border-t border-slate-800/40">
                                    <td className="pr-3 py-1.5 text-slate-200" title={s.user_agent}>{s.device_label || '—'}</td>
                                    <td className="pr-3 py-1.5 text-slate-400 font-mono">{s.ip || '—'}</td>
                                    <td className="pr-3 py-1.5 text-slate-500 tabular-nums">{s.created_at?.slice(0, 10) || '—'}</td>
                                    <td className={`pr-3 py-1.5 tabular-nums ${la.tone}`} title={la.title}>{la.label}</td>
                                    <td className="pr-3 py-1.5 text-slate-500 tabular-nums">{s.expires_at?.slice(0, 10) || '—'}</td>
                                    <td className="pr-3 py-1.5 text-right">
                                      <button
                                        onClick={(e) => { e.stopPropagation(); revokeSession(u.id, s.id, s.device_label); }}
                                        title="Revoke this session"
                                        className="text-slate-500 hover:text-danger px-1.5 py-0.5 rounded hover:bg-danger/10 transition"
                                      >
                                        ✕
                                      </button>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500 mt-3">{rows.length} row{rows.length === 1 ? '' : 's'} · server-side limit 200</p>
    </div>
  );
}
