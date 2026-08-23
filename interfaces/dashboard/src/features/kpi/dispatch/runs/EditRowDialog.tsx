/**
 * A row's EXTRAS — the money the loads don't carry (TONU, a bonus).
 * Days are the board's job, so this dialog only reports them and
 * offers the trip.
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
import { patchIncentiveRow, type RunRow } from '../../api';
import { usd } from './format';

export function EditRowDialog({ runId, row, onClose, onSaved, onGoBoard }: {
  runId: number; row: RunRow;
  onClose: () => void; onSaved: () => void;
  /** Close + switch to the board — the ONLY day editor. */
  onGoBoard: () => void;
}) {
  const { t } = useTranslation();
  const [extras, setExtras] = useState(String(row.extras));
  const [note, setNote] = useState(row.extras_note);
  const [busy, setBusy] = useState(false);
  const extrasChanged = Number(extras || 0) !== Number(row.extras);

  const save = async () => {
    setBusy(true);
    try {
      // Extras only — days are edited per-day on the board, the one
      // editor for that fact (a typed count here couldn't say WHICH
      // days, and its free-text reason diverged from the board's set).
      const patch: Parameters<typeof patchIncentiveRow>[2] = {};
      if (extrasChanged) patch.extras = Number(extras) || 0;
      if (note !== row.extras_note) patch.extras_note = note;
      if (Object.keys(patch).length > 0) await patchIncentiveRow(runId, row.id, patch);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t('kpi_runs.edit_title2', 'Unit {{unit}} — extras', { unit: row.vehicle_unit })}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.edit_body2',
            'Extras (TONU, bonus) adjust the gross revenue the % applies to; the row recomputes on save.')}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras', 'Extras ($)')}</span>
            <Input type="number" step="0.01" value={extras}
              onChange={(e) => setExtras(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras_note', 'Extras note')}</span>
            <Input value={note} placeholder={t('kpi_runs.extras_note_ph', 'TONU, bonus…')}
              onChange={(e) => setNote(e.target.value)} />
          </label>
        </div>
        {/* The live result — this dialog changes pay, so it says what
            the number becomes BEFORE the commit.  The % itself is the
            server's (snapshot pricing), so the sentence promises only
            what the client truly knows. */}
        {extrasChanged && (
          <p className="text-sm tabular-nums">
            {t('kpi_runs.extras_becomes',
              'Gross the % applies to becomes {{v}} (loads {{base}} + extras {{x}}). The % re-applies on save.', {
                v: usd(Number(row.base_gross) + (Number(extras) || 0)),
                base: usd(row.base_gross),
                x: usd(Number(extras) || 0),
              })}
          </p>
        )}
        {/* Days live on the BOARD — one editor per fact. */}
        <p className="text-sm text-muted-foreground">
          {row.inactive_days === 1
            ? t('kpi_runs.edit_days_ro1', '1 inactive day ({{why}}) — edited per-day on the board.',
                { why: row.inactive_reason || t('kpi_board.inactive', 'inactive') })
            : row.inactive_days > 0
            ? t('kpi_runs.edit_days_ro', '{{n}} inactive days ({{why}}) — edited per-day on the board.',
                { n: row.inactive_days, why: row.inactive_reason || t('kpi_board.inactive', 'inactive') })
            : t('kpi_runs.edit_days_ro0', 'No inactive days — mark repair / home time / holiday per-day on the board.')}
          {' '}
          <button type="button" onClick={onGoBoard}
            className="underline underline-offset-4 hover:text-foreground transition py-0.5 -my-0.5 min-h-tap">
            {t('kpi_runs.open_board', 'Open the board')}
          </button>
        </p>
        <p className="text-xs text-muted-foreground">
          {t('kpi_runs.edit_tracked',
            'Saved as an adjustment on this run, attributed to you — revertible from Adjustments.')}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={save} disabled={busy}>
            {busy && <Loader2 className="animate-spin mr-1.5" />}
            {t('kpi_runs.save_recompute', 'Save & recompute')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
