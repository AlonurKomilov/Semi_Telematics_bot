/**
 * A manual override of a row's percent — capped by the run's own
 * snapshot, and a reason is mandatory: an unexplained override is a
 * payout nobody can audit.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../../components/ui/button';
import { Input } from '../../../../components/ui/input';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../../components/ui/dialog';
import { setIncentiveException, type RunRow } from '../../api';

export function ExceptionDialog({ runId, row, onClose, onSaved }: {
  runId: number; row: RunRow;
  onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [pct, setPct] = useState(row.override_pct != null ? String(row.override_pct) : '');
  const [reason, setReason] = useState(row.override_reason);
  const [busy, setBusy] = useState(false);

  const apply = async (clear: boolean) => {
    setBusy(true);
    try {
      await setIncentiveException(
        runId, row.id,
        clear ? null : Number(pct), clear ? '' : reason,
      );
      onSaved();
    } catch (e) {
      // The server's message carries the cap ("exceeds the policy cap of
      // 2%") — verbatim, no paraphrase.
      toast.error(e instanceof Error ? e.message : 'Could not apply');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t('kpi_runs.exc_title', 'Unit {{unit}} — exception', { unit: row.vehicle_unit })}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.exc_body',
            'Replaces the computed {{pct}}% with a chosen percent, for a named reason (home time, truck issues, recovery, new MC). Overrides above the policy cap are refused.',
            { pct: row.pct })}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.exc_pct', 'Override %')}</span>
            <Input type="number" step="0.25" value={pct}
              onChange={(e) => setPct(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.exc_reason', 'Reason (required)')}</span>
            <Input value={reason} placeholder={t('kpi_runs.exc_reason_ph', 'new MC, recovery…')}
              onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
        <DialogFooter>
          {/* mr-auto: a server WRITE must not sit in the Cancel corner
              wearing Cancel's outline shape — far left is the "act on
              existing state" slot. */}
          {row.override_pct != null && (
            <Button variant="outline" className="mr-auto" onClick={() => apply(true)} disabled={busy}>
              {t('kpi_runs.exc_clear', 'Clear override')}
            </Button>
          )}
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={() => apply(false)} disabled={busy || pct === '' || !reason.trim()}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.exc_apply', 'Apply exception')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
