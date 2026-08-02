import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Plus, KeyRound, Upload, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  Sheet, SheetContent, SheetBody, SheetHeader, SheetTitle,
} from '../../components/ui/sheet';
import { apiJSON, apiFetch } from '../../api/client';
import DataGrid from '../../components/datagrid';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { toneClasses } from '../../lib/status';
import type { CompanyInfo, AnyColumn } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

// Company logo (authed) — fetched as a blob since the serve endpoint
// needs the Bearer token (an <img src> can't carry it).
function CompanyLogo({ id, hasLogo, version = 0, size = 28 }: {
  id: number; hasLogo?: boolean; version?: number; size?: number;
}) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!hasLogo) { setUrl(''); return; }
    let dead = false; let made = '';
    apiFetch(`/admin/companies/${id}/logo`).then(async (res) => {
      if (!res.ok) return;
      const blob = await res.blob();
      if (dead) return;
      made = URL.createObjectURL(blob); setUrl(made);
    }).catch(() => { /* fall back to placeholder */ });
    return () => { dead = true; if (made) URL.revokeObjectURL(made); };
  }, [id, hasLogo, version]);
  if (url) return <img src={url} alt="" style={{ width: size, height: size }} className="rounded object-cover border border-border" />;
  return (
    <span style={{ width: size, height: size }} className="inline-flex items-center justify-center rounded border border-border bg-muted text-muted-foreground">
      <Building2 size={Math.round(size * 0.5)} />
    </span>
  );
}

// Columns no longer include the API Key — that lives on the
// Integrations > Samsara card now.  Companies is back to being a
// pure business-entity list (code, name, status, dates).
const makeColumns = (tz: string): AnyColumn[] => [
  { key: 'logo', label: '', render: (_v, row) => <CompanyLogo id={(row as CompanyInfo).id} hasLogo={(row as CompanyInfo).has_logo} /> },
  { key: 'code', label: 'Code', sortable: true },
  { key: 'display_name', label: 'Name', sortable: true },
  // MC / DOT are the federal carrier ids used to match synced records
  // (e.g. Datatruck work orders) to this company.
  { key: 'mc_number', label: 'MC #', render: (v) => v ? <span className="font-mono text-xs">{String(v)}</span> : <span className="text-muted-foreground">—</span> },
  { key: 'usdot_number', label: 'DOT #', render: (v) => v ? <span className="font-mono text-xs">{String(v)}</span> : <span className="text-muted-foreground">—</span> },
  // "Active window" = Samsara GPS-recency filter (a vehicle is active if
  // seen within N days) — NOT a data-sync window.  Labelled (GPS) to keep
  // it from reading like the Integration card's history-window picker.
  { key: 'active_days', label: 'Active window (GPS)', sortable: true, render: (v) => <span title="A vehicle counts as active if Samsara has seen it within this many days — not a data-sync setting.">{`${v} days`}</span> },
  { key: 'is_active', label: 'Status', render: (v) => v ? <span className="text-ok">Active</span> : <span className="text-danger">Inactive</span> },
  { key: 'created_at', label: 'Created', render: (v) => v ? formatDay(String(v), { timeZone: tz }) : '—' },
];

export default function Companies() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const qc = useQueryClient();
  const columns = makeColumns(tz);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<CompanyInfo | null>(null);

  // Add form — no API key field; keys are entered on the Integrations
  // page once the company exists.
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [days, setDays] = useState(30);
  const [mc, setMc] = useState('');
  const [dot, setDot] = useState('');
  const [saving, setSaving] = useState(false);

  const [editName, setEditName] = useState('');
  const [editDays, setEditDays] = useState(30);
  const [editMc, setEditMc] = useState('');
  const [editDot, setEditDot] = useState('');
  // Brand/identity (edit drawer)
  const [editColor, setEditColor] = useState('');
  const [editWebsite, setEditWebsite] = useState('');
  const [editPhone, setEditPhone] = useState('');
  const [logoBusy, setLogoBusy] = useState(false);
  const [logoVersion, setLogoVersion] = useState(0);  // bump to refresh the <img> after up/remove

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
      await apiJSON('/admin/companies', { method: 'POST', body: { code, display_name: name || code, active_days: days, mc_number: mc.trim(), usdot_number: dot.trim() } });
      setShowAdd(false); setCode(''); setName(''); setDays(30); setMc(''); setDot('');
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
    if (editMc.trim() !== (selected.mc_number || '')) body.mc_number = editMc.trim();
    if (editDot.trim() !== (selected.usdot_number || '')) body.usdot_number = editDot.trim();
    if (editColor !== (selected.brand_color || '')) body.brand_color = editColor;
    if (editWebsite.trim() !== (selected.website || '')) body.website = editWebsite.trim();
    if (editPhone.trim() !== (selected.phone || '')) body.phone = editPhone.trim();
    if (Object.keys(body).length === 0) { setSaving(false); return; }
    try {
      await apiJSON('/admin/companies/' + selected.id, { method: 'PUT', body });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const uploadLogo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f || !selected) return;
    setLogoBusy(true); setError('');
    try {
      const fd = new FormData(); fd.append('file', f);
      const res = await apiFetch(`/admin/companies/${selected.id}/logo`, { method: 'POST', body: fd });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        setError(j.detail || 'Logo upload failed');
      } else {
        setSelected((s) => (s ? { ...s, has_logo: true } : s));
        setLogoVersion((v) => v + 1); load();
      }
    } finally { setLogoBusy(false); }
  };

  const removeLogo = async () => {
    if (!selected) return;
    await apiFetch(`/admin/companies/${selected.id}/logo`, { method: 'DELETE' }).catch(() => {});
    setSelected((s) => (s ? { ...s, has_logo: false } : s));
    setLogoVersion((v) => v + 1); load();
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
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Code</label>
            <input required value={code} onChange={e => setCode(e.target.value)} maxLength={20} placeholder="PTG" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
            <p className="mt-1 text-2xs text-muted-foreground">Short label used in reports and alerts — e.g. PTG, OSY.</p>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Display Name</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="Acme Trucking Inc" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
            <p className="mt-1 text-2xs text-muted-foreground">The company's full legal or trading name.</p>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Active window (GPS)</label>
            <input type="number" min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
            <p className="mt-1 text-2xs text-muted-foreground">A vehicle is "active" if Samsara saw it within this many days — not a data-sync window.</p>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">MC Number</label>
            <input value={mc} onChange={e => setMc(e.target.value)} maxLength={40} placeholder="123456" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
            <p className="mt-1 text-2xs text-muted-foreground">Federal MC number — matches synced records to this company.</p>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">USDOT Number</label>
            <input value={dot} onChange={e => setDot(e.target.value)} maxLength={40} placeholder="1234567" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
            <p className="mt-1 text-2xs text-muted-foreground">USDOT number — immutable id, reusable across integrations.</p>
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
        <DataGrid tableId="companies" columns={columns} data={companies as unknown as Record<string, unknown>[]} searchKey="display_name" onRowClick={(row) => {
          const c = row as unknown as CompanyInfo;
          setSelected(c); setEditName(c.display_name); setEditDays(c.active_days);
          setEditMc(c.mc_number || ''); setEditDot(c.usdot_number || '');
          setEditColor(c.brand_color || ''); setEditWebsite(c.website || ''); setEditPhone(c.phone || '');
        }} />
      )}

      {/* Was a hand-rolled backdrop + panel — no focus trap, no Escape,
          no ``aria-modal``, and no background scroll lock, so the page
          kept scrolling under the open drawer.  The PANEL scrolled too,
          taking the company name off screen with it.  <Sheet> brings the
          modal semantics; <SheetBody> makes the body a real scroll
          region and leaves the header in place. */}
      <Sheet open={!!selected} onOpenChange={(o) => { if (!o) setSelected(null); }}>
        <SheetContent side="right" className="p-0"
        size="lg">
          {selected && (
          <>
            <SheetHeader className="px-6 pt-6">
              <SheetTitle>{selected.display_name}</SheetTitle>
            </SheetHeader>
            <SheetBody label={`${selected.display_name} details`} className="px-6 pb-6">
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
                <input value={editName} onChange={e => setEditName(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Active window (GPS)</label>
                <input type="number" min={1} max={365} value={editDays} onChange={e => setEditDays(Number(e.target.value))} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">MC Number</label>
                <input value={editMc} onChange={e => setEditMc(e.target.value)} maxLength={40} placeholder="123456" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">USDOT Number</label>
                <input value={editDot} onChange={e => setEditDot(e.target.value)} maxLength={40} placeholder="1234567" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
              </div>

              {/* ── Brand (shown on the public application form + DQ packet) ── */}
              <div className="pt-3 border-t border-border">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Brand · application form</p>
                <div className="flex items-center gap-3 mb-3">
                  <CompanyLogo id={selected.id} hasLogo={selected.has_logo} version={logoVersion} size={48} />
                  <div className="flex flex-wrap gap-2">
                    <label className="cursor-pointer inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-muted">
                      <Upload size={12} /> {logoBusy ? 'Uploading…' : 'Upload logo'}
                      <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={uploadLogo} />
                    </label>
                    {selected.has_logo && (
                      <button onClick={removeLogo} className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-destructive">
                        <Trash2 size={12} /> Remove
                      </button>
                    )}
                  </div>
                </div>
                <p className="mb-3 text-2xs text-muted-foreground">PNG, JPG, or WEBP · up to 2 MB.</p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-muted-foreground mb-1">Brand color</label>
                    <div className="flex items-center gap-2">
                      <input type="color" value={editColor || '#2563eb'} onChange={e => setEditColor(e.target.value)} className="h-8 w-9 shrink-0 rounded border border-border bg-transparent p-0.5" />
                      <input value={editColor} onChange={e => setEditColor(e.target.value)} placeholder="#2563EB" maxLength={7} className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm font-mono text-foreground focus:outline-none focus:border-ring" />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs text-muted-foreground mb-1">Phone</label>
                    <input value={editPhone} onChange={e => setEditPhone(e.target.value)} maxLength={40} placeholder="(555) 123-4567" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-xs text-muted-foreground mb-1">Website</label>
                    <input value={editWebsite} onChange={e => setEditWebsite(e.target.value)} maxLength={200} placeholder="https://yourcompany.com" className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring" />
                  </div>
                </div>
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
            </SheetBody>
          </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
