import { useEffect, useState, useCallback } from 'react';
import { apiJSON, ApiError } from '../api/client';

// Global vendor directory curation — the platform-owned identity list
// every account's private vendors can link to.  Operators approve or
// reject account suggestions (pending queue floats to the top), edit
// contact/services, and add entries directly (born active).
// Identity data only: this screen never sees any account's invoices,
// spend, or which accounts use a shop (suggested_by_account is shown
// as a bare account id for audit, nothing more).

interface DirEntry {
  id: number;
  name: string;
  address: string;
  phone: string;
  email: string;
  website: string;
  services: string;
  notes: string;
  status: 'pending' | 'active' | 'rejected';
  source: string;
  suggested_by_account: number | null;
  created_at: string;
}

const STATUS_BADGE: Record<DirEntry['status'], string> = {
  pending: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  rejected: 'bg-slate-700/40 text-slate-500 border-slate-600/40',
};

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';

export default function VendorDirectoryPage() {
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [filter, setFilter] = useState<'all' | DirEntry['status']>('all');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<DirEntry>>({});
  const [newName, setNewName] = useState('');
  const [newAddress, setNewAddress] = useState('');
  const [newPhone, setNewPhone] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<{ entries: DirEntry[] }>('/system/vendor-directory')
      .then((r) => setEntries(r.entries))
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id);
    try { await fn(); load(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Action failed'); }
    finally { setBusy(null); }
  };

  const approve = (id: number) =>
    act(id, () => apiJSON(`/system/vendor-directory/${id}/approve`, { method: 'POST' }));
  const reject = (id: number) =>
    act(id, () => apiJSON(`/system/vendor-directory/${id}/reject`, { method: 'POST' }));
  const saveEdit = (id: number) =>
    act(id, async () => {
      await apiJSON(`/system/vendor-directory/${id}`, { method: 'PUT', body: draft });
      setEditing(null);
    });
  const create = () =>
    act(-1, async () => {
      if (!newName.trim()) return;
      await apiJSON('/system/vendor-directory', {
        method: 'POST',
        body: { name: newName, address: newAddress, phone: newPhone },
      });
      setNewName(''); setNewAddress(''); setNewPhone('');
    });

  const shown = filter === 'all' ? entries : entries.filter(e => e.status === filter);
  const pendingCount = entries.filter(e => e.status === 'pending').length;

  return (
    <div className="max-w-5xl">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-100">Vendor Directory</h1>
        <p className="text-sm text-slate-500 mt-1 max-w-2xl">
          Global repair-shop identities every account can link to. Identity data only —
          no account's invoices or spend ever appear here. Approve or reject the
          suggestion queue; add shops directly (born active).
        </p>
      </header>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      {/* Add entry */}
      <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex flex-wrap items-center gap-2">
        <input className={`${inputCls} max-w-56`} placeholder="Shop name" value={newName}
          onChange={e => setNewName(e.target.value)} />
        <input className={`${inputCls} max-w-64`} placeholder="Address" value={newAddress}
          onChange={e => setNewAddress(e.target.value)} />
        <input className={`${inputCls} max-w-40`} placeholder="Phone" value={newPhone}
          onChange={e => setNewPhone(e.target.value)} />
        <button
          onClick={create}
          disabled={!newName.trim() || busy === -1}
          className="px-3 py-1 rounded bg-slate-200 text-slate-900 text-sm font-medium hover:bg-white disabled:opacity-50"
        >
          Add active entry
        </button>
      </div>

      {/* Status filter */}
      <div className="mb-3 flex items-center gap-1.5 text-xs">
        {(['all', 'pending', 'active', 'rejected'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded-full border capitalize ${
              filter === f
                ? 'bg-slate-200 text-slate-900 border-slate-200 font-medium'
                : 'bg-transparent text-slate-400 border-slate-700 hover:text-slate-200'
            }`}>
            {f}{f === 'pending' && pendingCount > 0 ? ` (${pendingCount})` : ''}
          </button>
        ))}
      </div>

      {loading && entries.length === 0 && (
        <div className="text-slate-500 text-sm py-8 text-center">Loading…</div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
              <th className="px-4 py-2 font-medium">Shop</th>
              <th className="px-4 py-2 font-medium">Contact</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Source</th>
              <th className="px-4 py-2 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(e => (
              <tr key={e.id} className="border-b border-slate-800/60 last:border-0 align-top">
                <td className="px-4 py-3">
                  {editing === e.id ? (
                    <div className="flex flex-col gap-1.5">
                      <input className={inputCls} value={String(draft.name ?? e.name)}
                        onChange={ev => setDraft(d => ({ ...d, name: ev.target.value }))} />
                      <input className={inputCls} placeholder="Services (comma-separated)"
                        value={String(draft.services ?? e.services)}
                        onChange={ev => setDraft(d => ({ ...d, services: ev.target.value }))} />
                    </div>
                  ) : (
                    <>
                      <div className="text-slate-200 font-medium">{e.name}</div>
                      {e.services && <div className="text-xs text-slate-500 mt-0.5">{e.services}</div>}
                    </>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  {editing === e.id ? (
                    <div className="flex flex-col gap-1.5">
                      <input className={inputCls} placeholder="Address" value={String(draft.address ?? e.address)}
                        onChange={ev => setDraft(d => ({ ...d, address: ev.target.value }))} />
                      <input className={inputCls} placeholder="Phone" value={String(draft.phone ?? e.phone)}
                        onChange={ev => setDraft(d => ({ ...d, phone: ev.target.value }))} />
                    </div>
                  ) : (
                    <>
                      <div className="text-slate-300">{e.address || '—'}</div>
                      <div className="text-slate-500 mt-0.5">{e.phone || ''}</div>
                    </>
                  )}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <span className={`inline-block px-2 py-0.5 rounded-full border text-[11px] font-medium capitalize ${STATUS_BADGE[e.status]}`}>
                    {e.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {e.source === 'suggestion'
                    ? `suggestion · acct #${e.suggested_by_account ?? '?'}`
                    : 'operator'}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  <div className="inline-flex items-center gap-1.5 text-xs">
                    {editing === e.id ? (
                      <>
                        <button onClick={() => saveEdit(e.id)} disabled={busy === e.id}
                          className="px-2 py-1 rounded bg-slate-200 text-slate-900 font-medium hover:bg-white disabled:opacity-50">
                          Save
                        </button>
                        <button onClick={() => { setEditing(null); setDraft({}); }}
                          className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200">
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {e.status === 'pending' && (
                          <>
                            <button onClick={() => approve(e.id)} disabled={busy === e.id}
                              className="px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 disabled:opacity-50">
                              Approve
                            </button>
                            <button onClick={() => reject(e.id)} disabled={busy === e.id}
                              className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-50">
                              Reject
                            </button>
                          </>
                        )}
                        {e.status === 'rejected' && (
                          <button onClick={() => approve(e.id)} disabled={busy === e.id}
                            className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-50">
                            Re-approve
                          </button>
                        )}
                        <button onClick={() => { setEditing(e.id); setDraft({}); }}
                          className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200">
                          Edit
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {shown.length === 0 && !loading && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-600 text-sm">
                No entries{filter !== 'all' ? ` with status “${filter}”` : ' yet'}.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
