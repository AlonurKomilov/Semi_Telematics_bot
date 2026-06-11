import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Plus, X, KeyRound } from 'lucide-react';
import { Link } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import DataTable from '../../components/DataTable';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { toneClasses } from '../../lib/status';
import type { CompanyInfo, AnyColumn } from '../../types';

// Columns no longer include the API Key — that lives on the
// Integrations > Samsara card now.  Companies is back to being a
// pure business-entity list (code, name, status, dates).
const columns: AnyColumn[] = [
  { key: 'code', label: 'Code', sortable: true },
  { key: 'display_name', label: 'Name', sortable: true },
  { key: 'active_days', label: 'Active Days', sortable: true },
  { key: 'is_active', label: 'Status', render: (v) => v ? <span className="text-ok">Active</span> : <span className="text-danger">Inactive</span> },
  { key: 'created_at', label: 'Created', render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
];

export default function Companies() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<CompanyInfo | null>(null);

  // Add form — no API key field; keys are entered on the Integrations
  // page once the company exists.
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [days, setDays] = useState(30);
  const [saving, setSaving] = useState(false);

  const [editName, setEditName] = useState('');
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
      // samsara_api_key intentionally omitted — the new flow is
      // "create the company, then set its key on the Integration
      // card."  The backend defaults the field to empty string.
      await apiJSON('/admin/companies', { method: 'POST', body: { code, display_name: name || code, active_days: days } });
      setShowAdd(false); setCode(''); setName(''); setDays(30);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleUpdate = async () => {
    if (!selected) return;
    setSaving(true); setError('');
    const body: Record<string, unknown> = {};
    if (editName && editName !== selected.display_name) body.display_name = editName;
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
            <Plus size={14} />
            {showAdd ? 'Cancel' : 'Add company'}
          </button>
        }
      />

      {/* Locator banner — operators looking for the API Key column
          used to land here; the field moved to the Integrations card. */}
      <div className={`${toneClasses('info')} mb-4 rounded-md px-3 py-2 text-xs flex items-center gap-2`}>
        <KeyRound size={14} />
        <span>
          Samsara API keys are now managed under{' '}
          <Link to="/integrations" className="underline font-medium">
            Integrations &rsaquo; Samsara
          </Link>
          {' '}— add a company here, then set its key on the integration card.
        </span>
      </div>

      {(error || queryErrorMsg) && (
        <div className="mb-3"><ErrorState message={error || queryErrorMsg} /></div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Code</label>
            <input required value={code} onChange={e => setCode(e.target.value)} maxLength={20} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
            <input value={name} onChange={e => setName(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Active Days</label>
            <input type="number" min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="px-4 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition">
              {saving ? 'Saving...' : 'Add'}
            </button>
          </div>
        </form>
      )}

      {loading && companies.length === 0 ? (
        <TableSkeleton rows={6} cols={5} />
      ) : companies.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No companies yet"
          description="Add your first company, then set its Samsara API key on the Integrations page."
          action={
            <button
              onClick={() => setShowAdd(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={14} />
              Add company
            </button>
          }
        />
      ) : (
        <DataTable columns={columns} data={companies as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => {
          const c = row as unknown as CompanyInfo;
          setSelected(c); setEditName(c.display_name); setEditDays(c.active_days);
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
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Samsara key</dt>
                <dd>
                  {selected.has_api_key ? (
                    <span className="text-ok">Set</span>
                  ) : (
                    <Link to="/integrations" className="text-primary underline">
                      Set on Integrations →
                    </Link>
                  )}
                </dd>
              </div>
              <div className="flex justify-between"><dt className="text-muted-foreground">Status</dt><dd>{selected.is_active ? 'Active' : 'Inactive'}</dd></div>
            </dl>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
                <input value={editName} onChange={e => setEditName(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring" />
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
