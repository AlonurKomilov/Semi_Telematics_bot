import { useCallback, useEffect, useState } from 'react';
import { apiJSON } from '../api/client';

// Standard service tasks — the vocabulary every account is seeded
// from.  This used to be a Python tuple: adding one fleet-wide needed
// a code change, a migration and a deploy.
//
// Two behaviours to keep in mind while editing here:
//   • Adding an entry FANS OUT to every existing account immediately —
//     otherwise a new standard task would only reach accounts created
//     later.  Renaming fans out too, because the whole point of the
//     shared key is that a task means the same thing everywhere.
//   • Archiving does NOT reach into accounts.  It only stops the task
//     being seeded into NEW ones; existing accounts may have years of
//     history under it, and hiding it is their decision.

interface Candidate {
  name_key: string;
  sample_name: string;
  account_count: number;
}

interface LibraryEntry {
  id: number;
  canonical_key: string;
  name: string;
  description: string;
  expected_labor_hours: number;
  vehicle_type: '' | 'truck' | 'trailer';
  status: 'active' | 'archived';
  /** How many accounts currently hold this task. */
  accounts: number;
  created_at: string;
}

const STATUS_BADGE: Record<LibraryEntry['status'], string> = {
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  archived: 'bg-slate-700/40 text-slate-500 border-slate-600/40',
};

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';

const btnCls =
  'px-2.5 py-1 rounded text-xs font-medium border transition disabled:opacity-50';

export default function ServiceTaskLibraryPage() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [cands, setCands] = useState<Candidate[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [search, setSearch] = useState('');

  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<LibraryEntry>>({});
  const [newForm, setNewForm] = useState({
    name: '', description: '', expected_labor_hours: '', vehicle_type: '',
  });

  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([
        apiJSON<{ entries: LibraryEntry[] }>('/system/service-task-library'),
        apiJSON<{ candidates: Candidate[] }>('/system/service-task-library/candidates'),
      ]);
      setEntries(r.entries);
      setCands(c.candidates);
      setErr('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Load failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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
      apiJSON('/system/service-task-library', {
        method: 'POST',
        body: JSON.stringify({
          name: newForm.name,
          description: newForm.description,
          expected_labor_hours: Number(newForm.expected_labor_hours) || 0,
          vehicle_type: newForm.vehicle_type,
        }),
      }).then(() => setNewForm({
        name: '', description: '', expected_labor_hours: '', vehicle_type: '',
      })));

  const saveEdit = (id: number) =>
    act(`edit:${id}`, () =>
      apiJSON(`/system/service-task-library/${id}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: draft.name,
          description: draft.description,
          expected_labor_hours: draft.expected_labor_hours,
          vehicle_type: draft.vehicle_type,
        }),
      }).then(() => setEditing(null)));

  const setStatus = (id: number, status: LibraryEntry['status']) =>
    act(`status:${id}`, () =>
      apiJSON(`/system/service-task-library/${id}`, {
        method: 'PUT', body: JSON.stringify({ status }),
      }));

  const resync = (id: number) =>
    act(`resync:${id}`, () =>
      apiJSON<{ accounts_added: number }>(
        `/system/service-task-library/${id}/resync`, { method: 'POST' },
      ));

  const visible = entries.filter((e) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return e.name.toLowerCase().includes(q)
      || e.canonical_key.includes(q)
      || e.description.toLowerCase().includes(q);
  });

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Service Task Library</h1>
      <p className="text-sm text-slate-500 mb-5">
        The standard tasks every account is seeded with. Adding or renaming one
        <span className="text-slate-300"> pushes to every existing account</span>;
        archiving only stops it being given to new ones, because existing
        accounts may have history under it.
      </p>

      {err && <div className="mb-4 text-sm text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-3 py-2">{err}</div>}
      {loading && <p className="text-slate-500 text-sm">Loading…</p>}

      {/* ── Candidates: names 2+ accounts invented on their own ── */}
      {cands.length > 0 && (
        <div className="mb-6 border border-amber-500/30 rounded-lg overflow-hidden">
          <div className="bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-300">
            Candidates — custom tasks used by 2+ accounts ({cands.length})
          </div>
          <div className="p-3 space-y-2">
            {cands.map((c) => (
              <div key={c.name_key} className="flex items-center gap-3 text-sm">
                <span className="text-slate-200">{c.sample_name}</span>
                <span className="text-xs text-slate-500">
                  {c.account_count} accounts
                </span>
                <button
                  className={`${btnCls} ml-auto border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
                  onClick={() => setNewForm((f) => ({ ...f, name: c.sample_name }))}
                >
                  Fill add form
                </button>
              </div>
            ))}
            <p className="text-xs text-slate-500">
              Promoting = adding it below (tidy the name first if needed).
              Every account's matching custom task is then adopted in
              place — same row, history intact — and it seeds into
              accounts that never had it.
            </p>
          </div>
        </div>
      )}

      {/* ── Add ── */}
      <div className="mb-6 border border-slate-800 rounded-lg p-3">
        <div className="text-sm font-medium text-slate-300 mb-2">Add a standard task</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          <input
            className={inputCls} placeholder="Name (e.g. Kingpin Service)"
            value={newForm.name}
            onChange={(e) => setNewForm({ ...newForm, name: e.target.value })}
          />
          <input
            className={inputCls} placeholder="Description (optional)"
            value={newForm.description}
            onChange={(e) => setNewForm({ ...newForm, description: e.target.value })}
          />
          <input
            className={inputCls} type="number" min="0" step="0.25"
            placeholder="Est. labor hours"
            value={newForm.expected_labor_hours}
            onChange={(e) => setNewForm({ ...newForm, expected_labor_hours: e.target.value })}
          />
          <div className="flex gap-2">
            <select
              className={inputCls} value={newForm.vehicle_type}
              onChange={(e) => setNewForm({ ...newForm, vehicle_type: e.target.value })}
            >
              <option value="">Any vehicle</option>
              <option value="truck">Trucks only</option>
              <option value="trailer">Trailers only</option>
            </select>
            <button
              className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 whitespace-nowrap`}
              disabled={!newForm.name.trim() || busyKey === 'add'}
              onClick={addEntry}
            >
              {busyKey === 'add' ? 'Adding…' : 'Add + push'}
            </button>
          </div>
        </div>
      </div>

      <input
        className={`${inputCls} mb-3 max-w-sm`}
        placeholder="Search the library…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      <div className="border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Task</th>
              <th className="text-left px-3 py-2 font-medium">Key</th>
              <th className="text-left px-3 py-2 font-medium">Est. labor</th>
              <th className="text-left px-3 py-2 font-medium">Applies to</th>
              <th className="text-left px-3 py-2 font-medium">Accounts</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.map((e) => (
              <tr key={e.id} className="border-t border-slate-800/70">
                <td className="px-3 py-2">
                  {editing === e.id ? (
                    <input
                      className={inputCls}
                      value={draft.name ?? e.name}
                      onChange={(ev) => setDraft({ ...draft, name: ev.target.value })}
                    />
                  ) : (
                    <div>
                      <div className="text-slate-200">{e.name}</div>
                      {e.description && (
                        <div className="text-xs text-slate-500">{e.description}</div>
                      )}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500">
                  {e.canonical_key}
                </td>
                <td className="px-3 py-2 text-slate-400 tabular-nums">
                  {e.expected_labor_hours ? `${e.expected_labor_hours} h` : '—'}
                </td>
                <td className="px-3 py-2 text-slate-400 capitalize">
                  {e.vehicle_type ? `${e.vehicle_type}s only` : 'Any'}
                </td>
                <td className="px-3 py-2 text-slate-400 tabular-nums">{e.accounts}</td>
                <td className="px-3 py-2">
                  <span className={`px-1.5 py-0.5 rounded text-xs border ${STATUS_BADGE[e.status]}`}>
                    {e.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {editing === e.id ? (
                    <>
                      <button
                        className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 mr-1`}
                        disabled={busyKey === `edit:${e.id}`}
                        onClick={() => saveEdit(e.id)}
                      >Save + push</button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        onClick={() => setEditing(null)}
                      >Cancel</button>
                    </>
                  ) : (
                    <>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800 mr-1`}
                        onClick={() => { setEditing(e.id); setDraft(e); }}
                      >Edit</button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800 mr-1`}
                        disabled={busyKey === `resync:${e.id}`}
                        title="Re-push to any account missing it"
                        onClick={() => resync(e.id)}
                      >Resync</button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        disabled={busyKey === `status:${e.id}`}
                        onClick={() => setStatus(
                          e.id, e.status === 'active' ? 'archived' : 'active',
                        )}
                      >{e.status === 'active' ? 'Archive' : 'Restore'}</button>
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
