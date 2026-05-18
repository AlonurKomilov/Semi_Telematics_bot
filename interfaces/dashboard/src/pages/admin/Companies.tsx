import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Plus, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { CompanyInfo, AnyColumn } from '../../types';

const columns: AnyColumn[] = [
  { key: 'code', label: 'Code', sortable: true },
  { key: 'display_name', label: 'Name', sortable: true },
  { key: 'active_days', label: 'Active Days', sortable: true },
  { key: 'has_api_key', label: 'API Key', render: (v) => v ? <span className="text-green-600 dark:text-green-400">Connected</span> : <span className="text-muted-foreground">None</span> },
  { key: 'is_active', label: 'Status', render: (v) => v ? <span className="text-green-600 dark:text-green-400">Active</span> : <span className="text-destructive">Inactive</span> },
  { key: 'created_at', label: 'Created', render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
];

export default function Companies() {
  const { t } = useTranslation();
  const qc = useQueryClient();
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

  const { data, isLoading: loading, error: queryError } = useQuery({
    queryKey: ['admin-companies'],
    queryFn: () => apiJSON<{ companies: CompanyInfo[] }>('/admin/companies'),
  });
  const companies = data?.companies ?? [];
  const queryErrorMsg = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['admin-companies'] });

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
      <PageHeader
        icon={Building2}
        title={t('pages.companies_title')}
        description={t('pages.companies_desc')}
        actions={
          <button
            onClick={() => { setShowAdd(!showAdd); setError(''); }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
          >
            <Plus size={13} />
            {showAdd ? 'Cancel' : 'Add company'}
          </button>
        }
      />

      {(error || queryErrorMsg) && (
        <div className="mb-3"><ErrorState message={error || queryErrorMsg} /></div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-5 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Code</label>
            <input required value={code} onChange={e => setCode(e.target.value)} maxLength={20} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
            <input value={name} onChange={e => setName(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Samsara API Key</label>
            <input required type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Active Days</label>
            <input type="number" min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="px-4 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded text-sm font-medium text-foreground transition">
              {saving ? 'Saving...' : 'Add'}
            </button>
          </div>
        </form>
      )}

      {loading && companies.length === 0 ? (
        <TableSkeleton rows={6} cols={6} />
      ) : companies.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No companies yet"
          description="Add your first company to start syncing vehicles and drivers from Samsara."
          action={
            <button
              onClick={() => setShowAdd(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={13} />
              Add company
            </button>
          }
        />
      ) : (
        <DataTable columns={columns} data={companies as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => {
          const c = row as unknown as CompanyInfo;
          setSelected(c); setEditName(c.display_name); setEditKey(''); setEditDays(c.active_days);
        }} />
      )}

      {selected && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="w-96 bg-card border-l border-border p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{selected.display_name}</h2>
              <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X size={16} /></button>
            </div>
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-muted-foreground">Code</dt><dd>{selected.code}</dd></div>
              <div className="flex justify-between"><dt className="text-muted-foreground">API Key</dt><dd>{selected.has_api_key ? 'Connected' : 'None'}</dd></div>
              <div className="flex justify-between"><dt className="text-muted-foreground">Status</dt><dd>{selected.is_active ? 'Active' : 'Inactive'}</dd></div>
            </dl>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
                <input value={editName} onChange={e => setEditName(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">New API Key (leave blank to keep)</label>
                <input type="password" value={editKey} onChange={e => setEditKey(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Active Days</label>
                <input type="number" min={1} max={365} value={editDays} onChange={e => setEditDays(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
              </div>
              <button onClick={handleUpdate} disabled={saving} className="w-full py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition">
                {saving ? 'Saving...' : 'Update Company'}
              </button>
              {selected.is_active && (
                <button onClick={() => handleDeactivate(selected.id)} className="w-full py-2 bg-destructive/80 hover:bg-destructive rounded text-sm font-medium transition">
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
