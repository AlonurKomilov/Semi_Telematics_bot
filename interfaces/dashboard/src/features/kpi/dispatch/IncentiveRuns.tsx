/**
 * Incentive runs — the settlement sheet, as a screen.
 *
 * COMPENSATION surface: gated on ``can_kpi_incentives`` (route + API),
 * deliberately not ``can_kpi`` — grades are shared analytics, payout
 * amounts are money.
 *
 * The run detail grid mirrors the customer's Excel column-for-column
 * (unit, window, days, extras, gross, miles, RPM, target, KPI-%, KPI-$,
 * confirmed, reason) so the output is a document their managers already
 * know how to read.  Per-row editing goes through the grid's own
 * ``rowActions`` (the house pattern), opening small dialogs:
 * "Days & extras" edits inputs and recomputes; "Exception" overrides the
 * percent with a mandatory reason, validated server-side against the
 * run's snapshot cap.
 *
 * A finalized run renders read-only: the paid record, never re-priced.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BadgeDollarSign, Loader2, Lock, Pencil, Plus, Scale, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import DataGrid from '../../../components/datagrid';
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from '../../../components/shell';
import { Button } from '../../../components/ui/button';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { Input } from '../../../components/ui/input';
import { Tip } from '../../../components/tooltip';
import { toneClasses } from '../../../lib/status';
import type { AnyColumn } from '../../../types';
import {
  createIncentiveRun, deleteIncentiveRun, finalizeIncentiveRun, getIncentiveRun,
  listIncentiveRuns, patchIncentiveRow, setIncentiveException,
  type RunDetail, type RunRow,
} from '../api';

function usd(v: unknown): string {
  if (v == null) return '—';
  return `$${Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

const iso = (d: Date) => d.toISOString().slice(0, 10);

// The strip shows the WORKING SET; the archive collapses.  At weekly
// cadence a year mints 52 runs — an unbounded chip row would push the
// settlement grid below the fold and keep growing.
const RECENT_RUNS = 8;

/** In-cell free-text annotation (extras note, inactive reason, override
 *  reason): truncated so a long note can never inflate its column for
 *  every row; the full text lives on hover. */
function Note({ text }: { text: string }) {
  return (
    <Tip label={text}>
      <span className="ml-1 inline-block max-w-40 truncate align-bottom text-xs text-muted-foreground">
        {text}
      </span>
    </Tip>
  );
}

export default function IncentiveRuns() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [editRow, setEditRow] = useState<RunRow | null>(null);
  const [exceptRow, setExceptRow] = useState<RunRow | null>(null);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [showAllRuns, setShowAllRuns] = useState(false);

  const runsQ = useQuery({
    queryKey: ['kpi-incentive-runs'],
    queryFn: listIncentiveRuns,
  });

  // Newest first — the working period is the one being settled.
  const allRuns = [...(runsQ.data?.runs ?? [])].sort(
    (a, b) => b.period_start.localeCompare(a.period_start) || b.id - a.id,
  );
  // The selected run's chip stays visible even from the collapsed strip.
  const visibleRuns = showAllRuns ? allRuns : (() => {
    const head = allRuns.slice(0, RECENT_RUNS);
    const sel = allRuns.find((r) => r.id === selected);
    if (sel && !head.some((r) => r.id === sel.id)) head.push(sel);
    return head;
  })();

  // The detail region never renders BLANK while runs exist: with nothing
  // chosen (first load, after a discard) the newest run selects itself.
  const newestId = allRuns[0]?.id;
  useEffect(() => {
    if (selected == null && newestId != null) setSelected(newestId);
  }, [selected, newestId]);
  const detailQ = useQuery<RunDetail>({
    queryKey: ['kpi-incentive-run', selected],
    queryFn: () => getIncentiveRun(selected as number),
    enabled: selected != null,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['kpi-incentive-runs'] });
    if (selected != null) {
      qc.invalidateQueries({ queryKey: ['kpi-incentive-run', selected] });
    }
  };

  const run = detailQ.data;
  const draft = run?.status === 'draft';

  const discard = useMutation({
    mutationFn: () => deleteIncentiveRun(selected as number),
    onSuccess: () => {
      setDiscardOpen(false);
      setSelected(null);
      qc.invalidateQueries({ queryKey: ['kpi-incentive-runs'] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const finalize = useMutation({
    mutationFn: () => finalizeIncentiveRun(selected as number),
    onSuccess: () => { setFinalizeOpen(false); refresh(); },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const COLUMNS: AnyColumn[] = [
    { key: 'dispatcher_name', label: 'Dispatcher', sortable: true, filterable: true },
    { key: 'company_code', label: 'Company', sortable: true, filterable: true },
    { key: 'vehicle_unit', label: 'Unit', sortable: true },
    { key: 'window_start', label: 'Window', sortable: true,
      render: (_v, r) => (
        <span className="text-xs text-muted-foreground">
          {String(r.window_start)} – {String(r.window_end)}
        </span>
      ) },
    { key: 'total_days', label: 'Days', sortable: true,
      // active/total, with the inactive reason as visible context —
      // these two numbers move the target, so they must be readable.
      render: (_v, r) => {
        const total = Number(r.total_days); const inactive = Number(r.inactive_days);
        return (
          <span className="tabular-nums">
            {total - inactive}/{total}
            {inactive > 0 && (
              <Note text={`(${inactive} off${r.inactive_reason ? `: ${r.inactive_reason}` : ''})`} />
            )}
          </span>
        );
      } },
    { key: 'kpi_gross', label: 'Gross', sortable: true,
      render: (v, r) => (
        <span className="tabular-nums">
          {usd(v)}
          {Number(r.extras) !== 0 && (
            <Note text={`(incl. ${usd(r.extras)}${r.extras_note ? ` ${r.extras_note}` : ''})`} />
          )}
        </span>
      ) },
    { key: 'miles', label: 'Miles', sortable: true,
      render: (v) => <span className="tabular-nums">{Number(v).toLocaleString()}</span> },
    { key: 'rpm', label: 'RPM', sortable: true,
      render: (v) => <span className="tabular-nums">{v == null ? '—' : Number(v).toFixed(2)}</span> },
    { key: 'adjusted_target', label: 'Target', sortable: true,
      // NULL weekly_target = this company has no bar configured — say
      // so, never render an invented number.
      render: (v, r) => r.weekly_target == null
        ? <span className={`text-xs ${toneClasses('warn')} px-1.5 py-0.5 rounded`}>no target</span>
        : <span className="tabular-nums">{usd(v)}</span> },
    { key: 'pct', label: 'KPI %', sortable: true,
      // Every zero in a money column carries its reason — the first
      // question a dispatcher asks their manager is "why is this 0?".
      // no_target is annotated on the Target column already.
      render: (v, r) => (
        <span className="tabular-nums">
          {Number(v)}%
          {Number(v) === 0 && r.zero_reason && r.zero_reason !== 'no_target' && (
            <span className={`ml-1 text-xs ${toneClasses('warn')} px-1.5 py-0.5 rounded`}>
              {r.zero_reason === 'floor' ? 'below floor'
                : r.zero_reason === 'no_active_days' ? 'no active days'
                  : 'no tier met'}
            </span>
          )}
          {r.override_pct != null && (
            <span className={`ml-1 text-xs ${toneClasses('info')} px-1.5 py-0.5 rounded`}>
              → {Number(r.override_pct)}%
            </span>
          )}
        </span>
      ) },
    { key: 'kpi_dollars', label: 'KPI $', sortable: true,
      render: (v) => <span className="tabular-nums">{usd(v)}</span> },
    { key: 'confirmed_dollars', label: 'Confirmed', sortable: true, aggregable: true,
      render: (v, r) => (
        <span className="tabular-nums font-medium">
          {usd(v)}
          {r.override_reason ? <Note text={`(${String(r.override_reason)})`} /> : null}
        </span>
      ) },
  ];

  return (
    <div>
      <PageHeader
        icon={BadgeDollarSign}
        title={t('kpi_runs.title', 'Dispatcher Incentives')}
        description={t(
          'kpi_runs.desc',
          'Incentive runs: a period computed under the rules it was announced with. Finalized runs are the paid record.',
        )}
        actions={(
          <Button onClick={() => setNewOpen(true)}>
            <Plus size={16} className="mr-1.5" />
            {t('kpi_runs.new', 'New run')}
          </Button>
        )}
      />

      {/* ── Runs list ────────────────────────────────────────────── */}
      {runsQ.isLoading && <TableSkeleton />}
      {runsQ.error != null && (
        <ErrorState message={runsQ.error instanceof Error ? runsQ.error.message : 'Failed to load runs'} />
      )}
      {runsQ.data && runsQ.data.runs.length === 0 && (
        <EmptyState
          icon={BadgeDollarSign}
          title={t('kpi_runs.empty_title', 'No runs yet')}
          description={t(
            'kpi_runs.empty_desc',
            'Configure the incentive model and company targets in KPI configuration, then create the first run for a period.',
          )}
        />
      )}
      {allRuns.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {visibleRuns.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setSelected(r.id)}
              className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition ${
                selected === r.id
                  ? 'border-primary bg-primary/5 text-foreground'
                  : 'border-border bg-card text-foreground hover:border-ring'
              }`}
            >
              <span className="tabular-nums">{r.period_start} – {r.period_end}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                toneClasses(r.status === 'finalized' ? 'ok' : 'info')
              }`}>
                {r.status}
              </span>
            </button>
          ))}
          {allRuns.length > RECENT_RUNS && (
            <button
              type="button"
              onClick={() => setShowAllRuns((v) => !v)}
              className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground hover:border-ring transition"
            >
              {showAllRuns
                ? t('kpi_runs.show_recent', 'Show recent')
                : t('kpi_runs.show_all', 'Show all ({{n}})', { n: allRuns.length })}
            </button>
          )}
        </div>
      )}

      {/* ── Run detail: the sheet ───────────────────────────────── */}
      {selected != null && detailQ.isLoading && <TableSkeleton />}
      {run && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* Per-dispatcher payouts — the sheet's corner figure.
                Flat tint = readout; the bordered card-chip shape is
                reserved for the CLICKABLE run selectors above. */}
            <div className="flex flex-wrap gap-2">
              {Object.entries(run.payouts).map(([name, total]) => (
                <span key={name}
                  className="inline-flex items-center gap-1.5 rounded-md bg-muted px-2.5 py-1 text-sm">
                  <span className="text-muted-foreground">{name}</span>
                  <span className="font-medium tabular-nums">{usd(total)}</span>
                </span>
              ))}
            </div>
            {draft ? (
              <div className="flex items-center gap-2">
                <Button variant="ghost" onClick={() => setDiscardOpen(true)}>
                  <Trash2 size={14} className="mr-1.5" />
                  {t('kpi_runs.discard', 'Discard draft')}
                </Button>
                <Button variant="outline" onClick={() => setFinalizeOpen(true)}>
                  <Lock size={14} className="mr-1.5" />
                  {t('kpi_runs.finalize', 'Finalize run')}
                </Button>
              </div>
            ) : (
              <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded ${toneClasses('ok')}`}>
                <Lock size={12} />
                {t('kpi_runs.finalized', 'Finalized — the paid record')}
              </span>
            )}
          </div>

          <DataGrid
            tableId="kpi-incentive-run-rows"
            columns={COLUMNS}
            // Clicking a row IS the edit gesture on a draft — rowActions
            // alone is right-click-only (the audit's top finding: managers
            // migrating from Excel won't guess a context menu exists).
            onRowClick={draft
              ? (row) => setEditRow(row as unknown as RunRow)
              : undefined}
            data={run.rows as unknown as Record<string, unknown>[]}
            searchKey={['dispatcher_name', 'vehicle_unit', 'company_code']}
            searchPlaceholder={t('kpi_runs.search', 'Search unit, dispatcher…')}
            rowActions={(row) => draft ? [
              {
                key: 'edit',
                label: t('kpi_runs.edit_row', 'Days & extras…'),
                icon: <Pencil size={14} className="text-muted-foreground" />,
                onSelect: () => setEditRow(row as unknown as RunRow),
              },
              {
                key: 'exception',
                label: t('kpi_runs.exception', 'Exception…'),
                icon: <Scale size={14} className="text-muted-foreground" />,
                onSelect: () => setExceptRow(row as unknown as RunRow),
              },
            ] : []}
          />
        </div>
      )}

      <NewRunDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={(r) => { setNewOpen(false); setSelected(r.id); refresh(); }}
      />
      {editRow && selected != null && (
        <EditRowDialog
          runId={selected} row={editRow}
          onClose={() => setEditRow(null)}
          onSaved={() => { setEditRow(null); refresh(); }}
        />
      )}
      {exceptRow && selected != null && (
        <ExceptionDialog
          runId={selected} row={exceptRow}
          onClose={() => setExceptRow(null)}
          onSaved={() => { setExceptRow(null); refresh(); }}
        />
      )}

      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('kpi_runs.discard_title', 'Discard this draft?')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_runs.discard_body',
              'The draft and any hand-entered adjustments are deleted. A new run for the same period can be created at any time; finalized runs can never be discarded.')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscardOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={() => discard.mutate()} disabled={discard.isPending}>
              {discard.isPending && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_runs.discard_confirm', 'Discard')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={finalizeOpen} onOpenChange={setFinalizeOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('kpi_runs.finalize_title', 'Finalize this run?')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_runs.finalize_body',
              'A finalized run is the paid record. Rows can no longer be adjusted or overridden, and the run is never re-priced — even if the incentive rules change later.')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFinalizeOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={() => finalize.mutate()} disabled={finalize.isPending}>
              {finalize.isPending && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_runs.finalize_confirm', 'Finalize')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── New run ───────────────────────────────────────────────────────────

function NewRunDialog({ open, onClose, onCreated }: {
  open: boolean;
  onClose: () => void;
  onCreated: (r: RunDetail) => void;
}) {
  const { t } = useTranslation();
  // Default: the last full 7 days ending yesterday — a sensible weekly
  // period the admin adjusts to their cadence.
  const end = new Date(); end.setDate(end.getDate() - 1);
  const start = new Date(end); start.setDate(start.getDate() - 6);
  const [from, setFrom] = useState(iso(start));
  const [to, setTo] = useState(iso(end));
  const [busy, setBusy] = useState(false);

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
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={create} disabled={busy || !from || !to || to < from}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.create', 'Create run')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Days & extras ─────────────────────────────────────────────────────

function EditRowDialog({ runId, row, onClose, onSaved }: {
  runId: number; row: RunRow;
  onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [inactive, setInactive] = useState(String(row.inactive_days));
  const [reason, setReason] = useState(row.inactive_reason);
  const [extras, setExtras] = useState(String(row.extras));
  const [note, setNote] = useState(row.extras_note);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await patchIncentiveRow(runId, row.id, {
        inactive_days: Number(inactive) || 0,
        inactive_reason: reason,
        extras: Number(extras) || 0,
        extras_note: note,
      });
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
            {t('kpi_runs.edit_title', 'Unit {{unit}} — days & extras', { unit: row.vehicle_unit })}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.edit_body',
            'Inactive days (repair, home time) lower the gross target — the dispatcher is not responsible for them. Extras (TONU, bonus) adjust the gross the % applies to. The row recomputes on save.')}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">
              {t('kpi_runs.inactive', 'Inactive days (of {{total}})', { total: row.total_days })}
            </span>
            <Input type="number" min={0} max={row.total_days} value={inactive}
              onChange={(e) => setInactive(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.reason', 'Reason')}</span>
            <Input value={reason} placeholder={t('kpi_runs.reason_ph', 'repair, home time…')}
              onChange={(e) => setReason(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras', 'Extras ($)')}</span>
            <Input type="number" step="0.01" value={extras}
              onChange={(e) => setExtras(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras_note', 'Extras note')}</span>
            <Input value={note} placeholder="TONU, Bonus…"
              onChange={(e) => setNote(e.target.value)} />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={save} disabled={busy}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.save_recompute', 'Save & recompute')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Exception ─────────────────────────────────────────────────────────

function ExceptionDialog({ runId, row, onClose, onSaved }: {
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
