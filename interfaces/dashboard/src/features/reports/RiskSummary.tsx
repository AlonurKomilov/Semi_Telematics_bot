import { useSearchParams } from 'react-router-dom';
import { useState, useEffect, useRef } from 'react';
import { apiFetch, apiJSON } from '../../api/client';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

const AUDIENCES = [
  { value: 'insurance', label: 'Insurance Underwriter' },
  { value: 'owner',     label: 'Fleet Owner' },
  { value: 'broker',    label: 'Broker of Record' },
  { value: 'auditor',   label: 'DOT / Compliance' },
  { value: 'payroll',   label: 'Safety-Pay Evidence' },
];

const SUBJECT_TYPE_ITEMS = [
  { value: 'vehicle', label: 'Vehicle' },
  { value: 'driver',  label: 'Driver' },
];

// ── Simple autocomplete combobox ──────────────────────────────────

interface ComboboxProps {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}

function Combobox({ value, onChange, options, placeholder }: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const filtered = options.filter((o) =>
    value.length === 0 ||
    o.label.toLowerCase().includes(value.toLowerCase()) ||
    o.value.toLowerCase().includes(value.toLowerCase()),
  ).slice(0, 20);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <input
        className="w-full border rounded px-3 py-2 bg-background"
        value={value}
        placeholder={placeholder}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto rounded border border-border bg-background shadow-lg text-sm">
          {filtered.map((o) => (
            <li
              key={o.value}
              className="cursor-pointer px-3 py-2 hover:bg-accent"
              onMouseDown={() => { onChange(o.value); setOpen(false); }}
            >
              <span className="font-medium">{o.value}</span>
              {o.label !== o.value && (
                <span className="ml-2 text-xs text-muted-foreground">{o.label}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────

export default function RiskSummary() {
  const [params] = useSearchParams();
  const [subjectType, setSubjectType] = useState<'driver' | 'vehicle'>(
    (params.get('subject_type') as 'driver' | 'vehicle') || 'vehicle',
  );
  const [subjectId, setSubjectId] = useState<string>(params.get('subject_id') || '');
  const [audience, setAudience] = useState<string>(params.get('audience') || 'owner');
  const [days, setDays] = useState<number>(Number(params.get('days') || 30));
  const [company, setCompany] = useState<string>(params.get('company') || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Autocomplete options
  const [vehicleOptions, setVehicleOptions] = useState<{ value: string; label: string }[]>([]);

  useEffect(() => {
    // Fetch vehicles for autocomplete
    apiJSON<{ vehicles: { name: string; company?: string }[] }>('/vehicles/overview')
      .then((r) => {
        const seen = new Set<string>();
        const opts = (r.vehicles || [])
          .map((v) => {
            const value = (v.name || '').trim();
            return {
              value,
              label: v.company ? `${value} (${v.company})` : value,
            };
          })
          .filter((o) => {
            const key = o.value.toLowerCase();
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
          });
        setVehicleOptions(opts);
      })
      .catch(() => { /* non-fatal */ });
  }, []);

  // Clear subject when type changes
  const handleTypeChange = (t: 'driver' | 'vehicle') => {
    setSubjectType(t);
    setSubjectId('');
  };

  async function download(fmt: 'pdf' | 'csv') {
    setError('');
    if (!subjectId.trim()) {
      setError('Subject ID is required.');
      return;
    }
    setBusy(true);
    try {
      const qp = new URLSearchParams({
        subject_type: subjectType,
        subject_id: subjectId.trim(),
        audience,
        fmt,
        days: String(days),
      });
      if (company.trim()) qp.set('company', company.trim());
      const res = await apiFetch(`/reports/risk-summary?${qp}`, {}, 90_000);
      if (!res.ok) {
        const contentType = res.headers.get('content-type') || '';
        const txt = await res.text().catch(() => '');
        if (res.status === 504 || txt.includes('Gateway time-out')) {
          throw new Error('The report timed out on the server. Try CSV export or a shorter window such as 7 days.');
        }
        if (contentType.includes('text/html')) {
          throw new Error(`Report request failed (${res.status}).`);
        }
        throw new Error(txt || `Request failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `risk_summary_${audience}_${subjectType}_${subjectId.trim()}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError('The report took too long to generate. Try CSV export or a shorter window such as 7 days.');
      } else {
        setError(e instanceof Error ? e.message : 'Export failed');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">Subject type</span>
          <Select
            value={subjectType}
            onValueChange={(v) => handleTypeChange(v as 'driver' | 'vehicle')}
            items={SUBJECT_TYPE_ITEMS}
          >
            <SelectTrigger className="w-full" aria-label="Subject type"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SUBJECT_TYPE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">
            {subjectType === 'vehicle' ? 'Vehicle (truck #)' : 'Driver ID'}
          </span>
          {subjectType === 'vehicle' ? (
            <>
              <Combobox
                value={subjectId}
                onChange={setSubjectId}
                options={vehicleOptions}
                placeholder="Type truck # or pick from list…"
              />
              {vehicleOptions.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  {vehicleOptions.length} vehicle{vehicleOptions.length !== 1 ? 's' : ''} in account
                </span>
              )}
            </>
          ) : (
            <>
              <input
                className="w-full border rounded px-3 py-2 bg-background"
                value={subjectId}
                onChange={(e) => setSubjectId(e.target.value)}
                placeholder="Enter the Samsara driver ID"
              />
              <span className="text-xs text-muted-foreground">
                Driver reports use the Samsara driver ID.
              </span>
            </>
          )}
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">Audience</span>
          <Select value={audience} onValueChange={(v) => setAudience(v)} items={AUDIENCES}>
            <SelectTrigger className="w-full" aria-label="Audience"><SelectValue /></SelectTrigger>
            <SelectContent>
              {AUDIENCES.map((a) => <SelectItem key={a.value} value={a.value}>{a.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">Window (days)</span>
          <input
            type="number" min={1} max={90}
            className="border rounded px-3 py-2 bg-background"
            value={days}
            onChange={(e) => setDays(Math.max(1, Math.min(90, Number(e.target.value) || 30)))}
          />
        </label>

        <label className="flex flex-col gap-1 md:col-span-2">
          <span className="text-sm font-medium">Company name for report (optional)</span>
          <input
            className="border rounded px-3 py-2 bg-background"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            placeholder="e.g. ACME Logistics"
          />
        </label>
      </div>

      {error && (
        <div className="text-sm text-destructive mb-3" role="alert">{error}</div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => download('pdf')}
          className="rounded bg-primary text-primary-foreground px-4 py-2 disabled:opacity-50"
        >
          {busy ? 'Generating…' : 'Download PDF'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => download('csv')}
          className="rounded border px-4 py-2 disabled:opacity-50"
        >
          Download CSV
        </button>
      </div>

      <div className="mt-8 text-xs text-muted-foreground">
        <p className="mb-2">
          <strong>Audiences:</strong> Insurance / Owner / Broker / Auditor / Safety-Pay.
          Driver-identifiable info is automatically redacted in broker packets.
        </p>
        <p>
          Every export is recorded in the audit log with the requesting user and
          chosen audience.
        </p>
      </div>
    </div>
  );
}

