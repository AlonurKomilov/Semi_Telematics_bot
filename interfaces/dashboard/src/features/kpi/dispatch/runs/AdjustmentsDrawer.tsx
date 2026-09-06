/**
 * Every hand-touched row of a run, with WHO touched it — the
 * governance surface for "who moved this payout".  Reverting returns a
 * row to exactly what the engine computed.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Undo2 } from 'lucide-react';
import { toast } from '../../../../lib/toast';
import { Button } from '../../../../components/ui/button';
import {
  Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle,
} from '../../../../components/ui/sheet';
import {
  patchIncentiveRow, setIncentiveException,
  type RunDetail, type RunRow,
} from '../../api';
import { isHandAdjusted } from '../explain';
import { usd } from './format';

// ── Adjustments drawer — every hand-touched row, WHO and WHEN (the
// attribution migration 198 stores), with one-click revert on drafts.
export function AdjustmentsDrawer({ open, run, draft, onClose, onChanged }: {
  open: boolean; run: RunDetail; draft: boolean;
  onClose: () => void; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<number | null>(null);
  const rows = run.rows.filter(isHandAdjusted);
  const who = (id: number | null) =>
    id == null ? t('kpi_runs.adj_unknown', '—') : (run.user_names[String(id)] ?? `user ${id}`);
  const when = (iso: string) => iso ? iso.slice(0, 16).replace('T', ' ') : '';

  const revert = async (r: RunRow) => {
    setBusy(r.id);
    try {
      if (r.override_pct != null) await setIncentiveException(run.id, r.id, null, '');
      await patchIncentiveRow(run.id, r.id, {
        // Generation marks nothing — reverting means every day counts
        // again and the extras zero out.
        inactive_dates: [], extras: 0, extras_note: '',
      });
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not revert');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{t('kpi_runs.adj_title', 'Adjustments — {{a}} – {{b}}',
            { a: run.period_start, b: run.period_end })}</SheetTitle>
        </SheetHeader>
        <SheetBody label={t('kpi_runs.adj_title_short', 'Run adjustments')} className="p-4 space-y-3">
          <p className="text-xs text-muted-foreground max-w-prose">
            {t('kpi_runs.adj_desc',
              'Every hand adjustment on this run, with who made it. Reverting returns a row to its computed values.')}
          </p>
          {rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {t('kpi_runs.adj_empty', 'No adjustments — every row pays exactly what the engine computed.')}
            </p>
          )}
          <ul className="divide-y divide-border border-t border-border">
            {rows.map((r) => (
              <li key={r.id} className="py-2.5 space-y-1 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{r.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned')}
                    <span className="ml-1.5 text-xs text-muted-foreground">{r.company_code} · {r.dispatcher_name}</span>
                  </span>
                  {draft && (
                    <Button size="sm" variant="ghost" disabled={busy === r.id}
                      onClick={() => revert(r)}>
                      {busy === r.id
                        ? <Loader2 className="animate-spin" />
                        : <Undo2 className="mr-1" />}
                      {t('kpi_runs.adj_revert', 'Revert')}
                    </Button>
                  )}
                </div>
                <ul className="text-xs text-muted-foreground space-y-0.5">
                  {r.inactive_days > 0 && (
                    <li>
                      {r.inactive_days === 1
                        ? t('kpi_runs.adj_days_one', '1 inactive day')
                        : t('kpi_runs.adj_days', '{{n}} inactive days', { n: r.inactive_days })}
                      {r.inactive_dates.length > 0 && (
                        <> — {r.inactive_dates.map((m) => `${m.date.slice(5)}${m.reason ? ` (${m.reason})` : ''}`).join(', ')}</>
                      )}
                      {r.inactive_reason && r.inactive_dates.length === 0 && <> — {r.inactive_reason}</>}
                    </li>
                  )}
                  {Number(r.extras) !== 0 && (
                    <li>{t('kpi_runs.adj_extras', 'Extras {{v}}', { v: usd(r.extras) })}{r.extras_note ? ` — ${r.extras_note}` : ''}</li>
                  )}
                  {r.override_pct != null && (
                    <li>{t('kpi_runs.adj_override', 'Exception {{p}}% — {{reason}} (by {{who}})',
                      { p: r.override_pct, reason: r.override_reason, who: who(r.confirmed_by) })}</li>
                  )}
                  {(r.adjusted_by != null || r.adjusted_at) && (
                    <li className="text-muted-foreground/70">
                      {t('kpi_runs.adj_by', 'last input edit: {{who}} · {{when}}',
                        { who: who(r.adjusted_by), when: when(r.adjusted_at) })}
                    </li>
                  )}
                </ul>
              </li>
            ))}
          </ul>
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
