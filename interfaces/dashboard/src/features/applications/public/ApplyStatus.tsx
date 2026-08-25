// Public self-service status page — mounted on apply.<apex>/status[/<ref>]
// (no auth).  Two-factor lookup: an applicant enters their reference +
// email and sees their application status — nothing else.
import { useMemo, useState } from 'react';
import { Truck, Search, CheckCircle2 } from 'lucide-react';
import { statusTone } from '../../../lib/status';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// Applicant-facing wording (we don't expose internal stage jargon).
const STATUS_VIEW: Record<string, { label: string; msg: string }> = {
  submitted: { label: 'Received', msg: "We've received your application — it's in the queue for review." },
  screening: { label: 'Under review', msg: 'Our team is reviewing your application.' },
  interview: { label: 'Under review', msg: 'Your application is progressing; we may reach out to talk.' },
  approved: { label: 'Approved', msg: "Good news — you've been approved. We'll be in touch with next steps." },
  // Do NOT promise an email here: the hire path mints an invite and hands
  // the link to the RECRUITER to pass on — nothing is sent to the applicant.
  hired: { label: 'Hired', msg: "Congratulations — you've been hired! Your recruiter will send you your onboarding link directly." },
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
      // Anything other than a clean 2xx is OUR problem. Falling through to
      // `{}` made `found` undefined, which rendered the "we couldn't find
      // an application matching that reference and email" copy — telling a
      // legitimate applicant their record doesn't exist because our server
      // hiccuped.
      if (!res.ok) {
        setErr("We couldn't reach the system just now. Your application is unaffected — please try again in a moment.");
        return;
      }
      const json = (await res.json().catch(() => null)) as Result | null;
      if (!json || typeof json.found !== 'boolean') {
        setErr("We couldn't read the response. Please try again in a moment.");
        return;
      }
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
            <Truck className="size-6" />
            <SectionHeader size="card">Application Status</SectionHeader>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-md px-4 py-8">
        <Card render={<form />} onSubmit={check}>
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
              className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60 min-h-tap">
              <Search className="size-3.5" /> {loading ? 'Checking…' : 'Check status'}
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
                <CheckCircle2 className="text-muted-foreground size-4.5" />
                {/* The applicant's own status chip. It hand-rolled the
                    Badge geometry through the `statusClasses` door, which
                    the guard could not see for as long as it existed. */}
                <Badge tone={statusTone(result.status)} className="capitalize">
                  {view?.label ?? result.status}
                </Badge>
              </div>
              <p className="mt-2 text-sm text-foreground">{view?.msg ?? 'Your application is on file.'}</p>
              {result.submitted_at && (
                <p className="mt-1 text-xs text-muted-foreground">Submitted {result.submitted_at.slice(0, 10)}</p>
              )}
            </div>
          )}
        </Card>

        {/* "/" on apply.<apex> resolves to an EMPTY first path segment, so
            resolveToken() returns '' and the driver lands on "This
            application link is invalid" — a dead end presented as the way
            out. There is no generic apply URL to send them to, so point at
            the only thing that actually works. */}
        <p className="mt-4 text-sm text-muted-foreground">
          Applying somewhere else? Use the application link your recruiter
          sent you — each posting has its own.
        </p>
      </main>
    </div>
  );
}
