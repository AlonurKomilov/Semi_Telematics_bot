import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { ReactNode } from 'react';
import { UserPlus, Link as LinkIcon, Copy, Check, Ban, X, FileText, ExternalLink, Bell, Mail, MessageSquare, Monitor, CheckCheck, Download, ShieldCheck, LayoutGrid, List, Users, Building2, Pencil, Trash2, Clock3, Minus, Lock, ChevronDown } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON, apiFetch } from '../../api/client';
import { PageHeader } from '../../components/shell';
import { usePermissions } from '../../hooks/usePermissions';
import ApplicationsConfigPanel from './config/ApplicationsConfigPanel';
import { FeatureConfigGear } from '../_lib/FeatureConfigGear';
import { Tip } from '../../components/tooltip';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { statusClasses, toneClasses, toneText } from '../../lib/status';
import { APEX_DOMAIN } from '../../lib/safeReturnTo';
import { formatDate, formatDay } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import { useInboxSource, useInboxActions, type InboxNotice } from '../alerts/useInbox';
import DataGrid, {
  TAB_PREFIX, type DataGridSegment, type BulkAction,
} from '../../components/datagrid';
import { resolveScopeBase, scopeMatch, type TabScope } from './scope';
import { ContextMenu, type MenuAction } from '../../components/ui/context-menu';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../components/ui/select';
import {
  Sheet, SheetContent, SheetBody, SheetTitle, SheetClose,
} from '../../components/ui/sheet';
import type { AnyColumn } from '../../types';
import { useQueryClient } from '@tanstack/react-query';
import { useApplicationsQuery, type AppRow } from './useApplications';
import { ConfigMovedNotice } from '../_lib/ConfigMovedNotice';
import { Card } from '@/components/ui/card';

// Lifecycle split for the grid's segment tabs: the working pipeline
// vs the two terminal outcomes.  Finer stage slicing (Submitted /
// Screening / …) lives in the Status column filter, and the live
// stage counts sit in the topbar ApplicationsHero.
const OPEN_APP_STATUSES = new Set(['submitted', 'screening', 'interview', 'approved']);
const APP_SEGMENTS: DataGridSegment[] = [
  {
    key: 'active',
    label: 'Active',
    match: (r) => OPEN_APP_STATUSES.has(String(r.status ?? '')),
  },
  {
    key: 'hired',
    label: 'Hired',
    match: (r) => String(r.status ?? '') === 'hired',
  },
  {
    key: 'closed',
    label: 'Closed',
    match: (r) => r.status === 'rejected' || r.status === 'withdrawn',
  },
];

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
  /** Auto-remind policy for abandoned drafts (0 = off) + lifetime cap. */
  remind_every_hours?: number; remind_max?: number;
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
  // Legal/compliance details that fill the consent disclosures.
  legal_address: string; compliance_email: string;
  cra_name: string; cra_address: string; cra_phone: string; cra_site: string;
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

// Cosmetic touch-up for the carrier a link is branded for.  The recruiter can
// fix the contact / pitch / requirements WITHOUT owner access to
// Settings·Companies; the name + MC/DOT stay owner-managed (read-only).  The
// VISUAL design — logo, hero photo and every brand colour — is edited live in
// the Preview (see ApplyPreview / PreviewThemeBar), so it never drifts from
// what an applicant sees; this panel owns the form's DATA (contact / pitch /
// pre-qual requirements / FCRA agency).
function CompanyBrandPanel({ company, onChanged, onDirtyChange }: {
  company: PickerCompany; onChanged: () => void;
  /** Lets a parent that can UNMOUNT this panel refuse to do so silently. */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [website, setWebsite] = useState(company.website || '');
  const [phone, setPhone] = useState(company.phone || '');
  const [headline, setHeadline] = useState(company.headline || '');
  const [perks, setPerks] = useState(company.perks || '');
  const [reqYears, setReqYears] = useState(company.req_experience_years ?? 1);
  const [reqAge, setReqAge] = useState(company.req_min_age ?? 21);
  const [reqClass, setReqClass] = useState(company.req_cdl_class || 'A');
  const LEGAL_KEYS = ['legal_address', 'compliance_email', 'cra_name', 'cra_address', 'cra_phone', 'cra_site'] as const;
  const legalOf = (co: PickerCompany) => Object.fromEntries(LEGAL_KEYS.map((k) => [k, co[k] || ''])) as Record<string, string>;
  const [legal, setLegal] = useState<Record<string, string>>(legalOf(company));
  const setL = (k: string, v: string) => setLegal((s) => ({ ...s, [k]: v }));
  const [busy, setBusy] = useState(false);
  // This panel writes the CARRIER record, not the link — every link pointing
  // at this carrier changes with it. It renders inline under one link's
  // form, so without a dirty flag a link-save (which unmounts this panel)
  // silently discarded whatever was typed here and still said "Saved".
  useEffect(() => {
    setWebsite(company.website || ''); setPhone(company.phone || '');
    setHeadline(company.headline || ''); setPerks(company.perks || '');
    setReqYears(company.req_experience_years ?? 1); setReqAge(company.req_min_age ?? 21);
    setReqClass(company.req_cdl_class || 'A'); setLegal(legalOf(company));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company.id]);

  const legalDirty = LEGAL_KEYS.some((k) => legal[k] !== (company[k] || ''));
  const dirty = website !== (company.website || '')
    || phone !== (company.phone || '') || headline !== (company.headline || '') || perks !== (company.perks || '')
    || reqYears !== (company.req_experience_years ?? 1) || reqAge !== (company.req_min_age ?? 21)
    || reqClass !== (company.req_cdl_class || 'A') || legalDirty;

  // Report upward so a parent that can unmount this panel (LinkEditPanel's
  // Save) can refuse to discard the work silently.
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const saveBrand = async () => {
    setBusy(true);
    try {
      await apiJSON(`/applications/companies/${company.id}/brand`, {
        method: 'PATCH',
        body: { website, phone, headline, perks,
                req_experience_years: reqYears, req_min_age: reqAge, req_cdl_class: reqClass,
                ...legal },
      });
      toast.success('Brand updated');
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Update failed'); }
    finally { setBusy(false); }
  };

  return (
    <div className="mt-3 rounded-md border border-border bg-background/40 p-4">
      {/* State the scope where the editing happens. This panel is rendered
          inside ONE link's form, but it writes the carrier record — so the
          edits apply to every link pointing at this carrier, including ones
          already out in the world. */}
      <p className={`mb-3 rounded-md border px-2 py-1.5 text-2xs ${toneClasses('info')}`}>
        These are <b>{company.display_name || company.code}</b>&rsquo;s own details, shared by
        every link for this carrier — not settings for this one link.
        {dirty && <b> You have unsaved carrier changes.</b>}
      </p>
      <div className="flex items-start gap-4">
        <LinkCompanyLogo id={company.id} hasLogo={company.has_logo} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{company.display_name || company.code}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {company.mc_number ? `MC ${company.mc_number}` : 'MC —'} · {company.usdot_number ? `USDOT ${company.usdot_number}` : 'USDOT —'}
            <span className="ml-1 opacity-70">· name &amp; MC/DOT managed by owner</span>
          </p>
          <a href={`/applications/preview/${company.id}`} target="_blank" rel="noopener noreferrer"
            className="mt-1.5 inline-flex items-center gap-1.5 py-1 min-h-tap text-xs text-primary hover:underline">
            <ExternalLink className="size-3" /> Logo, hero photo &amp; theme colours — edit live in Preview
          </a>
        </div>
      </div>
      <div className="mt-3">
        <p className="text-2xs text-muted-foreground">Carrier contact — shown on the form footer + the §391.23 consent document (with the owner-set name/MC/DOT). Filled once, used everywhere.</p>
        <div className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Input placeholder="Website" value={website} onChange={(e) => setWebsite(e.target.value)} />
          <Input placeholder="Phone *" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <Input placeholder="Mailing address (street, city, state, zip) *" value={legal.legal_address} onChange={(e) => setL('legal_address', e.target.value)} />
          <Input placeholder="Safety / compliance email *" value={legal.compliance_email} onChange={(e) => setL('compliance_email', e.target.value)} />
        </div>
        {(!phone || !legal.legal_address || !legal.compliance_email) && (
          <p className={`mt-2 rounded-md px-2 py-1 text-2xs ${toneClasses('warn')}`}>
            Add the carrier's <b>phone, mailing address &amp; email</b> — the §391.23 consent document is incomplete without them.
          </p>
        )}
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2">
        <Input placeholder="Headline — e.g. Top pay. Home weekly. Modern trucks."
          value={headline} maxLength={140} onChange={(e) => setHeadline(e.target.value)} />
        <Textarea placeholder="Perks — one per line (e.g. $0.65/mile · Home weekends · 2022+ trucks · Full benefits)"
          value={perks} rows={3} maxLength={800} onChange={(e) => setPerks(e.target.value)} />
      </div>
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
            <Select value={reqClass} onValueChange={(v) => setReqClass(String(v))}>
              <SelectTrigger size="sm" aria-label="CDL class"><SelectValue /></SelectTrigger>
              <SelectContent>
                {['A', 'B', 'C'].map((x) => <SelectItem key={x} value={x}>{x}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
        </div>
      </div>
      <div className="mt-3 border-t border-border pt-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Background-check agency (FCRA)</p>
        <p className="mt-0.5 text-2xs text-muted-foreground">The consumer-reporting agency named in the FCRA disclosure. The legal wording is fixed — you only fill these blanks.</p>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Input placeholder="Agency name (e.g. TR Information Services)" value={legal.cra_name} onChange={(e) => setL('cra_name', e.target.value)} />
          <Input placeholder="Agency phone" value={legal.cra_phone} onChange={(e) => setL('cra_phone', e.target.value)} />
          <Input placeholder="Agency address" value={legal.cra_address} onChange={(e) => setL('cra_address', e.target.value)} />
          <Input placeholder="Agency website(s)" value={legal.cra_site} onChange={(e) => setL('cra_site', e.target.value)} />
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <a href={`/applications/preview/${company.id}`} target="_blank" rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline min-h-tap">
          <ExternalLink className="size-3" /> Preview application
        </a>
        <Button size="sm" onClick={saveBrand} disabled={busy || !dirty}>
          {busy ? '…' : 'Save carrier profile'}
        </Button>
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
// Auto-remind cadence / cap options (LinkEditPanel Selects).
const REMIND_CADENCE_ITEMS = [
  { value: '0', label: 'Off' },
  { value: '24', label: 'Every 24 hours' },
  { value: '48', label: 'Every 2 days' },
  { value: '72', label: 'Every 3 days' },
  { value: '168', label: 'Every 7 days' },
];
const REMIND_MAX_ITEMS = [1, 2, 3].map((n) => (
  { value: String(n), label: `up to ${n} reminder${n > 1 ? 's' : ''}` }
));
// AppRow moved to ./useApplications (shared with ApplicationsHero).

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
  // Auto-remind policy for abandoned drafts on this link (0 = off).
  const [remindHours, setRemindHours] = useState(link.remind_every_hours ?? 0);
  const [remindMax, setRemindMax] = useState(link.remind_max ?? 3);
  const [busy, setBusy] = useState(false);
  // Saving the LINK unmounts the nested CompanyBrandPanel, which silently
  // threw away anything typed there and still reported success. The panel
  // reports its dirty state up so we can stop instead.
  const [brandUnsaved, setBrandUnsaved] = useState(false);
  const sel = companyId ? companies.find((c) => String(c.id) === companyId) : null;
  // Item lists for the themed Selects — ``items`` lets SelectValue
  // render the label (carrier name) instead of the raw value (id).
  const editCompanyItems = [
    { value: '', label: 'No company (generic)' },
    ...companies.map((co) => ({ value: String(co.id), label: co.display_name || co.code })),
  ];
  const editExpiryItems = [
    { value: 'keep', label: 'Keep expiry' },
    ...EXPIRY_OPTIONS.map((o) => ({ value: String(o.days), label: `Reset · ${o.label}` })),
  ];
  const save = async () => {
    if (brandUnsaved && !confirm(
      'You have unsaved carrier details in the panel below.\n\n'
      + "Saving the link now closes that panel and those changes are lost. "
      + 'Save the carrier details first, or continue and discard them?',
    )) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        label, source, company_id: companyId ? Number(companyId) : null,
        remind_every_hours: remindHours, remind_max: remindMax,
      };
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
        <Input placeholder="Source" value={source} onChange={(e) => setSource(e.target.value)} className="max-w-40" />
        {companies.length > 0 && (
          <Select value={companyId} onValueChange={(v) => setCompanyId(String(v))} items={editCompanyItems}>
            <SelectTrigger title="Carrier this link brands for" aria-label="Carrier brand">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {editCompanyItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Select value={expiry} onValueChange={(v) => setExpiry(String(v))} items={editExpiryItems}>
          <SelectTrigger aria-label="Link expiry"><SelectValue /></SelectTrigger>
          <SelectContent>
            {editExpiryItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
          </SelectContent>
        </Select>
        {/* Named scope: this panel and the carrier panel nested inside it
            both had a bare "Save", and they write different objects. */}
        <Button size="sm" onClick={save} disabled={busy}>{busy ? '…' : 'Save link'}</Button>
        <button type="button" onClick={() => {
          if (brandUnsaved && !confirm(
            'Discard the unsaved carrier details in the panel below?',
          )) return;
          onCancel();
        }} className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap">Cancel</button>
      </div>
      {/* Auto-remind: nudge applicants who started but didn't submit.  Off by
          default; the cadence + lifetime cap are this link's policy. */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Auto-remind abandoned applications</span>
        <Select value={String(remindHours)} onValueChange={(v) => setRemindHours(Number(v))} items={REMIND_CADENCE_ITEMS}>
          <SelectTrigger aria-label="Reminder cadence"><SelectValue /></SelectTrigger>
          <SelectContent>
            {REMIND_CADENCE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
          </SelectContent>
        </Select>
        {remindHours > 0 && (
          <>
            <Select value={String(remindMax)} onValueChange={(v) => setRemindMax(Number(v))} items={REMIND_MAX_ITEMS}>
              <SelectTrigger aria-label="Reminder cap"><SelectValue /></SelectTrigger>
              <SelectContent>
                {REMIND_MAX_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <span className="text-2xs text-muted-foreground">
              per applicant, ever — emails their resume link; stops when they submit.
            </span>
          </>
        )}
      </div>
      {/* Same carrier brand + requirements + preview the create flow shows. */}
      {sel && <CompanyBrandPanel company={sel} onChanged={onCompaniesChanged}
        onDirtyChange={setBrandUnsaved} />}
    </div>
  );
}

// Hoisted to module scope for two reasons.  An inline array is a new
// identity every render, which re-runs the grid's faceted option pass
// over every row; and a saved TAB's scope predicate needs this exact
// array to replay its captured filters, which a value trapped inside
// the component cannot hand over.  Safe to hoist: no render function
// here closes over component state — they read only the row.
// Factory, not a constant: the Submitted cell renders a server INSTANT
// as a calendar day, and which day that is depends on the viewer's
// effective timezone — a bare .slice(0, 10) printed the UTC day (one
// day off during US evenings).
const makeAppColumns = (tz: string): AnyColumn[] => [
    {
      key: 'reference', label: 'Ref', sortable: false,
      render: (v) => <span className="font-mono text-xs">{String(v)}</span>,
    },
    {
      // Sort key is a synthesised last+first name (matches
      // the pre-migration external sort behaviour).
      key: 'last_name', label: 'Name', sortable: true,
      sortKey: (row) => {
        const r = row as unknown as AppRow;
        return `${r.last_name} ${r.first_name}`.toLowerCase();
      },
      render: (_v, row) => {
        const r = row as unknown as AppRow;
        return (
          <span>
            {r.first_name} {r.last_name}
            {r.duplicate && (
              <Tip label="Another application in this account shares an SSN, email, or phone">
                <span className={`ml-1.5 rounded-md px-1.5 py-0.5 text-2xs ${toneClasses('warn')}`}>
                  re-applicant
                </span>
              </Tip>
            )}
          </span>
        );
      },
    },
    {
      key: 'email', label: 'Contact', sortable: false,
      render: (_v, row) => {
        const r = row as unknown as AppRow;
        return (
          <span className="text-muted-foreground text-xs">
            {r.email}<br />{r.phone}
          </span>
        );
      },
    },
    {
      key: 'city', label: 'Location', sortable: false,
      render: (_v, row) => {
        const r = row as unknown as AppRow;
        return (
          <span className="text-xs">
            {[r.city, r.state].filter(Boolean).join(', ') || '—'}
          </span>
        );
      },
    },
    {
      key: 'cdl_class', label: 'CDL', sortable: false,
      render: (_v, row) => {
        const r = row as unknown as AppRow;
        return (
          <span className="text-xs">
            {r.cdl_class ? `Class ${r.cdl_class} · ${r.cdl_state}` : '—'}
          </span>
        );
      },
    },
    {
      key: 'status', label: 'Status', sortable: true,
      // Stage-level slicing (Submitted / Screening / …)
      // lives here now that the chip row is gone — the
      // segment tabs only split the lifecycle.
      filterable: true,
      filterValue: (row) => String((row as unknown as AppRow).status ?? ''),
      filterLabel: (row) => {
        const s = String((row as unknown as AppRow).status ?? '');
        return s ? s.charAt(0).toUpperCase() + s.slice(1) : '(none)';
      },
      render: (v) => (
        <span className={`px-2 py-0.5 rounded-md text-xs capitalize ${statusClasses(String(v))}`}>
          {String(v)}
        </span>
      ),
    },
    {
      key: 'submitted_at', label: 'Submitted', sortable: true,
      filterMode: 'date-range', filterable: true,
      render: (v) => (
        <span className="text-muted-foreground text-xs tabular-nums">
          {v ? formatDay(String(v), { timeZone: tz }) : '—'}
        </span>
      ),
    },
];

/** Row fields the search box reaches that are NOT columns. */
const APP_SEARCH_KEYS = ['first_name', 'last_name', 'email', 'reference'];

export default function Applications() {
  const qc = useQueryClient();
  const [links, setLinks] = useState<ApplicationLink[]>([]);
  const [view, setView] = useState<'table' | 'board'>('table');
  // The board used to render all seven columns of ALL rows regardless of
  // the table's tab, so switching Table→Board made Withdrawn records
  // reappear — the same dataset shown as two different populations.
  const [segment, setSegment] = useState('active');
  // A saved tab is a scope WITHIN a lifecycle slice, not a fourth slice.
  // Kept in its own state rather than stuffed into ``segment`` so that
  // everything below still reads a real lifecycle key: the bulk bar asks
  // "what can these rows do", and an opaque tab id cannot answer it.
  const [tab, setTab] = useState<TabScope | null>(null);

  // Mirrored for the Board so the two surfaces can't show different
  // populations of the same dataset.  Pure + tested in ./scope.
  const scopeBase = resolveScopeBase(segment, tab, APP_SEGMENTS);
  const tz = useTimezone();
  const appColumns = useMemo(() => makeAppColumns(tz), [tz]);
  const segmentMatch = useMemo(
    () => scopeMatch(scopeBase, tab, APP_SEGMENTS, appColumns, APP_SEARCH_KEYS),
    [scopeBase, tab, appColumns],
  );
  // (Bulk selection lives inside DataGrid now — no page-level set.)
  const [openId, setOpenId] = useState<number | null>(null);
  // ?app=<id> opens that application directly — the target of the
  // notification deep-link, so a notice lands ON the record it is about
  // rather than on the list with the reader hunting for it.  Consumed
  // once: the param is dropped as soon as it's applied, so closing the
  // panel and re-rendering can't re-open it.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const raw = searchParams.get('app');
    if (!raw) return;
    const id = Number(raw);
    if (Number.isFinite(id) && id > 0) setOpenId(id);
    const next = new URLSearchParams(searchParams);
    next.delete('app');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  // Shared react-query entry (also feeds the topbar ApplicationsHero,
  // so any mutation here re-renders the hero counts in the same tick).
  const { data: appsData, isLoading: loading, error: appsError } = useApplicationsQuery();
  const rows = useMemo(() => appsData?.items ?? [], [appsData]);
  // Past the page limit every count on this page — hero chips, segment
  // badges, board columns — counts the LOADED slice, not the pipeline.
  // Say so rather than let "Submitted 12" read as the whole truth.
  const truncated = (appsData?.total ?? 0) > rows.length;
  // Application Links opens only when there is nothing else to look at —
  // a fresh account's whole job is creating the first link.  Once
  // applications exist the table is the point, so the tool folds and the
  // grid can own a viewport again (see the section's own comment).
  // Seeded once from the first non-empty response rather than tracked, so
  // toggling it stays the operator's choice afterwards.
  const [linksOpen, setLinksOpen] = useState(true);
  const seededLinksOpen = useRef(false);
  useEffect(() => {
    if (seededLinksOpen.current || loading) return;
    seededLinksOpen.current = true;
    setLinksOpen(rows.length === 0);
  }, [loading, rows.length]);
  const boardRows = useMemo(
    () => (segmentMatch
      ? rows.filter((r) => segmentMatch(r as unknown as Record<string, unknown>))
      : rows),
    [rows, segmentMatch],
  );
  const err = appsError instanceof Error ? appsError.message : '';
  // Link create form
  const [label, setLabel] = useState('');
  const [source, setSource] = useState('');
  const [expiryDays, setExpiryDays] = useState(90);  // default: not forever
  const [companyId, setCompanyId] = useState('');   // '' → generic/no brand
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  // Carriers the recruiter can brand a link with (read-only picker).
  const [companies, setCompanies] = useState<PickerCompany[]>([]);

  // Refetch trigger for mutations elsewhere on the page (bulk moves,
  // the detail drawer's onChanged) — invalidates the shared cache so
  // the table, board, and topbar hero all refresh together.
  const loadApps = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['applications'] });
  }, [qc]);

  // Drag-to-stage on the board (and any status move): optimistic, with a
  // revert + message if the server rejects it (illegal jump, or the
  // vetting gate blocking 'approved').  Writes go through the shared
  // query cache so the hero counts move with the drag.
  const moveApp = async (id: number, status: string) => {
    const prev = qc.getQueryData<{ items: AppRow[] }>(['applications']);
    if (prev?.items.find((r) => r.id === id)?.status === status) return;
    qc.setQueryData<{ items: AppRow[] }>(['applications'], (d) =>
      d ? { items: d.items.map((r) => (r.id === id ? { ...r, status } : r)) } : d);
    try {
      await apiJSON(`/applications/${id}/status`, { method: 'PATCH', body: { status } });
    } catch (e) {
      qc.setQueryData(['applications'], prev);
      alert(e instanceof Error ? e.message : 'Could not move application');
    }
  };

  // Bulk move: DataGrid owns the selection + the floating bar + the
  // confirm; this just POSTs the ids.  The server enforces the per-app
  // rules (illegal jumps, the vetting gate, hired-only-via-Hire) and
  // tells us what it skipped.
  // `done` is the real past tense — appending 'd' to a UI label produced
  // "6 applications rejectd" / "2 applications move to screeningd".
  const bulkMove = useCallback((status: string, done: string) =>
    async (bulkRows: Record<string, unknown>[]) => {
      const ids = bulkRows.map(r => (r as unknown as AppRow).id);
      if (ids.length === 0) return;
      try {
        const r = await apiJSON<{ updated: number[]; skipped: { id: number; reason: string }[] }>(
          '/applications/bulk-status', { method: 'POST', body: { ids, status } });
        const n = r.updated.length;
        const skipped = r.skipped ?? [];
        const noun = (k: number) => `${k} application${k === 1 ? '' : 's'}`;
        if (skipped.length) {
          // Printing skipped[0].reason as if it covered every skip hid the
          // fact that rows are skipped for DIFFERENT reasons (illegal
          // transition vs the vetting gate). List each distinct one.
          const why = [...new Set(skipped.map((x) => x.reason))].join('; ');
          toast.warning(`${noun(n)} ${done} · ${skipped.length} skipped — ${why}`);
        } else {
          toast.success(`${noun(n)} ${done}`);
        }
        loadApps();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Bulk action failed');
      }
    }, [loadApps]);

  // Order matters: the constructive move leads so the eye doesn't land on
  // the red one first. Confirms name the count and the irreversibility —
  // NOT a notification, because a status change emails nobody.
  // Only verbs the CURRENT tab's rows can actually take. Reject and
  // Withdraw used to be offered on the Closed tab — i.e. on applications
  // that were already rejected or withdrawn — so the bulk bar advertised
  // work the server would refuse row by row.
  const bulkActions: BulkAction[] = useMemo(() => {
    const reopen: BulkAction = {
      label: scopeBase === 'closed' ? 'Reopen to screening' : 'Move to screening',
      icon: ShieldCheck,
      confirm: (n) => `Move ${n} application${n > 1 ? 's' : ''} to screening?`,
      onRun: bulkMove('screening', 'moved to screening'),
    };
    const reject: BulkAction = {
      label: 'Reject', icon: Ban, tone: 'danger',
      confirm: (n) => `Reject ${n} application${n > 1 ? 's' : ''}?\n\n`
        + 'Applicants are not emailed about status changes. Rejected '
        + 'applications stay on file and can be moved back to screening.',
      onRun: bulkMove('rejected', 'rejected'),
    };
    const withdraw: BulkAction = {
      label: 'Withdraw', icon: X,
      confirm: (n) => `Withdraw ${n} application${n > 1 ? 's' : ''}?\n\n`
        + 'Applicants are not emailed about status changes.',
      onRun: bulkMove('withdrawn', 'withdrawn'),
    };
    // 'hired' is terminal — ALLOWED_MOVES.hired is empty, so nothing here
    // would succeed and the bar stays out of the way.
    // An UNBOUNDED tab (no base) can hold active, hired and closed rows
    // at once, so no verb is valid for all of them — 'hired' is terminal.
    // Offering one anyway is the exact thing the note above records as
    // fixed: a bar advertising work the server refuses row by row.
    if (scopeBase === null) return [];
    if (scopeBase === 'hired') return [];
    if (scopeBase === 'closed') return [reopen];
    return [reopen, reject, withdraw];
  }, [scopeBase, bulkMove]);

  // A failed fetch must never render as "No links yet" — a recruiter who
  // reads that mints a duplicate link because the real one is invisible.
  // Loading / error+retry / empty are three distinct renders.
  const [linksError, setLinksError] = useState<string | null>(null);
  const [linksLoaded, setLinksLoaded] = useState(false);
  const loadLinks = useCallback(() => {
    setLinksError(null);
    return apiJSON<{ items: ApplicationLink[] }>('/applications/links')
      .then((r) => { setLinks(r.items); setLinksLoaded(true); })
      .catch((e) => {
        setLinksError(e instanceof Error ? e.message : 'Could not load application links');
        setLinksLoaded(true);
      });
  }, []);

  useEffect(() => { loadLinks(); }, [loadLinks]);
  // Carriers for the link company-picker + brand preview (no Samsara key).
  const loadCompanies = useCallback(() => {
    apiJSON<{ items: PickerCompany[] }>('/applications/companies')
      .then((r) => setCompanies(r.items))
      .catch(() => { /* non-fatal — picker just shows 'No company' */ });
  }, []);
  useEffect(() => { loadCompanies(); }, [loadCompanies]);

  // A link with no label becomes "(no label)" in a list of "(no label)"s
  // and its provenance is unrecoverable. Cheap to require, impossible to
  // reconstruct later.
  const createLink = async () => {
    if (!label.trim()) {
      toast.error('Give the link a label — it\u2019s how you tell your links apart later.');
      return;
    }
    // A branded link whose carrier is missing the §391.23 contact details
    // would generate an incomplete consent document — warn before creating.
    if (companyId) {
      const co = companies.find((x) => String(x.id) === companyId);
      if (co && (!co.phone || !co.legal_address || !co.compliance_email)
        && !confirm("This carrier is missing its phone, mailing address, or compliance email — the §391.23 consent document will be incomplete. Create the link anyway?")) return;
    }
    setCreating(true);
    try {
      const created = await apiJSON<{ token?: string }>('/applications/links', {
        method: 'POST',
        body: { label, source, expires_in_days: expiryDays || null, company_id: companyId ? Number(companyId) : null },
      });
      // Copy on create: the recruiter's very next act is to paste this
      // somewhere. Previously the handler just cleared three inputs and
      // said nothing at all, success or failure.
      let copiedOk = false;
      if (created?.token) {
        try { await navigator.clipboard.writeText(`${APPLY_BASE}/${created.token}`); copiedOk = true; }
        catch { /* clipboard blocked — the row's Copy button is the fallback */ }
      }
      toast.success(copiedOk ? 'Link created and copied' : 'Link created');
      setLabel(''); setSource(''); setCompanyId('');
      await loadLinks();
    } catch (e) {
      // Was a bare try/finally: a 422 "Unknown company", a 403 or a dropped
      // connection left the inputs full, the list stale and nothing said —
      // the recruiter concluded it had worked.
      toast.error(e instanceof Error ? e.message : 'Could not create the link');
    } finally {
      setCreating(false);
    }
  };

  const [editingLink, setEditingLink] = useState<number | null>(null);

  const revokeLink = async (id: number) => {
    const l = links.find((x) => x.id === id);
    // Name every consequence, not the smallest one. Revoke is irreversible
    // in the product (there is no un-revoke route) and anyone part-way
    // through the form loses their work when the URL dies.
    const name = l?.label ? `"${l.label}"` : 'this link';
    if (!confirm(
      `Revoke ${name}?\n\n`
      + 'The URL stops working immediately, and anyone part-way through the '
      + 'form will lose what they have entered.\n'
      + 'Applications already submitted are kept.\n\n'
      + "This can't be undone — you would have to create a new link.",
    )) return;
    try {
      await apiJSON(`/applications/links/${id}/revoke`, { method: 'POST' });
      toast.success('Link revoked');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not revoke the link');
    } finally {
      // Reconcile either way — a 404 means someone else already changed it.
      await loadLinks();
    }
  };

  const deleteLink = async (id: number) => {
    if (!confirm('Delete this link permanently? Submitted applications are kept; only the link + its stats are removed.')) return;
    try {
      await apiJSON(`/applications/links/${id}`, { method: 'DELETE' });
      toast.success('Link deleted');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not delete the link');
    } finally {
      await loadLinks();
    }
  };

  // Item lists for the create-link Selects (label ≠ value).
  const createCompanyItems = useMemo(() => [
    { value: '', label: 'No company (generic)' },
    ...companies.map((co) => ({ value: String(co.id), label: co.display_name || co.code })),
  ], [companies]);
  const createExpiryItems = useMemo(
    () => EXPIRY_OPTIONS.map((o) => ({ value: String(o.days), label: o.label })),
    [],
  );

  const copyLink = async (l: ApplicationLink) => {
    // The ✓ used to fire unconditionally behind optional chaining, so a
    // blocked clipboard (insecure context, denied permission, rejected
    // promise) showed success and the recruiter pasted nothing.
    try {
      await navigator.clipboard.writeText(`${APPLY_BASE}/${l.token}`);
      setCopied(l.id);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast.error('Couldn\u2019t copy — select the URL and copy it manually');
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Driver Applications" icon={UserPlus}
        description="Application links + submitted driver applications."
        actions={
          <div className="flex items-center gap-2">
            {/* Account-scope config lives behind the gear, in the same
                header slot on every feature. It was a labelled "DQF export"
                button here — which named the artifact rather than the
                action, and taught nothing transferable. DQF is not a peer
                of Applications' config; it is what that config IS.
                The gear self-gates on can_manage_config_all. */}
            <NotificationsBell onOpen={(id) => setOpenId(id)} />
            {/* The config gear is ALWAYS the last action in a page header
                — Scorecards already placed it there, and one position
                across every feature is the whole point of a shared entry
                point. It is also the least-used control on any of these
                pages, so it belongs at the end rather than pushing the
                frequently-used ones rightward. */}
            <FeatureConfigGear feature="Applications" size="xl">
              <ApplicationsConfigPanel />
            </FeatureConfigGear>
          </div>
        } />

      <ConfigMovedNotice what="DQF export settings" />

      {/* ── Application links ──────────────────────────────────── */}
      {/* Application Links is a SECOND FEATURE stacked above the primary
          table — create a link, brand it, list them.  Expanded it runs
          ~900px on a phone, which pushed the applications grid entirely
          below the fold AND past the point where it can own a viewport:
          ``useFittedHeight`` measures the room left under the grid's top
          and correctly declined, so the page grew with the row count
          instead.  The mechanism was right; the page composition was not.

          Collapsed, the table starts near the top and gets its viewport
          back.  The default is not a preference but an ANSWER: with no
          applications yet there is nothing to look at and creating a link
          is the whole job, so it opens; once applications exist the table
          is the point and the tool folds away. */}
      <Card render={<section />}>
        <h2 className="text-base font-semibold flex items-center gap-2 mb-3">
          <button
            type="button"
            onClick={() => setLinksOpen((o) => !o)}
            aria-expanded={linksOpen}
            className="flex flex-1 items-center gap-2 text-left"
          >
            <LinkIcon className="text-muted-foreground size-4" /> Application Links
            {!linksOpen && links.length > 0 && (
              <span className="text-xs font-normal text-muted-foreground">
                {links.length}
              </span>
            )}
            <ChevronDown
              aria-hidden
              className={`ml-auto text-muted-foreground transition-transform ${linksOpen ? '' : '-rotate-90'} size-4`}
            />
          </button>
        </h2>
        {linksOpen && (<>
        <div className="flex flex-wrap gap-2 mb-4">
          <Input placeholder="Label (e.g. Indeed campaign)" value={label}
            onChange={(e) => setLabel(e.target.value)} className="max-w-xs" />
          <Input placeholder="Source (optional)" value={source}
            onChange={(e) => setSource(e.target.value)} className="max-w-40" />
          {companies.length > 0 && (
            <Select value={companyId} onValueChange={(v) => setCompanyId(String(v))} items={createCompanyItems}>
              <SelectTrigger title="Brand the application form for this carrier" aria-label="Carrier brand">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {createCompanyItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
          <Select value={String(expiryDays)} onValueChange={(v) => setExpiryDays(Number(v))} items={createExpiryItems}>
            <SelectTrigger aria-label="Link expiry"><SelectValue /></SelectTrigger>
            <SelectContent>
              {createExpiryItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button onClick={createLink} disabled={creating || !label.trim()} size="sm">
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
        {!linksLoaded ? (
          <p className="text-sm text-muted-foreground">Loading links…</p>
        ) : linksError ? (
          /* An error is not an empty state. Without this branch a 500 or a
             dropped connection rendered "No links yet", and the recruiter
             created a second link because the first was invisible. */
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <span>{linksError}</span>
            <Button size="xs" variant="ghost" onClick={() => loadLinks()}>Try again</Button>
          </div>
        ) : links.length === 0 ? (
          <p className="text-sm text-muted-foreground">No links yet. Create one to start collecting applications.</p>
        ) : (
          <ul className="space-y-1.5">
            {links.map((l) => {
              const expired = !!l.expires_at
                && new Date(l.expires_at).getTime() < Date.now();
              const live = l.is_active === 1 && !expired;
              const daysLeft = l.expires_at
                ? Math.ceil((new Date(l.expires_at).getTime() - Date.now()) / 864e5) : null;
              const soon = live && daysLeft !== null && daysLeft <= 7;
              // Right-click the link → the same actions as the inline
              // buttons (which stay as the visible affordance).
              const linkMenu: MenuAction[] = [
                ...(live ? [{ key: 'copy', label: 'Copy link', icon: <Copy className="text-muted-foreground size-3.5" />, onSelect: () => copyLink(l) }] : []),
                ...(l.is_active === 1 ? [{ key: 'edit', label: 'Edit link', icon: <Pencil className="text-muted-foreground size-3.5" />, onSelect: () => setEditingLink(l.id) }] : []),
                ...(live ? [{ key: 'revoke', label: 'Revoke', icon: <Ban className="size-3.5" />, danger: true, separatorBefore: true, onSelect: () => revokeLink(l.id) }] : []),
                ...(!live ? [{ key: 'delete', label: 'Delete permanently', icon: <Trash2 className="size-3.5" />, danger: true, separatorBefore: true, onSelect: () => deleteLink(l.id) }] : []),
              ];
              return (
              <ContextMenu key={l.id} items={linkMenu} render={<li className="text-sm" />}>
                <div className="flex items-center gap-2">
                  {/* Revoked (deliberate) and expired (passive lapse) used
                      to share one amber pill — different causes, different
                      repairs. Colour encodes urgency only; the date is a
                      fixed secondary slot that survives expiry, so you can
                      still see when and how long a link ran. */}
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    l.is_active !== 1 ? toneClasses('danger')
                    : expired ? toneClasses('neutral')
                    : soon ? toneClasses('warn')
                    : statusClasses('active')}`}>
                    {l.is_active !== 1 ? 'revoked' : expired ? 'expired' : 'active'}
                  </span>
                  {(l.company_name || l.company_code) && (
                    <span className={`rounded-md px-1.5 py-0.5 text-2xs ${toneClasses('info')}`}
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
                    {/* A percentage off n=1 reads as a statistic and isn't
                        one. Under five submissions, say the raw ratio. */}
                    {(l.submissions ?? 0) >= 5 ? (
                      <span className="ml-1 text-foreground">
                        ({Math.round(((l.hires ?? 0) / (l.submissions || 1)) * 100)}% hire rate)
                      </span>
                    ) : null}
                    {expired && (l.view_count ?? 0) > (l.submissions ?? 0) && (
                      <span className="ml-1">· visits since expiry can&rsquo;t submit</span>
                    )}
                  </span>
                  {/* The window used to be gated on `live`, so the moment a
                      link lapsed its date vanished and you could no longer
                      see when or how long it ran. Always render it. */}
                  <span className="text-2xs text-muted-foreground whitespace-nowrap">
                    {!l.expires_at ? '· no expiry'
                      : expired
                        ? `· expired ${formatDate(l.expires_at, { timeZone: tz, intl: { hour: undefined, minute: undefined } })}`
                        : soon
                          ? `· expires in ${Math.max(daysLeft ?? 0, 0)}d`
                          : `· expires ${formatDate(l.expires_at, { timeZone: tz, intl: { hour: undefined, minute: undefined } })}`}
                  </span>
                  <div className="ml-auto flex items-center gap-1">
                    {live && (
                      <button onClick={() => copyLink(l)} aria-label="Copy link"
                        className="text-muted-foreground hover:text-foreground inline-flex size-7 items-center justify-center rounded-md hover:bg-muted min-h-tap min-w-tap">
                        {copied === l.id ? <Check className="text-ok size-3.5" /> : <Copy className="size-3.5" />}
                      </button>
                    )}
                    {l.is_active === 1 && (
                      <button onClick={() => setEditingLink(editingLink === l.id ? null : l.id)}
                        title="Edit link (label, source, carrier, expiry)"
                        className={`inline-flex size-7 items-center justify-center rounded-md hover:bg-muted ${editingLink === l.id ? 'text-foreground bg-muted' : 'text-muted-foreground hover:text-foreground'} min-h-tap min-w-tap`}>
                        <Pencil className="size-3.5" />
                      </button>
                    )}
                    {live && (
                      <button onClick={() => revokeLink(l.id)} aria-label="Revoke"
                        className="text-muted-foreground hover:text-destructive inline-flex size-7 items-center justify-center rounded-md hover:bg-muted min-h-tap min-w-tap">
                        <Ban className="size-3.5" />
                      </button>
                    )}
                    {!live && (
                      <button onClick={() => deleteLink(l.id)} aria-label="Delete link permanently"
                        className="text-muted-foreground hover:text-destructive inline-flex size-7 items-center justify-center rounded-md hover:bg-muted min-h-tap min-w-tap">
                        <Trash2 className="size-3.5" />
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
              </ContextMenu>
              );
            })}
          </ul>
        )}
        </>)}
      </Card>

      {/* ── In-progress drafts (save & resume funnel stage) ─────── */}
      <InProgressDrafts />

      {/* ── Applications (table or board) ──────────────────────────
          The grid is full-toolkit DataGrid (own card, segment tabs,
          toolbar search, column filters, export, pagination) — so
          this section is NOT a card anymore; the old per-status chip
          row + standalone search moved into the grid: lifecycle =
          Active / Hired / Closed tabs, stage slicing = the Status
          column filter, live stage counts = topbar ApplicationsHero. */}
      <section>
        <div className="flex flex-wrap items-center gap-1.5 pb-3">
          <div className="inline-flex rounded-md border border-border p-0.5">
            <button onClick={() => setView('table')}
              className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${view === 'table' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'} min-h-tap`}>
              <List className="size-3" /> Table
            </button>
            <button onClick={() => setView('board')}
              className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${view === 'board' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'} min-h-tap`}>
              <LayoutGrid className="size-3" /> Board
            </button>
          </div>
        </div>
        {/* Bulk-action bar is rendered by DataGrid from ``bulkActions``. */}
        {err && <div className="p-3 text-sm text-destructive">{err}</div>}
        {/* Narrowed, not deleted, now that ``totalRows`` is passed.  The
            grid disables the five whole-set operations itself and says
            why, so repeating that here would be a second voice on the
            same fact.  What it CANNOT speak for is what stays local:
            these segments carry ``match`` predicates, so the tab badges
            tally the loaded rows, and the column filters and search do
            too.  That is the part with no other disclosure. */}
        {truncated && (
          <div className={`mx-3 mb-2 rounded-md border px-3 py-2 text-xs ${toneClasses('warn')}`}>
            The newest {rows.length} of {appsData?.total} applications are
            loaded — the tab counts, filters and search cover only those.
            Search or pick a status to reach older ones.
          </div>
        )}
        {/* Both views stay MOUNTED and one is hidden. Conditionally
            rendering the grid unmounted it on every Table→Board→Table
            round trip, silently discarding the recruiter's search text,
            column filters, segment tab, sort and page index — their whole
            working set, with no warning and no way back. */}
        {view === 'board' && (
          <Card padding="none" className="overflow-hidden">
            <ApplicationsBoard rows={boardRows} loading={loading} onMove={moveApp} onOpen={setOpenId} />
          </Card>
        )}
        <div className={view === 'table' ? '' : 'hidden'}>
        {loading ? (
          <div className="bg-card border border-border rounded-lg text-center text-muted-foreground py-8">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="bg-card border border-border rounded-lg text-center text-muted-foreground py-8">
            No applications yet.
          </div>
        ) : (
          <DataGrid
            tableId="applications"
            // Declares the slice.  Undefined-vs-number is what the grid
            // reads, so passing the true total always is right: it only
            // gates once the total EXCEEDS the rows in hand.
            //
            // Without it every whole-set control stayed live over the
            // newest 500 — most damagingly Export, whose "All rows" item
            // wrote a file suffixed ``-all`` containing 500 of N.  A
            // recruiter hands that to compliance as the full applicant
            // set.  Now: sort, group and aggregate disable with a reason,
            // and the export says "All loaded rows" with both numbers.
            totalRows={appsData?.total}
            segments={APP_SEGMENTS}
            segmentKey={tab ? TAB_PREFIX + tab.id : segment}
            onSegmentChange={(k, saved) => {
              if (!k.startsWith(TAB_PREFIX)) { setTab(null); setSegment(k); return; }
              const id = k.slice(TAB_PREFIX.length);
              // ``baseSegment`` is absent for a tab saved while already on
              // a tab.  Keep the slice we're on rather than defaulting to
              // one — inventing a base would silently re-widen the scope.
              setTab({ id, filters: saved?.filters ?? [], search: saved?.search ?? '',
                       baseSegment: saved?.baseSegment });
              // Only a LIVE key: a tab persisted before a segment rename
              // carries a dead one, and `segment` is the slot every
              // lifecycle reader trusts.
              if (saved?.baseSegment
                  && APP_SEGMENTS.some((sg) => sg.key === saved.baseSegment)) {
                setSegment(saved.baseSegment);
              }
            }}
            savedTabs
            data={rows as unknown as Record<string, unknown>[]}
            searchKey={APP_SEARCH_KEYS}
            searchPlaceholder="Search name, email, ref…"
            onRowClick={(row) => setOpenId((row as unknown as AppRow).id)}
            columns={appColumns}
            // Bulk selection + action bar are DataGrid's (the SSOT).
            bulkSelection
            bulkActions={bulkActions}
            bulkRowLabel={(r) => (r as unknown as AppRow).reference}
          />
        )}
        </div>
      </section>

      {openId !== null && (
        <ApplicationDetail appId={openId} onOpen={setOpenId} onClose={() => setOpenId(null)}
          onChanged={() => { loadApps(); }} />
      )}
    </div>
  );
}

// ── In-progress drafts ──────────────────────────────────────────────
// Applicants who started but haven't submitted (server-side save&resume).
// Deliberately shows ONLY name / masked contact / progress — the draft
// body is pre-consent PII recruiters must not read.  [Remind] re-emails
// the applicant their resume link (capped server-side to 1/20h).
// Matches the resume token's idle window (router.py) — a draft that goes
// untouched this long can no longer be resumed.
const DRAFT_IDLE_DAYS = 14;

interface DraftRow {
  id: number; first_name: string; last_name: string; email_masked: string;
  step: number; steps_total: number; link_label: string;
  created_at: string; updated_at: string; reminder_sent_at: string | null;
}

function InProgressDrafts() {
  const tz = useTimezone();
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [remindBusy, setRemindBusy] = useState<number | null>(null);

  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await apiJSON<{ items: DraftRow[] }>('/applications/drafts');
      setRows(r.items || []);
    } catch (e) {
      // The whole section used to `return null` on failure — visually
      // identical to "nobody has an open draft". A recruiter would never
      // learn that live leads existed.
      setError(e instanceof Error ? e.message : 'Could not load in-progress drafts');
    } finally { setLoaded(true); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const remind = async (id: number) => {
    setRemindBusy(id);
    try {
      const r = await apiJSON<{ sent: boolean }>(`/applications/drafts/${id}/remind`, { method: 'POST' });
      if (r.sent) { toast.success('Reminder sent'); load(); }
      else toast.error('Email is not configured on this server');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not send the reminder');
    } finally { setRemindBusy(null); }
  };

  if (!loaded) return null;
  if (error) {
    return (
      <section className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
        {error} <button type="button" className="underline py-0.5 -my-0.5 min-h-tap" onClick={() => load()}>Try again</button>
      </section>
    );
  }
  if (rows.length === 0) return null;   // genuinely no drafts → no section

  const columns: AnyColumn[] = [
    {
      key: 'first_name', label: 'Applicant', sortable: true,
      render: (_v, row) => {
        const r = row as unknown as DraftRow;
        const name = `${r.first_name} ${r.last_name}`.trim();
        return <span className="font-medium text-foreground">{name || '(no name yet)'}</span>;
      },
    },
    {
      key: 'email_masked', label: 'Contact',
      render: (v) => <span className="font-mono text-xs text-muted-foreground">{String(v)}</span>,
    },
    {
      key: 'step', label: 'Progress', sortable: true,
      render: (_v, row) => {
        const r = row as unknown as DraftRow;
        const total = r.steps_total || 1;
        const pct = Math.min(100, Math.round(((r.step + 1) / total) * 100));
        return (
          <span className="flex items-center gap-2">
            <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
              <span className="block h-full bg-primary" style={{ width: `${pct}%` }} />
            </span>
            <span className="text-xs text-muted-foreground">{Math.min(r.step + 1, total)} of {total}</span>
          </span>
        );
      },
      sortKey: (row) => (row as unknown as DraftRow).step,
    },
    {
      key: 'link_label', label: 'Apply link', filterable: true,
      // A bare "—" under a column called "Link" told the reader nothing
      // about what it would ever contain.
      render: (v) => (
        <span className="text-muted-foreground">
          {String(v || '') || <span className="opacity-70">unlabelled link</span>}
        </span>
      ),
    },
    {
      key: 'updated_at', label: 'Last active', sortable: true,
      // A draft dies 14 days after the last activity (the resume token's
      // idle window), so "Remind" has a real deadline behind it. Without
      // it the nudge carried no urgency and no honest reason.
      render: (v) => {
        const last = new Date(String(v)).getTime();
        const daysLeft = Number.isFinite(last)
          ? Math.ceil((last + DRAFT_IDLE_DAYS * 864e5 - Date.now()) / 864e5)
          : null;
        return (
          <span className="flex flex-col">
            <span className="text-muted-foreground">{formatDate(String(v), { timeZone: tz })}</span>
            {daysLeft !== null && (
              <span className={`text-2xs ${daysLeft <= 3 ? toneText('warn') : 'text-muted-foreground'}`}>
                {daysLeft <= 0 ? 'draft expired' : `draft expires in ${daysLeft}d`}
              </span>
            )}
          </span>
        );
      },
    },
    {
      key: 'id', label: '',
      render: (_v, row) => {
        const r = row as unknown as DraftRow;
        return (
          <Button size="xs" variant="outline" disabled={remindBusy === r.id}
            onClick={(e) => { e.stopPropagation(); remind(r.id); }}>
            {remindBusy === r.id ? '…' : 'Remind'}
          </Button>
        );
      },
    },
  ];

  return (
    <Card render={<section />}>
      <h2 className="text-base font-semibold flex items-center gap-2 mb-1">
        <Clock3 className="text-muted-foreground size-4" /> In progress ({rows.length})
      </h2>
      <p className="text-xs text-muted-foreground mb-3">
        Started but not submitted. Drafts stay private until submission — you see
        progress only. Reminders re-send the applicant their resume link.
      </p>
      <DataGrid columns={columns} data={rows as unknown as Record<string, unknown>[]}
        enableToolbar={false} enablePagination={rows.length > 25} />
    </Card>
  );
}

// ── Kanban board ────────────────────────────────────────────────────

// Pipeline columns.  'hired' is NOT droppable — hiring goes through the
// Hire action (it mints the driver invite); dropping a card there would
// 409.  Dropping into 'approved' still hits the vetting gate server-side.
// Mirrors service.STATUS_TRANSITIONS. The server is authoritative — this
// copy exists only so a drag can SHOW which columns will accept the card
// instead of the recruiter learning by being silently reverted.  A stale
// copy costs a rejected drop with a real message, never a wrong write.
const ALLOWED_MOVES: Record<string, string[]> = {
  submitted: ['screening', 'rejected', 'withdrawn'],
  screening: ['interview', 'approved', 'rejected', 'withdrawn'],
  interview: ['approved', 'rejected', 'withdrawn'],
  approved: ['rejected', 'withdrawn'],
  rejected: ['screening'],
  withdrawn: ['screening'],
  hired: [],
};

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
  const tz = useTimezone();
  const [dragId, setDragId] = useState<number | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  const dragging = dragId != null ? rows.find((r) => r.id === dragId) : undefined;
  // Legal for THIS card, not just "is the column droppable at all".
  const canDropIn = (key: string) => {
    if (!dragging) return false;
    if (key === dragging.status) return false;
    return (ALLOWED_MOVES[dragging.status] ?? []).includes(key);
  };

  if (loading) return <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="flex gap-3 overflow-x-auto p-3">
      {BOARD_COLUMNS.map(({ key, droppable }) => {
        const items = rows.filter((r) => r.status === key);
        const ok = droppable && canDropIn(key);
        const isOver = overCol === key && ok;
        // While dragging, columns that can't take this card dim out — the
        // rules used to be knowable only by dropping and being reverted.
        const barred = dragging != null && !ok && key !== dragging.status;
        return (
          <div key={key}
            onDragOver={(e) => { if (ok) { e.preventDefault(); setOverCol(key); } }}
            onDragLeave={() => setOverCol((c) => (c === key ? null : c))}
            onDrop={(e) => { e.preventDefault(); setOverCol(null); if (ok && dragId != null) onMove(dragId, key); setDragId(null); }}
            title={
              key === 'hired'
                ? 'Hiring happens through the Hire button in the application — it also mints the driver invite.'
                : barred ? `Can't move a ${dragging?.status} application straight to ${key}.` : undefined
            }
            className={`flex w-56 shrink-0 flex-col rounded-lg border transition-opacity ${
              isOver ? 'border-primary bg-primary/5' : 'border-border bg-muted/30'
            } ${barred ? 'opacity-40' : ''}`}>
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className={`rounded-md px-2 py-0.5 text-xs font-medium capitalize ${statusClasses(key)}`}>{key}</span>
              <span className="text-2xs text-muted-foreground">
                {key === 'hired' && (
                  <Lock className="mr-1 inline text-muted-foreground size-3" aria-label="Locked — use the Hire button" />
                )}
                {items.length}
              </span>
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
                    <p className="text-2xs text-muted-foreground tabular-nums">{r.submitted_at ? formatDay(r.submitted_at, { timeZone: tz }) : '—'}</p>
                    {r.duplicate && <span className={`rounded-md px-1 py-0.5 text-3xs ${toneClasses('warn')}`}>re-applicant</span>}
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
  const tz = useTimezone();
  const [app, setApp] = useState<AppDetail | null>(null);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);

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

  // <Sheet> — which also RETIRES the manual portal this used to need.
  // The old note explained it well: rendered inline, a `fixed` overlay
  // gets re-anchored to the shell's rounded content card by any ancestor
  // forming a containing block (transform/filter/contain), so the drawer
  // started below the topbar and the scrim never dimmed the chrome.
  // SheetContent portals itself, so that whole class of bug is the
  // primitive's problem now — along with the focus trap, Escape,
  // aria-modal and scroll lock the hand-rolled version never had.
  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side="right"
        size="xl"
        aria-label="Application detail"
        // The header already carries a ✕ beside "Download packet"; the
        // primitive's own landed on top of it — the audit caught the two
        // overlapping here first, but all three conversions had it.
        showCloseButton={false}
      >
        <SheetBody label="Application detail" className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <SheetTitle className="text-lg font-semibold">
            {app ? `${app.first_name} ${app.last_name}` : 'Loading…'}
            {app && <span className="ml-2 font-mono text-xs text-muted-foreground">{app.reference}</span>}
          </SheetTitle>
          <div className="flex items-center gap-1">
            {app && (
              <button onClick={downloadPacket} aria-label="Download application packet (PDF)"
                className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground min-h-tap">
                <Download className="size-3.5" /> Download packet (PDF)
              </button>
            )}
            <SheetClose aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X className="size-4" /></SheetClose>
          </div>
        </div>

        {app && (
          <>
            {/* Re-applicant warning — informational, never blocks anything. */}
            {app.related && app.related.length > 0 && (
              <div className={`rounded-md p-3 text-sm ${toneClasses('warn')}`}>
                <p className="flex items-center gap-1.5 font-medium">
                  <Users className="size-3.5" /> Re-applicant — {app.related.length} prior application{app.related.length > 1 ? 's' : ''} in this account
                </p>
                <ul className="mt-1.5 space-y-0.5">
                  {app.related.map((r) => (
                    <li key={r.id}>
                      <button onClick={() => onOpen(r.id)} className="text-xs underline hover:no-underline py-1 -my-1 min-h-tap">
                        {r.reference} · <span className="capitalize">{r.status}</span> · {r.submitted_at ? formatDay(r.submitted_at, { timeZone: tz }) : ''}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Stage setter — not a filter row. All seven used to be
                equally clickable while the server accepts three to five,
                so the recruiter learned the state machine by collecting
                409s. Illegal moves are now disabled WITH the reason. */}
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">
                Stage — currently <b className="text-foreground capitalize">{app.status}</b>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {STATUSES.map((s) => {
                  const current = app.status === s;
                  const allowed = current || (ALLOWED_MOVES[app.status] ?? []).includes(s);
                  const needsChecks = s === 'approved' && allowed && (() => {
                    const req = VETTING_CHECKS.filter((c) => c.required);
                    return req.some((c) => !app.vetting?.[c.key]?.done);
                  })();
                  return (
                    <button key={s} onClick={() => setStatus(s)}
                      disabled={busy || !allowed || current || needsChecks}
                      title={
                        current ? 'Current stage'
                          : !allowed ? `Can't move a ${app.status} application straight to ${s}.`
                            : needsChecks ? 'Complete the required pre-hire checks first.'
                              : `Move to ${s}`
                      }
                      className={`px-2.5 py-1 rounded-md text-xs capitalize disabled:cursor-not-allowed ${
                        current
                          ? `${statusClasses(s)} ring-1 ring-ring`
                          : allowed && !needsChecks
                            ? 'border border-border text-foreground hover:bg-muted'
                            : 'border border-border text-muted-foreground opacity-40'
                      } min-h-tap`}>
                      {s}
                    </button>
                  );
                })}
              </div>
            </div>

            {app.status === 'approved' && (
              // Approving is where recruiting ends: onboarding mints a USER,
              // so it belongs to driver administration and happens on
              // Drivers → Onboarding (owner decision 2026-07-30).
              <div className="rounded-md border border-border p-3 text-2xs text-muted-foreground">
                <span className="font-medium text-foreground">Approved.</span>{' '}
                Whoever administers drivers finishes the hire from
                <span className="font-medium text-foreground"> Drivers → Onboarding</span> —
                that step creates the driver invite.
              </div>
            )}

            <Section title="Pre-hire checks">
              {/* Five identical empty circles with no tally made the
                  remaining work uncountable. Required ones lead, because
                  they are what actually blocks approval. */}
              {(() => {
                const req = VETTING_CHECKS.filter((c) => c.required);
                const doneReq = req.filter((c) => app.vetting?.[c.key]?.done).length;
                return (
                  <p className="mb-1.5 text-xs text-muted-foreground">
                    <b className={doneReq === req.length ? 'text-ok' : 'text-foreground'}>
                      {doneReq} of {req.length}
                    </b>{' '}required checks complete
                    {doneReq < req.length && ' — approval is blocked until they are'}
                  </p>
                );
              })()}
              <div className="space-y-1.5">
                {[...VETTING_CHECKS].sort((a, b) => Number(b.required) - Number(a.required))
                  .map(({ key, label, required }) => {
                  const done = !!app.vetting?.[key]?.done;
                  return (
                    <button key={key} type="button" onClick={() => toggleCheck(key, !done)}
                      className="flex w-full items-center gap-2 text-left text-sm py-1 -my-1 min-h-tap">
                      <span className={`flex size-4 shrink-0 items-center justify-center rounded border ${done ? 'border-ok bg-ok-bg' : 'border-border'}`}>
                        {done && <Check className="text-ok size-3" />}
                      </span>
                      <span className="text-foreground">{label}</span>
                      {required && <span className="text-2xs text-muted-foreground">required</span>}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 flex items-center gap-1 text-2xs text-muted-foreground">
                <ShieldCheck className="size-3" /> All required checks must be complete before approving.
              </p>
            </Section>

            <Section title="Documents">
              <DocumentGrid appId={appId} docs={app.docs} />
            </Section>

            <Section title="Contact">
              <Row k="Email" v={app.email} /><Row k="Phone" v={app.phone} />
              <Row k="Location" v={[app.city, app.state].filter(Boolean).join(', ')} />
              <PiiRow label="DOB" value={app.dob} kind="dob" />
              <PiiRow label="SSN" value={app.ssn} kind="ssn" />
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
            {['screening', 'interview', 'approved', 'hired'].includes(app.status) && (
              <Section title="Employment verification (§391.23)">
                <VerificationsPanel appId={app.id} />
              </Section>
            )}
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
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}


/** Identity PII behind a deliberate reveal.
 *
 *  The drawer opens on a row click, often with someone else at the desk, and
 *  SSN and date of birth were rendered permanently in clear text with
 *  nothing acknowledging what they are. Together they are the pair that
 *  makes identity theft easy, so both mask. One click on the rare occasion
 *  it is genuinely needed; gone from every shoulder-surf, screen-share and
 *  screenshot the rest of the time. */
function PiiRow({ label, value, kind }: {
  label: string; value?: string; kind: 'ssn' | 'dob';
}) {
  const [shown, setShown] = useState(false);
  if (!value) return <Row k={label} v={value} mono />;
  const masked = kind === 'ssn'
    ? `•••-••-${value.replace(/\D/g, '').slice(-4) || '••••'}`
    // Year alone is useless for impersonation but still answers the only
    // question a recruiter asks of a DOB: is this person old enough?
    : `••/••/${(value.match(/\d{4}/) || [''])[0] || '••••'}`;
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-28 shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="font-mono text-sm text-foreground">{shown ? value : masked}</span>
      <button type="button" onClick={() => setShown((v) => !v)}
        aria-label={`${shown ? 'Hide' : 'Reveal'} ${label}`}
        className="text-xs text-muted-foreground underline hover:text-foreground py-1 -my-1 min-h-tap">
        {shown ? 'Hide' : 'Reveal'}
      </button>
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
  // An array of OBJECTS (accidents, violations) used to stringify to
  // "[object Object], [object Object]" in front of a recruiter. Recurse.
  if (Array.isArray(v)) {
    return v.length
      ? v.map((x) => (x && typeof x === 'object' ? renderVal(x) : String(x)))
         .filter(Boolean).join(' — ')
      : '';
  }
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

// ── §391.23 employer verification panel ─────────────────────────────
// One row per prior FMCSA employer from the last 3 years.  [Send] emails
// the safety-history request PDF (with the driver's signed release) to the
// address the recruiter enters.  The driver's "may we contact?" answer is
// a timing courtesy — it soft-gates with a confirm, never blocks (the
// investigation is federally required before hire).
interface VerifRow {
  employer_index: number; company: string; city: string; state: string;
  phone: string; from: string; to: string; current: boolean;
  position: string; contact_ok: string; usdot?: string; mc?: string;
  employer_email?: string;
  verification: {
    id: number; status: string; attempts: number; employer_email: string;
    sent_at: string | null; responded_at: string | null; notes: string;
  } | null;
}

function VerificationsPanel({ appId }: { appId: number }) {
  const tz = useTimezone();
  const [rows, setRows] = useState<VerifRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emails, setEmails] = useState<Record<number, string>>({});
  const [busyIdx, setBusyIdx] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await apiJSON<{ items: VerifRow[] }>(`/applications/${appId}/verifications`);
      setRows(r.items || []);
      setEmails((prev) => {
        const next = { ...prev };
        for (const it of r.items || []) {
          // Priority: an address already used for a send > the FMCSA-registry
          // email captured when the driver picked the carrier > blank.
          const known = it.verification?.employer_email || it.employer_email;
          if (known && !next[it.employer_index]) next[it.employer_index] = known;
        }
        return next;
      });
    } catch (e) {
      // Critical: the empty branch prints "No FMCSA-regulated employers in
      // the last 3 years" — a compliance ALL-CLEAR. A fetch failure must
      // never be able to render that sentence.
      setError(e instanceof Error ? e.message : 'Could not load employment verifications');
    } finally { setLoaded(true); }
  }, [appId]);
  useEffect(() => { load(); }, [load]);

  const send = async (row: VerifRow) => {
    const email = (emails[row.employer_index] || '').trim();
    if (!email) { toast.error('Enter the employer’s safety-department email first'); return; }
    // Soft gate: the driver's timing preference gets an explicit confirm.
    if (row.contact_ok === 'later' &&
        !confirm(`${row.company}: the driver asked to wait until after the interview before contacting this employer. Send anyway?`)) return;
    if (row.contact_ok === 'no' &&
        !confirm(`${row.company}: the driver asked NOT to contact this employer. §391.23 still requires this investigation before hire — typically sent at the final stage. Send anyway?`)) return;
    setBusyIdx(row.employer_index);
    try {
      await apiJSON(`/applications/${appId}/verifications/send`, {
        method: 'POST', body: { employer_index: row.employer_index, email },
      });
      toast.success(`Request sent to ${row.company}`);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Send failed');
    } finally { setBusyIdx(null); }
  };

  const mark = async (row: VerifRow, status: 'received' | 'no_response') => {
    if (!row.verification) return;
    try {
      await apiJSON(`/applications/${appId}/verifications/${row.verification.id}`, {
        method: 'PATCH', body: { status },
      });
      load();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Update failed'); }
  };

  if (!loaded) return <p className="text-xs text-muted-foreground">Loading…</p>;
  if (error) {
    return (
      <p className="text-xs text-destructive">
        {error} — this is NOT a clearance; the §391.23 list could not be loaded.
      </p>
    );
  }
  if (rows.length === 0) {
    return <p className="text-xs text-muted-foreground">No FMCSA-regulated employers in the last 3 years — nothing to investigate.</p>;
  }
  const doneCount = rows.filter((r) => r.verification?.status === 'received').length;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-2xs text-muted-foreground">
        Safety-performance-history requests to prior DOT-regulated employers (last 3 years).
        The request PDF carries the driver's signed release. {doneCount} of {rows.length} verified.
      </p>
      {rows.map((r) => {
        const v = r.verification;
        const tone = v?.status === 'received' ? 'ok' : v?.status === 'no_response' ? 'warn' : v?.status === 'sent' ? 'info' : 'neutral';
        const statusLabel = !v ? 'Not sent'
          : v.status === 'sent' ? `Sent ${v.sent_at ? formatDay(v.sent_at, { timeZone: tz }) : ''} · ${v.attempts} attempt${v.attempts > 1 ? 's' : ''}`
          : v.status === 'received' ? `Response received ${v.responded_at ? formatDay(v.responded_at, { timeZone: tz }) : ''}`
          : v.status === 'no_response' ? `No response · ${v.attempts} attempt${v.attempts > 1 ? 's' : ''} documented`
          : v.status;
        return (
          <div key={r.employer_index} className="rounded-md border border-border p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-foreground">{r.company || `Employer #${r.employer_index + 1}`}</span>
              <span className="text-xs text-muted-foreground">
                {r.from} → {r.current ? 'present' : r.to}
                {r.usdot ? <span className="ml-1.5 font-mono">USDOT {r.usdot}</span> : null}
                {r.mc ? <span className="ml-1.5 font-mono">MC {r.mc}</span> : null}
                {r.phone ? <span className="ml-1.5">· {r.phone}</span> : null}
              </span>
              {r.contact_ok === 'later' && <span className={`rounded-md px-1.5 py-0.5 text-2xs ${toneClasses('warn')}`}>driver: after interview</span>}
              {r.contact_ok === 'no' && <span className={`rounded-md px-1.5 py-0.5 text-2xs ${toneClasses('danger')}`}>driver: do not contact</span>}
              <span className={`ml-auto rounded-md px-1.5 py-0.5 text-2xs ${toneClasses(tone)}`}>{statusLabel}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Input placeholder="safety@employer.com" value={emails[r.employer_index] ?? ''}
                onChange={(e) => setEmails((m) => ({ ...m, [r.employer_index]: e.target.value }))}
                className="h-8 max-w-60 text-xs" />
              <Button size="xs" variant="outline" disabled={busyIdx === r.employer_index}
                onClick={() => send(r)}>
                {busyIdx === r.employer_index ? '…' : v ? 'Re-send' : 'Send request'}
              </Button>
              {v && v.status === 'sent' && (
                <>
                  <Button size="xs" variant="ghost" onClick={() => mark(r, 'received')}>Mark received</Button>
                  <Button size="xs" variant="ghost" onClick={() => mark(r, 'no_response')}>No response</Button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

interface EmploymentRow {
  company?: string; position?: string; city?: string; state?: string;
  from?: string; to?: string; current?: boolean; reason?: string;
  gapExplanation?: string; fmcsa?: string; contactOk?: string;
  phone?: string; usdot?: string; employerEmail?: string;
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
            {j.usdot ? <> · <span className="font-mono">USDOT {j.usdot}</span></> : null}
            {j.phone ? <> · {j.phone}</> : null}
            {j.employerEmail ? <> · {j.employerEmail}</> : null}
          </p>
        </div>
      ))}
    </div>
  );
}

// Must stay in step with service.REQUIRED_CONSENTS — this list was missing
// `employment_verification`, the 49 CFR §391.23 prior-employer records
// release, so the one consent that authorises contacting past employers was
// the one the reviewer never saw.
const CONSENT_LABELS: [string, string][] = [
  ['psp', 'FMCSA PSP'], ['mvr', 'Motor Vehicle Record'], ['clearinghouse', 'Drug & Alcohol Clearinghouse'],
  ['fcra', 'Background check (FCRA)'], ['drug', 'Pre-employment drug screen'],
  ['employment_verification', 'Prior-employer records release (§391.23)'],
  ['truthful', 'Truthful & complete certification'],
];
function Consents({ c }: { c: Record<string, unknown> }) {
  // Every consent here is server-required at submit, so a missing key means
  // the record predates the field — NOT that the applicant refused. A red X
  // for "we never asked" is an accusation the data doesn't support.
  const asked = (k: string) => c != null && Object.prototype.hasOwnProperty.call(c, k);
  return (
    <div className="space-y-1">
      {CONSENT_LABELS.map(([k, label]) => (
        <div key={k} className="flex items-center gap-1.5 text-sm">
          {c?.[k]
            ? <Check className="text-ok shrink-0 size-3.5" />
            : asked(k)
              ? <X className="text-destructive shrink-0 size-3.5" />
              : <Minus className="text-muted-foreground shrink-0 size-3.5" />}
          <span className={c?.[k] ? 'text-foreground' : 'text-muted-foreground'}>
            {label}
            {!c?.[k] && !asked(k) && (
              <span className="ml-1 text-xs">— not asked on this application</span>
            )}
          </span>
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
      className="group flex flex-col overflow-hidden rounded-md border border-border text-left hover:border-ring disabled:cursor-default py-0.5 -my-0.5 min-h-tap">
      <div className="flex aspect-[4/3] items-center justify-center bg-muted">
        {err ? <span className="text-2xs text-muted-foreground">unavailable</span>
          : !url ? <span className="text-2xs text-muted-foreground">loading…</span>
          : isImage ? <img src={url} alt={label} className="h-full w-full object-cover" />
          : <FileText className="text-muted-foreground size-6" />}
      </div>
      <div className="flex items-center justify-between gap-1 px-2 py-1.5">
        <span className="truncate text-2xs text-foreground">{label}</span>
        {url && <ExternalLink className="shrink-0 text-muted-foreground group-hover:text-foreground size-3" />}
      </div>
    </button>
  );
}

// ── In-app notifications (bell + dropdown + channel prefs) ──────────
//
// The notices come from the ONE shared inbox, filtered to this feature's
// source — not a second store.  Read-state is therefore shared with the
// top-bar bell's Applications tab: clearing one clears the other.

const NOTIFY_CHANNELS: { key: string; label: string; icon: typeof Bell }[] = [
  { key: 'telegram', label: 'Bot', icon: MessageSquare },
  { key: 'email', label: 'Email', icon: Mail },
  { key: 'dashboard', label: 'Dashboard', icon: Monitor },
];

function NotificationsBell({ onOpen }: { onOpen: (appId: number) => void }) {
  const tz = useTimezone();
  const [open, setOpen] = useState(false);
  const [channels, setChannels] = useState<string[]>([]);
  // Channels this person can actually be reached on.  Email and Telegram
  // need a verified connection; the in-app inbox always delivers.
  const [connected, setConnected] = useState<string[]>([]);

  const { notices, unread } = useInboxSource('applications', true);
  const { markRead, markManyRead } = useInboxActions();
  // Blank is a REAL value here (all channels off), so a failed load must
  // not be allowed to render as "you have everything switched off".
  const [prefsLoaded, setPrefsLoaded] = useState(false);
  useEffect(() => {
    apiJSON<{ channels: string[]; connected: string[] }>('/applications/notify-prefs')
      .then((r) => {
        setChannels(r.channels); setConnected(r.connected ?? []);
        setPrefsLoaded(true);
      })
      .catch(() => { /* stays unloaded — the row hides rather than lying */ });
  }, []);

  // Only THIS feature's rows — read-all has no source parameter and
  // would clear System and Activity notices the reader never saw.
  const markAll = () => markManyRead(notices.filter((n) => !n.read).map((n) => n.id));
  const openNotif = (n: InboxNotice) => {
    if (!n.read) void markRead(n.id);
    setOpen(false);
    const appId = Number(new URLSearchParams(
      (n.url.split('?')[1] || '')).get('app') || 0);
    if (appId > 0) onOpen(appId);
  };
  const toggleChannel = async (key: string) => {
    const next = channels.includes(key) ? channels.filter((c) => c !== key) : [...channels, key];
    setChannels(next);
    const r = await apiJSON<{ channels: string[]; connected: string[] }>(
      '/applications/notify-prefs', { method: 'PUT', body: { channels: next } });
    setChannels(r.channels);
    if (r.connected) setConnected(r.connected);
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} aria-label="Notifications"
        className="relative rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground">
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-2xs font-semibold text-destructive-foreground">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <Card padding="none" className="absolute right-0 z-50 mt-2 w-80 shadow-lg">
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">Notifications</span>
              {unread > 0 && (
                <button onClick={markAll} className="inline-flex items-center gap-1 text-xs text-primary hover:underline py-1 -my-1 min-h-tap">
                  <CheckCheck className="size-3" /> Mark all read
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto">
              {notices.length === 0 ? (
                <p className="px-3 py-6 text-center text-sm text-muted-foreground">No notifications yet.</p>
              ) : notices.map((n) => (
                <button key={n.id} onClick={() => openNotif(n)}
                  className={`flex w-full flex-col items-start gap-0.5 border-b border-border/50 px-3 py-2 text-left hover:bg-muted/50 ${n.read ? '' : 'bg-primary/5'}`}>
                  <span className="flex w-full items-center gap-1.5">
                    {!n.read && <span className="size-1.5 shrink-0 rounded-full bg-primary" />}
                    <span className="text-sm font-medium text-foreground">{n.title}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {n.body}{n.context ? ` · ${n.context}` : ''}
                  </span>
                  <span className="text-2xs text-muted-foreground">{formatDate(n.created_at, { timeZone: tz })}</span>
                </button>
              ))}
            </div>
            <div className="border-t border-border px-3 py-2">
              <p className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">Notify me via</p>
              <div className="flex flex-wrap gap-1.5">
                {NOTIFY_CHANNELS.map(({ key, label, icon: I }) => {
                  const on = channels.includes(key);
                  // A channel with nothing connected can't deliver however
                  // this toggle is set, so say so instead of accepting a
                  // click that would change nothing the user can see.
                  const live = !prefsLoaded || connected.includes(key);
                  return (
                    <Tip key={key} label={live
                      ? (on ? `New applications reach you by ${label}`
                            : `${label} is off for new applications`)
                      : `Connect ${label} in Notification preferences first`}>
                      <button
                        onClick={() => live && toggleChannel(key)}
                        disabled={!live}
                        aria-pressed={on}
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs disabled:opacity-50 ${
                          !live ? 'border-dashed border-border text-muted-foreground'
                          : on ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border text-muted-foreground hover:bg-muted'} min-h-tap`}
                      >
                        <I className="size-3" /> {label}
                      </button>
                    </Tip>
                  );
                })}
              </div>
              {/* Always shown, not only when something is disconnected:
                  these chips edit the SAME rows as Notification
                  preferences, and two panels for one setting that never
                  mention each other read as two independent settings. */}
              <Link to="/notifications/preferences"
                className="mt-1 inline-flex items-center py-1 min-h-tap text-2xs text-primary hover:underline">
                {prefsLoaded && NOTIFY_CHANNELS.some((c) => !connected.includes(c.key))
                  ? 'Connect a channel' : 'Manage in Notification preferences'}
              </Link>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
