import { useEffect, useState, useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';
import { UserPlus, Link as LinkIcon, Copy, Check, Ban, X, FileText, ExternalLink, Bell, Mail, MessageSquare, Monitor, CheckCheck, Download, ShieldCheck, LayoutGrid, List, Users, Search, ArrowUp, ArrowDown, ChevronsUpDown, Building2, Upload, Pencil, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON, apiFetch } from '../../api/client';
import { PageHeader } from '../../components/shell';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { statusClasses, toneClasses } from '../../lib/status';
import { APEX_DOMAIN } from '../../lib/safeReturnTo';
import { formatDate } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';

// Public applicant link host — the form lives on apply.<apex> (its own
// subdomain by product decision).  Recruiters copy /<token> onto it.
const APPLY_BASE = `https://apply.${APEX_DOMAIN}`;

const STATUSES = [
  'submitted', 'screening', 'interview', 'approved', 'rejected', 'withdrawn', 'hired',
] as const;

interface ApplicationLink {
  id: number; token: string; label: string; source: string; is_active: number;
  created_at: string;
  /** ISO auto-close timestamp; null → never expires. */
  expires_at?: string | null;
  /** Per-link funnel stats. */
  view_count?: number; submissions?: number; hires?: number;
  /** Carrier this link brands for (null → generic). */
  company_id?: number | null; company_code?: string | null; company_name?: string | null;
}

interface PickerCompany {
  id: number; code: string; display_name: string; has_logo: boolean; brand_color: string;
  // Cosmetic brand the recruiter can edit + the owner-managed identity (read-only).
  website: string; phone: string; mc_number: string; usdot_number: string;
  // Apply-form content (recruiter-editable).
  headline: string; perks: string; has_banner: boolean;
  // Per-carrier pre-qual gate thresholds.
  req_experience_years: number; req_min_age: number; req_cdl_class: string;
  // Apply-form base theme: 'light' | 'dark'.
  form_theme: string;
}

// Authed carrier logo for the create-link preview (an <img src> can't carry
// the Bearer token, so we fetch the bytes and object-URL them).
function LinkCompanyLogo({ id, hasLogo, version = 0, size = 48 }: {
  id: number; hasLogo?: boolean; version?: number; size?: number;
}) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!hasLogo) { setUrl(''); return; }
    let dead = false; let made = '';
    apiFetch(`/applications/companies/${id}/logo`).then(async (res) => {
      if (!res.ok) return;
      const blob = await res.blob();
      if (dead) return;
      made = URL.createObjectURL(blob); setUrl(made);
    }).catch(() => { /* placeholder */ });
    return () => { dead = true; if (made) URL.revokeObjectURL(made); };
  }, [id, hasLogo, version]);
  if (url) return <img src={url} alt="" style={{ width: size, height: size }} className="rounded object-contain border border-border bg-card" />;
  return (
    <span style={{ width: size, height: size }} className="inline-flex items-center justify-center rounded border border-border bg-muted text-muted-foreground">
      <Building2 size={Math.round(size * 0.5)} />
    </span>
  );
}

// Authed carrier hero photo for the create-link preview.
function LinkCompanyBanner({ id, version = 0 }: { id: number; version?: number }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    let dead = false; let made = '';
    apiFetch(`/applications/companies/${id}/banner`).then(async (res) => {
      if (!res.ok) return;
      const blob = await res.blob();
      if (dead) return;
      made = URL.createObjectURL(blob); setUrl(made);
    }).catch(() => { /* none */ });
    return () => { dead = true; if (made) URL.revokeObjectURL(made); };
  }, [id, version]);
  if (!url) return null;
  return <img src={url} alt="" className="mt-2 h-28 w-full rounded-md border border-border object-cover" />;
}

// Preview + cosmetic touch-up for the carrier a link is branded for.  The
// recruiter can fix the logo / accent / contact / pitch WITHOUT owner access
// to Settings·Companies; the name + MC/DOT stay owner-managed (read-only).
// Accent colour + light/dark base are edited live INSIDE the preview (see
// PreviewThemeBar) — this panel owns the content brand (logo / contact /
// pitch / photo / pre-qual requirements).
function CompanyBrandPanel({ company, onChanged }: { company: PickerCompany; onChanged: () => void }) {
  const [website, setWebsite] = useState(company.website || '');
  const [phone, setPhone] = useState(company.phone || '');
  const [headline, setHeadline] = useState(company.headline || '');
  const [perks, setPerks] = useState(company.perks || '');
  const [reqYears, setReqYears] = useState(company.req_experience_years ?? 1);
  const [reqAge, setReqAge] = useState(company.req_min_age ?? 21);
  const [reqClass, setReqClass] = useState(company.req_cdl_class || 'A');
  const [busy, setBusy] = useState(false);
  const [logoBusy, setLogoBusy] = useState(false);
  const [logoVersion, setLogoVersion] = useState(0);
  const [bannerBusy, setBannerBusy] = useState(false);
  const [bannerVersion, setBannerVersion] = useState(0);
  useEffect(() => {
    setWebsite(company.website || ''); setPhone(company.phone || '');
    setHeadline(company.headline || ''); setPerks(company.perks || '');
    setReqYears(company.req_experience_years ?? 1); setReqAge(company.req_min_age ?? 21);
    setReqClass(company.req_cdl_class || 'A');
    setLogoVersion(0); setBannerVersion(0);
  }, [company.id, company.website, company.phone, company.headline, company.perks,
      company.req_experience_years, company.req_min_age, company.req_cdl_class]);

  const dirty = website !== (company.website || '')
    || phone !== (company.phone || '') || headline !== (company.headline || '') || perks !== (company.perks || '')
    || reqYears !== (company.req_experience_years ?? 1) || reqAge !== (company.req_min_age ?? 21)
    || reqClass !== (company.req_cdl_class || 'A');

  const saveBrand = async () => {
    setBusy(true);
    try {
      await apiJSON(`/applications/companies/${company.id}/brand`, {
        method: 'PATCH',
        body: { website, phone, headline, perks,
                req_experience_years: reqYears, req_min_age: reqAge, req_cdl_class: reqClass },
      });
      toast.success('Brand updated');
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Update failed'); }
    finally { setBusy(false); }
  };
  const uploadBanner = async (f: File) => {
    setBannerBusy(true);
    try {
      const fd = new FormData(); fd.append('file', f);
      const res = await apiFetch(`/applications/companies/${company.id}/banner`, { method: 'POST', body: fd });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || 'Upload failed'); }
      toast.success('Photo updated'); setBannerVersion((v) => v + 1); onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Upload failed'); }
    finally { setBannerBusy(false); }
  };
  const removeBanner = async () => {
    setBannerBusy(true);
    try {
      await apiFetch(`/applications/companies/${company.id}/banner`, { method: 'DELETE' });
      setBannerVersion((v) => v + 1); onChanged();
    } catch { /* non-fatal */ } finally { setBannerBusy(false); }
  };
  const uploadLogo = async (f: File) => {
    setLogoBusy(true);
    try {
      const fd = new FormData(); fd.append('file', f);
      const res = await apiFetch(`/applications/companies/${company.id}/logo`, { method: 'POST', body: fd });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || 'Upload failed'); }
      toast.success('Logo updated'); setLogoVersion((v) => v + 1); onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Upload failed'); }
    finally { setLogoBusy(false); }
  };
  const removeLogo = async () => {
    setLogoBusy(true);
    try {
      await apiFetch(`/applications/companies/${company.id}/logo`, { method: 'DELETE' });
      setLogoVersion((v) => v + 1); onChanged();
    } catch { /* non-fatal */ } finally { setLogoBusy(false); }
  };

  return (
    <div className="mt-3 rounded-md border border-border bg-background/40 p-4">
      <div className="flex items-start gap-4">
        <LinkCompanyLogo id={company.id} hasLogo={company.has_logo} version={logoVersion} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{company.display_name || company.code}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {company.mc_number ? `MC ${company.mc_number}` : 'MC —'} · {company.usdot_number ? `USDOT ${company.usdot_number}` : 'USDOT —'}
            <span className="ml-1 opacity-70">· name &amp; MC/DOT managed by owner</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="cursor-pointer">
              <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                disabled={logoBusy}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadLogo(f); e.currentTarget.value = ''; }} />
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-muted px-2.5 py-1.5 text-xs text-foreground hover:bg-muted/70">
                <Upload size={13} /> {company.has_logo ? 'Replace logo' : 'Upload logo'}
              </span>
            </label>
            {company.has_logo && (
              <button type="button" onClick={removeLogo} disabled={logoBusy}
                className="text-xs text-muted-foreground hover:text-danger">Remove</button>
            )}
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Input placeholder="Website" value={website} onChange={(e) => setWebsite(e.target.value)} />
        <Input placeholder="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2">
        <Input placeholder="Headline — e.g. Top pay. Home weekly. Modern trucks."
          value={headline} maxLength={140} onChange={(e) => setHeadline(e.target.value)} />
        <Textarea placeholder="Perks — one per line (e.g. $0.65/mile · Home weekends · 2022+ trucks · Full benefits)"
          value={perks} rows={3} maxLength={800} onChange={(e) => setPerks(e.target.value)} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Hero photo</span>
        <label className="cursor-pointer">
          <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={bannerBusy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadBanner(f); e.currentTarget.value = ''; }} />
          <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-muted px-2.5 py-1.5 text-xs text-foreground hover:bg-muted/70">
            <Upload size={13} /> {company.has_banner ? 'Replace photo' : 'Add photo'}
          </span>
        </label>
        {company.has_banner && (
          <button type="button" onClick={removeBanner} disabled={bannerBusy}
            className="text-xs text-muted-foreground hover:text-danger">Remove</button>
        )}
      </div>
      {company.has_banner && <LinkCompanyBanner id={company.id} version={bannerVersion} />}
      <div className="mt-3 border-t border-border pt-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Pre-qual requirements</p>
        <p className="mt-0.5 text-2xs text-muted-foreground">Adapts the apply form's eligibility questions for this carrier.</p>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
            Min experience (yrs)
            <input type="number" min={0} max={20} value={reqYears}
              onChange={(e) => setReqYears(Math.max(0, Math.min(20, Number(e.target.value) || 0)))}
              className="w-14 rounded border border-border bg-muted px-1.5 py-1 text-right text-foreground" />
          </label>
          <label className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
            Min age
            <input type="number" min={18} max={99} value={reqAge}
              onChange={(e) => setReqAge(Math.max(18, Math.min(99, Number(e.target.value) || 18)))}
              className="w-14 rounded border border-border bg-muted px-1.5 py-1 text-right text-foreground" />
          </label>
          <label className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
            CDL class
            <select value={reqClass} onChange={(e) => setReqClass(e.target.value)}
              className="rounded border border-border bg-muted px-1.5 py-1 text-foreground">
              {['A', 'B', 'C'].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <a href={`/applications/preview/${company.id}`} target="_blank" rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline">
          <ExternalLink size={13} /> Preview application
        </a>
        <Button size="sm" onClick={saveBrand} disabled={busy || !dirty}>{busy ? '…' : 'Save'}</Button>
      </div>
    </div>
  );
}

// Expiry choices for a new link.  Default 90 days so links don't live
// forever by accident; 'Never' is an explicit opt-in.
const EXPIRY_OPTIONS: { label: string; days: number }[] = [
  { label: 'Expires in 30 days', days: 30 },
  { label: 'Expires in 90 days', days: 90 },
  { label: 'Expires in 1 year', days: 365 },
  { label: 'Never expires', days: 0 },
];
interface AppRow {
  id: number; reference: string; status: string; first_name: string; last_name: string;
  email: string; phone: string; city: string; state: string; cdl_state: string;
  cdl_class: string; position_type: string; years_cdl: string; submitted_at: string;
  /** Another application in this account shares SSN/email/phone. */
  duplicate?: boolean;
}

interface RelatedApp {
  id: number; reference: string; status: string;
  first_name: string; last_name: string; submitted_at: string;
}

// Inline editor for an existing link — label / source / carrier / expiry.
// Expiry defaults to "keep" so saving other fields never silently resets
// the auto-close window; choosing a window re-bases it from now.
function LinkEditPanel({ link, companies, onSaved, onCancel, onCompaniesChanged }: {
  link: ApplicationLink; companies: PickerCompany[]; onSaved: () => void; onCancel: () => void;
  onCompaniesChanged: () => void;
}) {
  const [label, setLabel] = useState(link.label || '');
  const [source, setSource] = useState(link.source || '');
  const [companyId, setCompanyId] = useState(link.company_id ? String(link.company_id) : '');
  const [expiry, setExpiry] = useState('keep');
  const [busy, setBusy] = useState(false);
  const sel = companyId ? companies.find((c) => String(c.id) === companyId) : null;
  const save = async () => {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { label, source, company_id: companyId ? Number(companyId) : null };
      if (expiry !== 'keep') body.expires_in_days = Number(expiry);
      await apiJSON(`/applications/links/${link.id}`, { method: 'PATCH', body });
      toast.success('Link updated');
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 rounded-md border border-border bg-background/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="max-w-xs" />
        <Input placeholder="Source" value={source} onChange={(e) => setSource(e.target.value)} className="max-w-[10rem]" />
        {companies.length > 0 && (
          <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}
            title="Carrier this link brands for"
            className="bg-muted border border-border rounded-md px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring">
            <option value="">No company (generic)</option>
            {companies.map((co) => <option key={co.id} value={co.id}>{co.display_name || co.code}</option>)}
          </select>
        )}
        <select value={expiry} onChange={(e) => setExpiry(e.target.value)}
          className="bg-muted border border-border rounded-md px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring">
          <option value="keep">Keep expiry</option>
          {EXPIRY_OPTIONS.map((o) => <option key={o.days} value={o.days}>Reset · {o.label}</option>)}
        </select>
        <Button size="sm" onClick={save} disabled={busy}>{busy ? '…' : 'Save'}</Button>
        <button type="button" onClick={onCancel} className="text-xs text-muted-foreground hover:text-foreground">Cancel</button>
      </div>
      {/* Same carrier brand + requirements + preview the create flow shows. */}
      {sel && <CompanyBrandPanel company={sel} onChanged={onCompaniesChanged} />}
    </div>
  );
}

export default function Applications() {
  const [links, setLinks] = useState<ApplicationLink[]>([]);
  const [rows, setRows] = useState<AppRow[]>([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [view, setView] = useState<'table' | 'board'>('table');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ key: 'name' | 'status' | 'submitted'; dir: 'asc' | 'desc' }>({ key: 'submitted', dir: 'desc' });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [openId, setOpenId] = useState<number | null>(null);
  const tz = useTimezone();
  // Link create form
  const [label, setLabel] = useState('');
  const [source, setSource] = useState('');
  const [expiryDays, setExpiryDays] = useState(90);  // default: not forever
  const [companyId, setCompanyId] = useState('');   // '' → generic/no brand
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  // Carriers the recruiter can brand a link with (read-only picker).
  const [companies, setCompanies] = useState<PickerCompany[]>([]);

  // Load ALL applications once; the table filters client-side and the
  // board groups by status — both need the full set.
  const loadApps = useCallback(() => {
    apiJSON<{ items: AppRow[] }>('/applications?limit=500')
      .then((r) => setRows(r.items))
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  // Drag-to-stage on the board (and any status move): optimistic, with a
  // revert + message if the server rejects it (illegal jump, or the
  // vetting gate blocking 'approved').
  const moveApp = async (id: number, status: string) => {
    const prev = rows;
    if (prev.find((r) => r.id === id)?.status === status) return;
    setRows(prev.map((r) => (r.id === id ? { ...r, status } : r)));
    try {
      await apiJSON(`/applications/${id}/status`, { method: 'PATCH', body: { status } });
    } catch (e) {
      setRows(prev);
      alert(e instanceof Error ? e.message : 'Could not move application');
    }
  };

  // Table rows: status filter → text search → sort (all client-side over
  // the full set already loaded).
  const tableRows = useMemo(() => {
    let rs = statusFilter ? rows.filter((r) => r.status === statusFilter) : rows;
    const q = search.trim().toLowerCase();
    if (q) rs = rs.filter((r) => `${r.first_name} ${r.last_name} ${r.email} ${r.reference}`.toLowerCase().includes(q));
    const key = (r: AppRow) =>
      sort.key === 'name' ? `${r.last_name} ${r.first_name}`.toLowerCase()
        : sort.key === 'status' ? r.status
        : (r.submitted_at || '');
    rs = [...rs].sort((a, b) => {
      const av = key(a), bv = key(b);
      const c = av < bv ? -1 : av > bv ? 1 : 0;
      return sort.dir === 'asc' ? c : -c;
    });
    return rs;
  }, [rows, statusFilter, search, sort]);

  const toggleSort = (key: 'name' | 'status' | 'submitted') =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }));

  const toggleRow = (id: number) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allVisibleSelected = tableRows.length > 0 && tableRows.every((r) => selected.has(r.id));
  const toggleAll = () =>
    setSelected((s) => {
      const n = new Set(s);
      if (allVisibleSelected) tableRows.forEach((r) => n.delete(r.id));
      else tableRows.forEach((r) => n.add(r.id));
      return n;
    });

  // Bulk move: the server enforces the per-app rules (illegal jumps, the
  // vetting gate, hired-only-via-Hire) and tells us what it skipped.
  const bulkAction = async (status: string, label: string) => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!confirm(`${label} ${ids.length} application${ids.length > 1 ? 's' : ''}?`)) return;
    try {
      const r = await apiJSON<{ updated: number[]; skipped: { id: number; reason: string }[] }>(
        '/applications/bulk-status', { method: 'POST', body: { ids, status } });
      const skipped = r.skipped?.length ?? 0;
      if (skipped) toast.warning(`${r.updated.length} updated · ${skipped} skipped (${r.skipped[0].reason})`);
      else toast.success(`${r.updated.length} application${r.updated.length > 1 ? 's' : ''} ${label.toLowerCase()}d`);
      setSelected(new Set());
      loadApps();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk action failed');
    }
  };

  // Sortable column header.
  const sortTh = (k: 'name' | 'status' | 'submitted', labelNode: ReactNode) => (
    <th className="px-3 py-2 text-left">
      <button onClick={() => toggleSort(k)} className="inline-flex items-center gap-1 hover:text-foreground">
        {labelNode}
        {sort.key === k
          ? (sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)
          : <ChevronsUpDown size={11} className="opacity-40" />}
      </button>
    </th>
  );

  const loadLinks = useCallback(() => {
    apiJSON<{ items: ApplicationLink[] }>('/applications/links')
      .then((r) => setLinks(r.items))
      .catch(() => { /* non-fatal */ });
  }, []);

  useEffect(() => { loadApps(); }, [loadApps]);
  useEffect(() => { loadLinks(); }, [loadLinks]);
  // Carriers for the link company-picker + brand preview (no Samsara key).
  const loadCompanies = useCallback(() => {
    apiJSON<{ items: PickerCompany[] }>('/applications/companies')
      .then((r) => setCompanies(r.items))
      .catch(() => { /* non-fatal — picker just shows 'No company' */ });
  }, []);
  useEffect(() => { loadCompanies(); }, [loadCompanies]);

  const createLink = async () => {
    setCreating(true);
    try {
      await apiJSON('/applications/links', {
        method: 'POST',
        body: { label, source, expires_in_days: expiryDays || null, company_id: companyId ? Number(companyId) : null },
      });
      setLabel(''); setSource(''); setCompanyId('');
      loadLinks();
    } finally {
      setCreating(false);
    }
  };

  const [editingLink, setEditingLink] = useState<number | null>(null);

  const revokeLink = async (id: number) => {
    if (!confirm('Revoke this link? Applicants can no longer use it.')) return;
    await apiJSON(`/applications/links/${id}/revoke`, { method: 'POST' });
    loadLinks();
  };

  const deleteLink = async (id: number) => {
    if (!confirm('Delete this link permanently? Submitted applications are kept; only the link + its stats are removed.')) return;
    await apiJSON(`/applications/links/${id}`, { method: 'DELETE' });
    loadLinks();
  };

  const copyLink = (l: ApplicationLink) => {
    navigator.clipboard?.writeText(`${APPLY_BASE}/${l.token}`);
    setCopied(l.id);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Driver Applications" icon={UserPlus}
        description="Application links + submitted driver applications."
        actions={<NotificationsBell onOpen={(id) => setOpenId(id)} />} />

      {/* ── Application links ──────────────────────────────────── */}
      <section className="bg-card border border-border rounded-lg p-4">
        <h2 className="text-base font-semibold flex items-center gap-2 mb-3">
          <LinkIcon size={16} className="text-muted-foreground" /> Application Links
        </h2>
        <div className="flex flex-wrap gap-2 mb-4">
          <Input placeholder="Label (e.g. Indeed campaign)" value={label}
            onChange={(e) => setLabel(e.target.value)} className="max-w-xs" />
          <Input placeholder="Source (optional)" value={source}
            onChange={(e) => setSource(e.target.value)} className="max-w-[10rem]" />
          {companies.length > 0 && (
            <select
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              title="Brand the application form for this carrier"
              className="bg-muted border border-border rounded-md px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            >
              <option value="">No company (generic)</option>
              {companies.map((co) => (
                <option key={co.id} value={co.id}>{co.display_name || co.code}</option>
              ))}
            </select>
          )}
          <select
            value={expiryDays}
            onChange={(e) => setExpiryDays(Number(e.target.value))}
            className="bg-muted border border-border rounded-md px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
          >
            {EXPIRY_OPTIONS.map((o) => (
              <option key={o.days} value={o.days}>{o.label}</option>
            ))}
          </select>
          <Button onClick={createLink} disabled={creating} size="sm">
            {creating ? '…' : 'Create link'}
          </Button>
        </div>
        {companyId && (() => {
          const sel = companies.find((c) => String(c.id) === companyId);
          return sel ? (
            <div className="mb-4">
              <p className="text-xs text-muted-foreground">This link will be branded for — review &amp; fix the carrier's look before creating:</p>
              <CompanyBrandPanel company={sel} onChanged={loadCompanies} />
            </div>
          ) : null;
        })()}
        {links.length === 0 ? (
          <p className="text-sm text-muted-foreground">No links yet. Create one to start collecting applications.</p>
        ) : (
          <ul className="space-y-1.5">
            {links.map((l) => {
              const expired = !!l.expires_at
                && new Date(l.expires_at).getTime() < Date.now();
              const live = l.is_active === 1 && !expired;
              return (
              <li key={l.id} className="text-sm">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    l.is_active !== 1 ? statusClasses('disabled')
                    : expired ? statusClasses('disabled')
                    : statusClasses('active')}`}>
                    {l.is_active !== 1 ? 'revoked' : expired ? 'expired' : 'active'}
                  </span>
                  {(l.company_name || l.company_code) && (
                    <span className={`rounded px-1.5 py-0.5 text-2xs ${toneClasses('info')}`}
                      title="This link brands the application form for this carrier">
                      {l.company_name || l.company_code}
                    </span>
                  )}
                  <span className="font-medium">{l.label || '(no label)'}</span>
                  {l.source && <span className="text-muted-foreground text-xs">· {l.source}</span>}
                  <code className="text-2xs text-muted-foreground truncate max-w-[16rem]">
                    {APPLY_BASE}/{l.token}
                  </code>
                  <span className="text-2xs text-muted-foreground whitespace-nowrap" title="views · applications · hires">
                    · {l.view_count ?? 0} views · {l.submissions ?? 0} applied · {l.hires ?? 0} hired
                    {(l.submissions ?? 0) > 0 && (
                      <span className="ml-1 text-foreground">({Math.round(((l.hires ?? 0) / (l.submissions || 1)) * 100)}%)</span>
                    )}
                  </span>
                  {live && l.expires_at && (
                    <span className="text-2xs text-muted-foreground whitespace-nowrap">
                      · expires {formatDate(l.expires_at, { timeZone: tz, intl: { hour: undefined, minute: undefined } })}
                    </span>
                  )}
                  <div className="ml-auto flex items-center gap-1">
                    {live && (
                      <button onClick={() => copyLink(l)} title="Copy link"
                        className="text-muted-foreground hover:text-foreground p-1 rounded hover:bg-muted">
                        {copied === l.id ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
                      </button>
                    )}
                    {l.is_active === 1 && (
                      <button onClick={() => setEditingLink(editingLink === l.id ? null : l.id)}
                        title="Edit link (label, source, carrier, expiry)"
                        className={`p-1 rounded hover:bg-muted ${editingLink === l.id ? 'text-foreground bg-muted' : 'text-muted-foreground hover:text-foreground'}`}>
                        <Pencil size={14} />
                      </button>
                    )}
                    {live && (
                      <button onClick={() => revokeLink(l.id)} title="Revoke"
                        className="text-muted-foreground hover:text-destructive p-1 rounded hover:bg-muted">
                        <Ban size={14} />
                      </button>
                    )}
                    {!live && (
                      <button onClick={() => deleteLink(l.id)} title="Delete link permanently"
                        className="text-muted-foreground hover:text-destructive p-1 rounded hover:bg-muted">
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
                {editingLink === l.id && (
                  <LinkEditPanel link={l} companies={companies}
                    onSaved={() => { setEditingLink(null); loadLinks(); }}
                    onCancel={() => setEditingLink(null)}
                    onCompaniesChanged={loadCompanies} />
                )}
              </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* ── Applications (table or board) ──────────────────────── */}
      <section className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="flex flex-wrap items-center gap-1.5 p-3 border-b border-border">
          <div className="inline-flex rounded-md border border-border p-0.5">
            <button onClick={() => setView('table')}
              className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${view === 'table' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'}`}>
              <List size={13} /> Table
            </button>
            <button onClick={() => setView('board')}
              className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${view === 'board' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'}`}>
              <LayoutGrid size={13} /> Board
            </button>
          </div>
          {view === 'table' && (
            <>
              <span className="mx-1 h-4 w-px bg-border" />
              <button onClick={() => setStatusFilter('')}
                className={`px-2.5 py-1 rounded text-xs ${statusFilter === '' ? statusClasses('active') : 'text-muted-foreground hover:bg-muted'}`}>
                All
              </button>
              {STATUSES.map((s) => (
                <button key={s} onClick={() => setStatusFilter(s)}
                  className={`px-2.5 py-1 rounded text-xs capitalize ${statusFilter === s ? statusClasses(s) : 'text-muted-foreground hover:bg-muted'}`}>
                  {s}
                </button>
              ))}
              <div className="relative ml-auto">
                <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, email, ref…"
                  className="w-48 rounded-md border border-border bg-card py-1 pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground/60 outline-none focus:border-ring" />
              </div>
            </>
          )}
        </div>
        {view === 'table' && selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs">
            <span className="font-medium text-foreground">{selected.size} selected</span>
            <button onClick={() => bulkAction('screening', 'Move to screening')} className="rounded-md border border-border px-2 py-1 hover:bg-muted">Move to screening</button>
            <button onClick={() => bulkAction('rejected', 'Reject')} className={`rounded-md px-2 py-1 ${toneClasses('danger')}`}>Reject</button>
            <button onClick={() => bulkAction('withdrawn', 'Withdraw')} className="rounded-md border border-border px-2 py-1 hover:bg-muted">Withdraw</button>
            <button onClick={() => setSelected(new Set())} className="ml-auto text-muted-foreground hover:text-foreground">Clear</button>
          </div>
        )}
        {err && <div className="p-3 text-sm text-destructive">{err}</div>}
        {view === 'board' ? (
          <ApplicationsBoard rows={rows} loading={loading} onMove={moveApp} onOpen={setOpenId} />
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-8 px-3 py-2">
                  <input type="checkbox" aria-label="Select all" checked={allVisibleSelected}
                    onChange={toggleAll} className="accent-primary" />
                </th>
                <th className="px-3 py-2 text-left">Ref</th>
                {sortTh('name', 'Name')}
                <th className="px-3 py-2 text-left">Contact</th>
                <th className="px-3 py-2 text-left">Location</th>
                <th className="px-3 py-2 text-left">CDL</th>
                {sortTh('status', 'Status')}
                {sortTh('submitted', 'Submitted')}
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={8} className="text-center text-muted-foreground py-8">Loading…</td></tr>}
              {!loading && tableRows.length === 0 && (
                <tr><td colSpan={8} className="text-center text-muted-foreground py-8">
                  No applications{search ? ' match your search' : statusFilter ? ` in '${statusFilter}'` : ' yet'}.
                </td></tr>
              )}
              {tableRows.map((r) => (
                <tr key={r.id} onClick={() => setOpenId(r.id)}
                  className={`border-b border-border/50 cursor-pointer hover:bg-muted/40 ${selected.has(r.id) ? 'bg-primary/5' : ''}`}>
                  <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${r.reference}`} checked={selected.has(r.id)}
                      onChange={() => toggleRow(r.id)} className="accent-primary" />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{r.reference}</td>
                  <td className="px-3 py-2">
                    {r.first_name} {r.last_name}
                    {r.duplicate && (
                      <span className={`ml-1.5 rounded px-1.5 py-0.5 text-2xs ${toneClasses('warn')}`}
                        title="Another application in this account shares an SSN, email, or phone">re-applicant</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground text-xs">{r.email}<br />{r.phone}</td>
                  <td className="px-3 py-2 text-xs">{[r.city, r.state].filter(Boolean).join(', ') || '—'}</td>
                  <td className="px-3 py-2 text-xs">{r.cdl_class ? `Class ${r.cdl_class} · ${r.cdl_state}` : '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs capitalize ${statusClasses(r.status)}`}>{r.status}</span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground text-xs tabular-nums">{r.submitted_at?.slice(0, 10) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {openId !== null && (
        <ApplicationDetail appId={openId} onOpen={setOpenId} onClose={() => setOpenId(null)}
          onChanged={() => { loadApps(); }} />
      )}
    </div>
  );
}

// ── Kanban board ────────────────────────────────────────────────────

// Pipeline columns.  'hired' is NOT droppable — hiring goes through the
// Hire action (it mints the driver invite); dropping a card there would
// 409.  Dropping into 'approved' still hits the vetting gate server-side.
const BOARD_COLUMNS: { key: string; droppable: boolean }[] = [
  { key: 'submitted', droppable: true },
  { key: 'screening', droppable: true },
  { key: 'interview', droppable: true },
  { key: 'approved', droppable: true },
  { key: 'hired', droppable: false },
  { key: 'rejected', droppable: true },
  { key: 'withdrawn', droppable: true },
];

function ApplicationsBoard({ rows, loading, onMove, onOpen }: {
  rows: AppRow[]; loading: boolean; onMove: (id: number, status: string) => void; onOpen: (id: number) => void;
}) {
  const [dragId, setDragId] = useState<number | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);

  if (loading) return <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="flex gap-3 overflow-x-auto p-3">
      {BOARD_COLUMNS.map(({ key, droppable }) => {
        const items = rows.filter((r) => r.status === key);
        const isOver = overCol === key && droppable && dragId != null;
        return (
          <div key={key}
            onDragOver={(e) => { if (droppable && dragId != null) { e.preventDefault(); setOverCol(key); } }}
            onDragLeave={() => setOverCol((c) => (c === key ? null : c))}
            onDrop={(e) => { e.preventDefault(); setOverCol(null); if (droppable && dragId != null) onMove(dragId, key); setDragId(null); }}
            className={`flex w-56 shrink-0 flex-col rounded-lg border ${isOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/30'}`}>
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${statusClasses(key)}`}>{key}</span>
              <span className="text-2xs text-muted-foreground">{items.length}</span>
            </div>
            <div className="flex min-h-16 flex-col gap-2 p-2">
              {items.map((r) => (
                <div key={r.id} draggable
                  onDragStart={() => setDragId(r.id)}
                  onDragEnd={() => { setDragId(null); setOverCol(null); }}
                  onClick={() => onOpen(r.id)}
                  className="cursor-grab rounded-md border border-border bg-card p-2.5 text-sm hover:border-ring active:cursor-grabbing">
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate font-medium text-foreground">{r.first_name} {r.last_name}</span>
                    <span className="shrink-0 font-mono text-2xs text-muted-foreground">{r.reference}</span>
                  </div>
                  <p className="truncate text-xs text-muted-foreground">
                    {[[r.city, r.state].filter(Boolean).join(', '), r.cdl_class ? `Class ${r.cdl_class}` : ''].filter(Boolean).join(' · ') || '—'}
                  </p>
                  <div className="flex items-center justify-between">
                    <p className="text-2xs text-muted-foreground tabular-nums">{r.submitted_at?.slice(0, 10) || '—'}</p>
                    {r.duplicate && <span className={`rounded px-1 py-0.5 text-3xs ${toneClasses('warn')}`}>re-applicant</span>}
                  </div>
                </div>
              ))}
              {items.length === 0 && <p className="py-3 text-center text-2xs text-muted-foreground">—</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Detail drawer ───────────────────────────────────────────────────

interface AppDetail {
  id: number; reference: string; status: string; submitted_at: string;
  first_name: string; last_name: string; email: string; phone: string;
  dob: string; ssn: string; city: string; state: string;
  personal: Record<string, unknown>; cdl: Record<string, unknown>;
  experience: Record<string, unknown>; employment: unknown[];
  incidents: Record<string, unknown>; position: Record<string, unknown>;
  consents: Record<string, unknown>; docs: Record<string, string>;
  address_history: unknown[]; recruiter_notes: string;
  vetting: Record<string, { done?: boolean; at?: string } | undefined>;
  related?: RelatedApp[];
}

// Pre-hire checks; the first three are required before 'approved'.
const VETTING_CHECKS: { key: string; label: string; required: boolean }[] = [
  { key: 'psp', label: 'PSP query', required: true },
  { key: 'mvr', label: 'MVR pulled', required: true },
  { key: 'clearinghouse', label: 'Clearinghouse query', required: true },
  { key: 'drug', label: 'Drug screen', required: false },
  { key: 'background', label: 'Background check', required: false },
];

function ApplicationDetail({ appId, onClose, onChanged, onOpen }: {
  appId: number; onClose: () => void; onChanged: () => void; onOpen: (id: number) => void;
}) {
  const [app, setApp] = useState<AppDetail | null>(null);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [hireResult, setHireResult] = useState<string>('');

  useEffect(() => {
    apiJSON<AppDetail>(`/applications/${appId}`).then((d) => {
      setApp(d); setNotes(d.recruiter_notes || '');
    });
  }, [appId]);

  const setStatus = async (status: string) => {
    setBusy(true);
    try {
      await apiJSON(`/applications/${appId}/status`, { method: 'PATCH', body: { status } });
      setApp((a) => a ? { ...a, status } : a);
      onChanged();
    } catch (e) {
      // Surfaces the vetting/lifecycle gate (e.g. "Complete the required
      // checks before approving: PSP, MVR").
      alert(e instanceof Error ? e.message : 'Could not change status');
    } finally { setBusy(false); }
  };

  const toggleCheck = async (key: string, done: boolean) => {
    const r = await apiJSON<{ vetting: AppDetail['vetting'] }>(
      `/applications/${appId}/vetting`, { method: 'PATCH', body: { check: key, done } },
    );
    setApp((a) => (a ? { ...a, vetting: r.vetting } : a));
  };

  const downloadPacket = async () => {
    const res = await apiFetch(`/applications/${appId}/packet.pdf`);
    if (!res.ok) { alert('Could not download the packet.'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `application-${app?.reference || appId}.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const saveNotes = async () => {
    setBusy(true);
    try {
      await apiJSON(`/applications/${appId}/notes`, { method: 'PATCH', body: { notes } });
    } finally { setBusy(false); }
  };

  const hire = async () => {
    if (!confirm('Hire this applicant? This creates a driver invite link to share with them.')) return;
    setBusy(true);
    try {
      const r = await apiJSON<{ invite_link: string }>(`/applications/${appId}/convert`, { method: 'POST' });
      setHireResult(r.invite_link);
      setApp((a) => a ? { ...a, status: 'hired' } : a);
      onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Convert failed');
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl h-full bg-card border-l border-border overflow-y-auto p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            {app ? `${app.first_name} ${app.last_name}` : 'Loading…'}
            {app && <span className="ml-2 font-mono text-xs text-muted-foreground">{app.reference}</span>}
          </h2>
          <div className="flex items-center gap-1">
            {app && (
              <button onClick={downloadPacket} title="Download application packet (PDF)"
                className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                <Download size={14} /> Packet
              </button>
            )}
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1"><X size={18} /></button>
          </div>
        </div>

        {app && (
          <>
            {/* Re-applicant warning — informational, never blocks anything. */}
            {app.related && app.related.length > 0 && (
              <div className={`rounded-md p-3 text-sm ${toneClasses('warn')}`}>
                <p className="flex items-center gap-1.5 font-medium">
                  <Users size={14} /> Re-applicant — {app.related.length} prior application{app.related.length > 1 ? 's' : ''} in this account
                </p>
                <ul className="mt-1.5 space-y-0.5">
                  {app.related.map((r) => (
                    <li key={r.id}>
                      <button onClick={() => onOpen(r.id)} className="text-xs underline hover:no-underline">
                        {r.reference} · <span className="capitalize">{r.status}</span> · {r.submitted_at?.slice(0, 10)}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Status + actions */}
            <div className="flex flex-wrap items-center gap-2">
              {STATUSES.map((s) => (
                <button key={s} onClick={() => setStatus(s)} disabled={busy}
                  className={`px-2.5 py-1 rounded text-xs capitalize disabled:opacity-50 ${app.status === s ? statusClasses(s) : 'text-muted-foreground border border-border hover:bg-muted'}`}>
                  {s}
                </button>
              ))}
            </div>

            {hireResult ? (
              <div className={`rounded-md p-3 text-sm ${statusClasses('hired')}`}>
                <p className="font-medium">Invite created — share this link with the driver:</p>
                <code className="text-xs break-all">{hireResult}</code>
              </div>
            ) : app.status !== 'hired' && (
              <Button onClick={hire} disabled={busy} size="sm" className="gap-1.5">
                <UserPlus size={14} /> Hire — create driver invite
              </Button>
            )}

            <Section title="Pre-hire checks">
              <div className="space-y-1.5">
                {VETTING_CHECKS.map(({ key, label, required }) => {
                  const done = !!app.vetting?.[key]?.done;
                  return (
                    <button key={key} type="button" onClick={() => toggleCheck(key, !done)}
                      className="flex w-full items-center gap-2 text-left text-sm">
                      <span className={`flex size-4 shrink-0 items-center justify-center rounded border ${done ? 'border-ok bg-ok-bg' : 'border-border'}`}>
                        {done && <Check size={11} className="text-ok" />}
                      </span>
                      <span className="text-foreground">{label}</span>
                      {required && <span className="text-2xs text-muted-foreground">required</span>}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 flex items-center gap-1 text-2xs text-muted-foreground">
                <ShieldCheck size={11} /> All required checks must be complete before approving.
              </p>
            </Section>

            <Section title="Documents">
              <DocumentGrid appId={appId} docs={app.docs} />
            </Section>

            <Section title="Contact">
              <Row k="Email" v={app.email} /><Row k="Phone" v={app.phone} />
              <Row k="Location" v={[app.city, app.state].filter(Boolean).join(', ')} />
              <Row k="DOB" v={app.dob} /><Row k="SSN" v={app.ssn} mono />
              {(() => {
                const em = (app.personal?.emergency ?? {}) as Record<string, string>;
                const v = [em.name, em.phone, em.relationship].filter(Boolean).join(' · ');
                return v ? <Row k="Emergency contact" v={v} /> : null;
              })()}
            </Section>
            {app.address_history?.length > 0 && (
              <Section title={`Address history — last 3 yrs (${app.address_history.length})`}>
                <AddressHistory rows={app.address_history as AddressRow[]} />
              </Section>
            )}
            <Section title="CDL"><Json obj={app.cdl} /></Section>
            <Section title="Experience"><Json obj={app.experience} /></Section>
            <Section title={`Employment (${app.employment?.length || 0})`}>
              <Employment jobs={app.employment as EmploymentRow[]} />
            </Section>
            <Section title="Accidents & violations"><Json obj={app.incidents} /></Section>
            <Section title="Position"><Json obj={app.position} /></Section>
            <Section title="Consents & signature"><Consents c={app.consents} /></Section>

            <div>
              <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recruiter notes</label>
              <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className="mt-1" />
              <Button onClick={saveNotes} disabled={busy} size="sm" variant="outline" className="mt-2">Save notes</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-border pt-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">{title}</h3>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}
function Row({ k, v, mono }: { k: string; v?: string; mono?: boolean }) {
  return (
    <div className="flex justify-between text-sm gap-4">
      <span className="text-muted-foreground">{k}</span>
      <span className={mono ? 'font-mono text-xs' : ''}>{v || '—'}</span>
    </div>
  );
}
// camelCase / snake_case → "Title Case".
function humanize(k: string): string {
  return k.replace(/([A-Z])/g, ' $1').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()).trim();
}
// Render any leaf/array/nested-object value as a short human string.
function renderVal(v: unknown): string {
  if (v == null || v === '') return '';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (Array.isArray(v)) return v.length ? v.map((x) => String(x)).join(', ') : '';
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== '' && x != null && x !== false)
      .map(([kk, xx]) => (typeof xx === 'boolean' ? humanize(kk) : `${humanize(kk)}: ${xx}`))
      .join(' · ');
  }
  return String(v);
}
// Generic readable key/value block (CDL, experience, position, incidents).
function Json({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj || {})
    .map(([k, v]) => [k, renderVal(v)] as const)
    .filter(([, v]) => v !== '');
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">—</p>;
  return <>{entries.map(([k, v]) => <Row key={k} k={humanize(k)} v={v} />)}</>;
}

// ── Readable section renderers ──────────────────────────────────────

interface AddressRow { addr1?: string; city?: string; state?: string; zip?: string; from?: string; to?: string; }
function AddressHistory({ rows }: { rows: AddressRow[] }) {
  return (
    <div className="space-y-1.5">
      {rows.map((a, i) => (
        <div key={i} className="text-sm">
          <span className="text-foreground">{[a.addr1, a.city, a.state, a.zip].filter(Boolean).join(', ') || '—'}</span>
          <span className="ml-1.5 text-xs text-muted-foreground">({a.from || '?'} – {a.to || '?'})</span>
        </div>
      ))}
    </div>
  );
}

interface EmploymentRow {
  company?: string; position?: string; city?: string; state?: string;
  from?: string; to?: string; current?: boolean; reason?: string;
  gapExplanation?: string; fmcsa?: string; contactOk?: string;
}
function Employment({ jobs }: { jobs: EmploymentRow[] }) {
  if (!jobs?.length) return <p className="text-xs text-muted-foreground">No employment history.</p>;
  return (
    <div className="space-y-2">
      {jobs.map((j, i) => (
        <div key={i} className="rounded-md border border-border p-2.5 text-sm">
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-medium text-foreground">{j.company || '—'}</span>
            <span className="text-xs text-muted-foreground tabular-nums">{j.from || '?'} → {j.current ? 'present' : (j.to || '?')}</span>
          </div>
          {(j.position || j.city) && (
            <p className="text-xs text-muted-foreground">{[j.position, [j.city, j.state].filter(Boolean).join(', ')].filter(Boolean).join(' · ')}</p>
          )}
          {j.reason && <p className="mt-0.5 text-xs">Reason for leaving: {j.reason}</p>}
          {j.gapExplanation && <p className="mt-0.5 text-xs text-warn">Gap: {j.gapExplanation}</p>}
          <p className="mt-1 text-2xs text-muted-foreground">
            FMCSA-regulated: <b>{j.fmcsa || '—'}</b> · may contact: <b>{j.contactOk || '—'}</b>
          </p>
        </div>
      ))}
    </div>
  );
}

const CONSENT_LABELS: [string, string][] = [
  ['psp', 'FMCSA PSP'], ['mvr', 'Motor Vehicle Record'], ['clearinghouse', 'Drug & Alcohol Clearinghouse'],
  ['fcra', 'Background check (FCRA)'], ['drug', 'Pre-employment drug screen'], ['truthful', 'Truthful & complete certification'],
];
function Consents({ c }: { c: Record<string, unknown> }) {
  return (
    <div className="space-y-1">
      {CONSENT_LABELS.map(([k, label]) => (
        <div key={k} className="flex items-center gap-1.5 text-sm">
          {c?.[k] ? <Check size={14} className="text-ok shrink-0" /> : <X size={14} className="text-destructive shrink-0" />}
          <span className={c?.[k] ? 'text-foreground' : 'text-muted-foreground'}>{label}</span>
        </div>
      ))}
      <p className="mt-1.5 text-xs text-muted-foreground">
        Signed by <span className="text-foreground">{String(c?.sigName || '(drawn signature)')}</span>
        {c?.sigDate ? ` on ${String(c.sigDate)}` : ''} · {String(c?.sigMode || 'type')} mode
      </p>
    </div>
  );
}

// ── Document viewer (authed blob fetch → preview/open) ──────────────

const DOC_LABELS: Record<string, string> = {
  cdlFront: 'CDL — Front', cdlBack: 'CDL — Back', medical: 'DOT Medical Card',
  truckPic: 'Truck Photo', dotInspection: 'DOT Inspection', signature: 'Signature',
};
const DOC_ORDER = ['cdlFront', 'cdlBack', 'medical', 'truckPic', 'dotInspection', 'signature'];

function DocumentGrid({ appId, docs }: { appId: number; docs: Record<string, string> }) {
  const slots = DOC_ORDER.filter((s) => docs?.[s]);
  if (slots.length === 0) return <p className="text-xs text-muted-foreground">No documents uploaded.</p>;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {slots.map((s) => <DocThumb key={s} appId={appId} slot={s} />)}
    </div>
  );
}

function DocThumb({ appId, slot }: { appId: number; slot: string }) {
  const [url, setUrl] = useState('');
  const [isImage, setIsImage] = useState(true);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let dead = false;
    let made = '';
    apiFetch(`/applications/${appId}/docs/${slot}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const blob = await res.blob();
        if (dead) return;
        made = URL.createObjectURL(blob);
        setIsImage(blob.type.startsWith('image/'));
        setUrl(made);
      })
      .catch(() => !dead && setErr(true));
    return () => { dead = true; if (made) URL.revokeObjectURL(made); };
  }, [appId, slot]);

  const label = DOC_LABELS[slot] ?? slot;
  const open = () => url && window.open(url, '_blank', 'noopener');

  return (
    <button type="button" onClick={open} disabled={!url}
      className="group flex flex-col overflow-hidden rounded-md border border-border text-left hover:border-ring disabled:cursor-default">
      <div className="flex aspect-[4/3] items-center justify-center bg-muted">
        {err ? <span className="text-2xs text-muted-foreground">unavailable</span>
          : !url ? <span className="text-2xs text-muted-foreground">loading…</span>
          : isImage ? <img src={url} alt={label} className="h-full w-full object-cover" />
          : <FileText size={24} className="text-muted-foreground" />}
      </div>
      <div className="flex items-center justify-between gap-1 px-2 py-1.5">
        <span className="truncate text-2xs text-foreground">{label}</span>
        {url && <ExternalLink size={12} className="shrink-0 text-muted-foreground group-hover:text-foreground" />}
      </div>
    </button>
  );
}

// ── In-app notifications (bell + dropdown + channel prefs) ──────────

interface Notif {
  id: number; application_id: number | null; reference: string;
  title: string; body: string; is_read: number; created_at: string;
}
const NOTIFY_CHANNELS: { key: string; label: string; icon: typeof Bell }[] = [
  { key: 'telegram', label: 'Bot', icon: MessageSquare },
  { key: 'email', label: 'Email', icon: Mail },
  { key: 'dashboard', label: 'Dashboard', icon: Monitor },
];

function NotificationsBell({ onOpen }: { onOpen: (appId: number) => void }) {
  const tz = useTimezone();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notif[]>([]);
  const [unread, setUnread] = useState(0);
  const [channels, setChannels] = useState<string[]>([]);

  const load = useCallback(() => {
    apiJSON<{ items: Notif[]; unread_count: number }>('/applications/notifications?limit=20')
      .then((r) => { setItems(r.items); setUnread(r.unread_count); })
      .catch(() => { /* non-fatal */ });
  }, []);

  // Initial load + a light 60s poll so the badge stays current.
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);
  useEffect(() => {
    apiJSON<{ channels: string[] }>('/applications/notify-prefs')
      .then((r) => setChannels(r.channels)).catch(() => { /* non-fatal */ });
  }, []);

  const markAll = async () => {
    await apiJSON('/applications/notifications/read', { method: 'POST', body: {} });
    setItems((xs) => xs.map((x) => ({ ...x, is_read: 1 })));
    setUnread(0);
  };
  const openNotif = (n: Notif) => {
    if (!n.is_read) {
      apiJSON('/applications/notifications/read', { method: 'POST', body: { ids: [n.id] } }).catch(() => {});
      setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, is_read: 1 } : x)));
      setUnread((u) => Math.max(0, u - 1));
    }
    setOpen(false);
    if (n.application_id) onOpen(n.application_id);
  };
  const toggleChannel = async (key: string) => {
    const next = channels.includes(key) ? channels.filter((c) => c !== key) : [...channels, key];
    setChannels(next);
    const r = await apiJSON<{ channels: string[] }>('/applications/notify-prefs', { method: 'PUT', body: { channels: next } });
    setChannels(r.channels);
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Notifications"
        className="relative rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground">
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-2xs font-semibold text-destructive-foreground">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-2 w-80 rounded-lg border border-border bg-card shadow-lg">
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">Notifications</span>
              {unread > 0 && (
                <button onClick={markAll} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                  <CheckCheck size={13} /> Mark all read
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto">
              {items.length === 0 ? (
                <p className="px-3 py-6 text-center text-sm text-muted-foreground">No notifications yet.</p>
              ) : items.map((n) => (
                <button key={n.id} onClick={() => openNotif(n)}
                  className={`flex w-full flex-col items-start gap-0.5 border-b border-border/50 px-3 py-2 text-left hover:bg-muted/50 ${n.is_read ? '' : 'bg-primary/5'}`}>
                  <span className="flex w-full items-center gap-1.5">
                    {!n.is_read && <span className="size-1.5 shrink-0 rounded-full bg-primary" />}
                    <span className="text-sm font-medium text-foreground">{n.title}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">{n.body}</span>
                  <span className="text-2xs text-muted-foreground">{formatDate(n.created_at, { timeZone: tz })}</span>
                </button>
              ))}
            </div>
            <div className="border-t border-border px-3 py-2">
              <p className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">Notify me via</p>
              <div className="flex gap-1.5">
                {NOTIFY_CHANNELS.map(({ key, label, icon: I }) => {
                  const on = channels.includes(key);
                  return (
                    <button key={key} onClick={() => toggleChannel(key)}
                      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${on ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:bg-muted'}`}>
                      <I size={12} /> {label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
