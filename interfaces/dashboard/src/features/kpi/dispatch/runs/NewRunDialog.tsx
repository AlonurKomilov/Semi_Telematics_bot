/**
 * Create a run for a period — with the overlap guard, because two runs
 * covering one week would pay the same loads twice.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../../components/ui/button';
import { toneClasses } from '../../../../lib/status';
import { Input } from '../../../../components/ui/input';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../../components/ui/dialog';
import {
  createIncentiveRun, previewIncentiveRun,
  type RunDetail, type RunSummary,
} from '../../api';

/** A Date as the wire's calendar day. */
const iso = (d: Date) => d.toISOString().slice(0, 10);
import { usd } from './format';

export function NewRunDialog({ open, onClose, onCreated, existing }: {
  open: boolean;
  onClose: () => void;
  onCreated: (r: RunDetail) => void;
  /** Existing runs, newest first — drives the default period and the
   *  overlap warning. */
  existing: RunSummary[];
}) {
  const { t } = useTranslation();
  // Default: the next UNCOVERED 7-day period — the day after the newest
  // run, when that period has already happened; otherwise the last full
  // 7 days ending yesterday.  Defaulting into a period that already has
  // a run invites a duplicate; the warning below catches the rest.
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  let start = new Date(yesterday); start.setDate(start.getDate() - 6);
  const latest = existing[0];
  if (latest) {
    const next = new Date(`${latest.period_end.slice(0, 10)}T00:00:00`);
    next.setDate(next.getDate() + 1);
    if (iso(next) <= iso(yesterday)) start = next;
  }
  const defEnd = new Date(start); defEnd.setDate(defEnd.getDate() + 6);
  const [from, setFrom] = useState(iso(start));
  const [to, setTo] = useState(
    iso(defEnd) > iso(yesterday) ? iso(yesterday) : iso(defEnd));
  const [busy, setBusy] = useState(false);

  const badOrder = !!from && !!to && to < from;
  const overlap = !badOrder && from && to
    ? existing.find((r) => from <= r.period_end && to >= r.period_start)
    : undefined;
  const previewQ = useQuery({
    queryKey: ['kpi-run-preview', from, to],
    queryFn: () => previewIncentiveRun(from, to),
    enabled: open && !!from && !!to && !badOrder,
  });

  const create = async () => {
    setBusy(true);
    try {
      onCreated(await createIncentiveRun(from, to));
    } catch (e) {
      // 422s carry the setup message ("Incentives are not configured…").
      toast.error(e instanceof Error ? e.message : 'Could not create the run');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('kpi_runs.new_title', 'New incentive run')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.new_body',
            'Rows are generated from the period’s loads and computed under the CURRENT rules, which the run keeps as its snapshot.')}
          {' '}
          <Link to="/kpi/dispatch/configuration" className="text-primary underline underline-offset-4 hover:no-underline">
            {t('kpi_runs.view_rules', 'View current rules')}
          </Link>
        </p>
        <div className="flex items-center gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.from', 'From')}</span>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.to', 'To')}</span>
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
        {previewQ.data && (
          /* Scope BEFORE commit: a mis-typed range shows up as a wrong
             truck count here, not as a generated wrong run. */
          <p className="text-xs text-muted-foreground">
            {t('kpi_runs.preview_line',
              'This period holds {{loads}} loads · {{trucks}} trucks · {{dispatchers}} dispatchers · {{gross}} gross.',
              { loads: previewQ.data.loads, trucks: previewQ.data.trucks,
                dispatchers: previewQ.data.dispatchers,
                gross: usd(previewQ.data.gross) })}
          </p>
        )}
        {/* A disabled Create must say WHY; an overlapping period must
            say so BEFORE the duplicate exists. */}
        {badOrder && (
          <p className="text-sm text-danger">
            {t('kpi_runs.bad_order', 'The end date must come after the start date.')}
          </p>
        )}
        {overlap && (
          <p className={`inline-flex items-center text-xs ${toneClasses('warn')} px-2 py-1 rounded-md`}>
            {t('kpi_runs.overlap_warn',
              'This period overlaps the {{status}} run {{a}} – {{b}} — creating it makes a second run over the same loads.',
              { status: overlap.status, a: overlap.period_start, b: overlap.period_end })}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={create} disabled={busy || !from || !to || badOrder}>
            {busy && <Loader2 className="animate-spin mr-1.5" />}
            {t('kpi_runs.create', 'Create run')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
