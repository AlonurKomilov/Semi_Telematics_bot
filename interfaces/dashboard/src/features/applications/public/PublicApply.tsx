// Public driver-application page — mounted standalone on apply.<apex>
// (no auth / shell / router; see main.tsx host branch).
//
// Submits multipart to POST /api/applications/apply: a JSON `application`
// part (file blobs stripped) + the raw document files as their own parts.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Truck, Clock, ShieldCheck, CheckCircle2, ArrowLeft, ArrowRight, Lock } from 'lucide-react';
import { STEPS } from './steps';
import { deepGet, deepSet, DISCLOSURE_VERSION, todayISO } from './lib';
import type { Data } from './lib';
import { brandTintStyle, onColorStyle, surfaceThemeStyle } from './theme';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// Carrier branding for a recruiting link, from GET /applications/brand.
// Display-only — the public endpoint never returns the Samsara key.
export interface Brand {
  name: string; brand_color: string; website: string; phone: string;
  mc_number: string; usdot_number: string; has_logo: boolean;
  headline: string; perks: string[]; has_banner: boolean;
  // Per-carrier pre-qual gate thresholds.
  req_experience_years: number; req_min_age: number; req_cdl_class: string;
  // Base theme the form renders on: 'light' | 'dark' (legacy).
  form_theme: string;
  // Surface colour — derives the whole neutral palette (card/text/borders).
  surface_color: string;
  // Optional extra colours (empty → base default).
  header_color: string; bg_color: string; heading_color: string;
  // Legal/compliance details that fill the Step 8 consent disclosures.
  legal_address: string; compliance_email: string;
  cra_name: string; cra_address: string; cra_phone: string; cra_site: string;
}

// Recruiter preview: the authed dashboard wrapper passes the carrier brand
// + pre-loaded (authed-blob) image URLs; the form renders read-only.
export interface ApplyPreviewProps { brand: Brand; logoUrl?: string; bannerUrl?: string }

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

// ── Auto-save (this device) ─────────────────────────────────────────
// The in-progress application persists to localStorage (keyed per link
// token) so an interrupted applicant can pick up where they left off on
// THIS device.  File blobs can't be serialised — they're stripped, and the
// resume banner says documents need re-attaching (validation re-asks).
// Bump DRAFT_VERSION whenever the step list / data shape changes so stale
// drafts are ignored rather than restored misaligned.
const DRAFT_VERSION = 1;
const draftKey = (t: string) => `apply.draft.${t}`;
interface Draft { v: number; data: Data; step: number; maxReached: number; savedAt: string }

function stripFilesForDraft(data: Data): Data {
  const clone: Data = JSON.parse(JSON.stringify(data));
  if (clone.cdl) delete clone.cdl.docs;
  if (clone.position?.truck) {
    delete clone.position.truck.picture;
    delete clone.position.truck.dotInspection;
  }
  return clone;
}

function Header({ compact, brand, token, logoUrl, bannerUrl }: {
  compact?: boolean; brand?: Brand | null; token?: string;
  // Preview mode passes pre-loaded (authed-blob) image URLs that override
  // the public token URLs below.
  logoUrl?: string; bannerUrl?: string;
}) {
  // The carrier brand colours (accent / header / bg) are per-tenant runtime
  // data — like chart/map colours, the sanctioned place a literal colour
  // reaches the DOM.  onColorStyle() tints the header band + sets readable
  // text vars scoped to it.
  const accent = brand?.brand_color || '';
  const hdrStyle = onColorStyle(brand?.header_color);
  // Optional manual heading colour — tints the title + carrier name only.
  const headingStyle = brand?.heading_color ? { color: brand.heading_color } : undefined;
  const logoSrc = logoUrl ?? (brand?.has_logo && token
    ? `${API_BASE}/applications/brand-logo?token=${encodeURIComponent(token)}`
    : '');
  const bannerSrc = bannerUrl ?? (brand?.has_banner && token
    ? `${API_BASE}/applications/brand-banner?token=${encodeURIComponent(token)}`
    : '');
  return (
    <header className="border-b border-border bg-card" style={hdrStyle}>
      {!compact && bannerSrc && (
        <img src={bannerSrc} alt=""
          className="h-44 w-full object-cover object-center sm:h-56 lg:h-72" />
      )}
      {accent && <div className="h-1 w-full" style={{ backgroundColor: accent }} />}
      <div className="mx-auto max-w-5xl px-4 py-5 sm:px-6">
        <div className="flex items-center gap-2 text-primary">
          {logoSrc
            ? <img src={logoSrc} alt={brand?.name || 'Carrier'} className="h-7 w-auto max-w-40 object-contain" />
            : <Truck size={22} style={accent ? { color: accent } : undefined} />}
          <span className="text-base font-semibold text-foreground" style={headingStyle}>
            {brand?.name ? `${brand.name} · Driver Application` : 'Driver Application'}
          </span>
        </div>
        {!compact && (
          <>
            <h1 className="mt-3 text-2xl font-bold text-foreground" style={headingStyle}>
              {brand?.headline || (brand?.name ? `Apply to drive with ${brand.name}.` : 'Join our driving team.')}
            </h1>
            {brand?.perks && brand.perks.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {brand.perks.slice(0, 6).map((p) => (
                  <span key={p} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-foreground">
                    <CheckCircle2 size={14} style={accent ? { color: accent } : undefined} /> {p}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-3 max-w-2xl text-sm text-muted-foreground">
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

// Collapse consecutive same-group steps into one "display unit", so the grouped
// Final-Authorizations screens count + show as a SINGLE step — one sidebar row,
// one progress dot, "Step 8 of 8" — while each still renders on its own screen.
interface DisplayUnit { label: string; sub: string; group?: string; steps: number[] }
// Sidebar sub-label for each grouped unit (a group spans several screens, so
// it can't borrow one member's sub).
const GROUP_SUB: Record<string, string> = {
  'Driver Profile': 'Identity, license & experience',
  'Final Authorizations': 'PSP · Background check · Consents',
};
const UNITS: DisplayUnit[] = [];
STEPS.forEach((s, i) => {
  const last = UNITS[UNITS.length - 1];
  if (s.group && last && last.group === s.group) last.steps.push(i);
  else UNITS.push({ label: s.group || s.title, sub: s.group ? (GROUP_SUB[s.group] ?? '') : s.sub, group: s.group, steps: [i] });
});
const unitIndexOf = (stepIndex: number) => UNITS.findIndex((u) => u.steps.includes(stepIndex));

function Stepper({ current, max, onJump, style }: {
  current: number; max: number; onJump: (i: number) => void; style?: React.CSSProperties;
}) {
  const curUnit = unitIndexOf(current);
  return (
    // `style` carries readable text vars when a custom page Background is set,
    // so the sidebar never goes dark-on-dark (the card stays base-themed).
    <nav aria-label="Application progress" className="hidden lg:block" style={style}>
      <div className="sticky top-6 flex flex-col gap-1">
        {UNITS.map((u, ui) => {
          const lastStep = u.steps[u.steps.length - 1];
          const done = current > lastStep, on = ui === curUnit, reachable = max >= u.steps[0];
          // Jump to the furthest sub-step already reached in this unit, so a
          // grouped unit never bounces the driver back to its first screen.
          const target = u.steps.filter((i) => i <= max).pop() ?? u.steps[0];
          return (
            <button key={ui} type="button" disabled={!reachable} onClick={() => reachable && onJump(target)}
              className={`flex items-start gap-3 rounded-md px-3 py-2 text-left transition-colors ${
                on ? 'bg-primary/10' : reachable ? 'hover:bg-muted' : 'opacity-50'
              }`}>
              <span className={`flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                done ? 'border-primary bg-primary text-primary-foreground' : on ? 'border-primary text-primary' : 'border-border text-muted-foreground'
              }`}>{done ? <CheckCircle2 size={14} /> : ui + 1}</span>
              <span>
                <span className={`block text-sm ${on ? 'font-medium text-foreground' : 'text-foreground'}`}>{u.label}</span>
                <span className="block text-xs text-muted-foreground">{u.sub}</span>
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
        <a href={`/status/${encodeURIComponent(reference)}`} className="mt-2 inline-block text-xs text-primary hover:underline">
          Check your application status later →
        </a>
      </div>
      <button onClick={onReset} className="mt-6 text-sm text-primary hover:underline">Start a new application</button>
    </div>
  );
}

export default function PublicApply({ preview }: { preview?: ApplyPreviewProps } = {}) {
  const realToken = useMemo(resolveToken, []);
  // In preview the token is EMPTY (the wrapper supplies brand + image blobs):
  // the network calls (track-view / brand / submit) are skipped below, and an
  // empty token means no token-based <img> URLs are built — so the form never
  // requests /brand-logo?token=… (which 404s for the non-existent preview).
  const token = preview ? '' : realToken;
  // Pre-seed the signature defaults (today's date, type mode) so Step 8
  // never has to write state during render.
  const [data, setData] = useState<Data>(() => ({ consents: { sigDate: todayISO(), sigMode: 'type' } }));

  // Count the page view for per-link funnel analytics (best-effort,
  // oracle-safe: the endpoint always 204s).
  useEffect(() => {
    if (preview || !token) return;
    fetch(`${API_BASE}/applications/track-view`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    }).catch(() => { /* analytics is non-critical */ });
  }, [token, preview]);

  // Carrier branding for this link (logo / name / accent colour).  Always
  // 200 with {company:null} for generic links — falls back to default look.
  // In preview the wrapper supplies the brand directly (no public fetch).
  const [brand, setBrand] = useState<Brand | null>(preview?.brand ?? null);
  // In preview the wrapper OWNS the brand (live theme edits from the toolbar);
  // sync it in so accent/base changes re-tint the form immediately — no
  // refresh needed.  No-op outside preview (the fetch below owns it).
  useEffect(() => {
    if (preview?.brand) setBrand(preview.brand);
  }, [preview?.brand]);
  useEffect(() => {
    if (preview || !token) return;
    fetch(`${API_BASE}/applications/brand?token=${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setBrand(j?.company || null))
      .catch(() => { /* generic look on failure */ });
  }, [token, preview]);
  // Compose the form's theme from carrier colours, all scoped to the form
  // root: a derived neutral palette from the Surface colour (card / text /
  // borders / muted), the accent tint (button/steps/links/ring), and an
  // optional custom page background.  No `.dark` class — the Surface colour
  // is the single base now.
  const merged: React.CSSProperties = {
    ...(surfaceThemeStyle(brand?.surface_color) || {}),
    ...(brandTintStyle(brand?.brand_color) || {}),
    ...(brand?.bg_color ? { backgroundColor: brand.bg_color } : {}),
  };
  const brandStyle: React.CSSProperties | undefined = Object.keys(merged).length ? merged : undefined;
  const rootClass = 'min-h-screen bg-background';

  const [step, setStep] = useState(0);
  const [maxReached, setMaxReached] = useState(0);
  const [attempted, setAttempted] = useState<Record<number, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [reference, setReference] = useState('');
  const [done, setDone] = useState(false);
  // Honeypot — a hidden field real applicants never see/fill; a bot that
  // auto-fills every input trips it and the server silently drops it.
  const [honeypot, setHoneypot] = useState('');
  const cardRef = useRef<HTMLFormElement>(null);

  // ── Auto-save & resume (see draftKey above) ───────────────────────
  // A found draft is held here until the applicant chooses Resume / Start
  // over; auto-save is paused while the banner is pending so a pristine
  // mount can't overwrite the saved progress.
  const [resumeDraft, setResumeDraft] = useState<Draft | null>(null);
  const [savedTick, setSavedTick] = useState(false);
  useEffect(() => {
    if (preview || !token) return;
    try {
      const raw = localStorage.getItem(draftKey(token));
      if (!raw) return;
      const d = JSON.parse(raw) as Draft;
      // Ignore stale/foreign drafts, and don't nag over trivial progress
      // (they never made it past the first step).
      if (d?.v !== DRAFT_VERSION || typeof d.step !== 'number') return;
      if (d.step < 0 || d.step >= STEPS.length || (d.maxReached ?? 0) < 1) return;
      setResumeDraft(d);
    } catch { /* corrupted draft — start fresh */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (preview || !token || done || resumeDraft) return;
    const id = setTimeout(() => {
      try {
        localStorage.setItem(draftKey(token), JSON.stringify({
          v: DRAFT_VERSION, data: stripFilesForDraft(data), step, maxReached,
          savedAt: new Date().toISOString(),
        } satisfies Draft));
        setSavedTick(true);
      } catch { /* storage full / disabled — auto-save is best-effort */ }
    }, 500);
    return () => clearTimeout(id);
  }, [data, step, maxReached, preview, token, done, resumeDraft]);
  const clearDraft = () => { try { if (token) localStorage.removeItem(draftKey(token)); } catch { /* ignore */ } };
  const resumeSaved = () => {
    if (!resumeDraft) return;
    setData({ consents: { sigDate: todayISO(), sigMode: 'type' }, ...resumeDraft.data });
    const s = Math.min(resumeDraft.step, STEPS.length - 1);
    setStep(s);
    setMaxReached(Math.max(resumeDraft.maxReached ?? s, s));
    setResumeDraft(null);
  };
  const discardSaved = () => { clearDraft(); setResumeDraft(null); };

  const set = (path: string, value: unknown) => setData((d) => deepSet(d, path, value));
  // Prefill helper for the CDL fast-fill: writes only when the field is
  // still blank, checked atomically against the LATEST state — so an OCR
  // response can never overwrite something the applicant typed while the
  // photo was being read.
  const setIfEmpty = (path: string, value: unknown) => setData((d) => {
    const cur_ = deepGet(d, path);
    if (cur_ !== undefined && cur_ !== null && String(cur_).trim() !== '') return d;
    return deepSet(d, path, value);
  });
  const cur = STEPS[step];
  // The grouped consent screens collapse into ONE display unit, so the counter,
  // sidebar, and progress bar treat them as a single "Step 8 of 8".  Within it,
  // a "· N of 3" sub-counter shows which legally-separate screen is on view.
  const curUnit = unitIndexOf(step);
  const groupSize = cur.group ? STEPS.filter((s) => s.group === cur.group).length : 0;
  const groupPos = cur.group ? STEPS.slice(0, step + 1).filter((s) => s.group === cur.group).length : 0;
  const errors = useMemo(() => cur.validate(data), [cur, data]);
  const hasErrors = Object.keys(errors).length > 0;
  const visibleErrors = attempted[step] ? errors : {};
  const isLast = step === STEPS.length - 1;

  const scrollUp = () => cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const submit = async () => {
    if (preview) return;  // read-only recruiter preview — never posts
    setSubmitting(true);
    setSubmitError('');
    try {
      const fd = new FormData();
      fd.append('link_token', token);
      fd.append('hp', honeypot);
      fd.append('application', JSON.stringify(sanitizeForJson(data)));
      for (const [part, file] of Object.entries(extractFiles(data))) if (file) fd.append(part, file);
      const res = await fetch(`${API_BASE}/applications/apply`, { method: 'POST', body: fd });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json.success === false) throw new Error(json.detail || json.message || `Server returned ${res.status}`);
      setReference(json.reference || 'SUBMITTED');
      setDone(true);
      clearDraft();   // submitted — the saved draft has served its purpose
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
    // Preview: walk through every step freely (no validation, never posts).
    if (preview) {
      if (isLast) return;
      const n = step + 1; setStep(n); setMaxReached((m) => Math.max(m, n)); setTimeout(scrollUp, 30);
      return;
    }
    if (hasErrors) { setAttempted((a) => ({ ...a, [step]: true })); return; }
    if (isLast) {
      // Final gate: EVERY step must be complete, not just this one — a driver
      // may have jumped back via the stepper and blanked an earlier required
      // field.  Bounce to the first incomplete step and reveal its errors.
      const bad = STEPS.findIndex((s) => Object.keys(s.validate(data)).length > 0);
      if (bad >= 0) {
        setAttempted((a) => ({ ...a, [bad]: true }));
        setStep(bad);
        setTimeout(scrollUp, 30);
        return;
      }
      submit();
      return;
    }
    const n = step + 1;
    setStep(n);
    setMaxReached((m) => Math.max(m, n));
    setTimeout(scrollUp, 30);
  };
  const back = () => { if (step > 0 && !submitting) { setStep(step - 1); setTimeout(scrollUp, 30); } };
  const jump = (i: number) => { if (i <= maxReached && !submitting) { setStep(i); setTimeout(scrollUp, 30); } };
  const reset = () => {
    clearDraft();
    setData({ consents: { sigDate: todayISO(), sigMode: 'type' } });
    setStep(0); setMaxReached(0); setAttempted({});
    setDone(false); setSubmitError(''); setReference('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (!token && !preview) {
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
      <div className={rootClass} style={brandStyle}>
        <Header compact brand={brand} token={token} />
        <Success data={data} reference={reference} onReset={reset} />
      </div>
    );
  }

  const Render = cur.Render;
  return (
    <div className={rootClass} style={brandStyle}>
      {preview && (
        <div className="bg-warn-bg px-4 py-2 text-center text-xs font-medium text-warn">
          Preview — this is exactly what an applicant sees. Submitting is disabled here.
        </div>
      )}
      <Header brand={brand} token={token} logoUrl={preview?.logoUrl} bannerUrl={preview?.bannerUrl} />
      <main className={`mx-auto max-w-5xl px-4 py-6 sm:px-6${preview ? ' pb-32' : ''}`}>
        {resumeDraft && (
          <div className="mb-5 flex flex-col gap-3 rounded-md border border-info-bd bg-info-bg p-4 sm:flex-row sm:items-center">
            <p className="flex-1 text-sm text-info">
              <b>Welcome back.</b> Your progress was saved on this device — pick up where you
              left off? Any uploaded documents will need to be re-attached.
            </p>
            <div className="flex shrink-0 gap-2">
              <button type="button" onClick={resumeSaved}
                className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90">
                Resume
              </button>
              <button type="button" onClick={discardSaved}
                className="rounded-md border border-border bg-card px-3 py-1.5 text-sm text-foreground hover:bg-muted">
                Start over
              </button>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[15rem_1fr]">
          <Stepper current={step} max={maxReached} onJump={jump} style={onColorStyle(brand?.bg_color)} />
          <form ref={cardRef} onSubmit={next} noValidate className="rounded-lg border border-border bg-card">
            {/* Honeypot: off-screen + aria-hidden + not tabbable + a name no
                autofiller targets, so a real applicant never fills it; a
                form-filling bot does. */}
            <input type="text" name="contact_time" tabIndex={-1} autoComplete="off"
              aria-hidden="true" value={honeypot} onChange={(e) => setHoneypot(e.target.value)}
              style={{ position: 'absolute', left: '-9999px', width: 1, height: 1, opacity: 0 }} />
            <div className="flex flex-col gap-2 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs text-muted-foreground">
                  {cur.group ? `${cur.group} · ${groupPos} of ${groupSize}` : `Step ${curUnit + 1} of ${UNITS.length}`}
                  {savedTick && (step > 0 || maxReached > 0) && (
                    <span className="ml-2 inline-flex items-center gap-1 text-2xs text-muted-foreground/70">
                      <CheckCircle2 size={12} /> Saved on this device
                    </span>
                  )}
                </p>
                <h2 className="text-lg font-semibold text-foreground">{cur.title}</h2>
              </div>
              <div className="flex flex-wrap gap-1">
                {UNITS.map((_, ui) => (
                  <span key={ui} className={`h-1.5 w-5 rounded-full ${ui <= curUnit ? 'bg-primary' : 'bg-muted'}`} />
                ))}
              </div>
            </div>
            <div className="px-5 py-5">
              <Render data={data} set={set} errors={visibleErrors}
                token={token} setIfEmpty={setIfEmpty}
                req={brand ? { years: brand.req_experience_years, age: brand.req_min_age, cls: brand.req_cdl_class } : undefined}
                carrier={brand ? {
                  name: brand.name, dot: brand.usdot_number, mc: brand.mc_number, phone: brand.phone,
                  legal_address: brand.legal_address, compliance_email: brand.compliance_email,
                  cra_name: brand.cra_name, cra_address: brand.cra_address,
                  cra_phone: brand.cra_phone, cra_site: brand.cra_site,
                } : undefined} />
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
              <button type="submit" disabled={submitting || (!!preview && isLast)}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">
                {preview
                  ? (isLast ? 'Submit disabled in preview' : 'Continue')
                  : submitting ? 'Submitting…' : isLast ? 'Submit application' : 'Continue'}
                {!submitting && !(preview && isLast) && <ArrowRight size={16} />}
              </button>
            </div>
          </form>
        </div>
      </main>
      {(brand?.website || brand?.phone) && (
        <footer className="mx-auto max-w-5xl px-4 pb-8 text-xs text-muted-foreground sm:px-6"
          style={onColorStyle(brand?.bg_color)}>
          Questions about driving with {brand?.name || 'us'}?{' '}
          {brand?.phone && (
            <a href={`tel:${brand.phone}`} className="text-foreground hover:underline">{brand.phone}</a>
          )}
          {brand?.phone && brand?.website && <span> · </span>}
          {brand?.website && (
            <a href={brand.website.startsWith('http') ? brand.website : `https://${brand.website}`}
              target="_blank" rel="noopener noreferrer" className="text-foreground hover:underline">
              {brand.website}
            </a>
          )}
        </footer>
      )}
    </div>
  );
}
