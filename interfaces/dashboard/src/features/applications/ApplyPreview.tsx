// Recruiter-only full-form preview.  Renders the REAL public apply form
// (so the preview can't drift from what applicants see) with the selected
// carrier's brand + pre-qual requirements, read-only.  Authed on the
// dashboard: it loads the brand from the recruiter company list and the
// logo/photo via authed blobs, then hands everything to PublicApply.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { Monitor, Smartphone } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { Button } from '../../components/ui/button';
import PublicApply, { type Brand } from './public/PublicApply';
import { applyPublicFormTheme } from './public/theme';

// Live theme editor that floats over the preview — adjust the carrier's
// accent + light/dark base and watch the form re-tint in real time, then
// save.  Rendered OUTSIDE the form root, so it stays on the dashboard's
// (light) theme and is always legible even over a dark-base preview.
function Swatch({ label, value, onChange }: { label: string; value: string; onChange: (c: string) => void }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {label}
      <input type="color" value={value || '#000000'} onChange={(e) => onChange(e.target.value)}
        title={label} className="h-7 w-8 cursor-pointer rounded border border-border bg-card" />
      {value && <button type="button" onClick={() => onChange('')} title="clear" className="text-2xs hover:text-danger">×</button>}
    </span>
  );
}

function PreviewThemeBar({ brand, saving, device, onColor, onBase, onDevice, onSave }: {
  brand: Brand; saving: boolean; device: 'desktop' | 'mobile';
  onColor: (key: 'brand_color' | 'header_color' | 'bg_color' | 'heading_color', c: string) => void;
  onBase: (t: string) => void; onDevice: (d: 'desktop' | 'mobile') => void; onSave: () => void;
}) {
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-card px-4 py-2.5 text-foreground shadow-lg">
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="font-medium">View</span>
        <span className="inline-flex overflow-hidden rounded-md border border-border">
          {([['desktop', Monitor], ['mobile', Smartphone]] as const).map(([d, Icon]) => (
            <button key={d} type="button" onClick={() => onDevice(d)} title={d}
              className={`px-2 py-1 ${device === d ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground hover:bg-muted/70'}`}>
              <Icon size={14} />
            </button>
          ))}
        </span>
      </span>
      <span className="h-5 w-px bg-border" aria-hidden="true" />
      <span className="text-xs font-medium text-muted-foreground">Theme</span>
      <Swatch label="Accent" value={brand.brand_color} onChange={(c) => onColor('brand_color', c)} />
      <Swatch label="Header" value={brand.header_color} onChange={(c) => onColor('header_color', c)} />
      <Swatch label="Background" value={brand.bg_color} onChange={(c) => onColor('bg_color', c)} />
      <Swatch label="Heading" value={brand.heading_color} onChange={(c) => onColor('heading_color', c)} />
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        Base
        <span className="inline-flex overflow-hidden rounded-md border border-border">
          {(['light', 'dark'] as const).map((t) => (
            <button key={t} type="button" onClick={() => onBase(t)}
              className={`px-2.5 py-1 text-xs capitalize ${brand.form_theme === t ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground hover:bg-muted/70'}`}>
              {t}
            </button>
          ))}
        </span>
      </span>
      <Button size="sm" onClick={onSave} disabled={saving}>{saving ? '…' : 'Save theme'}</Button>
    </div>
  );
}

interface RawCompany {
  id: number; code: string; display_name: string; has_logo: boolean; brand_color: string;
  website: string; phone: string; mc_number: string; usdot_number: string;
  headline: string; perks: string; has_banner: boolean;
  req_experience_years: number; req_min_age: number; req_cdl_class: string;
  form_theme: string; header_color: string; bg_color: string; heading_color: string;
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
  // Device view + iframe-context detection.  When THIS preview is rendered
  // inside the mobile iframe (window.self !== top), it shows just the form
  // (no toolbar) — and the narrow iframe gives the form a real phone
  // viewport, so its mobile breakpoints actually fire.
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop');
  const inFrame = useMemo(() => { try { return window.self !== window.top; } catch { return true; } }, []);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const brandRef = useRef<Brand | null>(brand);
  brandRef.current = brand;

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
        iframeRef.current?.contentWindow?.postMessage(
          { type: 'preview-brand', brand: brandRef.current }, ORIGIN);
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [inFrame, ORIGIN]);
  // Frame mode: receive live brand from the parent + announce readiness.
  useEffect(() => {
    if (!inFrame) return;
    const onMsg = (e: MessageEvent) => {
      if (e.origin === ORIGIN && e.data?.type === 'preview-brand' && e.data.brand) {
        setBrand(e.data.brand);
      }
    };
    window.addEventListener('message', onMsg);
    window.parent?.postMessage({ type: 'preview-ready' }, ORIGIN);
    return () => window.removeEventListener('message', onMsg);
  }, [inFrame, ORIGIN]);

  // Live edits to any colour / base re-render the form immediately; Save
  // persists the whole theme to the carrier.
  const onColor = (key: 'brand_color' | 'header_color' | 'bg_color' | 'heading_color', c: string) =>
    setBrand((b) => (b ? { ...b, [key]: c } : b));
  const setBase = (t: string) => setBrand((b) => (b ? { ...b, form_theme: t } : b));
  const saveTheme = async () => {
    if (!brand) return;
    setSaving(true);
    try {
      await apiJSON(`/applications/companies/${companyId}/brand`, {
        method: 'PATCH',
        body: { brand_color: brand.brand_color, form_theme: brand.form_theme,
                header_color: brand.header_color, bg_color: brand.bg_color,
                heading_color: brand.heading_color },
      });
      toast.success('Theme saved');
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
          form_theme: c.form_theme, header_color: c.header_color, bg_color: c.bg_color,
          heading_color: c.heading_color,
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

  // Inside the mobile iframe: render JUST the form (no toolbar).  The narrow
  // iframe is its own viewport, so the form's mobile breakpoints fire for real.
  if (inFrame) return <PublicApply preview={{ brand, logoUrl, bannerUrl }} />;

  return (
    <>
      {device === 'desktop' ? (
        <PublicApply preview={{ brand, logoUrl, bannerUrl }} />
      ) : (
        <div className="flex min-h-screen justify-center bg-muted/40 py-8">
          {/* A real iframe at phone width → the form sees a phone viewport.
              Theme edits stream in live via postMessage (see effects above). */}
          <iframe ref={iframeRef} title="Mobile preview" src={window.location.pathname}
            className="h-[780px] w-[390px] max-w-full rounded-3xl border-4 border-foreground/20 bg-background shadow-xl" />
        </div>
      )}
      <PreviewThemeBar brand={brand} saving={saving} device={device}
        onColor={onColor} onBase={setBase} onDevice={setDevice} onSave={saveTheme} />
    </>
  );
}
