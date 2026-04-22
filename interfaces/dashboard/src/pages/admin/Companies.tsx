import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import type { CompanyInfo, AnyColumn } from '../../types';

const columns: AnyColumn[] = [
  { key: 'code', label: 'Code', sortable: true },
  { key: 'display_name', label: 'Name', sortable: true },
  { key: 'active_days', label: 'Active Days', sortable: true },
  { key: 'has_api_key', label: 'API Key', render: (v) => v ? <span className="text-green-400">Connected</span> : <span className="text-gray-500">None</span> },
  { key: 'is_active', label: 'Status', render: (v) => v ? <span className="text-green-400">Active</span> : <span className="text-red-400">Inactive</span> },
  { key: 'created_at', label: 'Created', render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
];

export default function Companies() {
  const [companies, setCompanies] = useState<CompanyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<CompanyInfo | null>(null);

  // Form
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [days, setDays] = useState(30);
  const [saving, setSaving] = useState(false);

  // Edit form
  const [editName, setEditName] = useState('');
  const [editKey, setEditKey] = useState('');
  const [editDays, setEditDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiJSON<{ companies: CompanyInfo[] }>('/admin/companies');
      setCompanies(data.companies || []);
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true); setError('');
    try {
      await apiJSON('/admin/companies', { method: 'POST', body: { code, samsara_api_key: apiKey, display_name: name || code, active_days: days } });
      setShowAdd(false); setCode(''); setName(''); setApiKey(''); setDays(30);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleUpdate = async () => {
    if (!selected) return;
    setSaving(true); setError('');
    const body: Record<string, unknown> = {};
    if (editName && editName !== selected.display_name) body.display_name = editName;
    if (editKey) body.samsara_api_key = editKey;
    if (editDays !== selected.active_days) body.active_days = editDays;
    if (Object.keys(body).length === 0) { setSaving(false); return; }
    try {
      await apiJSON('/admin/companies/' + selected.id, { method: 'PUT', body });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleDeactivate = async (id: number) => {
    try {
      await apiJSON('/admin/companies/' + id, { method: 'DELETE' });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Companies</h1>
        <button onClick={() => { setShowAdd(!showAdd); setError(''); }} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition">
          {showAdd ? 'Cancel' : '+ Add Company'}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 grid grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Code</label>
            <input required value={code} onChange={e => setCode(e.target.value)} maxLength={20} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Display Name</label>
            <input value={name} onChange={e => setName(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Samsara API Key</label>
            <input required type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Active Days</label>
            <input type="number" min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium transition">
              {saving ? 'Saving...' : 'Add'}
            </button>
          </div>
        </form>
      )}

      {loading ? <p className="text-gray-500">Loading...</p> : (
        <DataTable columns={columns} data={companies as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => {
          const c = row as unknown as CompanyInfo;
          setSelected(c); setEditName(c.display_name); setEditKey(''); setEditDays(c.active_days);
        }} />
      )}

      {selected && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="w-96 bg-gray-900 border-l border-gray-800 p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{selected.display_name}</h2>
              <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white">✕</button>
            </div>
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-gray-400">Code</dt><dd>{selected.code}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-400">API Key</dt><dd>{selected.has_api_key ? 'Connected' : 'None'}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-400">Status</dt><dd>{selected.is_active ? 'Active' : 'Inactive'}</dd></div>
            </dl>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Display Name</label>
                <input value={editName} onChange={e => setEditName(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">New API Key (leave blank to keep)</label>
                <input type="password" value={editKey} onChange={e => setEditKey(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Active Days</label>
                <input type="number" min={1} max={365} value={editDays} onChange={e => setEditDays(Number(e.target.value))} className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <button onClick={handleUpdate} disabled={saving} className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded text-sm font-medium transition">
                {saving ? 'Saving...' : 'Update Company'}
              </button>
              {selected.is_active && (
                <button onClick={() => handleDeactivate(selected.id)} className="w-full py-2 bg-red-600/80 hover:bg-red-600 rounded text-sm font-medium transition">
                  Deactivate Company
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
