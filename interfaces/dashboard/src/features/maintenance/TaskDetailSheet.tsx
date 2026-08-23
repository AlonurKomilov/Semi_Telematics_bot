// ── The maintenance task detail sheet ───────────────────────────────
//
// Stage 3 of splitting Tasks.tsx: fourteen ``e``-prefixed hooks, the
// update handler and 562 lines of drawer, out of a page that was 2,310
// lines when this began.  The prefix was the smell — ``e*`` here and
// ``f*`` in the add form existed only because two forms shared one
// component's scope.
//
// ⚠️ The one REDESIGN, not a move: the page seeded these fields
// imperatively when a row was clicked.  A component takes the task as a
// PROP, so seeding is an effect keyed on it — which turns the async
// odometer fetch from a hand-rolled task-id guard into a real
// cancellation.  The page renders this only while a task is open, so
// switching tasks REMOUNTS and nothing carries over.
//
// What stays on the PAGE and why: the history modal opens from the
// grid's columns too (5 call sites outside this sheet), so it keeps its
// state there and this takes one ``onShowHistory`` callback.  Delete and
// snooze are used nowhere else, so they moved in here with the handlers
// that touch them.

import { useState, useEffect } from 'react';
import {
  Bell, BellOff, CheckSquare, FileText, Image as ImageIcon, Paperclip,
  Upload, X, History, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';

import { apiJSON, apiFetch } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { formatDate } from '../../utils/datetime';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../components/ui/select';
import { Sheet, SheetContent, SheetBody } from '../../components/ui/sheet';
import ServiceTaskPicker from '../service-tasks/ServiceTaskPicker';
import { MilesPicker, HoursPicker, DaysPicker } from './pickers';
import { type Priority } from './badges';
import {
  PRIORITY_ITEMS, STATUS_ITEMS, type TriggerMode,
  _dueDateToPeriodDays, _periodDaysToDueDate, _formatDate, _todayLabel,
  _isOverdueOrApproaching, initialTriggerMode, centsToDollars, remainingFrom,
} from './taskFields';
import type { MaintenanceTask } from '../../types';

export interface TaskDetailSheetProps {
  /** The open task.  Never null — the page renders this only while one
   *  is selected, and remounting on change is what guarantees no field
   *  carries over from the previous task. */
  task: MaintenanceTask;
  onClose: () => void;
  /** Refetch the list after a successful save. */
  onSaved: () => void;
  onError: (message: string) => void;
  /** The VEHICLE's past services — the header pill.  A different thing
   *  from the task's activity trail below, and the two must not share a
   *  callback: collapsing them fired both dialogs at once. */
  onShowServiceHistory: () => void;
  /** THIS TASK's activity trail — who changed what, field-level
   *  old→new (capabilities/activity_trail).  The dashboard rule keeps
   *  these two apart precisely because "history" already means services. */
  onShowActivityTrail: () => void;
  /** An attachment change re-fetches the task; the PAGE owns which task
   *  is open, so it takes the fresh copy.  Without this the drawer would
   *  keep showing the attachment list it had before the upload. */
  onTaskChanged: (task: MaintenanceTask) => void;
  canCreateTasks: boolean;
  customTypeLabelByValue: Record<string, string>;
  tz: string;
}

export default function TaskDetailSheet({
  task, onClose, onSaved, onError, onTaskChanged,
  onShowServiceHistory, onShowActivityTrail,
  canCreateTasks, customTypeLabelByValue, tz,
}: TaskDetailSheetProps) {
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);

  // Edit form
  const [eStatus, setEStatus] = useState('');
  // Task-type picker for the drawer.  Lets the operator FIX a
  // mistyped type after creation — without this they had to delete
  // the task and re-create it to swap, say, "Oil Change" to
  // "Inspection".  Defaults to whatever the row already has so an
  // unchanged save doesn't accidentally rewrite it.
  const [eType, setEType] = useState('inspection');
  const [eDesc, setEDesc] = useState('');
  const [eDueDate, setEDueDate] = useState('');
  const [eDueMiles, setEDueMiles] = useState('');
  const [ePriority, setEPriority] = useState<Priority>('medium');
  const [eDueEngineHours, setEDueEngineHours] = useState('');
  // Same single-trigger mode the add form uses.  Initial value is
  // inferred from the loaded task (whichever trigger is set), so the
  // user opens the drawer to the field they previously cared about.
  const [eTriggerMode, setETriggerMode] = useState<TriggerMode>('date');
  // Repeat checkbox for the active trigger.  Reflects whether the
  // corresponding ``recur_interval_*`` column is set on the loaded
  // task; toggling on save populates/clears that one column only,
  // leaving the other dimensions' recurrence intact.
  const [eRepeat, setERepeat] = useState(false);
  // Recurrence period — DECOUPLED from the due-period so an operator
  // can schedule a one-time oil change "in 39,250 mi" but make the
  // recurrence land on the standard 40,000 mi interval.  Defaults to
  // the due-period value when the checkbox flips on (so today's
  // "they're the same" muscle-memory keeps working), but the input
  // is freely editable.  String to match the other due-period inputs.
  const [eRecurValue, setERecurValue] = useState('');
  // Cost is held as a dollars string in the form (free-text decimal),
  // converted to integer cents on submit.  Vendor is plain text.
  const [eCost, setECost] = useState('');
  const [eVendor, setEVendor] = useState('');
  const [eOdometer, setEOdometer] = useState<number | null>(null);
  const [eEngineHours, setEEngineHours] = useState<number | null>(null);

  // Open the Edit sidebar with a task.  Note the absolute → period
  // conversion: the backend stores absolute due-mileage / due-hours /
  // due-date, but the form inputs hold *periods* (intervals from
  // current).  Miles & hours are converted on submit (current +
  // period), and back-converted here so a user re-opening a task sees
  // "miles remaining" rather than the raw absolute target.
  // Date is similarly converted to "days remaining".
  // Upload a receipt/photo to the open task.  Backend stamps the
  // single attachment metadata; previous file is replaced on the
  // object store side too (folder-keyed by task id).
  const handleAttachmentUpload = async (file: File) => {
    if (!task) return;
    // Client-side mirror of the API's 10 MB cap so the user gets
    // immediate feedback without a round-trip on huge phone photos.
    if (file.size > 10 * 1024 * 1024) {
      toast.error('File exceeds 10 MB limit.');
      return;
    }
    setUploadingAttachment(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await apiFetch(
        '/maintenance/tasks/' + task.id + '/attachment',
        { method: 'POST', body: form },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        toast.error(typeof err.detail === 'string' ? err.detail : 'Upload failed');
        return;
      }
      toast.success('Attachment uploaded');
      onSaved();
      // Refetch the open task so the sidebar shows the new attachment
      // without forcing the user to reopen the drawer.
      try {
        const refreshed = await apiJSON<MaintenanceTask>(
          '/maintenance/tasks/' + task.id,
        );
        onTaskChanged(refreshed);
      } catch { /* non-fatal; list refresh above will sync next open */ }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploadingAttachment(false);
    }
  };

  const handleAttachmentDelete = async () => {
    if (!task) return;
    const ok = window.confirm(
      'Remove the attached file?\n\nThis can\'t be undone.',
    );
    if (!ok) return;
    try {
      await apiJSON('/maintenance/tasks/' + task.id + '/attachment', {
        method: 'DELETE',
      });
      toast.success('Attachment removed');
      onSaved();
      try {
        const refreshed = await apiJSON<MaintenanceTask>(
          '/maintenance/tasks/' + task.id,
        );
        onTaskChanged(refreshed);
      } catch { /* non-fatal */ }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  // Snooze the currently-open task for ``hours`` hours, or clear an
  // active snooze when ``hours`` is null.  Backend stamps
  // ``snoozed_until`` and clears ``alerted_at`` so the next alert fires
  // fresh once the snooze expires.  We refresh the list so the row
  // shows its updated snooze badge.
  // One-click "this task is done" — mirrors the bulk-select
  // ``Mark complete`` action so the per-task drawer doesn't force a
  // two-step (change Status dropdown → click Update).  The drawer
  // closes and the list refreshes so the row's status badge flips
  // and the row sinks below the open tasks per the urgency sort.
  const handleMarkComplete = async () => {
    if (!task) return;
    try {
      await apiJSON('/maintenance/tasks/' + task.id, {
        method: 'PUT', body: { status: 'completed' },
      });
      toast.success('Marked complete');
      onClose();
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Mark complete failed');
    }
  };

  const handleSnooze = async (hours: number | null) => {
    if (!task) return;
    const until = hours === null
      ? null
      : new Date(Date.now() + hours * 3600_000).toISOString();
    try {
      await apiJSON('/maintenance/tasks/' + task.id + '/snooze', {
        method: 'POST', body: { until },
      });
      toast.success(
        until
          ? `Snoozed until ${formatDate(until, { timeZone: tz })}`
          : 'Snooze cleared',
      );
      onClose();
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Snooze failed');
    }
  };

  // Inline-confirmed delete: the drawer's Delete button reveals a
  // "Danger Zone" expansion with a final Confirm action.  No
  // window.confirm because the in-drawer expansion already serves as
  // the friction step — duplicating it would feel like Wizard of Oz
  // dialogs ("are you sure you're sure?").
  const confirmDelete = async () => {
    if (!task) return;
    try {
      await apiJSON('/maintenance/tasks/' + task.id, { method: 'DELETE' });
      onClose(); onSaved();
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed'); }
  };

  useEffect(() => {
    let cancelled = false;
    setEStatus(task.status);
    setEType(task.task_type || 'inspection');
    setEDesc(task.description);
    setEDueDate(_dueDateToPeriodDays(task.due_date));
    setEPriority(((task.priority || 'medium') as Priority));
    // Infer the initial trigger view from what the task actually has.
    // Preference order: date > miles > hours.  Falls back to date so
    // the drawer always opens with a sane view.
    const initialMode = initialTriggerMode(task);
    setETriggerMode(initialMode);
    // Initial repeat-checkbox state reflects the existing recurrence
    // for the chosen dimension.  Switching tabs after this will
    // re-derive via the effect below.
    setERepeat(
      initialMode === 'date'  ? task.recur_interval_days != null
      : initialMode === 'miles' ? task.recur_interval_miles != null
      : task.recur_interval_engine_hours != null,
    );
    // Cost — display as dollars (cents / 100) with up to 2 decimals.
    setECost(centsToDollars(task.cost_cents));
    setEVendor(task.vendor_name || '');
    // Period from the task's own engine-hours snapshot (best signal
    // without a live endpoint).  When no snapshot, fall back to
    // showing the absolute value.
    const baseHours = task.last_engine_hours ?? null;
    setEEngineHours(baseHours);
    setEDueEngineHours(remainingFrom(task.due_engine_hours, baseHours));
    // Miles period requires the live odometer, which we fetch async.
    // Seed with the absolute value first; the .then() below replaces
    // it once the odometer lands.
    setEOdometer(null);
    setEDueMiles(remainingFrom(task.due_miles, null));
    if (task.vehicle_name) {
      void apiJSON<{
        odometer_miles: number | null;
        engine_hours: number | null;
      }>(
        '/maintenance/odometer/' + encodeURIComponent(task.vehicle_name),
      ).then((d) => {
        // ⚠️ The response may belong to a task the operator has already
        // navigated away from.  Open A, click B before A's odometer
        // lands, and without this guard A's reading overwrites B's
        // fields — a plausible WRONG MILEAGE sitting in an open form,
        // which is then saved.  Nothing about it looks like an error.
        if (cancelled) return;
        const odo = d.odometer_miles ?? null;
        setEOdometer(odo);
        if (odo != null) setEDueMiles(remainingFrom(task.due_miles, odo));
        // Prefer the live engine-hours reading over the task's stored
        // snapshot when the warehouse has a fresher value.  Falls back
        // to last_engine_hours (already seeded above) when null.
        const liveHrs = d.engine_hours ?? null;
        if (liveHrs != null) {
          setEEngineHours(liveHrs);
          setEDueEngineHours(remainingFrom(task.due_engine_hours, liveHrs));
        }
      }).catch(() => { if (!cancelled) setEOdometer(null); });
    }
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id]);

  // Re-seed the Repeat checkbox whenever the active edit-trigger mode
  // changes — the loaded task's per-dimension recurrence is the source
  // of truth, so switching from Date → Miles reveals whatever the
  // existing miles-recurrence state was without mixing dimensions.
  useEffect(() => {
    if (!task) return;
    setERepeat(
      eTriggerMode === 'date'  ? task.recur_interval_days != null
      : eTriggerMode === 'miles' ? task.recur_interval_miles != null
      : task.recur_interval_engine_hours != null,
    );
    // Re-seed the recur input from the row's stored interval for the
    // active trigger.  Falls back to '' when no interval is set;
    // the checkbox handler below seeds from the due-period when the
    // user flips it on without a pre-existing interval.
    const stored =
      eTriggerMode === 'date'  ? task.recur_interval_days
      : eTriggerMode === 'miles' ? task.recur_interval_miles
      : task.recur_interval_engine_hours;
    setERecurValue(stored != null ? String(stored) : '');
  }, [eTriggerMode, task]);

  const handleUpdate = async () => {
    if (!task) return;
    // Resolve absolute values from the period inputs.  Miles & hours
    // fall through to "period as absolute" when the baseline isn't
    // known yet (odometer fetch still pending, no last_engine_hours
    // snapshot).
    const dueDateAbs = eDueDate ? _periodDaysToDueDate(eDueDate) : '';
    const dueMilesAbs = eDueMiles
      ? (eOdometer != null
          ? Math.round(eOdometer) + Number(eDueMiles)
          : Number(eDueMiles))
      : null;
    const dueEngineHoursAbs = eDueEngineHours
      ? (eEngineHours != null
          ? Math.round(eEngineHours) + Number(eDueEngineHours)
          : Number(eDueEngineHours))
      : null;
    const body: Record<string, unknown> = {};
    if (eStatus !== task.status) body.status = eStatus;
    if (eType !== (task.task_type || 'inspection')) body.task_type = eType;
    if (eDesc !== task.description) body.description = eDesc;
    // Only patch the trigger field matching the current view —
    // switching modes in the segmented control doesn't wipe a
    // previously-set trigger of another type.  A multi-trigger task
    // stays multi-trigger until the user explicitly opens that
    // dimension's view and clears it.
    if (eTriggerMode === 'date' && dueDateAbs !== (task.due_date || '')) {
      body.due_date = dueDateAbs || null;
    }
    if (eTriggerMode === 'miles' && dueMilesAbs !== (task.due_miles ?? null)) {
      body.due_miles = dueMilesAbs;
    }
    if (ePriority !== (task.priority || 'medium')) body.priority = ePriority;
    if (eTriggerMode === 'hours' && dueEngineHoursAbs !== (task.due_engine_hours ?? null)) {
      body.due_engine_hours = dueEngineHoursAbs;
    }
    // Recurrence: the "Repeat after completion" checkbox controls the
    // active trigger's recurrence interval — when checked, the
    // recur_interval_* column is set to the user-typed recurrence
    // value (separate from the due-period).  Falls back to the
    // due-period when the recur input is blank, so the old
    // "they're the same" UX still works for users who don't bother
    // entering a separate value.  When unchecked, that column is
    // nulled.  Only the active dimension is touched so the other two
    // recurrence columns (set via a previous edit on another tab)
    // stay intact.
    const recurNum = eRecurValue.trim() ? Number(eRecurValue) : NaN;
    if (eTriggerMode === 'date') {
      const fallback = eDueDate ? Number(eDueDate) : null;
      const want = eRepeat
        ? (Number.isFinite(recurNum) ? recurNum : fallback)
        : null;
      if (want !== (task.recur_interval_days ?? null)) {
        body.recur_interval_days = want;
      }
    }
    if (eTriggerMode === 'miles') {
      const fallback = eDueMiles ? Number(eDueMiles) : null;
      const want = eRepeat
        ? (Number.isFinite(recurNum) ? recurNum : fallback)
        : null;
      if (want !== (task.recur_interval_miles ?? null)) {
        body.recur_interval_miles = want;
      }
    }
    if (eTriggerMode === 'hours') {
      const fallback = eDueEngineHours ? Number(eDueEngineHours) : null;
      const want = eRepeat
        ? (Number.isFinite(recurNum) ? recurNum : fallback)
        : null;
      if (want !== (task.recur_interval_engine_hours ?? null)) {
        body.recur_interval_engine_hours = want;
      }
    }
    // Cost dollars → integer cents.  Empty string means "clear".
    // Reject obviously bad input (negative, non-numeric) here so the
    // user sees the error before the round-trip.
    const trimmedCost = eCost.trim();
    let costCentsAbs: number | null = null;
    if (trimmedCost) {
      const parsed = Number(trimmedCost);
      if (!Number.isFinite(parsed) || parsed < 0) {
        onError('Cost must be a positive number.');
        return;
      }
      costCentsAbs = Math.round(parsed * 100);
    }
    if (costCentsAbs !== (task.cost_cents ?? null)) {
      body.cost_cents = costCentsAbs;
    }
    const trimmedVendor = eVendor.trim();
    if ((trimmedVendor || null) !== (task.vendor_name || null)) {
      body.vendor_name = trimmedVendor || null;
    }
    if (Object.keys(body).length === 0) return;
    // Completion confirmation — flipping a task to "completed" stamps
    // the current user as the attester (server-side, see the API route
    // handler).  Make that explicit so the user understands they're
    // signing off, not just updating a field.  Matches the
    // window.confirm pattern used elsewhere in the dashboard
    // (PoiLayerPanel, Coaching).
    if (body.status === 'completed' && task.status !== 'completed') {
      const ok = window.confirm(
        'Mark this task complete?\n\n'
        + 'This will record you as the attester for DOT audit purposes. '
        + 'The completion timestamp and your identity are stored permanently.',
      );
      if (!ok) return;
    }
    setSaving(true); onError('');
    try {
      const res = await apiJSON<{ ok: boolean; spawned_id?: number | null }>(
        '/maintenance/tasks/' + task.id, { method: 'PUT', body },
      );
      // Surface recurring auto-spawn so users see the chain is alive.
      // Quietly skips when the parent had no recurrence interval.
      if (res?.spawned_id) {
        toast.success(`Marked complete — next occurrence created (#${res.spawned_id}).`);
      }
      onClose(); onSaved();
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  return (
      <Sheet open={!!task} onOpenChange={(o) => { if (!o) onClose(); }}>
        <SheetContent
          side="right"
          className="p-0"
          // The header already carries a ✕ beside the History pill, so
          // the primitive's own would sit on top of it — two close
          // buttons crowding each other.  Fourth sheet in this codebase
          // to hit it: SheetContent renders one by DEFAULT, and a
          // hand-rolled modal being converted always brings its own.
          showCloseButton={false}
          size="lg"
        >
          {task && (
          <SheetBody label="Maintenance task details" className="p-6">
            {/* Header: vehicle + task type for disambiguation when
                multiple tabs/drawers are juggled. History sits as a
                clear chip rather than a faint inline link. */}
            <div className="flex items-start justify-between gap-2 mb-4">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold truncate">
                  {task.vehicle_name}
                  {task.company_code && (
                    <span
                      title="Company this truck belongs to — disambiguates when two companies share a vehicle name"
                      className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-3xs align-middle"
                    >
                      {task.company_code}
                    </span>
                  )}
                  <span className="text-muted-foreground font-normal">
                    {' · '}
                    <span>{customTypeLabelByValue[task.task_type] ?? task.task_type.replace(/_/g, ' ')}</span>
                  </span>
                </h2>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  type="button"
                  onClick={onShowServiceHistory}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-muted hover:bg-muted/80 border border-border rounded-md transition min-h-tap"
                  title="View service history"
                >
                  <History className="size-3" />
                  History
                </button>
                <button onClick={() => onClose()} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X className="size-4" /></button>
              </div>
            </div>
            {/* Auto-renewal breadcrumb — shown at the top of the
                sidebar so users instantly understand why this task
                exists when it was machine-created.  Only renders when
                ``spawned_from_id`` is set (legacy or user-created tasks
                show nothing here). */}
            {task.spawned_from_id && (
              <div className={`mb-4 px-3 py-2 min-h-tap rounded text-xs inline-flex items-center gap-1.5 ${toneClasses('info')}`}>
                <span aria-hidden>↻</span>
                Auto-renewed from task #{task.spawned_from_id}
              </div>
            )}
            {/* Work-order cross-link — when this task was closed by a
                shop visit, surface a clickable badge that opens the
                work-order page.  Uses ``window.location`` to traverse
                the SPA so the maintenance sidebar can close cleanly
                without router context plumbing.  Read-only — editing
                lives on the work-order page itself. */}
            {task.work_order_id && (
              <a
                href={`/work-orders/${task.work_order_id}`}
                className={`mb-4 px-3 py-2 min-h-tap rounded text-xs inline-flex items-center gap-1.5 ${toneClasses('ok')}`}
              >
                <span aria-hidden>📄</span>
                Closed by Work Order #{task.work_order_id}
              </a>
            )}
            {/* Active-snooze banner — when the task has a future
                ``snoozed_until``, surface it prominently so the user
                doesn't think the system is broken when overdue tasks
                stop generating alerts.  One-click Resume clears it. */}
            {task.snoozed_until
              && new Date(task.snoozed_until).getTime() > Date.now() && (
              <div className={`mb-4 px-3 py-2 rounded text-xs flex items-center gap-2 ${toneClasses('warn')}`}>
                <BellOff className="shrink-0 size-3.5" />
                <span className="flex-1">
                  Snoozed until {formatDate(task.snoozed_until, { timeZone: tz })}
                </span>
                <button
                  type="button"
                  onClick={() => handleSnooze(null)}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-warn-bg hover:bg-warn-bg rounded text-warn"
                >
                  <Bell className="size-3" />
                  Resume
                </button>
              </div>
            )}
            {/* Immutable facts only — editable fields (status,
                description, due triggers, priority) live in the form
                below so the same value never appears twice. */}
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-muted-foreground">Created</dt><dd>{_formatDate(task.created_at, tz)}</dd></div>
              {task.completed_at && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Completed</dt>
                  <dd>{_formatDate(task.completed_at, tz)}</dd>
                </div>
              )}
              {(task.recur_interval_days
                || task.recur_interval_miles
                || task.recur_interval_engine_hours) && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Recurrence</dt>
                  <dd className="text-right">
                    {[
                      task.recur_interval_days
                        ? `every ${task.recur_interval_days} days` : null,
                      task.recur_interval_miles
                        ? `every ${Number(task.recur_interval_miles).toLocaleString()} mi` : null,
                      task.recur_interval_engine_hours
                        ? `every ${Number(task.recur_interval_engine_hours).toLocaleString()} h` : null,
                    ].filter(Boolean).join(' · ')}
                  </dd>
                </div>
              )}
              {/* Attestation: surfaces the audit trail.  Renders inside
                  the dl block so it's visually grouped with the other
                  task metadata.  Multi-line because the name+date can
                  wrap on narrow sidebars. */}
              {task.attested_at && (
                <div className="pt-2 border-t border-border">
                  <dt className="text-muted-foreground text-xs mb-1">Attestation</dt>
                  <dd className="text-xs text-ok">
                    <span aria-hidden>✓</span>{' '}
                    <span className="font-medium">
                      {task.attested_by_name || `user ${task.attested_by}`}
                    </span>
                    {' '}confirmed on{' '}
                    {formatDate(task.attested_at, { timeZone: tz })}
                  </dd>
                </div>
              )}
            </dl>
            <div className="space-y-3">
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Status</span>
                <Select value={eStatus} onValueChange={setEStatus} items={STATUS_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="Status"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {STATUS_ITEMS.map(it => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </label>
              {/* Type selector — lets the operator FIX a mistyped task
                  type after creation.  Without this they had to delete
                  the row and re-create just to flip e.g. "Oil Change"
                  to "Inspection".  Uses the same ServiceTaskPicker the add
                  form does so custom types created here also surface
                  on the create form (and vice versa). */}
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Service task</span>
                <ServiceTaskPicker value={eType} onChange={setEType} canCreate={canCreateTasks} />
              </label>
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Priority</span>
                <Select value={ePriority} onValueChange={(v) => setEPriority(v as Priority)} items={PRIORITY_ITEMS}>
                  <SelectTrigger className="w-full" aria-label="Priority"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PRIORITY_ITEMS.map(it => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </label>
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Description</span>
                <textarea
                  value={eDesc}
                  onChange={e => setEDesc(e.target.value)}
                  rows={3}
                  minLength={3}
                  maxLength={500}
                  placeholder="e.g. Oil + filter, full synthetic 15W-40"
                  className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
                />
              </label>
              {/* Single-trigger picker — segmented control + the
                  corresponding picker.  Initial mode is inferred from
                  the loaded task in ``openTaskForEdit`` so the drawer
                  opens to the field the user previously cared about.
                  Switching modes here doesn't wipe the other
                  dimension's value in the DB — only the currently-shown
                  trigger is patched on save (see ``handleUpdate``). */}
              <div>
                <span className="block text-xs text-muted-foreground mb-1">
                  Due by
                </span>
                <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md mb-2" role="group" aria-label="Due by">
                  {([
                    { k: 'date',  label: 'Date'  },
                    { k: 'miles', label: 'Miles' },
                    { k: 'hours', label: 'Hours' },
                  ] as const).map(opt => {
                    const active = eTriggerMode === opt.k;
                    return (
                      <button
                        key={opt.k}
                        type="button"
                        onClick={() => setETriggerMode(opt.k)}
                        aria-pressed={active}
                        className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                          active
                            ? 'bg-card text-foreground shadow-sm'
                            : 'text-muted-foreground hover:text-foreground'
                        } min-h-tap`}
                      >
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
                {eTriggerMode === 'date' && (
                  <label className="block">
                    <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
                      <span title="Today's date — the period below is added to this">Today: {_todayLabel(tz)}</span>
                      <span className="text-primary">
                        Due: {eDueDate ? _formatDate(_periodDaysToDueDate(eDueDate), tz) : '—'}
                      </span>
                    </div>
                    <DaysPicker value={eDueDate} onChange={setEDueDate} />
                  </label>
                )}
                {eTriggerMode === 'miles' && (
                  <label className="block">
                    <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
                      <span>
                        Current: {eOdometer != null ? `${Math.round(eOdometer).toLocaleString()} mi` : '—'}
                      </span>
                      <span className="text-primary">
                        Due:{' '}
                        {eDueMiles
                          ? `${(eOdometer != null ? Math.round(eOdometer) + Number(eDueMiles) : Number(eDueMiles)).toLocaleString()} mi`
                          : '—'}
                      </span>
                    </div>
                    <MilesPicker
                      value={eDueMiles}
                      onChange={setEDueMiles}
                      mode={eOdometer != null ? 'period' : 'absolute'}
                    />
                  </label>
                )}
                {eTriggerMode === 'hours' && (
                  <label className="block">
                    {eEngineHours != null ? (
                      <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
                        <span>Current: {Math.round(eEngineHours).toLocaleString()} h</span>
                        <span className="text-primary">
                          Due:{' '}
                          {eDueEngineHours
                            ? `${(Math.round(eEngineHours) + Number(eDueEngineHours)).toLocaleString()} h`
                            : '—'}
                        </span>
                      </div>
                    ) : null  /* picker now renders its own no-telemetry hint */}
                    <HoursPicker
                      value={eDueEngineHours}
                      onChange={setEDueEngineHours}
                      mode={eEngineHours != null ? 'period' : 'absolute'}
                    />
                  </label>
                )}
              </div>
              {/* Single recurrence checkbox — only the active
                  trigger's recurrence is touched on save.  Other
                  dimensions' recurrence (if any) stays intact and
                  surfaces when the user switches tabs. */}
              {/* Repeat-after-completion with a SEPARATE recurrence
                  input.  The due-period is when this specific service
                  is due; the recur input is the standard interval to
                  apply on every completion.  They CAN be the same
                  (default) but no longer HAVE to be — common case:
                  schedule an oil change in 39,250 mi but make the
                  recurrence land on the standard 40,000 mi. */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <label className="inline-flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={eRepeat}
                    onChange={(e) => {
                      const on = e.target.checked;
                      setERepeat(on);
                      // Seed the recur input from the due-period when
                      // the user flips the checkbox on and the field
                      // is still empty — preserves the "they're the
                      // same" default for users who don't care about
                      // setting a separate interval.
                      if (on && !eRecurValue.trim()) {
                        const seed =
                          eTriggerMode === 'date'  ? eDueDate
                          : eTriggerMode === 'miles' ? eDueMiles
                          : eDueEngineHours;
                        if (seed) setERecurValue(String(seed));
                      }
                    }}
                    className="accent-primary cursor-pointer"
                  />
                  <span className="text-foreground">Repeat every</span>
                </label>
                <input
                  type="number"
                  min={0}
                  value={eRecurValue}
                  onChange={e => setERecurValue(e.target.value)}
                  disabled={!eRepeat}
                  placeholder={
                    eTriggerMode === 'date' ? '30'
                    : eTriggerMode === 'miles' ? '5000'
                    : '500'
                  }
                  className="w-20 bg-muted border border-border rounded px-2 py-1 text-xs focus:outline-none focus:border-ring disabled:opacity-50"
                />
                <span className="text-foreground">
                  {eTriggerMode === 'date' ? 'days' : eTriggerMode === 'miles' ? 'mi' : 'h'} after completion
                </span>
              </div>

              {/* Completion-time evidence (receipt / photo / cost /
                  vendor) sits AFTER the trigger/repeat block since it
                  only matters once the task is being closed out.
                  Hidden for Pending / In Progress / Cancelled.

                  Visually separated from the config fields above with
                  a hairline divider — receipt + cost + vendor read as
                  one logical "completion evidence" group, not as more
                  form fields. */}
              {(task.status === 'completed' || eStatus === 'completed') && (<>
                <div className="border-t border-border/40 pt-3 -mx-0" aria-hidden />
                <div className="block">
                  <span className="block text-xs text-muted-foreground mb-1 inline-flex items-center gap-1">
                    <Paperclip className="size-3" />
                    Receipt / Photo
                  </span>
                  {task.attachment_name ? (
                    <div className="flex items-center gap-2 p-2 bg-muted/40 border border-border rounded text-xs">
                      {(task.attachment_content_type || '').startsWith('image/')
                        ? <ImageIcon className="text-muted-foreground shrink-0 size-3.5" />
                        : <FileText className="text-muted-foreground shrink-0 size-3.5" />}
                      <a
                        href={'/api/maintenance/tasks/' + task.id + '/attachment'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-1 truncate hover:underline min-h-tap"
                        title={task.attachment_name}
                      >
                        {task.attachment_name}
                      </a>
                      <label className="cursor-pointer text-muted-foreground hover:text-foreground" title="Replace">
                        <Upload className="size-3.5" />
                        <input
                          type="file"
                          accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif"
                          className="hidden"
                          disabled={uploadingAttachment}
                          onChange={e => {
                            const f = e.target.files?.[0];
                            if (f) handleAttachmentUpload(f);
                            e.target.value = '';
                          }}
                        />
                      </label>
                      <button
                        type="button"
                        onClick={handleAttachmentDelete}
                        className="text-muted-foreground hover:text-destructive py-0.5 -my-0.5 min-h-tap"
                        aria-label="Remove attachment"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ) : (
                    <label className="flex items-center justify-center gap-1.5 p-2 bg-muted/40 hover:bg-muted border border-dashed border-border rounded text-xs cursor-pointer text-muted-foreground hover:text-foreground">
                      <Upload className="size-3.5" />
                      {uploadingAttachment ? 'Uploading…' : 'Attach a receipt or photo (max 10 MB)'}
                      <input
                        type="file"
                        accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif"
                        className="hidden"
                        disabled={uploadingAttachment}
                        onChange={e => {
                          const f = e.target.files?.[0];
                          if (f) handleAttachmentUpload(f);
                          e.target.value = '';
                        }}
                      />
                    </label>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="block text-xs text-muted-foreground mb-1">Cost (USD)</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      value={eCost}
                      onChange={e => setECost(e.target.value)}
                      placeholder="e.g. 285.50"
                      className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
                    />
                  </label>
                  <label className="block">
                    <span className="block text-xs text-muted-foreground mb-1">Vendor</span>
                    <input
                      type="text"
                      maxLength={120}
                      value={eVendor}
                      onChange={e => setEVendor(e.target.value)}
                      placeholder="e.g. Joe's Truck Shop"
                      className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
                    />
                  </label>
                </div>
              </>)}

              {/* Hairline divider between editable fields and the
                  action area — actions are categorically different
                  from form inputs and benefit from a clear visual
                  break. */}
              <div className="border-t border-border/40 mt-1" aria-hidden />

              {/* Action area — Cancel / Update on the primary row;
                  Snooze + Delete as collapsed pills below.  Snooze
                  expands inline with the 48h/7d/30d picker.  Delete
                  expands inline with a Danger-Zone confirm step
                  (no extra browser-level confirm dialog — the inline
                  expansion IS the confirmation). */}
              <div className="flex items-center gap-2 pt-3">
                <button
                  type="button"
                  onClick={() => onClose()}
                  disabled={saving}
                  className="px-3 py-2 bg-muted hover:bg-muted/80 border border-border rounded text-sm font-medium transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdate}
                  disabled={saving}
                  className="flex-1 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition min-h-tap"
                >
                  {saving ? 'Saving...' : 'Update Task'}
                </button>
              </div>

              {/* Mark complete — one-click parity with the bulk-select
                  toolbar's "Mark complete" pill.  Hidden when the
                  task is already completed or cancelled (it would be
                  a no-op) so the action area stays uncluttered. */}
              {task.status !== 'completed' && task.status !== 'cancelled' && (
                <button
                  type="button"
                  onClick={handleMarkComplete}
                  disabled={saving}
                  className={`mt-2 w-full py-1.5 min-h-tap rounded text-xs font-medium transition inline-flex items-center justify-center gap-1.5 disabled:opacity-50 ${toneClasses('ok')}`}
                >
                  <CheckSquare className="size-3" />
                  Mark complete
                </button>
              )}

              {/* Snooze pill — only shows when a snooze would actually
                  do something (task is overdue or close to threshold).
                  Collapsed: subtle button.  Expanded: inline 48h/7d/30d
                  picker with a close-X. */}
              {_isOverdueOrApproaching(task) && (
                snoozeOpen ? (
                  <div className="flex items-center gap-1.5 pt-2">
                    <span className="text-2xs text-muted-foreground inline-flex items-center gap-1 mr-1">
                      <BellOff className="size-3" />
                      Snooze for:
                    </span>
                    <button
                      type="button"
                      onClick={() => handleSnooze(48)}
                      className="flex-1 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded text-xs font-medium"
                    >
                      48h
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSnooze(24 * 7)}
                      className="flex-1 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded text-xs font-medium"
                    >
                      7 days
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSnooze(24 * 30)}
                      className="flex-1 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded text-xs font-medium"
                    >
                      30 days
                    </button>
                    <button
                      type="button"
                      onClick={() => setSnoozeOpen(false)}
                      aria-label="Cancel snooze"
                      className="text-muted-foreground hover:text-foreground p-1"
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setSnoozeOpen(true)}
                    className="mt-2 w-full py-1.5 bg-muted/60 hover:bg-muted border border-border rounded text-xs font-medium text-foreground transition inline-flex items-center justify-center gap-1.5"
                  >
                    <BellOff className="size-3" />
                    Snooze
                  </button>
                )
              )}

              {/* Delete pill — same collapsed/expanded pattern.  The
                  expanded state IS the Danger-Zone confirmation; no
                  browser-level confirm dialog is fired. */}
              {deleteOpen ? (
                <div className="mt-2 p-2 bg-destructive/10 border border-destructive/30 rounded">
                  <p className="text-2xs uppercase tracking-wide text-destructive mb-1.5">
                    Danger zone — this can&apos;t be undone
                  </p>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setDeleteOpen(false)}
                      className="px-3 py-1.5 bg-muted hover:bg-muted/80 border border-border rounded text-xs font-medium"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={confirmDelete}
                      className="flex-1 py-1.5 bg-destructive text-destructive-foreground hover:bg-destructive/90 rounded text-xs font-medium inline-flex items-center justify-center gap-1.5 min-h-tap"
                    >
                      <Trash2 className="size-3" />
                      Yes, delete this task
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setDeleteOpen(true)}
                  className="mt-2 w-full py-1.5 bg-muted/60 hover:bg-destructive/10 border border-border hover:border-destructive/30 rounded text-xs font-medium text-muted-foreground hover:text-destructive transition inline-flex items-center justify-center gap-1.5"
                >
                  <Trash2 className="size-3" />
                  Delete
                </button>
              )}

              {/* THIS TASK's activity trail — who changed what, field-level
                  old→new.  It sits down here, away from the header's
                  "History" pill, because that one shows the VEHICLE's past
                  SERVICES: two different records, two similar words, and
                  150px apart they read as one thing.  The dashboard rule
                  keeps the concepts apart in code for the same reason.
                  A quiet LINK, not a button — everything above it writes,
                  this only looks. */}
              <div className="mt-4 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={onShowActivityTrail}
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors py-1 -my-1 min-h-tap"
                >
                  <History className="size-3.5" /> View activity history
                </button>
              </div>
            </div>
          </SheetBody>
          )}
        </SheetContent>
      </Sheet>
  );
}
