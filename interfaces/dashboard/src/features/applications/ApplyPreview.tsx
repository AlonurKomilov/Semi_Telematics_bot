// Recruiter-only full-form preview.  Renders the REAL public apply form
// (so the preview can't drift from what applicants see) with the selected
// carrier's brand + pre-qual requirements, read-only.  Authed on the
// dashboard: it loads the brand from the recruiter company list and the
// logo/photo via authed blobs, then hands everything to PublicApply.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { Monitor, Smartphone, ImagePlus, Sparkles, Loader2 } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { Button } from '../../components/ui/button';
import PublicApply, { type Brand } from './public/PublicApply';
import { applyPublicFormTheme, surfaceContrastWeak } from './public/theme';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

// A staged (not-yet-saved) logo/hero change: a picked file (with its preview
// object URL), an explicit removal, or null = no change.  Committed on Save.
type AssetPending = { file: File; url: string } | 'remove' | null;

// Live design editor that floats over the preview — swap the carrier's logo
// and hero photo and adjust every brand colour, watching the form update in
// real time, then save.  Rendered OUTSIDE the form root, so it stays on the
// dashboard's (light) theme and is always legible even over a dark surface.
function Swatch({ label, value, onChange }: { label: string; value: string; onChange: (c: string) => void }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {label}
      <input type="color" value={value || '#000000'} onChange={(e) => onChange(e.target.value)}
        title={label} className="h-7 w-8 cursor-pointer rounded border border-border bg-card" />
      {value && <button type="button" onClick={() => onChange('')} aria-label="Clear" className="text-2xs hover:text-danger py-1 -my-1 min-h-tap">×</button>}
    </span>
  );
}

// A logo / hero image control: a small thumbnail that opens a file picker to
// upload-or-replace, plus a × to remove.  Like the colour swatches, a pick or
// remove is STAGED — shown live but only written to the carrier on Save.
function AssetControl({ label, url, present, busy, onPick, onClear }: {
  label: string; url?: string; present: boolean; busy: boolean;
  onPick: (f: File) => void; onClear: () => void;
}) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {label}
      <label aria-label={`${present ? 'Replace' : 'Upload'} ${label.toLowerCase()}`}
        className={`inline-flex h-7 w-9 items-center justify-center overflow-hidden rounded border border-border bg-card ${busy ? 'opacity-50' : 'cursor-pointer hover:border-primary'}`}>
        <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); e.currentTarget.value = ''; }} />
        {url ? <img src={url} alt="" className="h-full w-full object-contain" /> : <ImagePlus className="size-3.5" />}
      </label>
      {present && <button type="button" onClick={onClear} disabled={busy} aria-label="remove" className="text-2xs hover:text-danger disabled:opacity-50 py-1 -my-1 min-h-tap">×</button>}
    </span>
  );
}

// One AI-proposed palette: the five theme slots + a mood name.
export interface AiPalette {
  name: string; surface: string; accent: string; header: string;
  bg: string; heading: string;
}

function PreviewThemeBar({ brand, saving, device, logoUrl, bannerUrl, logoPresent, bannerPresent, onColor, onDevice, onAsset, onClearAsset, onSave, onAiTheme, aiOpen }: {
  brand: Brand; saving: boolean; device: 'desktop' | 'mobile';
  logoUrl?: string; bannerUrl?: string; logoPresent: boolean; bannerPresent: boolean;
  onColor: (key: 'brand_color' | 'surface_color' | 'header_color' | 'bg_color' | 'heading_color', c: string) => void;
  onDevice: (d: 'desktop' | 'mobile') => void;
  onAsset: (kind: 'logo' | 'banner', f: File) => void;
  onClearAsset: (kind: 'logo' | 'banner') => void;
  onSave: () => void;
  onAiTheme: () => void; aiOpen: boolean;
}) {
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-card px-4 py-2.5 text-foreground shadow-lg">
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="font-medium">View</span>
        <span className="inline-flex overflow-hidden rounded-md border border-border">
          {([['desktop', Monitor], ['mobile', Smartphone]] as const).map(([d, Icon]) => (
            <button key={d} type="button" onClick={() => onDevice(d)} aria-label={`Preview on ${d}`}
              className={`px-2 py-1 ${device === d ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground hover:bg-muted/70'}`}>
              <Icon className="size-3.5" />
            </button>
          ))}
        </span>
      </span>
      <span className="h-5 w-px bg-border" aria-hidden="true" />
      <span className="text-xs font-medium text-muted-foreground">Brand</span>
      <AssetControl label="Logo" url={logoUrl} present={logoPresent} busy={saving}
        onPick={(f) => onAsset('logo', f)} onClear={() => onClearAsset('logo')} />
      <AssetControl label="Hero" url={bannerUrl} present={bannerPresent} busy={saving}
        onPick={(f) => onAsset('banner', f)} onClear={() => onClearAsset('banner')} />
      <span className="h-5 w-px bg-border" aria-hidden="true" />
      <span className="text-xs font-medium text-muted-foreground">Theme</span>
      <Swatch label="Surface" value={brand.surface_color} onChange={(c) => onColor('surface_color', c)} />
      <Swatch label="Accent" value={brand.brand_color} onChange={(c) => onColor('brand_color', c)} />
      <Swatch label="Header" value={brand.header_color} onChange={(c) => onColor('header_color', c)} />
      <Swatch label="Background" value={brand.bg_color} onChange={(c) => onColor('bg_color', c)} />
      <Swatch label="Heading" value={brand.heading_color} onChange={(c) => onColor('heading_color', c)} />
      <button type="button" onClick={onAiTheme} aria-label="Generate palettes from the logo"
        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 min-h-tap text-xs transition-colors ${
          aiOpen ? 'border-primary bg-primary/10 text-foreground' : 'border-border bg-muted text-foreground hover:bg-muted/70'
        }`}>
        <Sparkles className="size-3" /> AI theme
      </button>
      {surfaceContrastWeak(brand.surface_color) && (
        <Badge tone="warn">Surface is mid-tone — text may be low-contrast</Badge>
      )}
      <Button size="sm" onClick={onSave} disabled={saving}>{saving ? '…' : 'Save'}</Button>
    </div>
  );
}

interface RawCompany {
  id: number; code: string; display_name: string; has_logo: boolean; brand_color: string;
  website: string; phone: string; mc_number: string; usdot_number: string;
  headline: string; perks: string; has_banner: boolean;
  req_experience_years: number; req_min_age: number; req_cdl_class: string;
  form_theme: string; surface_color: string; header_color: string; bg_color: string; heading_color: string;
  legal_address: string; compliance_email: string;
  cra_name: string; cra_address: string; cra_phone: string; cra_site: string;
}

export default function ApplyPreview() {
  const { companyId } = useParams<{ companyId: string }>();

  // Reproduce the PUBLIC apply form's theme (not the dashboard's) so the
  // preview matches apply.4truck.us exactly — and restore the recruiter's
  // dashboard theme on exit.  The rule lives in one place (applyPublicFormTheme),
  // shared with the real form, so this never drifts if that theme changes.
  // useLayoutEffect → applied before paint, so there's no flash.
  useLayoutEffect(() => applyPublicFormTheme(), []);

  const [brand, setBrand] = useState<Brand | null>(null);
  const [logoUrl, setLogoUrl] = useState<string | undefined>();
  const [bannerUrl, setBannerUrl] = useState<string | undefined>();
  const [err, setErr] = useState('');
  const [saving, setSaving] = useState(false);
  // Staged logo/hero edits — shown live but only written to the carrier on
  // Save (like the colours), so a recruiter can preview "how does it look
  // without the logo?" without it going live until they commit.
  const [logoPending, setLogoPending] = useState<AssetPending>(null);
  const [bannerPending, setBannerPending] = useState<AssetPending>(null);
  // Object URLs minted from picked files — revoked on unmount.
  const madeUrls = useRef<string[]>([]);
  useEffect(() => () => { madeUrls.current.forEach(URL.revokeObjectURL); }, []);
  // Device view + iframe-context detection.  When THIS preview is rendered
  // inside the mobile iframe (window.self !== top), it shows just the form
  // (no toolbar) — and the narrow iframe gives the form a real phone
  // viewport, so its mobile breakpoints actually fire.
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop');
  // ── AI theme maker ────────────────────────────────────────────────
  const [aiOpen, setAiOpen] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiPalettes, setAiPalettes] = useState<AiPalette[]>([]);
  const generateAiThemes = async () => {
    if (aiBusy) return;
    setAiBusy(true);
    try {
      const r = await apiJSON<{ palettes: AiPalette[] }>(
        `/applications/companies/${companyId}/brand/ai-theme`,
        { method: 'POST', body: { prompt: aiPrompt } },
      );
      setAiPalettes(r.palettes || []);
      if (!r.palettes?.length) toast.error("Couldn't generate a palette — try different style wishes.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setAiBusy(false);
    }
  };
  // Applying a candidate is a LIVE preview change like the manual swatches —
  // nothing persists until the normal Save.
  const applyPalette = (p: AiPalette) =>
    setBrand((b) => (b ? {
      ...b, surface_color: p.surface, brand_color: p.accent,
      header_color: p.header, bg_color: p.bg, heading_color: p.heading,
    } : b));
  const inFrame = useMemo(() => { try { return window.self !== window.top; } catch { return true; } }, []);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const brandRef = useRef<Brand | null>(brand);
  brandRef.current = brand;
  const pendRef = useRef<{ logo: AssetPending; banner: AssetPending }>({ logo: logoPending, banner: bannerPending });
  pendRef.current = { logo: logoPending, banner: bannerPending };

  // Stream live theme edits into the mobile iframe so it updates in real
  // time, exactly like the desktop view.  Parent → iframe via postMessage;
  // the iframe (frame mode, below) listens + replaces its brand.
  const ORIGIN = window.location.origin;
  useEffect(() => {
    if (inFrame || device !== 'mobile') return;
    iframeRef.current?.contentWindow?.postMessage({ type: 'preview-brand', brand }, ORIGIN);
  }, [brand, device, inFrame, ORIGIN]);
  // When the iframe (re)mounts it announces 'preview-ready'; send it the
  // current brand then (its own listener may not exist at onLoad time yet).
  useEffect(() => {
    if (inFrame) return;
    const onMsg = (e: MessageEvent) => {
      if (e.origin === ORIGIN && e.data?.type === 'preview-ready') {
        const w = iframeRef.current?.contentWindow;
        w?.postMessage({ type: 'preview-brand', brand: brandRef.current }, ORIGIN);
        // A freshly-mounted mobile iframe loads the SAVED images itself — also
        // re-send any STAGED change so unsaved edits show there too.
        (['logo', 'banner'] as const).forEach((kind) => {
          const p = pendRef.current[kind];
          if (p) w?.postMessage({ type: 'preview-asset', kind, blob: p === 'remove' ? null : p.file }, ORIGIN);
        });
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [inFrame, ORIGIN]);
  // Frame mode: receive live brand + asset swaps from the parent + announce
  // readiness.  Images arrive as raw Blobs (structured-clone copies them
  // across the document boundary — a parent `blob:` URL wouldn't resolve
  // here), so the frame object-URLs them itself.
  useEffect(() => {
    if (!inFrame) return;
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== ORIGIN) return;
      if (e.data?.type === 'preview-brand' && e.data.brand) setBrand(e.data.brand);
      if (e.data?.type === 'preview-asset') {
        const { kind, blob } = e.data as { kind: 'logo' | 'banner'; blob: Blob | null };
        const u = blob ? URL.createObjectURL(blob) : undefined;
        if (kind === 'logo') setLogoUrl(u); else setBannerUrl(u);
      }
    };
    window.addEventListener('message', onMsg);
    window.parent?.postMessage({ type: 'preview-ready' }, ORIGIN);
    return () => window.removeEventListener('message', onMsg);
  }, [inFrame, ORIGIN]);

  // Live edits to any colour re-render the form immediately; Save persists the
  // whole brand (colours + logo + hero) to the carrier in one commit.
  const onColor = (key: 'brand_color' | 'surface_color' | 'header_color' | 'bg_color' | 'heading_color', c: string) =>
    setBrand((b) => (b ? { ...b, [key]: c } : b));

  // Stage a logo/hero change locally + stream it into the mobile iframe (as a
  // raw Blob — a parent blob: URL wouldn't resolve across the frame).  Nothing
  // touches the server until Save.
  const setPending = (kind: 'logo' | 'banner', val: AssetPending) => {
    const cur = kind === 'logo' ? logoPending : bannerPending;
    if (cur && cur !== 'remove') URL.revokeObjectURL(cur.url);
    (kind === 'logo' ? setLogoPending : setBannerPending)(val);
    if (device === 'mobile') {
      const blob = val && val !== 'remove' ? val.file : null;
      iframeRef.current?.contentWindow?.postMessage({ type: 'preview-asset', kind, blob }, ORIGIN);
    }
  };
  const pickAsset = (kind: 'logo' | 'banner', f: File) => {
    if (f.size > 2 * 1024 * 1024) { toast.error('Image is too large — max 2 MB.'); return; }
    const url = URL.createObjectURL(f); madeUrls.current.push(url);
    setPending(kind, { file: f, url });
  };
  const clearAsset = (kind: 'logo' | 'banner') => {
    // Removing a SAVED image stages a deletion; discarding an only-staged
    // upload just reverts to nothing (no deletion to commit).
    const savedHas = kind === 'logo' ? brand?.has_logo : brand?.has_banner;
    setPending(kind, savedHas ? 'remove' : null);
  };

  const saveTheme = async () => {
    if (!brand) return;
    setSaving(true);
    try {
      // 1. Commit staged logo/hero on their own (multipart) endpoints.
      for (const kind of ['logo', 'banner'] as const) {
        const p = kind === 'logo' ? logoPending : bannerPending;
        if (!p) continue;
        if (p === 'remove') {
          const res = await apiFetch(`/applications/companies/${companyId}/${kind}`, { method: 'DELETE' });
          if (!res.ok) throw new Error(kind === 'logo' ? 'Could not remove the logo.' : 'Could not remove the hero photo.');
        } else {
          const fd = new FormData(); fd.append('file', p.file);
          const res = await apiFetch(`/applications/companies/${companyId}/${kind}`, { method: 'POST', body: fd });
          if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || 'Image upload failed.'); }
        }
      }
      // 2. Commit the colours.
      await apiJSON(`/applications/companies/${companyId}/brand`, {
        method: 'PATCH',
        body: { brand_color: brand.brand_color, surface_color: brand.surface_color,
                header_color: brand.header_color, bg_color: brand.bg_color,
                heading_color: brand.heading_color },
      });
      // 3. Fold now-saved images into the originals + clear staging (keep the
      // staged object URL as the new live image — it's revoked on unmount).
      if (logoPending) {
        setLogoUrl(logoPending === 'remove' ? undefined : logoPending.url);
        setBrand((b) => (b ? { ...b, has_logo: logoPending !== 'remove' } : b));
        setLogoPending(null);
      }
      if (bannerPending) {
        setBannerUrl(bannerPending === 'remove' ? undefined : bannerPending.url);
        setBrand((b) => (b ? { ...b, has_banner: bannerPending !== 'remove' } : b));
        setBannerPending(null);
      }
      toast.success('Saved');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    let dead = false;
    const urls: string[] = [];
    (async () => {
      try {
        const r = await apiJSON<{ items: RawCompany[] }>('/applications/companies');
        const c = r.items.find((x) => String(x.id) === companyId);
        if (dead) return;
        if (!c) { setErr('Company not found'); return; }
        setBrand({
          name: c.display_name || c.code, brand_color: c.brand_color,
          website: c.website, phone: c.phone, mc_number: c.mc_number, usdot_number: c.usdot_number,
          has_logo: c.has_logo, headline: c.headline,
          perks: (c.perks || '').split('\n').map((s) => s.trim()).filter(Boolean),
          has_banner: c.has_banner,
          req_experience_years: c.req_experience_years, req_min_age: c.req_min_age, req_cdl_class: c.req_cdl_class,
          form_theme: c.form_theme, surface_color: c.surface_color, header_color: c.header_color, bg_color: c.bg_color,
          heading_color: c.heading_color,
          legal_address: c.legal_address, compliance_email: c.compliance_email,
          cra_name: c.cra_name, cra_address: c.cra_address, cra_phone: c.cra_phone, cra_site: c.cra_site,
        });
        const loadImg = async (path: string, set: (u: string) => void) => {
          try {
            const res = await apiFetch(path);
            if (!res.ok || dead) return;
            const u = URL.createObjectURL(await res.blob());
            urls.push(u); set(u);
          } catch { /* placeholder if it fails */ }
        };
        if (c.has_logo) loadImg(`/applications/companies/${c.id}/logo`, setLogoUrl);
        if (c.has_banner) loadImg(`/applications/companies/${c.id}/banner`, setBannerUrl);
      } catch (e) {
        if (!dead) setErr(e instanceof Error ? e.message : 'Failed to load preview');
      }
    })();
    return () => { dead = true; urls.forEach(URL.revokeObjectURL); };
  }, [companyId]);

  if (err) return <div className="p-8 text-sm text-muted-foreground">{err}</div>;
  if (!brand) return <div className="p-8 text-sm text-muted-foreground">Loading preview…</div>;

  // What the form should SHOW right now = the staged change if any, else the
  // saved image.  `present` (image exists, so offer a × ) follows the same rule.
  const logoDisp = logoPending === 'remove' ? undefined : logoPending ? logoPending.url : logoUrl;
  const bannerDisp = bannerPending === 'remove' ? undefined : bannerPending ? bannerPending.url : bannerUrl;
  const logoPresent = logoPending === 'remove' ? false : logoPending ? true : brand.has_logo;
  const bannerPresent = bannerPending === 'remove' ? false : bannerPending ? true : brand.has_banner;

  // Inside the mobile iframe: render JUST the form (no toolbar).  The narrow
  // iframe is its own viewport, so the form's mobile breakpoints fire for real.
  // Its logo/banner are driven by the parent's postMessage stream (above).
  if (inFrame) return <PublicApply preview={{ brand, logoUrl, bannerUrl }} />;

  return (
    <>
      {device === 'desktop' ? (
        <PublicApply preview={{ brand, logoUrl: logoDisp, bannerUrl: bannerDisp }} />
      ) : (
        <div className="flex min-h-screen justify-center bg-muted/40 py-8">
          {/* A real iframe at phone width → the form sees a phone viewport.
              Theme edits stream in live via postMessage (see effects above). */}
          <iframe ref={iframeRef} title="Mobile preview" src={window.location.pathname}
            className="h-[780px] w-[390px] max-w-full rounded-3xl border-4 border-foreground/20 bg-background shadow-xl" />
        </div>
      )}
      <PreviewThemeBar brand={brand} saving={saving} device={device}
        logoUrl={logoDisp} bannerUrl={bannerDisp} logoPresent={logoPresent} bannerPresent={bannerPresent}
        onColor={onColor} onDevice={setDevice}
        onAsset={pickAsset} onClearAsset={clearAsset} onSave={saveTheme}
        onAiTheme={() => setAiOpen((o) => !o)} aiOpen={aiOpen} />
      {aiOpen && (
        <Card className="fixed bottom-20 left-1/2 z-50 w-104 max-w-[calc(100vw-2rem)] -translate-x-1/2 text-foreground shadow-xl">
          <div className="flex items-center justify-between">
            <p className="flex items-center gap-1.5 text-sm font-medium">
              <Sparkles className="text-primary size-3.5" /> AI theme
            </p>
            <button type="button" onClick={() => setAiOpen(false)}
              className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap">Close</button>
          </div>
          <p className="mt-1 text-2xs text-muted-foreground">
            Palettes built from the carrier's logo with colour-theory rules.
            Click one to try it live — nothing is kept until you Save.
          </p>
          <div className="mt-2 flex gap-2">
            <input value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') generateAiThemes(); }}
              placeholder="Style wishes (optional) — e.g. bold & modern, warm, dark…"
              className="h-8 flex-1 rounded-md border border-border bg-muted px-2.5 text-xs text-foreground outline-none placeholder:text-muted-foreground/60 focus:border-ring" />
            <Button size="sm" onClick={generateAiThemes} disabled={aiBusy}>
              {aiBusy ? <><Loader2 className="animate-spin" /> Designing…</> : 'Generate'}
            </Button>
          </div>
          {aiPalettes.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1.5">
              {aiPalettes.map((p) => (
                <li key={p.name + p.accent}>
                  <button type="button" onClick={() => applyPalette(p)}
                    title="Apply to the preview"
                    className="flex w-full items-center gap-2 rounded-md border border-border px-2.5 py-2 text-left hover:border-primary/50 hover:bg-muted/50">
                    <span className="flex shrink-0 gap-1">
                      {[p.surface, p.accent, p.header, p.bg, p.heading].map((c, i) => (
                        <span key={i} className="size-5 rounded border border-border" style={{ backgroundColor: c }} />
                      ))}
                    </span>
                    <span className="truncate text-xs text-foreground">{p.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </>
  );
}
