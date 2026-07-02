// One carrier's reference profile.  Recruiters see it read-only; a recruiting
// MANAGER (recruiter + is_manager, i.e. can_manage_carrier_directory) can edit
// every section (name/contact, the free-text application process, and the
// pre-qual / presentation / recruiter-only label→value sheets) or delete the
// carrier.  Info-only (v1).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Pencil, Trash2, Plus, X, ExternalLink } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import DataTable from '../../components/DataTable';
import type { AnyColumn } from '../../types';
import { useRoleView } from '../../context/RoleViewContext';
import { SECTIONS, mergeRows } from './fields';
import type { CarrierContent, FieldRow } from './fields';

interface Profile {
  id: number; name: string; website: string; video_url: string;
  experience_summary: string; content: CarrierContent;
}

interface Draft {
  name: string; website: string; video_url: string; experience_summary: string;
  application_process: string;
  rows: Record<string, FieldRow[]>;   // section key → rows (template merged)
}

const ROW_SECTIONS = SECTIONS.filter((s) => s.kind === 'rows');
const templateOf = (key: string) => SECTIONS.find((s) => s.key === key)?.fields ?? [];

function buildDraft(p: Profile): Draft {
  const rows: Record<string, FieldRow[]> = {};
  for (const s of ROW_SECTIONS) {
    const stored = (p.content[s.key] as FieldRow[]) || [];
    rows[s.key] = mergeRows(s.fields, stored);
  }
  return {
    name: p.name, website: p.website, video_url: p.video_url,
    experience_summary: p.experience_summary,
    application_process: (p.content.application_process as string) || '',
    rows,
  };
}

const VALUE_COLS: AnyColumn[] = [
  { key: 'label', label: 'Field', render: (v) => <span className="font-medium text-foreground">{String(v)}</span> },
  { key: 'value', label: 'Value', render: (v) => <span className="whitespace-pre-wrap text-muted-foreground">{String(v)}</span> },
];

export default function CarrierProfile() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const { viewHasAny } = useRoleView();
  const canEdit = viewHasAny('can_manage_carrier_directory');

  const [profile, setProfile] = useState<Profile | null>(null);
  const [err, setErr] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const p = await apiJSON<Profile>(`/carrier-directory/carriers/${id}`);
      setProfile(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load carrier');
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const startEdit = () => { if (profile) { setDraft(buildDraft(profile)); setEditing(true); } };
  const cancelEdit = () => { setEditing(false); setDraft(null); };

  type TextField = 'name' | 'website' | 'video_url' | 'experience_summary' | 'application_process';
  const setField = (k: TextField, v: string) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const setRow = (sec: string, idx: number, patch: Partial<FieldRow>) =>
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: d.rows[sec].map((r, i) => (i === idx ? { ...r, ...patch } : r)) } } : d));
  const addRow = (sec: string) =>
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: [...d.rows[sec], { label: '', value: '' }] } } : d));
  const removeRow = (sec: string, idx: number) =>
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: d.rows[sec].filter((_, i) => i !== idx) } } : d));

  const save = async () => {
    if (!draft) return;
    if (!draft.name.trim()) { toast.error('Carrier name is required'); return; }
    setSaving(true);
    try {
      const content: CarrierContent = { application_process: draft.application_process };
      for (const s of ROW_SECTIONS) {
        // Persist only rows that carry a value (label + value); blanks are
        // re-seeded from the template on the next edit.
        content[s.key] = draft.rows[s.key].filter((r) => r.value.trim() && r.label.trim());
      }
      const updated = await apiJSON<Profile>(`/carrier-directory/carriers/${id}`, {
        method: 'PATCH',
        body: {
          name: draft.name.trim(), website: draft.website.trim(),
          video_url: draft.video_url.trim(), experience_summary: draft.experience_summary.trim(),
          content,
        },
      });
      setProfile(updated);
      setEditing(false); setDraft(null);
      toast.success('Carrier saved');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm('Delete this carrier from the directory? This cannot be undone.')) return;
    try {
      await apiJSON(`/carrier-directory/carriers/${id}`, { method: 'DELETE' });
      toast.success('Carrier deleted');
      nav('/workforce/carrier-directory');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const filledView = useMemo(() => {
    const out: Record<string, FieldRow[]> = {};
    if (profile) {
      for (const s of ROW_SECTIONS) {
        const stored = (profile.content[s.key] as FieldRow[]) || [];
        out[s.key] = mergeRows(s.fields, stored).filter((r) => r.value.trim());
      }
    }
    return out;
  }, [profile]);

  if (err) return <div className="p-8 text-sm text-muted-foreground">{err}</div>;
  if (!profile) return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;

  const sectionTitle = 'text-lg font-semibold text-foreground';
  const capsLabel = 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

  return (
    <div className="flex flex-col gap-6">
      {/* Top bar: back + edit/delete or save/cancel */}
      <div className="flex items-center justify-between gap-3">
        <button type="button" onClick={() => nav('/workforce/carrier-directory')}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft size={16} /> Carrier Directory
        </button>
        {canEdit && (editing ? (
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={saving}>Cancel</Button>
            <Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={remove}><Trash2 size={16} /> Delete</Button>
            <Button size="sm" onClick={startEdit}><Pencil size={16} /> Edit</Button>
          </div>
        ))}
      </div>

      {/* Header / identity */}
      {editing && draft ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Carrier name *</span>
            <Input value={draft.name} onChange={(e) => setField('name', e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Website</span>
            <Input value={draft.website} onChange={(e) => setField('website', e.target.value)} placeholder="https://…" />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Video overview URL</span>
            <Input value={draft.video_url} onChange={(e) => setField('video_url', e.target.value)} placeholder="https://…" />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Accepted experience levels</span>
            <Input value={draft.experience_summary} onChange={(e) => setField('experience_summary', e.target.value)}
              placeholder="e.g. 2 years verifiable OTR in the past 3 years" />
          </label>
        </div>
      ) : (
        <div>
          <h1 className="text-2xl font-bold text-foreground">{profile.name}</h1>
          {profile.experience_summary && (
            <p className="mt-1 text-sm text-muted-foreground">{profile.experience_summary}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-3 text-sm">
            {profile.website && (
              <a href={profile.website.startsWith('http') ? profile.website : `https://${profile.website}`}
                target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                <ExternalLink size={14} /> Website
              </a>
            )}
            {profile.video_url && (
              <a href={profile.video_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline">
                <ExternalLink size={14} /> Video overview
              </a>
            )}
          </div>
        </div>
      )}

      {/* Application Process (free text) */}
      <div className="flex flex-col gap-2">
        <p className={sectionTitle}>Application Process</p>
        {editing && draft ? (
          <Textarea rows={6} value={draft.application_process}
            onChange={(e) => setField('application_process', e.target.value)}
            placeholder="How a recruiter submits an application for this carrier, follow-up steps, approval + invoicing notes…" />
        ) : profile.content.application_process ? (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">{String(profile.content.application_process)}</p>
        ) : (
          <p className="text-sm text-muted-foreground/70">Not provided.</p>
        )}
      </div>

      {/* Label→value sheets */}
      {ROW_SECTIONS.map((s) => {
        const tplLen = templateOf(s.key).length;
        return (
          <div key={s.key} className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <p className={sectionTitle}>{s.title}</p>
              {editing && draft && (
                <Button size="xs" variant="ghost" onClick={() => addRow(s.key)}><Plus size={14} /> Add field</Button>
              )}
            </div>
            {editing && draft ? (
              <div className="flex flex-col gap-2">
                {draft.rows[s.key].map((r, i) => (
                  <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
                    {i < tplLen ? (
                      <span className="flex items-center text-sm text-foreground">{r.label}</span>
                    ) : (
                      <Input value={r.label} placeholder="Field name"
                        onChange={(e) => setRow(s.key, i, { label: e.target.value })} />
                    )}
                    <Input value={r.value} placeholder="—"
                      onChange={(e) => setRow(s.key, i, { value: e.target.value })} />
                    {i >= tplLen ? (
                      <button type="button" onClick={() => removeRow(s.key, i)}
                        className="inline-flex items-center justify-center text-muted-foreground hover:text-danger">
                        <X size={16} />
                      </button>
                    ) : <span />}
                  </div>
                ))}
              </div>
            ) : filledView[s.key]?.length ? (
              <DataTable columns={VALUE_COLS} data={filledView[s.key] as unknown as Record<string, unknown>[]}
                enableToolbar={false} enablePagination={false} />
            ) : (
              <p className="text-sm text-muted-foreground/70">Not provided.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
