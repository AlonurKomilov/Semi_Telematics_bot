// Public driver-application page — mounted standalone on apply.<apex>
// (no auth / shell / router; see main.tsx host branch).
//
// Submits multipart to POST /api/recruitment/apply: a JSON `application`
// part (file blobs stripped) + the raw document files as their own parts.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Truck, Clock, ShieldCheck, CheckCircle2, ArrowLeft, ArrowRight, Lock } from 'lucide-react';
import { STEPS } from './steps';
import { deepSet, DISCLOSURE_VERSION, todayISO } from './lib';
import type { Data } from './lib';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// The link token is the first path segment (apply.<apex>/<token>); a
// ?token= / ?apply= query param is honoured too for local dev.
function resolveToken(): string {
  const q = new URLSearchParams(window.location.search);
  const fromQuery = q.get('token') || q.get('apply');
  if (fromQuery) return fromQuery.trim();
  const seg = window.location.pathname.split('/').filter(Boolean)[0];
  return seg ? seg.trim() : '';
}

// Pull the raw File objects out for the multipart parts.
function extractFiles(data: Data): Record<string, File | undefined> {
  const docs = data.cdl?.docs || {};
  const truck = data.position?.truck || {};
  return {
    cdl_front: docs.cdlFront?.file,
    cdl_back: docs.cdlBack?.file,
    medical: docs.medical?.file,
    truck_photo: truck.picture?.file,
    dot_inspection: truck.dotInspection?.file,
  };
}

// JSON payload with the (large, non-serializable) file blobs removed —
// the server reconstructs `docs` from the uploaded parts.  The drawn
// signature dataUrl stays (the server decodes + validates it).
function sanitizeForJson(data: Data): Data {
  const clone: Data = JSON.parse(JSON.stringify(data));
  if (clone.cdl) delete clone.cdl.docs;
  if (clone.position?.truck) {
    delete clone.position.truck.picture;
    delete clone.position.truck.dotInspection;
  }
  clone.disclosureVersion = DISCLOSURE_VERSION;
  return clone;
}

function Header({ compact }: { compact?: boolean }) {
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto max-w-5xl px-4 py-5 sm:px-6">
        <div className="flex items-center gap-2 text-primary">
          <Truck size={22} />
          <span className="text-base font-semibold text-foreground">Driver Application</span>
        </div>
        {!compact && (
          <>
            <h1 className="mt-3 text-2xl font-bold text-foreground">Join our driving team.</h1>
            <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
              Complete your DOT driver file in about 8 minutes — we'll review and reach out shortly.
              Have your CDL, DOT medical card, and last 10 years of employer info ready.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {[
                { icon: Clock, text: '≈8 minutes' },
                { icon: Lock, text: 'Encrypted · DOT-compliant' },
                { icon: ShieldCheck, text: 'FMCSA §391.21' },
              ].map(({ icon: I, text }) => (
                <span key={text} className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
                  <I size={14} /> {text}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </header>
  );
}

function Stepper({ current, max, onJump }: { current: number; max: number; onJump: (i: number) => void }) {
  return (
    <nav aria-label="Application progress" className="hidden lg:block">
      <div className="sticky top-6 flex flex-col gap-1">
        {STEPS.map((s, i) => {
          const done = i < current, on = i === current, reachable = i <= max;
          return (
            <button key={i} type="button" disabled={!reachable} onClick={() => reachable && onJump(i)}
              className={`flex items-start gap-3 rounded-md px-3 py-2 text-left transition-colors ${
                on ? 'bg-primary/10' : reachable ? 'hover:bg-muted' : 'opacity-50'
              }`}>
              <span className={`flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                done ? 'border-primary bg-primary text-primary-foreground' : on ? 'border-primary text-primary' : 'border-border text-muted-foreground'
              }`}>{done ? <CheckCircle2 size={14} /> : i + 1}</span>
              <span>
                <span className={`block text-sm ${on ? 'font-medium text-foreground' : 'text-foreground'}`}>{s.title}</span>
                <span className="block text-xs text-muted-foreground">{s.sub}</span>
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function Success({ data, reference, onReset }: { data: Data; reference: string; onReset: () => void }) {
  return (
    <div className="mx-auto max-w-lg px-4 py-16 text-center">
      <span className="mx-auto flex size-14 items-center justify-center rounded-full bg-ok-bg text-ok"><CheckCircle2 size={30} /></span>
      <h2 className="mt-4 text-2xl font-bold text-foreground">Application submitted</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Thanks {data.personal?.first || 'driver'} — a recruiter will reach out at{' '}
        <span className="font-medium text-foreground">{data.personal?.phone || 'the number you provided'}</span>.
        Have your CDL, DOT medical card, and employer info ready for the call.
      </p>
      <div className="mt-5 rounded-md border border-border bg-card p-4">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Your reference number</p>
        <p className="mt-1 font-mono text-lg font-semibold text-foreground">{reference}</p>
      </div>
      <button onClick={onReset} className="mt-6 text-sm text-primary hover:underline">Start a new application</button>
    </div>
  );
}

export default function PublicApply() {
  const token = useMemo(resolveToken, []);
  // Pre-seed the signature defaults (today's date, type mode) so Step 8
  // never has to write state during render.
  const [data, setData] = useState<Data>(() => ({ consents: { sigDate: todayISO(), sigMode: 'type' } }));

  // Count the page view for per-link funnel analytics (best-effort,
  // oracle-safe: the endpoint always 204s).
  useEffect(() => {
    if (!token) return;
    fetch(`${API_BASE}/recruitment/track-view`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    }).catch(() => { /* analytics is non-critical */ });
  }, [token]);

  const [step, setStep] = useState(0);
  const [maxReached, setMaxReached] = useState(0);
  const [attempted, setAttempted] = useState<Record<number, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [reference, setReference] = useState('');
  const [done, setDone] = useState(false);
  const cardRef = useRef<HTMLFormElement>(null);

  const set = (path: string, value: unknown) => setData((d) => deepSet(d, path, value));
  const cur = STEPS[step];
  const errors = useMemo(() => cur.validate(data), [cur, data]);
  const hasErrors = Object.keys(errors).length > 0;
  const visibleErrors = attempted[step] ? errors : {};
  const isLast = step === STEPS.length - 1;

  const scrollUp = () => cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const submit = async () => {
    setSubmitting(true);
    setSubmitError('');
    try {
      const fd = new FormData();
      fd.append('link_token', token);
      fd.append('application', JSON.stringify(sanitizeForJson(data)));
      for (const [part, file] of Object.entries(extractFiles(data))) if (file) fd.append(part, file);
      const res = await fetch(`${API_BASE}/recruitment/apply`, { method: 'POST', body: fd });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) throw new Error(json.detail || json.message || `Server returned ${res.status}`);
      setReference(json.reference || 'SUBMITTED');
      setDone(true);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Submission failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const next = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (submitting) return;
    if (hasErrors) { setAttempted((a) => ({ ...a, [step]: true })); return; }
    if (isLast) { submit(); return; }
    const n = step + 1;
    setStep(n);
    setMaxReached((m) => Math.max(m, n));
    setTimeout(scrollUp, 30);
  };
  const back = () => { if (step > 0 && !submitting) { setStep(step - 1); setTimeout(scrollUp, 30); } };
  const jump = (i: number) => { if (i <= maxReached && !submitting) { setStep(i); setTimeout(scrollUp, 30); } };
  const reset = () => {
    setData({ consents: { sigDate: todayISO(), sigMode: 'type' } });
    setStep(0); setMaxReached(0); setAttempted({});
    setDone(false); setSubmitError(''); setReference('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (!token) {
    return (
      <div className="min-h-screen bg-background">
        <Header compact />
        <div className="mx-auto max-w-lg px-4 py-16 text-center">
          <h2 className="text-xl font-semibold text-foreground">This application link is invalid</h2>
          <p className="mt-2 text-sm text-muted-foreground">Please use the full link your recruiter sent you. If you keep seeing this, contact them for a new link.</p>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="min-h-screen bg-background">
        <Header compact />
        <Success data={data} reference={reference} onReset={reset} />
      </div>
    );
  }

  const Render = cur.Render;
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[15rem_1fr]">
          <Stepper current={step} max={maxReached} onJump={jump} />
          <form ref={cardRef} onSubmit={next} noValidate className="rounded-lg border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <p className="text-xs text-muted-foreground">Step {step + 1} of {STEPS.length}</p>
                <h2 className="text-lg font-semibold text-foreground">{cur.title}</h2>
              </div>
              <div className="flex gap-1">
                {STEPS.map((_, i) => (
                  <span key={i} className={`h-1.5 w-5 rounded-full ${i < step ? 'bg-primary' : i === step ? 'bg-primary/60' : 'bg-muted'}`} />
                ))}
              </div>
            </div>
            <div className="px-5 py-5">
              <Render data={data} set={set} errors={visibleErrors} />
              {submitError && (
                <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  <b>Submission failed.</b> {submitError}
                </p>
              )}
            </div>
            <div className="flex items-center justify-between border-t border-border px-5 py-4">
              <button type="button" onClick={back} disabled={step === 0 || submitting}
                className="inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-40">
                <ArrowLeft size={16} /> Back
              </button>
              <button type="submit" disabled={submitting}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">
                {submitting ? 'Submitting…' : isLast ? 'Submit application' : 'Continue'}
                {!submitting && <ArrowRight size={16} />}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
