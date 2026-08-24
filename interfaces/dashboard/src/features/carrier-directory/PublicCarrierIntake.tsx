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
import { PUBLIC_SECTIONS, mergeRows } from './fields';
import type { CarrierContent, FieldRow } from './fields';
import FieldValueInput from './FieldValueInput';
import { toneClasses } from '../../lib/status';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// Sections the carrier fills.  PUBLIC_SECTIONS is the SSOT for "not
// internal" (fields.ts), mirroring the backend's _INTAKE_ROW_SECTIONS —
// so adding an internal section can never accidentally expose it here.
const PUBLIC_ROW_SECTIONS = PUBLIC_SECTIONS.filter((s) => s.kind === 'rows');

interface IntakePayload {
  carrier: {
    name: string; website: string; video_url: string;
    experience_summary: string; content: CarrierContent;
  };
  /** Resolved sender name (per-link override → account public name).
   *  Legitimately '' when the sender chose to stay unnamed — every use
   *  below has a neutral fallback.  Never the tenant's registered name. */
  agency: string;
  expires_at: string | null;
  /** Set once the carrier has sent the sheet at least one time, so a
   *  return visit can acknowledge their earlier work instead of looking
   *  like a fresh first visit. */
  submitted_at?: string | null;
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

  // 'invalid' means the LINK is genuinely dead. 'unreachable' means we
  // could not ask — a phone losing signal, captive wifi, a 500, a 429.
  // These were one state, so a subway tunnel told the carrier their link
  // had expired and to go ask for a new one.
  const [state, setState] = useState<
    'loading' | 'invalid' | 'unreachable' | 'ready' | 'done'
  >('loading');
  const [carrierName, setCarrierName] = useState('');
  const [agency, setAgency] = useState('');
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [alreadySubmitted, setAlreadySubmitted] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  // "Your typing is saved on this device" was asserted once at the top and
  // never confirmed again, on a form that takes an hour.
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Focus the row "+ Add field" just created — it was appended below the
  // fold, unfocused and unscrolled, so nothing appeared to happen and the
  // obvious response was to click again and make duplicates.
  const pendingFocus = useRef<string | null>(null);
  const [urlWarn, setUrlWarn] = useState<Record<string, string>>({});

  // Per-section progress, plus an overall count.  Scoped per section on
  // purpose: one bar over ~74 fields reads "0 of 74" on an empty sheet and
  // barely moves for the first ten minutes, which is the exact discouraging
  // shape a goal gradient is supposed to avoid.  A section of 13 visibly
  // completes.
  const progress = useMemo(() => {
    const basics = [draft?.website, draft?.video_url, draft?.experience_summary]
      .filter((v) => (v ?? '').trim()).length;
    const per: Record<string, { filled: number; total: number; extra?: number }> = {
      // The four intro fields belong to no visible section, so 53 + 17
      // never reconciled with the footer's 74. They get their own counter.
      basics: { filled: basics, total: 3 },
      application_process: {
        filled: (draft?.application_process ?? '').trim() ? 1 : 0, total: 1,
      },
    };
    for (const s of PUBLIC_ROW_SECTIONS) {
      const rows = draft?.rows[s.key] ?? [];
      const tplLen = (s.fields ?? []).length;
      // Denominator is the STANDARD schema only. Counting user-created rows
      // meant "+ Add field" moved the total from 74 to 75 — contributing
      // more made the bar shrink, which is the exact inverse of a goal
      // gradient. Customs are reported beside the fraction, not inside it.
      per[s.key] = {
        filled: rows.slice(0, tplLen).filter((r) => r.value.trim()).length,
        total: tplLen,
        extra: rows.slice(tplLen).filter((r) => r.value.trim() && r.label.trim()).length,
      };
    }
    const filled = Object.values(per).reduce((n, x) => n + x.filled, 0);
    const total = Object.values(per).reduce((n, x) => n + x.total, 0);
    const extra = Object.values(per).reduce((n, x) => n + (x.extra ?? 0), 0);
    return { per, filled, total, extra };
  }, [draft]);

  useEffect(() => {
    (async () => {
      if (!token) { setState('invalid'); return; }
      setState('loading');
      try {
        const r = await fetch(`${API_BASE}/carrier-directory/intake?token=${encodeURIComponent(token)}`);
        // Only a 404 means the link itself is gone. Anything else is our
        // problem, not theirs, and must not send them away.
        if (r.status === 404) { setState('invalid'); return; }
        if (!r.ok) { setState('unreachable'); return; }
        const data = (await r.json()) as IntakePayload;
        setCarrierName(data.carrier.name);
        setAgency(data.agency);
        setExpiresAt(data.expires_at);
        setAlreadySubmitted(Boolean(data.submitted_at));
        // A locally saved draft (tab closed mid-fill) wins over the server
        // copy — it is strictly newer.
        //
        // Rows are re-matched BY LABEL through mergeRows, never spread in
        // positionally. A cached draft is a snapshot of whatever template
        // shipped when it was written; splicing its array over today's
        // template lines up index N of the old order against index N of
        // the new one, so a reorder or rename silently re-files answers
        // under the wrong field — and the carrier submits it without ever
        // seeing an error. Treating the cache exactly like server-stored
        // content is what makes the template safe to evolve.
        let d = buildDraft(data.carrier);
        try {
          const saved = localStorage.getItem(storageKey);
          if (saved) {
            const cached = JSON.parse(saved) as Partial<Draft>;
            d = {
              website: cached.website ?? d.website,
              video_url: cached.video_url ?? d.video_url,
              experience_summary: cached.experience_summary ?? d.experience_summary,
              application_process: cached.application_process ?? d.application_process,
              rows: Object.fromEntries(PUBLIC_ROW_SECTIONS.map((s) => [
                s.key,
                cached.rows?.[s.key]
                  ? mergeRows(s.fields, cached.rows[s.key])
                  : d.rows[s.key],
              ])),
            };
          }
        } catch { /* corrupt local draft — start from server */ }
        setDraft(d);
        setState('ready');
      } catch {
        setState('unreachable');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, reloadKey]);

  // Debounced local autosave on every edit.
  const persist = useCallback((d: Draft) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(d));
        setSavedAt(new Date());
      } catch { /* full/blocked — the indicator simply doesn't advance */ }
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
  const addRow = (sec: string) => {
    setDraft((d) => {
      if (!d) return d;
      pendingFocus.current = `${sec}:${d.rows[sec].length}`;
      return { ...d, rows: { ...d.rows, [sec]: [...d.rows[sec], { label: '', value: '' }] } };
    });
  };

  useEffect(() => {
    const key = pendingFocus.current;
    if (!key) return;
    pendingFocus.current = null;
    const el = document.querySelector<HTMLInputElement>(`[data-newrow="${key}"]`);
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el?.focus();
  }, [draft]);
  const removeRow = (sec: string, idx: number) =>
    setDraft((d) => {
      if (!d) return d;
      const next = { ...d, rows: { ...d.rows, [sec]: d.rows[sec].filter((_, i) => i !== idx) } };
      persist(next); return next;
    });

  const submit = async () => {
    if (!draft) return;
    // The overwrite consequence is disclosed in the intro bullets and then
    // never repeated at the moment it applies. Honest disclosure an hour
    // earlier is not the same as a safe overwrite — and Send was fully
    // enabled at 0 of 74 with no caution that an empty sheet transmits.
    const lines = [`You're sending ${progress.filled} of ${progress.total} fields.`];
    if (progress.filled === 0) {
      lines.push('', 'Nothing is filled in yet — this would send an empty profile.');
    }
    if (alreadySubmitted) {
      lines.push('', 'This replaces the version you sent earlier.');
    }
    lines.push('', 'Continue?');
    if (!confirm(lines.join('\n'))) return;
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
      <div className="flex min-h-screen flex-col items-center justify-center gap-3">
        <Loader2 className="animate-spin text-primary size-6" />
        <p className="text-sm text-muted-foreground">Loading your saved answers…</p>
      </div>
    );
  }

  if (state === 'invalid') {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card padding="panel" className="max-w-md text-center shadow-sm">
          <Building2 className="mx-auto text-muted-foreground size-6" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">This link isn't available</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            The invite link may have expired or been replaced. Reply to the
            email that brought you here and the recruiter who sent it will
            get a fresh one to you.
          </p>
        </Card>
      </div>
    );
  }

  // Our fault, not theirs — never tell someone their link is dead because
  // a request failed.  Retry in place; their local draft is untouched.
  if (state === 'unreachable') {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card padding="panel" className="max-w-md text-center shadow-sm">
          <Building2 className="mx-auto text-muted-foreground size-6" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">Couldn't load the form</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your link is fine — we just couldn't reach the server. Check your
            connection and try again. Anything you typed earlier is still
            saved on this device.
          </p>
          <Button className="mt-4" onClick={() => setReloadKey((k) => k + 1)}>Try again</Button>
        </Card>
      </div>
    );
  }

  if (state === 'done') {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card padding="panel" className="max-w-md text-center shadow-sm">
          <CheckCircle2 className="mx-auto text-ok size-6" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">Thank you!</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your profile was sent to {agency || 'the recruiting team'}.
            You can reopen this link to revise your answers while it stays active.
          </p>
        </Card>
      </div>
    );
  }

  if (!draft) return null;

  const pct = progress.total ? Math.round((progress.filled / progress.total) * 100) : 0;

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      {/* Sticky rail: the only progress signal used to live at the very
          bottom of a ~4,000px page, so during the actual work no progress
          was ever visible and Send was a long scroll away. */}
      <div className="sticky top-0 z-30 -mx-4 mb-6 border-b border-border bg-background/95 px-4 py-2 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          <div className="h-1.5 min-w-32 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
          </div>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {progress.filled} of {progress.total}
            {progress.extra > 0 && ` · +${progress.extra} of your own`}
          </span>
          <span className="whitespace-nowrap text-2xs text-muted-foreground">
            {savedAt
              ? `Saved ${savedAt.toLocaleTimeString()} · this browser only`
              : 'Saves on this device as you type'}
          </span>
          <Button size="sm" onClick={submit} disabled={submitting}>
            {submitting ? 'Sending…' : alreadySubmitted ? 'Send update' : 'Send'}
          </Button>
        </div>
      </div>
      {/* Header */}
      <div className="mb-8">
        {/* Always the document type, so this slot means one thing whether
            or not the sender chose to name themselves. */}
        <p className={capsLabel}>Carrier profile</p>
        <h1 className="mt-1 text-2xl font-bold text-foreground">{carrierName}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {/* The sender may deliberately stay unnamed.  It still has to
              say WHY this landed in front of them — an unattributed
              request for pay data reads as phishing otherwise, and the
              invite email already carries this same sentence. */}
          {agency
            ? `${agency} recruits drivers for your company. `
            : 'A recruiting partner is presenting your company to driver candidates. '}
          Please fill in your hiring requirements, pay and benefits below so
          recruiters present your company accurately to driver candidates. Any
          field can be left blank.
        </p>
        {!alreadySubmitted && progress.filled > 0 && (
          <p className={`mt-3 rounded-md border px-3 py-2 text-sm ${toneClasses('info')}`}>
            Welcome back — your {progress.filled} saved answer{progress.filled === 1 ? '' : 's'}
            {' '}are still here on this device. You haven&rsquo;t sent the profile yet.
          </p>
        )}
        {alreadySubmitted && (
          <p className={`mt-3 rounded-md border px-3 py-2 text-sm ${toneClasses('ok')}`}>
            You already sent this sheet. Your answers are below — change
            anything you like and send it again; the newest version replaces
            the last one.
          </p>
        )}
        {/* What we can actually promise.  The token is bearer auth with no
            recipient identity, so "private to your company" was a guarantee
            we had no way to keep — a forwarded link works for whoever gets
            it, and the last submit silently wins. */}
        <ul className="mt-3 flex flex-col gap-1 text-xs text-muted-foreground">
          <li>· Your profile goes only to the recruiting team that sent you this link. It is not published.</li>
          <li>· Anyone who opens this link can see and change this profile, and the newest send replaces the previous one — forward it only inside your company.</li>
          <li>· Your typing is saved on this device as you go, so closing the tab loses nothing. It is not saved on your other devices.</li>
          <li>· Questions? Reply to the email that brought you here and it reaches the recruiter who sent it.</li>
        </ul>
      </div>

      {/* Basics — the SAME label-left / control-right row template every
          other field uses. This block used to stack label-above-input in
          grey ALL-CAPS, giving the page a third layout grammar and making
          field labels visually identical to group headers 300px later. */}
      <div className="mb-8 flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SectionHeader>About your company</SectionHeader>
          <span className="text-xs text-muted-foreground">
            {progress.per.basics.filled} of {progress.per.basics.total}
          </span>
        </div>
        {([
          ['website', 'Website', 'https://…'],
          ['video_url', 'Company video URL', 'https://…'],
          ['experience_summary', 'Accepted experience levels',
            'e.g. 2 years verifiable OTR in the past 3 years'],
        ] as const).map(([key, label, ph]) => (
          <div key={key} className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
            <span className="flex flex-col justify-center text-sm text-foreground">
              {label}
              {key !== 'experience_summary' && urlWarn[key] && (
                <span className="text-xs text-danger">{urlWarn[key]}</span>
              )}
            </span>
            <Input
              value={draft[key]}
              placeholder={ph}
              onChange={(e) => {
                update({ [key]: e.target.value } as Partial<Draft>);
                if (urlWarn[key]) setUrlWarn((w) => ({ ...w, [key]: '' }));
              }}
              onBlur={(e) => {
                if (key === 'experience_summary') return;
                const v = e.target.value.trim();
                // Soft, never blocking: a typo'd URL used to sail through
                // and get stored as "https://not a url".
                setUrlWarn((w) => ({
                  ...w,
                  [key]: v && !/^(https?:\/\/)?[^\s.]+\.[^\s]{2,}$/.test(v)
                    ? "That doesn't look like a web address — check it before sending."
                    : '',
                }));
              }}
            />
            <span />
          </div>
        ))}
      </div>

      {/* How to submit drivers */}
      <div className="mb-8 flex flex-col gap-2">
        <SectionHeader>How should drivers be submitted to you?</SectionHeader>
        <Textarea rows={5} value={draft.application_process}
          onChange={(e) => update({ application_process: e.target.value })}
          placeholder="Where recruiters send applications, who to contact, turnaround expectations, next steps for the driver…" />
      </div>

      {/* Label→value sheets */}
      {PUBLIC_ROW_SECTIONS.map((s) => {
        const tpl = s.fields ?? [];
        const tplLen = tpl.length;
        const p = progress.per[s.key] ?? { filled: 0, total: 0 };
        return (
          <div key={s.key} className="mb-8 flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-lg font-semibold text-foreground">{s.title}</p>
              <div className="flex items-center gap-2">
                {/* Per-section, so finishing 13 pay fields is a visible win
                    rather than 13/74 of an unmoving bar. */}
                <span className="text-xs text-muted-foreground">
                  {p.filled} of {p.total}
                  {(p.extra ?? 0) > 0 && ` · +${p.extra} of your own`}
                </span>
                <Button size="xs" variant="outline" onClick={() => addRow(s.key)}>
                  <Plus /> Add field
                </Button>
              </div>
            </div>
            {s.blurb && <p className="text-xs text-muted-foreground">{s.blurb}</p>}
            <div className="flex flex-col gap-2">
              {draft.rows[s.key].map((r, i) => {
                const def = i < tplLen ? tpl[i] : undefined;
                const newGroup = def?.group && def.group !== tpl[i - 1]?.group;
                return (
                  <div key={i} className="flex flex-col gap-2">
                    {newGroup && (
                      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">{def!.group}</p>
                    )}
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
                      {def ? (
                        <span className="flex flex-col justify-center text-sm text-foreground">
                          {def.label}
                          {def.hint && <span className="text-xs text-muted-foreground">{def.hint}</span>}
                        </span>
                      ) : (
                        <Input value={r.label} placeholder="Field name" aria-label="Custom field name"
                          data-newrow={`${s.key}:${i}`}
                          onChange={(e) => setRow(s.key, i, { label: e.target.value })} />
                      )}
                      {/* The label is a sibling span, not a wrapping <label>,
                          so the control carries its own accessible name. */}
                      <FieldValueInput def={def} value={r.value}
                        ariaLabel={def?.label ?? r.label ?? 'Value'}
                        onChange={(v) => setRow(s.key, i, { value: v })} />
                      {!def ? (
                        <button type="button" onClick={() => removeRow(s.key, i)}
                          aria-label={`Remove ${r.label || 'field'}`}
                          className="inline-flex items-center justify-center text-muted-foreground hover:text-danger">
                          <X className="size-4" />
                        </button>
                      ) : <span />}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {/* Submit */}
      <div className="flex flex-col gap-3 border-t border-border pt-6">
        <div className="flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${progress.total ? Math.round((progress.filled / progress.total) * 100) : 0}%` }}
            />
          </div>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {progress.filled} of {progress.total} profile fields
          </span>
        </div>
        <div className="flex flex-col items-end gap-2">
          {submitErr && (
            <div className="w-full text-right">
              <p className="text-sm text-danger">{submitErr}</p>
              {/* The single fact that stops the panic after 15 minutes of
                  typing: nothing was lost. */}
              <p className="text-xs text-muted-foreground">
                Nothing you typed was lost — it is saved on this device. Try
                sending again, or reopen this link later.
              </p>
            </div>
          )}
          <Button onClick={submit} disabled={submitting}>
            {submitting
              ? 'Sending…'
              : alreadySubmitted
                ? `Send updated sheet to ${agency || 'the recruiting team'}`
                : `Send to ${agency || 'the recruiting team'}`}
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
