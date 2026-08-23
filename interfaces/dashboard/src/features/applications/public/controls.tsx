// Public driver-application form — field controls.
//
// Text inputs / textareas / uploads are hand-rolled so the apply.<apex>
// bundle stays light, but every colour/space/radius/icon follows the
// design system: semantic tokens, lucide icons, 4px scale.  The one
// exception is SelectInput, which composes the shared Select primitive so
// its dropdown is themed + drops below the field (a native <select> opened
// an OS-styled list that ignored the form theme — see the State picker).
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { UploadCloud, Camera, X, FileText, Check } from 'lucide-react';
import { formatPhone, formatSsn } from './lib';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../../components/ui/select';

const inputCls =
  'w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground ' +
  'placeholder:text-muted-foreground/60 outline-none transition-colors ' +
  'focus:border-ring focus:ring-2 focus:ring-ring/40 disabled:opacity-50';
const errCls = 'border-destructive focus:border-destructive focus:ring-destructive/30';

// ── Field wrapper (label + hint + error) ────────────────────────────
export function Field({ label, required, hint, error, children, className = '' }: {
  label?: string; required?: boolean; hint?: string; error?: string;
  children: ReactNode; className?: string;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label className="text-xs font-medium text-foreground">
          {label}{required && <span className="text-destructive"> *</span>}
          {hint && <span className="ml-1.5 font-normal text-muted-foreground">{hint}</span>}
        </label>
      )}
      {children}
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}

// ── Text input (with phone / ssn auto-format) ───────────────────────
export function TextInput({ value = '', onChange, type = 'text', placeholder, mono, format, error, ...rest }: {
  value?: string; onChange: (v: string) => void; type?: string; placeholder?: string;
  mono?: boolean; format?: 'phone' | 'ssn'; error?: boolean; disabled?: boolean;
}) {
  const handle = (raw: string) => {
    if (format === 'phone') return onChange(formatPhone(raw));
    if (format === 'ssn') return onChange(formatSsn(raw));
    onChange(raw);
  };
  return (
    <input
      type={type} value={value} placeholder={placeholder}
      onChange={(e) => handle(e.target.value)}
      className={`${inputCls} ${mono ? 'font-mono' : ''} ${error ? errCls : ''}`}
      {...rest}
    />
  );
}

export function TextArea({ value = '', onChange, placeholder, rows = 3, error }: {
  value?: string; onChange: (v: string) => void; placeholder?: string; rows?: number; error?: boolean;
}) {
  return (
    <textarea
      value={value} placeholder={placeholder} rows={rows}
      onChange={(e) => onChange(e.target.value)}
      className={`${inputCls} resize-y ${error ? errCls : ''}`}
    />
  );
}

type Opt = string | { value: string; label: string };
const optVal = (o: Opt) => (typeof o === 'string' ? o : o.value);
const optLbl = (o: Opt) => (typeof o === 'string' ? o : o.label);

export function SelectInput({ value = '', onChange, options, placeholder = 'Select…', mono, error }: {
  value?: string; onChange: (v: string) => void; options: Opt[];
  placeholder?: string; mono?: boolean; error?: boolean;
}) {
  const items = options.map((o) => ({ value: optVal(o), label: optLbl(o) }));
  // Trigger metrics match the hand-rolled inputs above (rounded-md,
  // bg-card, px-3, ~h-10) so a Select row sits level with text fields.
  return (
    <Select value={value} onValueChange={onChange} items={items}>
      <SelectTrigger
        className={`h-10 w-full rounded-md border-border bg-card px-3 ${mono ? 'font-mono' : ''} ${
          error ? 'border-destructive focus-visible:border-destructive focus-visible:ring-destructive/30' : ''
        }`}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {items.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

// ── Radio group ─────────────────────────────────────────────────────
export function Choices({ value, onChange, options, name }: {
  value?: string; onChange: (v: string) => void; options: Opt[]; name?: string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const v = optVal(o), on = value === v;
        return (
          <label key={v}
            className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors ${
              on ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:bg-muted'
            }`}>
            <input type="radio" name={name} checked={on} onChange={() => onChange(v)} className="sr-only" />
            <span className={`flex size-4 items-center justify-center rounded-full border ${on ? 'border-primary' : 'border-border'}`}>
              {on && <span className="size-2 rounded-full bg-primary" />}
            </span>
            {optLbl(o)}
          </label>
        );
      })}
    </div>
  );
}

// ── Single checkbox row ─────────────────────────────────────────────
export function Check_({ checked, onChange, children }: {
  checked: boolean; onChange: (b: boolean) => void; children: ReactNode;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm hover:bg-muted/50">
      <span className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded border ${
        checked ? 'border-primary bg-primary text-primary-foreground' : 'border-border'
      }`}>
        {checked && <Check size={12} />}
      </span>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="sr-only" />
      <span className="text-foreground">{children}</span>
    </label>
  );
}

// ── Chip toggle (equipment / regions / endorsements) ────────────────
export function Chip({ code, label, checked, onChange }: {
  code?: string; label: string; checked: boolean; onChange: (b: boolean) => void;
}) {
  return (
    <label className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors ${
      checked ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:bg-muted'
    }`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="sr-only" />
      {code && <span className={`rounded px-1.5 py-0.5 text-2xs font-semibold ${checked ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}>{code}</span>}
      {label}
    </label>
  );
}

// ── Document upload ─────────────────────────────────────────────────
// Keeps the raw File (sent as a multipart part) plus a preview dataUrl
// for images.  Client-side size guard mirrors the server's 8 MB cap.
export interface DocValue { file: File; name: string; size: number; type: string; dataUrl?: string; }

export function DocUpload({ label, sub, required, accept = 'image/*,application/pdf', maxMB = 8, value, onChange, error }: {
  label: string; sub?: string; required?: boolean; accept?: string; maxMB?: number;
  value?: DocValue | null; onChange: (v: DocValue | null) => void; error?: string;
}) {
  const [localErr, setLocalErr] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const allowsImage = accept.includes('image');

  const accept_ = (f: File | undefined) => {
    if (!f) return;
    if (f.size > maxMB * 1024 * 1024) { setLocalErr(`File exceeds ${maxMB} MB`); return; }
    setLocalErr('');
    const base: DocValue = { file: f, name: f.name, size: f.size, type: f.type };
    if (f.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = () => onChange({ ...base, dataUrl: String(reader.result) });
      reader.readAsDataURL(f);
    } else {
      onChange(base);
    }
  };

  const shownErr = error || localErr;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-foreground">
        {label}{required && <span className="text-destructive"> *</span>}
        {sub && <span className="ml-1.5 font-normal text-muted-foreground">{sub}</span>}
      </span>
      {value ? (
        <div className="flex items-center gap-3 rounded-md border border-border bg-card p-2.5">
          {value.dataUrl
            ? <img src={value.dataUrl} alt="" className="size-12 rounded object-cover" />
            : <span className="flex size-12 items-center justify-center rounded bg-muted"><FileText size={20} className="text-muted-foreground" /></span>}
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-foreground">{value.name}</p>
            <p className="text-xs text-muted-foreground">{(value.size / 1024).toFixed(0)} KB</p>
          </div>
          <button type="button" onClick={() => onChange(null)}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"><X size={16} /></button>
        </div>
      ) : (
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); accept_(e.dataTransfer.files?.[0]); }}
          className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-md border border-dashed p-4 text-center transition-colors hover:bg-muted/50 ${
            shownErr ? 'border-destructive' : 'border-border'
          }`}>
          <UploadCloud size={20} className="text-muted-foreground" />
          <span className="text-sm text-foreground">Tap to upload or drag a file</span>
          <span className="text-xs text-muted-foreground">JPG, PNG or PDF · up to {maxMB} MB</span>
          {allowsImage && (
            <button type="button" onClick={(e) => { e.stopPropagation(); camRef.current?.click(); }}
              className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-foreground hover:bg-muted min-h-tap">
              <Camera size={14} /> Take photo
            </button>
          )}
        </div>
      )}
      <input ref={fileRef} type="file" accept={accept} className="hidden" onChange={(e) => accept_(e.target.files?.[0])} />
      {allowsImage && <input ref={camRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => accept_(e.target.files?.[0])} />}
      {shownErr && <span className="text-xs text-destructive">{shownErr}</span>}
    </div>
  );
}

// ── Signature block (type or draw) ──────────────────────────────────
export function SignatureBlock({ mode, name, dataUrl, fullName, onMode, onName, onDraw, error }: {
  mode: 'type' | 'draw'; name?: string; dataUrl?: string; fullName: string;
  onMode: (m: 'type' | 'draw') => void; onName: (v: string) => void;
  onDraw: (url: string | null) => void; error?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);

  // Size the buffer to the displayed size × DPR.  Sizing a canvas clears
  // it, so only resize when the dimensions actually change.  Returns a
  // ready-to-draw context (no transform — we draw in buffer coords).
  const prep = useCallback((): CanvasRenderingContext2D | null => {
    const c = canvasRef.current;
    if (!c) return null;
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext('2d');
    if (!ctx) return null;
    ctx.lineWidth = 2.2 * dpr;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#1e293b';
    return ctx;
  }, []);

  const point = (e: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    return { x: (e.clientX - rect.left) * dpr, y: (e.clientY - rect.top) * dpr };
  };

  // On entering draw mode, size the canvas and restore any prior stroke.
  useEffect(() => {
    if (mode !== 'draw') return;
    const ctx = prep();
    if (ctx && dataUrl) {
      const img = new Image();
      img.onload = () => ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      img.src = dataUrl;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const down = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    prep();                       // guarantee the buffer is sized before drawing
    drawing.current = true;
    last.current = point(e);
  };
  const move = (e: React.PointerEvent) => {
    if (!drawing.current || !last.current) return;
    const ctx = canvasRef.current!.getContext('2d');
    if (!ctx) return;
    const p = point(e);
    ctx.beginPath();
    ctx.moveTo(last.current.x, last.current.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last.current = p;
  };
  const up = () => {
    if (!drawing.current) return;
    drawing.current = false;
    last.current = null;
    onDraw(canvasRef.current!.toDataURL('image/png'));
  };
  const clear = () => {
    const c = canvasRef.current!;
    c.getContext('2d')!.clearRect(0, 0, c.width, c.height);
    onDraw(null);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="inline-flex w-fit rounded-md border border-border p-0.5">
        {(['type', 'draw'] as const).map((m) => (
          <button key={m} type="button" onClick={() => onMode(m)}
            className={`rounded px-3 py-1 text-sm capitalize ${mode === m ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'} min-h-tap`}>
            {m === 'type' ? 'Type' : 'Draw'}
          </button>
        ))}
      </div>

      {mode === 'type' ? (
        <Field label="Type your full legal name" required
          hint={fullName ? `must match "${fullName}"` : undefined} error={error}>
          <TextInput value={name} onChange={onName} placeholder={fullName || 'First Last'} error={!!error} />
          {name && (
            <div className="mt-1 rounded-md border border-border bg-muted/40 px-4 py-3 text-center">
              <span className="text-2xl text-foreground" style={{ fontFamily: 'Georgia, serif', fontStyle: 'italic' }}>{name}</span>
            </div>
          )}
        </Field>
      ) : (
        <div className="flex flex-col gap-1.5">
          <canvas ref={canvasRef}
            onPointerDown={down} onPointerMove={move} onPointerUp={up}
            onPointerLeave={up} onPointerCancel={up}
            className="h-40 w-full touch-none cursor-crosshair rounded-md border border-border bg-card" />
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">Sign above with your mouse or finger.</span>
            <button type="button" onClick={clear} className="text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap">Clear</button>
          </div>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      )}
    </div>
  );
}
