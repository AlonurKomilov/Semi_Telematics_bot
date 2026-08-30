/**
 * DOT Binder — compliance PDF generator.
 *
 * Bundles per-vehicle maintenance + work orders + inspection records
 * + attestation trail into one printable PDF intended for DOT
 * auditors.  Pulled out of the Maintenance page in 2026-06 — see
 * docs/architecture/reports-hierarchy-audit.md for the rationale (it
 * was always conceptually a Report; the button on Maintenance was
 * miscategorised next to operational task editing).
 *
 * Permission-scoped to ``can_maintenance_all`` at the route level
 * (router.tsx); managers only, since the binder is sensitive
 * aggregate data managers — not drivers — are authorised to disclose.
 */
import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { FileText } from 'lucide-react';
import { toast } from 'sonner';
import { apiFetch } from '../../api/client';
import { DateRangePresets } from '../../components/shell';
import type { ReportsLayoutOutletContext } from './ReportsLayout';
import { Card } from '@/components/ui/card';


const WINDOW_OPTIONS = [
  { days: '30',  label: 'Last 30 days' },
  { days: '90',  label: 'Last 90 days' },
  { days: '180', label: 'Last 6 months' },
  { days: '365', label: 'Last 12 months (DOT default)' },
  { days: '730', label: 'Last 24 months' },
];



export default function DotBinder() {
  const [days, setDays] = useState('365');
  const [vehicle, setVehicle] = useState('');
  const [generating, setGenerating] = useState(false);

  // Outlet context comes from ReportsLayout — this page doesn't push
  // any header actions (the Generate button lives inline with the
  // form below), but we still claim the slot on mount so leftover
  // actions from a previous tab don't bleed through.
  const outletCtx = useOutletContext<ReportsLayoutOutletContext | undefined>();
  useEffect(() => {
    if (!outletCtx) return;
    outletCtx.setActions(null);
    return () => outletCtx.setActions(null);
  }, [outletCtx]);

  const generate = async () => {
    setGenerating(true);
    try {
      const params = new URLSearchParams({ days });
      if (vehicle.trim()) params.set('vehicle', vehicle.trim());
      // Canonical URL under /api/reports/*; the old
      // /api/maintenance/dot-binder is still served as the underlying
      // handler so any pinned external caller keeps working for a
      // release cycle.
      const res = await apiFetch(
        `/reports/dot-binder?${params.toString()}`,
        {},
        // PDF generation on a 50-truck fleet can legitimately take
        // 10-20 s — override the default 30s timeout so the request
        // doesn't get aborted under the user.
        90_000,
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        toast.error(typeof err.detail === 'string' ? err.detail : 'Binder generation failed');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const datePart = new Date().toISOString().slice(0, 10);
      const vehiclePart = vehicle.trim() ? `-${vehicle.trim()}` : '';
      a.href = url;
      a.download = `dot-binder-${datePart}${vehiclePart}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success('DOT binder downloaded');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Binder generation failed');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="max-w-xl">
      <Card>
        <div className="mb-5">
          <h2 className="text-base font-semibold inline-flex items-center gap-2">
            <FileText className="text-muted-foreground size-4" />
            Generate DOT Compliance Binder
          </h2>
          <p className="text-xs text-muted-foreground mt-1.5">
            One printable PDF covering maintenance tasks, work orders,
            inspection records, and the attestation trail for the
            selected window — formatted for DOT auditor review.
          </p>
        </div>

        <div className="space-y-4 mb-5">
          {/* div, not label: the picker is buttons, not a labelable input */}
          <div className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              Coverage window
            </span>
            <DateRangePresets
              value={Number(days)}
              onChange={(d) => setDays(String(d))}
              options={WINDOW_OPTIONS.map((o) => ({ label: o.label, days: Number(o.days) }))}
              maxDays={730}
              disabled={generating}
            />
          </div>

          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              Single vehicle (optional)
            </span>
            <input
              type="text"
              value={vehicle}
              onChange={(e) => setVehicle(e.target.value)}
              disabled={generating}
              placeholder="Leave empty for the whole fleet"
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
            <p className="text-2xs text-muted-foreground mt-1">
              Useful when only one truck is being audited or sold to
              another carrier.
            </p>
          </label>
        </div>

        <button
          type="button"
          onClick={generate}
          disabled={generating}
          className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary-hover disabled:opacity-50 rounded-lg text-sm font-medium text-primary-foreground transition min-h-tap"
        >
          <FileText className="size-3.5" />
          {generating ? 'Generating PDF…' : 'Generate PDF'}
        </button>

        {generating && (
          <p className="text-xs text-muted-foreground mt-3 text-center">
            Compiling per-vehicle records — this may take up to 30
            seconds for large fleets.
          </p>
        )}
      </Card>
    </div>
  );
}
