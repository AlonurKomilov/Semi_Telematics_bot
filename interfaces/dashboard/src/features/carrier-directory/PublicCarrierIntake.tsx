// PUBLIC carrier self-fill form — apply.<apex>/carrier/<token>.
//
// A recruiting manager mints a tokenized invite link on a carrier's
// directory profile and sends it to the carrier's office; whoever opens it
// fills in the carrier's own requirements/presentation sheet (the same
// SECTIONS template the dashboard editor uses — one SSOT, no drift).  The
// "For Recruiters Only" section never reaches this page: the public GET
// strips it and the public POST can't write it.
//
// No login — the unguessable token is the auth (same model as the driver
// apply links).  Answers autosave to localStorage per token so an
// accidental tab-close loses nothing; the link stays live until expiry so
// the carrier can come back and revise (each submit overwrites).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Building2, CheckCircle2, Plus, X, Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { formatDay } from '../../utils/datetime';
import { SECTIONS, mergeRows } from './fields';
import type { CarrierContent, FieldRow } from './fields';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// Sections the carrier fills — everything except the agency-internal one.
const PUBLIC_ROW_SECTIONS = SECTIONS.filter(
  (s) => s.kind === 'rows' && s.key !== 'recruiter_only',
);

interface IntakePayload {
  carrier: {
    name: string; website: string; video_url: string;
    experience_summary: string; content: CarrierContent;
  };
  agency: string;
  expires_at: string | null;
}

interface Draft {
  website: string; video_url: string; experience_summary: string;
  application_process: string;
  rows: Record<string, FieldRow[]>;
}

function buildDraft(p: IntakePayload['carrier']): Draft {
  const rows: Record<string, FieldRow[]> = {};
  for (const s of PUBLIC_ROW_SECTIONS) {
    rows[s.key] = mergeRows(s.fields, (p.content[s.key] as FieldRow[]) || []);
  }
  return {
    website: p.website, video_url: p.video_url,
    experience_summary: p.experience_summary,
    application_process: (p.content.application_process as string) || '',
    rows,
  };
}

const capsLabel = 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

export default function PublicCarrierIntake() {
  // apply.<apex>/carrier/<token> — token is the second path segment.
  const token = window.location.pathname.split('/').filter(Boolean)[1] || '';
  const storageKey = `carrier-intake-${token}`;

  const [state, setState] = useState<'loading' | 'invalid' | 'ready' | 'done'>('loading');
  const [carrierName, setCarrierName] = useState('');
  const [agency, setAgency] = useState('');
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState('');
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Goal-gradient counter: filled vs total fields, live.  Thanks to the
  // server prefill this usually starts above zero — the form reads as
  // "already under way", not a blank mountain.  Purely informational:
  // every field stays optional and submit is never blocked by it.
  const progress = useMemo(() => {
    if (!draft) return { filled: 0, total: 0 };
    let filled = 0;
    let total = 4; // website, video, experience summary, submission process
    for (const v of [draft.website, draft.video_url, draft.experience_summary, draft.application_process]) {
      if (v.trim()) filled++;
    }
    for (const s of PUBLIC_ROW_SECTIONS) {
      for (const r of draft.rows[s.key]) {
        total++;
        if (r.value.trim() && r.label.trim()) filled++;
      }
    }
    return { filled, total };
  }, [draft]);

  useEffect(() => {
    (async () => {
      if (!token) { setState('invalid'); return; }
      try {
        const r = await fetch(`${API_BASE}/carrier-directory/intake?token=${encodeURIComponent(token)}`);
        if (!r.ok) { setState('invalid'); return; }
        const data = (await r.json()) as IntakePayload;
        setCarrierName(data.carrier.name);
        setAgency(data.agency);
        setExpiresAt(data.expires_at);
        // A locally saved draft (tab closed mid-fill) wins over the server
        // copy — it is strictly newer.
        let d = buildDraft(data.carrier);
        try {
          const saved = localStorage.getItem(storageKey);
          if (saved) d = { ...d, ...(JSON.parse(saved) as Draft) };
        } catch { /* corrupt local draft — start from server */ }
        setDraft(d);
        setState('ready');
      } catch {
        setState('invalid');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Debounced local autosave on every edit.
  const persist = useCallback((d: Draft) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      try { localStorage.setItem(storageKey, JSON.stringify(d)); } catch { /* full/blocked */ }
    }, 400);
  }, [storageKey]);

  const update = (patch: Partial<Draft>) =>
    setDraft((d) => { if (!d) return d; const next = { ...d, ...patch }; persist(next); return next; });
  const setRow = (sec: string, idx: number, patch: Partial<FieldRow>) =>
    setDraft((d) => {
      if (!d) return d;
      const next = { ...d, rows: { ...d.rows, [sec]: d.rows[sec].map((r, i) => (i === idx ? { ...r, ...patch } : r)) } };
      persist(next); return next;
    });
  const addRow = (sec: string) =>
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: [...d.rows[sec], { label: '', value: '' }] } } : d));
  const removeRow = (sec: string, idx: number) =>
    setDraft((d) => {
      if (!d) return d;
      const next = { ...d, rows: { ...d.rows, [sec]: d.rows[sec].filter((_, i) => i !== idx) } };
      persist(next); return next;
    });

  const submit = async () => {
    if (!draft) return;
    setSubmitting(true);
    setSubmitErr('');
    try {
      const content: CarrierContent = { application_process: draft.application_process };
      for (const s of PUBLIC_ROW_SECTIONS) {
        content[s.key] = draft.rows[s.key].filter((r) => r.value.trim() && r.label.trim());
      }
      const r = await fetch(`${API_BASE}/carrier-directory/intake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          website: draft.website.trim(),
          video_url: draft.video_url.trim(),
          experience_summary: draft.experience_summary.trim(),
          content,
        }),
      });
      if (!r.ok) {
        const err = (await r.json().catch(() => ({}))) as { detail?: string };
        throw new Error(typeof err.detail === 'string' ? err.detail : 'Submission failed');
      }
      try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
      setState('done');
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : 'Submission failed — please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (state === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  if (state === 'invalid') {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-border bg-card p-8 text-center shadow-sm">
          <Building2 size={24} className="mx-auto text-muted-foreground" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">This link isn't available</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            The invite link may have expired or been replaced. Please ask your
            recruiting contact to send you a fresh one.
          </p>
        </div>
      </div>
    );
  }

  if (state === 'done') {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-border bg-card p-8 text-center shadow-sm">
          <CheckCircle2 size={24} className="mx-auto text-ok" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">Thank you!</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your carrier profile was sent to {agency || 'the recruiting team'}.
            You can reopen this link to revise your answers while it stays active.
          </p>
        </div>
      </div>
    );
  }

  if (!draft) return null;

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      {/* Header */}
      <div className="mb-8">
        <p className={capsLabel}>{agency || 'Carrier profile'}</p>
        <h1 className="mt-1 text-2xl font-bold text-foreground">{carrierName}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {agency ? `${agency} recruits drivers for your company.` : 'Your recruiting partner'}{' '}
          Please fill in your hiring requirements, pay and benefits below so
          recruiters present your company accurately to driver candidates. Any
          field can be left blank; your answers save on this device as you type.
        </p>
      </div>

      {/* Basics */}
      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className={capsLabel}>Website</span>
          <Input value={draft.website} onChange={(e) => update({ website: e.target.value })} placeholder="https://…" />
        </label>
        <label className="flex flex-col gap-1">
          <span className={capsLabel}>Company video URL</span>
          <Input value={draft.video_url} onChange={(e) => update({ video_url: e.target.value })} placeholder="https://…" />
        </label>
        <label className="flex flex-col gap-1 sm:col-span-2">
          <span className={capsLabel}>Accepted experience levels</span>
          <Input value={draft.experience_summary} onChange={(e) => update({ experience_summary: e.target.value })}
            placeholder="e.g. 2 years verifiable OTR in the past 3 years" />
        </label>
      </div>

      {/* How to submit drivers */}
      <div className="mb-8 flex flex-col gap-2">
        <p className="text-lg font-semibold text-foreground">How should drivers be submitted to you?</p>
        <Textarea rows={5} value={draft.application_process}
          onChange={(e) => update({ application_process: e.target.value })}
          placeholder="Where recruiters send applications, who to contact, turnaround expectations, next steps for the driver…" />
      </div>

      {/* Label→value sheets */}
      {PUBLIC_ROW_SECTIONS.map((s) => {
        const tplLen = (s.fields ?? []).length;
        return (
          <div key={s.key} className="mb-8 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <p className="text-lg font-semibold text-foreground">{s.title}</p>
              <Button size="xs" variant="ghost" onClick={() => addRow(s.key)}>
                <Plus size={14} /> Add field
              </Button>
            </div>
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
          </div>
        );
      })}

      {/* Submit */}
      <div className="flex flex-col gap-3 border-t border-border pt-6">
        {/* Live fill progress — starts above zero when the sheet came prefilled. */}
        <div className="flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${progress.total ? Math.round((progress.filled / progress.total) * 100) : 0}%` }}
            />
          </div>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {progress.filled} of {progress.total} fields filled
          </span>
        </div>
        <div className="flex flex-col items-end gap-2">
          {submitErr && <p className="text-sm text-danger">{submitErr}</p>}
          <Button onClick={submit} disabled={submitting}>
            {submitting ? 'Sending…' : `Send to ${agency || 'the recruiting team'}`}
          </Button>
          <p className="text-xs text-muted-foreground">
            {expiresAt
              ? `This link stays active until ${formatDay(expiresAt)} — you can revise your answers until then.`
              : 'You can reopen this link to revise your answers while it stays active.'}
          </p>
        </div>
      </div>
    </div>
  );
}
