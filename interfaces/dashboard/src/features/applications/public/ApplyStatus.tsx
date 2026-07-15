// Public self-service status page — mounted on apply.<apex>/status[/<ref>]
// (no auth).  Two-factor lookup: an applicant enters their reference +
// email and sees their application status — nothing else.
import { useMemo, useState } from 'react';
import { Truck, Search, CheckCircle2, ArrowLeft } from 'lucide-react';
import { statusClasses } from '../../../lib/status';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// Applicant-facing wording (we don't expose internal stage jargon).
const STATUS_VIEW: Record<string, { label: string; msg: string }> = {
  submitted: { label: 'Received', msg: "We've received your application — it's in the queue for review." },
  screening: { label: 'Under review', msg: 'Our team is reviewing your application.' },
  interview: { label: 'Under review', msg: 'Your application is progressing; we may reach out to talk.' },
  approved: { label: 'Approved', msg: "Good news — you've been approved. We'll be in touch with next steps." },
  hired: { label: 'Hired', msg: 'Congratulations! Check your email for your onboarding link.' },
  rejected: { label: 'Not selected', msg: "Thank you for your interest — we won't be moving forward at this time." },
  withdrawn: { label: 'Withdrawn', msg: 'This application has been withdrawn.' },
};

interface Result { found: boolean; status?: string; submitted_at?: string }

// Prefill the reference from /status/<ref> if present.
function refFromPath(): string {
  const segs = window.location.pathname.split('/').filter(Boolean);
  const i = segs.findIndex((s) => s.toLowerCase() === 'status');
  return i >= 0 && segs[i + 1] ? decodeURIComponent(segs[i + 1]).toUpperCase() : '';
}

export default function ApplyStatus() {
  const [reference, setReference] = useState(useMemo(refFromPath, []));
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState('');

  const check = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reference.trim() || !email.trim()) return;
    setLoading(true); setErr(''); setResult(null);
    try {
      const res = await fetch(`${API_BASE}/applications/application-status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reference: reference.trim(), email: email.trim() }),
      });
      if (res.status === 429) { setErr('Too many attempts — please try again later.'); return; }
      const json = (await res.json().catch(() => ({}))) as Result;
      setResult(json);
    } catch {
      setErr('Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const view = result?.found && result.status ? STATUS_VIEW[result.status] : undefined;
  const inputCls = 'w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 outline-none focus:border-ring focus:ring-2 focus:ring-ring/40';

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card">
        <div className="mx-auto max-w-5xl px-4 py-5 sm:px-6">
          <div className="flex items-center gap-2 text-primary">
            <Truck size={24} />
            <span className="text-base font-semibold text-foreground">Application Status</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-md px-4 py-8">
        <form onSubmit={check} className="rounded-lg border border-border bg-card p-5">
          <h1 className="text-lg font-semibold text-foreground">Check your application</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enter your reference number and the email you applied with.
          </p>
          <div className="mt-4 flex flex-col gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-foreground">Reference number</span>
              <input value={reference} onChange={(e) => setReference(e.target.value)}
                placeholder="APP-XXXXXX" className={`${inputCls} font-mono`} />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-foreground">Email</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com" className={inputCls} />
            </label>
            <button type="submit" disabled={loading || !reference.trim() || !email.trim()}
              className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">
              <Search size={14} /> {loading ? 'Checking…' : 'Check status'}
            </button>
          </div>

          {err && <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{err}</p>}

          {result && !result.found && (
            <p className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              We couldn't find an application matching that reference and email. Double-check both and try again.
            </p>
          )}

          {result?.found && (
            <div className="mt-4 rounded-md border border-border p-4">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={18} className="text-muted-foreground" />
                <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${statusClasses(result.status)}`}>
                  {view?.label ?? result.status}
                </span>
              </div>
              <p className="mt-2 text-sm text-foreground">{view?.msg ?? 'Your application is on file.'}</p>
              {result.submitted_at && (
                <p className="mt-1 text-xs text-muted-foreground">Submitted {result.submitted_at.slice(0, 10)}</p>
              )}
            </div>
          )}
        </form>

        <a href="/" className="mt-4 inline-flex items-center gap-1 text-sm text-primary hover:underline">
          <ArrowLeft size={14} /> Start a new application
        </a>
      </main>
    </div>
  );
}
