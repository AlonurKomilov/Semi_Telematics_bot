import { useEffect, useState, useCallback } from 'react';
import type { ReactNode } from 'react';
import { UserPlus, Link as LinkIcon, Copy, Check, Ban, X, FileText, ExternalLink, Bell, Mail, MessageSquare, Monitor, CheckCheck, Download, ShieldCheck, LayoutGrid, List, Users } from 'lucide-react';
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

interface RecruitLink {
  id: number; token: string; label: string; source: string; is_active: number;
  created_at: string;
  /** ISO auto-close timestamp; null → never expires. */
  expires_at?: string | null;
  /** Per-link funnel stats. */
  view_count?: number; submissions?: number; hires?: number;
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

export default function Applications() {
  const [links, setLinks] = useState<RecruitLink[]>([]);
  const [rows, setRows] = useState<AppRow[]>([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [view, setView] = useState<'table' | 'board'>('table');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [openId, setOpenId] = useState<number | null>(null);
  const tz = useTimezone();
  // Link create form
  const [label, setLabel] = useState('');
  const [source, setSource] = useState('');
  const [expiryDays, setExpiryDays] = useState(90);  // default: not forever
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);

  // Load ALL applications once; the table filters client-side and the
  // board groups by status — both need the full set.
  const loadApps = useCallback(() => {
    apiJSON<{ items: AppRow[] }>('/recruitment/applications?limit=500')
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
      await apiJSON(`/recruitment/applications/${id}/status`, { method: 'PATCH', body: { status } });
    } catch (e) {
      setRows(prev);
      alert(e instanceof Error ? e.message : 'Could not move application');
    }
  };

  const visibleRows = statusFilter ? rows.filter((r) => r.status === statusFilter) : rows;

  const loadLinks = useCallback(() => {
    apiJSON<{ items: RecruitLink[] }>('/recruitment/links')
      .then((r) => setLinks(r.items))
      .catch(() => { /* non-fatal */ });
  }, []);

  useEffect(() => { loadApps(); }, [loadApps]);
  useEffect(() => { loadLinks(); }, [loadLinks]);

  const createLink = async () => {
    setCreating(true);
    try {
      await apiJSON('/recruitment/links', {
        method: 'POST',
        body: { label, source, expires_in_days: expiryDays || null },
      });
      setLabel(''); setSource('');
      loadLinks();
    } finally {
      setCreating(false);
    }
  };

  const revokeLink = async (id: number) => {
    if (!confirm('Revoke this link? Applicants can no longer use it.')) return;
    await apiJSON(`/recruitment/links/${id}/revoke`, { method: 'POST' });
    loadLinks();
  };

  const copyLink = (l: RecruitLink) => {
    navigator.clipboard?.writeText(`${APPLY_BASE}/${l.token}`);
    setCopied(l.id);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Driver Applications" icon={UserPlus}
        description="Recruiting links + submitted driver applications."
        actions={<NotificationsBell onOpen={(id) => setOpenId(id)} />} />

      {/* ── Recruiting links ───────────────────────────────────── */}
      <section className="bg-card border border-border rounded-lg p-4">
        <h2 className="text-base font-semibold flex items-center gap-2 mb-3">
          <LinkIcon size={16} className="text-muted-foreground" /> Recruiting links
        </h2>
        <div className="flex flex-wrap gap-2 mb-4">
          <Input placeholder="Label (e.g. Indeed campaign)" value={label}
            onChange={(e) => setLabel(e.target.value)} className="max-w-xs" />
          <Input placeholder="Source (optional)" value={source}
            onChange={(e) => setSource(e.target.value)} className="max-w-[10rem]" />
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
        {links.length === 0 ? (
          <p className="text-sm text-muted-foreground">No links yet. Create one to start collecting applications.</p>
        ) : (
          <ul className="space-y-1.5">
            {links.map((l) => {
              const expired = !!l.expires_at
                && new Date(l.expires_at).getTime() < Date.now();
              const live = l.is_active === 1 && !expired;
              return (
              <li key={l.id} className="flex items-center gap-2 text-sm">
                <span className={`px-2 py-0.5 rounded text-xs ${
                  l.is_active !== 1 ? statusClasses('disabled')
                  : expired ? statusClasses('disabled')
                  : statusClasses('active')}`}>
                  {l.is_active !== 1 ? 'revoked' : expired ? 'expired' : 'active'}
                </span>
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
                {live && (
                  <>
                    <button onClick={() => copyLink(l)} title="Copy link"
                      className="text-muted-foreground hover:text-foreground p-1 rounded hover:bg-muted">
                      {copied === l.id ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
                    </button>
                    <button onClick={() => revokeLink(l.id)} title="Revoke"
                      className="text-muted-foreground hover:text-destructive p-1 rounded hover:bg-muted">
                      <Ban size={14} />
                    </button>
                  </>
                )}
                {l.is_active === 1 && expired && (
                  <button onClick={() => revokeLink(l.id)} title="Remove expired link"
                    className="text-muted-foreground hover:text-destructive p-1 rounded hover:bg-muted">
                    <Ban size={14} />
                  </button>
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
            </>
          )}
        </div>
        {err && <div className="p-3 text-sm text-destructive">{err}</div>}
        {view === 'board' ? (
          <ApplicationsBoard rows={rows} loading={loading} onMove={moveApp} onOpen={setOpenId} />
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="text-left px-3 py-2">Ref</th>
                <th className="text-left px-3 py-2">Name</th>
                <th className="text-left px-3 py-2">Contact</th>
                <th className="text-left px-3 py-2">Location</th>
                <th className="text-left px-3 py-2">CDL</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={7} className="text-center text-muted-foreground py-8">Loading…</td></tr>}
              {!loading && visibleRows.length === 0 && (
                <tr><td colSpan={7} className="text-center text-muted-foreground py-8">No applications{statusFilter ? ` in '${statusFilter}'` : ' yet'}.</td></tr>
              )}
              {visibleRows.map((r) => (
                <tr key={r.id} onClick={() => setOpenId(r.id)}
                  className="border-b border-border/50 cursor-pointer hover:bg-muted/40">
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
    apiJSON<AppDetail>(`/recruitment/applications/${appId}`).then((d) => {
      setApp(d); setNotes(d.recruiter_notes || '');
    });
  }, [appId]);

  const setStatus = async (status: string) => {
    setBusy(true);
    try {
      await apiJSON(`/recruitment/applications/${appId}/status`, { method: 'PATCH', body: { status } });
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
      `/recruitment/applications/${appId}/vetting`, { method: 'PATCH', body: { check: key, done } },
    );
    setApp((a) => (a ? { ...a, vetting: r.vetting } : a));
  };

  const downloadPacket = async () => {
    const res = await apiFetch(`/recruitment/applications/${appId}/packet.pdf`);
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
      await apiJSON(`/recruitment/applications/${appId}/notes`, { method: 'PATCH', body: { notes } });
    } finally { setBusy(false); }
  };

  const hire = async () => {
    if (!confirm('Hire this applicant? This creates a driver invite link to share with them.')) return;
    setBusy(true);
    try {
      const r = await apiJSON<{ invite_link: string }>(`/recruitment/applications/${appId}/convert`, { method: 'POST' });
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
    apiFetch(`/recruitment/applications/${appId}/docs/${slot}`)
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
    apiJSON<{ items: Notif[]; unread_count: number }>('/recruitment/notifications?limit=20')
      .then((r) => { setItems(r.items); setUnread(r.unread_count); })
      .catch(() => { /* non-fatal */ });
  }, []);

  // Initial load + a light 60s poll so the badge stays current.
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);
  useEffect(() => {
    apiJSON<{ channels: string[] }>('/recruitment/notify-prefs')
      .then((r) => setChannels(r.channels)).catch(() => { /* non-fatal */ });
  }, []);

  const markAll = async () => {
    await apiJSON('/recruitment/notifications/read', { method: 'POST', body: {} });
    setItems((xs) => xs.map((x) => ({ ...x, is_read: 1 })));
    setUnread(0);
  };
  const openNotif = (n: Notif) => {
    if (!n.is_read) {
      apiJSON('/recruitment/notifications/read', { method: 'POST', body: { ids: [n.id] } }).catch(() => {});
      setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, is_read: 1 } : x)));
      setUnread((u) => Math.max(0, u - 1));
    }
    setOpen(false);
    if (n.application_id) onOpen(n.application_id);
  };
  const toggleChannel = async (key: string) => {
    const next = channels.includes(key) ? channels.filter((c) => c !== key) : [...channels, key];
    setChannels(next);
    const r = await apiJSON<{ channels: string[] }>('/recruitment/notify-prefs', { method: 'PUT', body: { channels: next } });
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
