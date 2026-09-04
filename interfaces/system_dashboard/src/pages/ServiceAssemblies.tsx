import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiJSON } from '../api/client';

// Assembly library — level 2 of System → Assembly → Part, curated here
// (owner decision: shared-across-accounts vocabulary lives in the
// operator console).  Two things are IMMUTABLE by design: the key
// (parts store it as a plain string) and the system parent
// (re-parenting an assembly would rewrite historical rollups — fix a
// wrong parent by archive + recreate).

interface Assembly {
  id: number;
  key: string;
  label: string;
  system_key: string;
  status: 'active' | 'archived';
  parts: number;
}

interface TaskSystem { key: string; label: string }

const STATUS_BADGE: Record<Assembly['status'], string> = {
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  archived: 'bg-slate-700/40 text-slate-500 border-slate-600/40',
};

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';

const btnCls =
  'px-2.5 py-1 rounded text-xs font-medium border transition disabled:opacity-50';

export default function ServiceAssembliesPage() {
  const [rows, setRows] = useState<Assembly[]>([]);
  const [systems, setSystems] = useState<TaskSystem[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [search, setSearch] = useState('');

  const [editing, setEditing] = useState<number | null>(null);
  const [draftLabel, setDraftLabel] = useState('');
  const [newForm, setNewForm] = useState({ label: '', system_key: '' });

  const load = useCallback(async () => {
    try {
      const [a, sy] = await Promise.all([
        apiJSON<{ assemblies: Assembly[] }>('/system/service-assemblies'),
        // System-owner-gated, like every other call this console makes.
        // This previously fetched the tenant `/service-tasks/systems`,
        // which is gated on TENANT permissions (can_manage_service_tasks etc.)
        // — it only worked when the operator's Telegram id also
        // belonged to a customer account holding those perms.  Any
        // operator without that coincidence got a 403 and a page of
        // raw keys.  The operator console must never lean on
        // tenant-permission endpoints.
        apiJSON<{ systems: TaskSystem[] }>('/system/service-task-library/systems'),
      ]);
      setRows(a.assemblies);
      setSystems(sy.systems);
      setErr('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Load failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const sysLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of systems) m.set(s.key, s.label);
    return m;
  }, [systems]);

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusyKey(key);
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusyKey('');
    }
  };

  const addEntry = () =>
    act('add', () =>
      apiJSON('/system/service-assemblies', {
        method: 'POST', body: JSON.stringify(newForm),
      }).then(() => setNewForm({ label: '', system_key: '' })));

  const saveLabel = (id: number) =>
    act(`edit:${id}`, () =>
      apiJSON(`/system/service-assemblies/${id}`, {
        method: 'PUT', body: JSON.stringify({ label: draftLabel }),
      }).then(() => setEditing(null)));

  const setStatus = (id: number, status: Assembly['status']) =>
    act(`status:${id}`, () =>
      apiJSON(`/system/service-assemblies/${id}`, {
        method: 'PUT', body: JSON.stringify({ status }),
      }));

  const visible = rows.filter((r) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return r.label.toLowerCase().includes(q) || r.key.includes(q)
      || (sysLabel.get(r.system_key) ?? '').toLowerCase().includes(q);
  });

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Assemblies</h1>
      <p className="text-sm text-slate-500 mb-5">
        Level 2 of System → Assembly → Part. The key and the
        <span className="text-slate-300"> system parent never change</span> —
        re-parenting would rewrite historical rollups; archive and recreate
        instead. Archiving stops new assignments; parts already tagged keep
        their label.
      </p>

      {err && <div className="mb-4 text-sm text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-3 py-2">{err}</div>}
      {loading && <p className="text-slate-500 text-sm">Loading…</p>}

      <div className="mb-6 border border-slate-800 rounded-lg p-3">
        <div className="text-sm font-medium text-slate-300 mb-2">Add an assembly</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          <input
            className={inputCls} placeholder="Label (e.g. Radiator)"
            value={newForm.label}
            onChange={(e) => setNewForm({ ...newForm, label: e.target.value })}
          />
          <select
            className={inputCls} value={newForm.system_key}
            onChange={(e) => setNewForm({ ...newForm, system_key: e.target.value })}
          >
            <option value="">System (immutable after create)…</option>
            {systems.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
          <button
            className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
            disabled={!newForm.label.trim() || !newForm.system_key || busyKey === 'add'}
            onClick={addEntry}
          >
            {busyKey === 'add' ? 'Adding…' : 'Add assembly'}
          </button>
        </div>
      </div>

      <input
        className={`${inputCls} mb-3 max-w-sm`}
        placeholder="Search assemblies…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      <div className="border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Assembly</th>
              <th className="text-left px-3 py-2 font-medium">System</th>
              <th className="text-left px-3 py-2 font-medium">Key</th>
              <th className="text-left px-3 py-2 font-medium">Parts</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id} className="border-t border-slate-800/70">
                <td className="px-3 py-2">
                  {editing === r.id ? (
                    <input
                      className={inputCls}
                      value={draftLabel}
                      onChange={(e) => setDraftLabel(e.target.value)}
                    />
                  ) : (
                    <span className="text-slate-200">{r.label}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {sysLabel.get(r.system_key) ?? r.system_key}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500">{r.key}</td>
                <td className="px-3 py-2 text-slate-400 tabular-nums">{r.parts}</td>
                <td className="px-3 py-2">
                  <span className={`px-1.5 py-0.5 rounded text-xs border ${STATUS_BADGE[r.status]}`}>
                    {r.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {editing === r.id ? (
                    <>
                      <button
                        className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 mr-1`}
                        disabled={busyKey === `edit:${r.id}`}
                        onClick={() => saveLabel(r.id)}
                      >Save</button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        onClick={() => setEditing(null)}
                      >Cancel</button>
                    </>
                  ) : (
                    <>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800 mr-1`}
                        onClick={() => { setEditing(r.id); setDraftLabel(r.label); }}
                      >Rename</button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        disabled={busyKey === `status:${r.id}`}
                        onClick={() => setStatus(
                          r.id, r.status === 'active' ? 'archived' : 'active',
                        )}
                      >{r.status === 'active' ? 'Archive' : 'Restore'}</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
