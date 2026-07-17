import { useCallback, useEffect, useState } from 'react';
import { apiJSON } from '../api/client';

// Public parts catalog curation — operator-owned CANONICAL part
// identities every account's private parts link to.  No user
// contribution pipeline by design: curation is top-down (create /
// import / promote from the cross-account candidates queue).  The
// candidates view is the ONE cross-account signal (name + counts),
// operator-eyes only; the account-facing browse shows identity only.
//
// Guardrails enforced server-side and surfaced here verbatim:
// generic-name blocklist (a "Labor" can never become canonical),
// alias keys never equal entry keys, dismissals tombstone forever.

interface PartEntry {
  id: number;
  name: string;
  name_key: string;
  category: string;
  part_number: string;
  description: string;
  status: 'active' | 'archived';
  source: string;
  linked_parts: number;
  aliases: Array<{ id: number; name_key: string }>;
  created_at: string;
}

interface Candidate {
  name_key: string;
  sample_name: string;
  account_count: number;
  catalog_rows: number;
  usage_count: number;
}

const STATUS_BADGE: Record<PartEntry['status'], string> = {
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  archived: 'bg-slate-700/40 text-slate-500 border-slate-600/40',
};

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';

const btnCls =
  'px-2.5 py-1 rounded text-xs font-medium border transition disabled:opacity-50';

export default function PartsDirectoryPage() {
  const [entries, setEntries] = useState<PartEntry[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [search, setSearch] = useState('');
  const [shownCount, setShownCount] = useState(100);

  // Promote flow: one candidate expands into a canonical-name form.
  const [promoting, setPromoting] = useState<string | null>(null);
  const [promoteForm, setPromoteForm] = useState({ canonical_name: '', category: '', part_number: '' });

  // Inline edit on entries.
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<PartEntry>>({});
  const [aliasDrafts, setAliasDrafts] = useState<Record<number, string>>({});

  // Add + import.
  const [newForm, setNewForm] = useState({ name: '', category: '', part_number: '' });
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importResult, setImportResult] = useState('');

  const load = useCallback(async () => {
    try {
      const [e, c] = await Promise.all([
        apiJSON<{ entries: PartEntry[] }>('/system/parts-directory'),
        apiJSON<{ candidates: Candidate[] }>('/system/parts-directory/candidates'),
      ]);
      setEntries(e.entries);
      setCandidates(c.candidates);
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

  const promote = (c: Candidate) =>
    act(`promote:${c.name_key}`, () =>
      apiJSON('/system/parts-directory/candidates/promote', {
        method: 'POST',
        body: JSON.stringify({ raw_key: c.name_key, ...promoteForm }),
      }).then(() => setPromoting(null)));

  const dismiss = (c: Candidate) =>
    act(`dismiss:${c.name_key}`, () =>
      apiJSON('/system/parts-directory/candidates/dismiss', {
        method: 'POST', body: JSON.stringify({ raw_key: c.name_key }),
      }));

  const saveEdit = (id: number) =>
    act(`edit:${id}`, () =>
      apiJSON(`/system/parts-directory/${id}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: draft.name, category: draft.category,
          part_number: draft.part_number, description: draft.description,
        }),
      }).then(() => setEditing(null)));

  const setStatus = (id: number, status: 'active' | 'archived') =>
    act(`status:${id}`, () =>
      apiJSON(`/system/parts-directory/${id}`, {
        method: 'PUT', body: JSON.stringify({ status }),
      }));

  const addAlias = (id: number) =>
    act(`alias:${id}`, () =>
      apiJSON(`/system/parts-directory/${id}/aliases`, {
        method: 'POST', body: JSON.stringify({ name: aliasDrafts[id] || '' }),
      }).then(() => setAliasDrafts((d) => ({ ...d, [id]: '' }))));

  const deleteAlias = (aliasId: number) =>
    act(`unalias:${aliasId}`, () =>
      apiJSON(`/system/parts-directory/aliases/${aliasId}`, { method: 'DELETE' }));

  const addEntry = () =>
    act('add', () =>
      apiJSON('/system/parts-directory', {
        method: 'POST', body: JSON.stringify(newForm),
      }).then(() => setNewForm({ name: '', category: '', part_number: '' })));

  const runImport = () =>
    act('import', async () => {
      const rows = importText.split('\n').map((l) => l.trim()).filter(Boolean)
        .map((l) => {
          const [name = '', category = '', part_number = '', description = ''] = l.split('\t');
          return { name: name.trim(), category: category.trim(), part_number: part_number.trim(), description: description.trim() };
        })
        .filter((r) => r.name);
      const res = await apiJSON<{ created: number; skipped: Array<{ name: string; reason: string }> }>(
        '/system/parts-directory/import',
        { method: 'POST', body: JSON.stringify({ rows }) },
      );
      setImportResult(`Created ${res.created} · skipped ${res.skipped.length}` +
        (res.skipped.length ? ` — ${res.skipped.slice(0, 5).map((s) => `${s.name}: ${s.reason}`).join('; ')}${res.skipped.length > 5 ? '…' : ''}` : ''));
      setImportText('');
    });

  const visible = entries.filter((e) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return e.name.toLowerCase().includes(q) || e.category.toLowerCase().includes(q)
      || e.part_number.toLowerCase().includes(q)
      || e.aliases.some((a) => a.name_key.includes(q));
  });

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Parts Directory</h1>
      <p className="text-sm text-slate-500 mb-5">
        Canonical public part identities. Promote only <span className="text-slate-300">specific,
        product-identifying</span> names — "Labor", "Shop Supplies" and friends mean something
        different at every shop; when in doubt, dismiss.
      </p>

      {err && <div className="mb-4 text-sm text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-3 py-2">{err}</div>}
      {loading && <p className="text-slate-500 text-sm">Loading…</p>}

      {/* ── Candidates: the cross-account curation signal ── */}
      {candidates.length > 0 && (
        <div className="mb-6 border border-amber-500/30 rounded-lg overflow-hidden">
          <div className="bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-300">
            Candidates — names used by 2+ companies, not yet curated ({candidates.length})
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-1.5 font-medium">Name (sample)</th>
                <th className="px-3 py-1.5 font-medium">Companies</th>
                <th className="px-3 py-1.5 font-medium">Catalog rows</th>
                <th className="px-3 py-1.5 font-medium">Invoice lines</th>
                <th className="px-3 py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <>
                  <tr key={c.name_key} className="border-b border-slate-800/60">
                    <td className="px-3 py-1.5 text-slate-200">{c.sample_name}</td>
                    <td className="px-3 py-1.5 text-slate-300 tabular-nums">{c.account_count}</td>
                    <td className="px-3 py-1.5 text-slate-400 tabular-nums">{c.catalog_rows}</td>
                    <td className="px-3 py-1.5 text-slate-400 tabular-nums">{c.usage_count}</td>
                    <td className="px-3 py-1.5 text-right whitespace-nowrap">
                      <button
                        className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 mr-2`}
                        onClick={() => {
                          setPromoting(promoting === c.name_key ? null : c.name_key);
                          setPromoteForm({ canonical_name: c.sample_name, category: '', part_number: '' });
                        }}
                      >
                        Promote…
                      </button>
                      <button
                        className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        disabled={busyKey === `dismiss:${c.name_key}`}
                        onClick={() => dismiss(c)}
                      >
                        Dismiss
                      </button>
                    </td>
                  </tr>
                  {promoting === c.name_key && (
                    <tr key={`${c.name_key}:form`} className="border-b border-slate-800/60 bg-slate-900/60">
                      <td colSpan={5} className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <input className={`${inputCls} max-w-xs`} placeholder="Canonical name"
                            value={promoteForm.canonical_name}
                            onChange={(e) => setPromoteForm((f) => ({ ...f, canonical_name: e.target.value }))} />
                          <input className={`${inputCls} max-w-[10rem]`} placeholder="Category"
                            value={promoteForm.category}
                            onChange={(e) => setPromoteForm((f) => ({ ...f, category: e.target.value }))} />
                          <input className={`${inputCls} max-w-[10rem]`} placeholder="Part #"
                            value={promoteForm.part_number}
                            onChange={(e) => setPromoteForm((f) => ({ ...f, part_number: e.target.value }))} />
                          <button
                            className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
                            disabled={!promoteForm.canonical_name.trim() || busyKey === `promote:${c.name_key}`}
                            onClick={() => promote(c)}
                          >
                            {busyKey === `promote:${c.name_key}` ? 'Promoting…' : 'Promote + adopt'}
                          </button>
                          <span className="text-xs text-slate-500">
                            Raw name becomes an alias; every company's matching parts link automatically.
                          </span>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Add + import ── */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input className={`${inputCls} max-w-xs`} placeholder="New canonical part name…"
          value={newForm.name} onChange={(e) => setNewForm((f) => ({ ...f, name: e.target.value }))} />
        <input className={`${inputCls} max-w-[10rem]`} placeholder="Category"
          value={newForm.category} onChange={(e) => setNewForm((f) => ({ ...f, category: e.target.value }))} />
        <input className={`${inputCls} max-w-[10rem]`} placeholder="Part #"
          value={newForm.part_number} onChange={(e) => setNewForm((f) => ({ ...f, part_number: e.target.value }))} />
        <button className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
          disabled={!newForm.name.trim() || busyKey === 'add'} onClick={addEntry}>
          Add entry
        </button>
        <button className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800 ml-auto`}
          onClick={() => setImportOpen((o) => !o)}>
          {importOpen ? 'Close import' : 'Bulk import'}
        </button>
      </div>
      {importOpen && (
        <div className="mb-4 border border-slate-800 rounded-lg p-3">
          <p className="text-xs text-slate-500 mb-2">
            One row per line, TAB-separated: <span className="font-mono text-slate-400">name → category → part # → description</span>.
            Generic names and duplicates are skipped per-row.
          </p>
          <textarea className={`${inputCls} h-32 font-mono`} value={importText}
            onChange={(e) => setImportText(e.target.value)} />
          <div className="mt-2 flex items-center gap-3">
            <button className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
              disabled={!importText.trim() || busyKey === 'import'} onClick={runImport}>
              {busyKey === 'import' ? 'Importing…' : 'Import rows'}
            </button>
            {importResult && <span className="text-xs text-slate-400">{importResult}</span>}
          </div>
        </div>
      )}

      {/* ── Entries ── */}
      <div className="mb-3 flex items-center gap-3">
        <input className={`${inputCls} max-w-sm`} placeholder="Search entries, categories, aliases…"
          value={search} onChange={(e) => { setSearch(e.target.value); setShownCount(100); }} />
        <span className="text-xs text-slate-500">{visible.length} of {entries.length}</span>
      </div>
      <table className="w-full text-sm border border-slate-800 rounded-lg overflow-hidden">
        <thead>
          <tr className="text-left text-slate-500 border-b border-slate-800 bg-slate-900/60">
            <th className="px-3 py-1.5 font-medium">ID</th>
            <th className="px-3 py-1.5 font-medium">Name</th>
            <th className="px-3 py-1.5 font-medium">Category</th>
            <th className="px-3 py-1.5 font-medium">Part #</th>
            <th className="px-3 py-1.5 font-medium">Linked</th>
            <th className="px-3 py-1.5 font-medium">Aliases</th>
            <th className="px-3 py-1.5 font-medium">Status</th>
            <th className="px-3 py-1.5 font-medium" />
          </tr>
        </thead>
        <tbody>
          {visible.slice(0, shownCount).map((e) => (
            <tr key={e.id} className="border-b border-slate-800/60 align-top">
              <td className="px-3 py-1.5 font-mono text-xs text-slate-500">#{e.id}</td>
              <td className="px-3 py-1.5 text-slate-200">
                {editing === e.id ? (
                  <input className={inputCls} value={draft.name ?? ''}
                    onChange={(ev) => setDraft((d) => ({ ...d, name: ev.target.value }))} />
                ) : e.name}
              </td>
              <td className="px-3 py-1.5 text-slate-400">
                {editing === e.id ? (
                  <input className={inputCls} value={draft.category ?? ''}
                    onChange={(ev) => setDraft((d) => ({ ...d, category: ev.target.value }))} />
                ) : (e.category || '—')}
              </td>
              <td className="px-3 py-1.5 font-mono text-xs text-slate-400">
                {editing === e.id ? (
                  <input className={inputCls} value={draft.part_number ?? ''}
                    onChange={(ev) => setDraft((d) => ({ ...d, part_number: ev.target.value }))} />
                ) : (e.part_number || '—')}
              </td>
              <td className="px-3 py-1.5 text-slate-300 tabular-nums">{e.linked_parts}</td>
              <td className="px-3 py-1.5">
                <div className="flex flex-wrap items-center gap-1">
                  {e.aliases.map((a) => (
                    <span key={a.id} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-slate-700 text-xs text-slate-400">
                      {a.name_key}
                      <button className="text-slate-600 hover:text-rose-400" aria-label={`Remove alias ${a.name_key}`}
                        onClick={() => deleteAlias(a.id)}>×</button>
                    </span>
                  ))}
                  <input className={`${inputCls} !w-28`} placeholder="+ alias"
                    value={aliasDrafts[e.id] ?? ''}
                    onChange={(ev) => setAliasDrafts((d) => ({ ...d, [e.id]: ev.target.value }))}
                    onKeyDown={(ev) => { if (ev.key === 'Enter' && (aliasDrafts[e.id] || '').trim()) addAlias(e.id); }} />
                </div>
              </td>
              <td className="px-3 py-1.5">
                <span className={`inline-flex px-1.5 py-0.5 rounded-full border text-xs ${STATUS_BADGE[e.status]}`}>{e.status}</span>
              </td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap">
                {editing === e.id ? (
                  <>
                    <button className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 mr-2`}
                      disabled={busyKey === `edit:${e.id}`} onClick={() => saveEdit(e.id)}>Save</button>
                    <button className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                      onClick={() => setEditing(null)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800 mr-2`}
                      onClick={() => { setEditing(e.id); setDraft(e); }}>Edit</button>
                    {e.status === 'active' ? (
                      <button className={`${btnCls} border-slate-700 text-slate-400 hover:bg-slate-800`}
                        disabled={busyKey === `status:${e.id}`}
                        onClick={() => setStatus(e.id, 'archived')}>Archive</button>
                    ) : (
                      <button className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
                        disabled={busyKey === `status:${e.id}`}
                        onClick={() => setStatus(e.id, 'active')}>Activate</button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {visible.length > shownCount && (
        <button className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800 mt-3`}
          onClick={() => setShownCount((n) => n + 200)}>
          Show more ({visible.length - shownCount} hidden)
        </button>
      )}
    </div>
  );
}
