import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Wrench, Plus, X, Download, History, FileText,
  List, CalendarDays, Trash2, CheckSquare,
} from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { MaintenanceTask, AnyColumn } from '../../types';
import {
  PriorityBadge, EngineHoursProgress, TaskTypeCell, DueDateChip, MileageProgress,
  TASK_TYPE_OPTIONS, PRIORITY_OPTIONS,
  type Priority,
} from './badges';
import { VehiclePicker, MilesPicker, HoursPicker, DaysPicker, type FleetVehicle } from './pickers';
import { CalendarMonth } from './CalendarMonth';
import { ServiceHistoryModal } from './ServiceHistoryModal';

// Status dropdown options. Labels are computed via STATUS_LABELS so
// the on-screen text is properly capitalised ("In Progress", not
// "in progress") regardless of the wire value.
const STATUS_OPTIONS = ['pending', 'in_progress', 'completed', 'cancelled'] as const;
const STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
  overdue: 'Overdue',
};

// Convert a period in days (e.g. "30") to a YYYY-MM-DD due date by
// adding to today's calendar day. Returns empty when the period is
// empty/non-numeric (caller treats that as "no date trigger").
function _periodDaysToDueDate(periodDays: string): string {
  if (!periodDays) return '';
  const n = Number(periodDays);
  if (!Number.isFinite(n)) return '';
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + Math.round(n));
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

// Inverse of _periodDaysToDueDate — pulls the period out of an
// existing absolute due date so the Edit drawer can pre-fill the
// period input with "remaining days until due".
function _dueDateToPeriodDays(due: string | null | undefined): string {
  if (!due) return '';
  const target = new Date(due + 'T00:00:00');
  if (Number.isNaN(target.getTime())) return '';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
  return String(diff);
}

// Single date formatter used everywhere in the drawer so Created /
// Completed / Current / Due all read the same way ("May 17, 2026")
// instead of mixing numeric and long-form locale defaults.
const DATE_FMT: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'short', day: 'numeric' };

function _formatDate(input: string | null | undefined): string {
  if (!input) return '';
  // YYYY-MM-DD inputs land at midnight UTC if parsed bare; pin to
  // local midnight so the rendered day matches the user's calendar.
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(input) ? input + 'T00:00:00' : input;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, DATE_FMT);
}

function _todayLabel(): string {
  return new Date().toLocaleDateString(undefined, DATE_FMT);
}

// Base column set, excluding the bulk-select checkbox.  The component
// composes the final columns array by prepending a selection column
// whose render closes over the page-level ``selectedIds`` state.
const baseColumns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  // Priority badge — first column after vehicle so it carries the most
  // visual weight.  Sortable by string value (low < medium < high <
  // critical alphabetically — close enough; future revision can pass a
  // sortKey if needed).
  { key: 'priority', label: 'Priority', sortable: true, render: (v) => <PriorityBadge value={v} /> },
  { key: 'task_type', label: 'Type', sortable: true, render: (v) => <TaskTypeCell type={String(v || 'custom')} /> },
  { key: 'description', label: 'Description', render: (v) => {
    const s = String(v || '');
    return s.length > 60 ? <span title={s}>{s.slice(0, 60)}…</span> : s;
  }},
  { key: 'due_date', label: 'Due Date', sortable: true, render: (v) => <DueDateChip value={v} /> },
  // Combined "Mileage Progress" replaces the bare "Due Miles" cell so the
  // operator can see how close each truck is to the next service without
  // doing the arithmetic in their head.
  { key: 'due_miles', label: 'Mileage', render: (_v, row) => <MileageProgress row={row as MaintenanceTask} /> },
  // Engine-hours parallel to mileage.  Only renders something when the
  // task has a due_engine_hours threshold; one-dimension tasks (mileage-
  // only or date-only) show a blank cell.
  { key: 'due_engine_hours', label: 'Engine Hours', render: (_v, row) => <EngineHoursProgress row={row as MaintenanceTask} /> },
  { key: 'status', label: 'Status', sortable: true, render: (v) => <StatusBadge status={String(v)} /> },
  { key: 'updated_at', label: 'Updated', sortable: true, render: (v) => v ? new Date(String(v)).toLocaleDateString() : '—' },
];

// ── Main component ─────────────────────────────────────────────

export default function Tasks() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<MaintenanceTask | null>(null);
  // Service-history modal — null when closed; vehicle_name when open.
  // Kept separate from ``selected`` so the user can open history without
  // losing their place in the edit sidebar.
  const [historyVehicle, setHistoryVehicle] = useState<string | null>(null);
  // View mode toggle: list (default) or calendar.  Persisted to
  // localStorage so a fleet manager who lives in calendar view doesn't
  // need to flip every session.
  const [viewMode, setViewMode] = useState<'list' | 'calendar'>(() => {
    try {
      const v = localStorage.getItem('4truck.maintenance.viewMode');
      return v === 'calendar' ? 'calendar' : 'list';
    } catch { return 'list'; }
  });
  useEffect(() => {
    try { localStorage.setItem('4truck.maintenance.viewMode', viewMode); } catch { /* ignore */ }
  }, [viewMode]);
  // Bulk selection — list of task ids the user has multi-selected for a
  // batch operation.  Cleared whenever the visible task list changes
  // (filter chip flip, refetch) so stale ids never get sent to the
  // server.  Kept as Set for O(1) toggle.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // DOT binder filter dialog — null when closed; the dialog state
  // collects window + vehicle filter and submits to the export route.
  const [binderDialogOpen, setBinderDialogOpen] = useState(false);
  const [binderDays, setBinderDays] = useState('365');
  const [binderVehicle, setBinderVehicle] = useState('');
  const [binderGenerating, setBinderGenerating] = useState(false);
  const [saving, setSaving] = useState(false);

  // Keyboard accessibility: Escape closes the open detail sidebar,
  // history modal, or DOT binder dialog (whichever is on top).
  // Native pattern — sighted users expect this on every modal/drawer.
  // selectedIds tracked separately — bulk-action bar stays visible on
  // Escape (user might want to refine the selection, not abandon it);
  // cleared via the dedicated Clear button instead.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // Close the topmost surface first so chained dismissals (e.g.
      // binder dialog opened from inside an open sidebar) unwind one
      // layer at a time.
      if (binderDialogOpen) { setBinderDialogOpen(false); return; }
      if (historyVehicle)   { setHistoryVehicle(null); return; }
      if (selected)         { setSelected(null); return; }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selected, historyVehicle, binderDialogOpen]);

  // Add form
  const [fVehicle, setFVehicle] = useState('');
  const [fType, setFType] = useState('inspection');
  const [fDesc, setFDesc] = useState('');
  const [fDueDate, setFDueDate] = useState('');
  const [fDueMiles, setFDueMiles] = useState('');
  // Priority + engine-hours form fields.
  const [fPriority, setFPriority] = useState<Priority>('medium');
  const [fDueEngineHours, setFDueEngineHours] = useState('');
  const [fOdometer, setFOdometer] = useState<number | null>(null);
  const [fEngineHours, setFEngineHours] = useState<number | null>(null);
  const [fOdometerLoading, setFOdometerLoading] = useState(false);

  // Edit form
  const [eStatus, setEStatus] = useState('');
  const [eDesc, setEDesc] = useState('');
  const [eDueDate, setEDueDate] = useState('');
  const [eDueMiles, setEDueMiles] = useState('');
  const [ePriority, setEPriority] = useState<Priority>('medium');
  const [eDueEngineHours, setEDueEngineHours] = useState('');
  // Current odometer / engine-hours snapshot for the truck this task
  // is attached to — drives the "current: 245,678 mi" hint and lets
  // the +3k/+5k preset buttons add to the real odometer instead of to
  // whatever stale value is already in the field.
  const [eOdometer, setEOdometer] = useState<number | null>(null);
  const [eEngineHours, setEEngineHours] = useState<number | null>(null);

  // Fleet vehicle list for the vehicle picker — only fetched once the
  // add form opens (the picker is the only consumer). Cached for 60s by
  // the global QueryClient default so reopening the form is instant.
  const { data: fleetData, isLoading: fleetLoading } = useQuery({
    queryKey: ['maintenance-fleet-vehicles'],
    queryFn: () => apiJSON<{ vehicles: FleetVehicle[] }>('/vehicles?page_size=200'),
    enabled: showAdd,
  });
  const fleetVehicles = fleetData?.vehicles ?? [];

  const fetchOdometer = async (name: string) => {
    if (!name.trim()) { setFOdometer(null); setFEngineHours(null); return; }
    setFOdometerLoading(true);
    try {
      const data = await apiJSON<{
        odometer_miles: number | null;
        engine_hours: number | null;
      }>('/maintenance/odometer/' + encodeURIComponent(name.trim()));
      setFOdometer(data.odometer_miles ?? null);
      setFEngineHours(data.engine_hours ?? null);
    } catch { setFOdometer(null); setFEngineHours(null); }
    finally { setFOdometerLoading(false); }
  };

  // Open the Edit sidebar with a task.  Note the absolute → period
  // conversion: the backend stores absolute due-mileage / due-hours /
  // due-date, but the form inputs hold *periods* (intervals from
  // current).  Miles & hours are converted on submit (current +
  // period), and back-converted here so a user re-opening a task sees
  // "miles remaining" rather than the raw absolute target.
  // Date is similarly converted to "days remaining".
  const openTaskForEdit = (t: MaintenanceTask) => {
    setSelected(t);
    setEStatus(t.status);
    setEDesc(t.description);
    setEDueDate(_dueDateToPeriodDays(t.due_date));
    setEPriority(((t.priority || 'medium') as Priority));
    // Period from the task's own engine-hours snapshot (best signal
    // without a live endpoint).  When no snapshot, fall back to
    // showing the absolute value.
    const baseHours = t.last_engine_hours ?? null;
    setEEngineHours(baseHours);
    if (t.due_engine_hours && baseHours != null) {
      setEDueEngineHours(String(Math.max(0, Math.round(t.due_engine_hours - baseHours))));
    } else {
      setEDueEngineHours(t.due_engine_hours ? String(t.due_engine_hours) : '');
    }
    // Miles period requires the live odometer, which we fetch async.
    // Seed with the absolute value first; the .then() below replaces
    // it once the odometer lands.
    setEOdometer(null);
    setEDueMiles(t.due_miles ? String(t.due_miles) : '');
    if (t.vehicle_name) {
      void apiJSON<{
        odometer_miles: number | null;
        engine_hours: number | null;
      }>(
        '/maintenance/odometer/' + encodeURIComponent(t.vehicle_name),
      ).then((d) => {
        const odo = d.odometer_miles ?? null;
        setEOdometer(odo);
        if (odo != null && t.due_miles) {
          setEDueMiles(String(Math.max(0, Math.round(t.due_miles - odo))));
        }
        // Prefer the live engine-hours reading over the task's stored
        // snapshot when the warehouse has a fresher value.  Falls back
        // to last_engine_hours (already seeded above) when null.
        const liveHrs = d.engine_hours ?? null;
        if (liveHrs != null) {
          setEEngineHours(liveHrs);
          if (t.due_engine_hours) {
            setEDueEngineHours(
              String(Math.max(0, Math.round(t.due_engine_hours - liveHrs))),
            );
          }
        }
      }).catch(() => setEOdometer(null));
    }
  };

  // load the full task set once so the filter chips (overdue /
  // due-soon / pending / completed) can show accurate per-bucket counts.
  // A typical account has <500 tasks; round-tripping per chip click added
  // latency for no benefit.  The chip the user clicks just narrows what's
  // rendered client-side.
  const { data: tasksData, isLoading: loading, error: queryError } = useQuery({
    queryKey: ['maintenance-tasks'],
    queryFn: () => apiJSON<{ tasks: MaintenanceTask[] }>('/maintenance/tasks?page_size=200'),
    placeholderData: (prev) => prev,
  });
  const allTasks = tasksData?.tasks ?? [];
  const fetchError = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['maintenance-tasks'] });

  // Bucket each task once per data change so we can both filter and
  // count without re-walking the list on every render of a chip.  Same
  // boundary as ``DueDateChip`` (calendar-day basis) so a "due today"
  // task lands in the due-soon bucket and not overdue.
  const buckets = useMemo(() => {
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const overdue: MaintenanceTask[] = [];
    const dueSoon: MaintenanceTask[] = [];
    const pending: MaintenanceTask[] = [];
    const completed: MaintenanceTask[] = [];
    const cancelled: MaintenanceTask[] = [];
    for (const t of allTasks) {
      if (t.status === 'completed') { completed.push(t); continue; }
      if (t.status === 'cancelled') { cancelled.push(t); continue; }
      // Classify by overdue → due_soon → pending in priority order.
      // Each task lands in EXACTLY ONE bucket so the chip counts sum to
      // ``all``.  Previously every non-completed task was also pushed
      // into ``pending`` and a flagged-overdue task showed up in both
      // the Overdue and Pending chips, making the counts overlap.
      const isOverdueStatus = t.status === 'overdue';
      let placed = false;
      if (t.due_date) {
        const due = new Date(t.due_date);
        if (!Number.isNaN(due.getTime())) {
          const startOfDue = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
          const days = Math.round((startOfDue - startOfToday) / 86_400_000);
          if (days < 0 || isOverdueStatus) { overdue.push(t); placed = true; }
          else if (days <= 7) { dueSoon.push(t); placed = true; }
        }
      }
      if (!placed && isOverdueStatus) { overdue.push(t); placed = true; }
      if (!placed) { pending.push(t); }
    }
    return { overdue, dueSoon, pending, completed, cancelled };
  }, [allTasks]);

  // statusFilter values: '' (all), 'overdue', 'due_soon', 'pending',
  // 'completed', 'cancelled' — these are now client-side bucket keys,
  // not API query params.
  const tasks = useMemo(() => {
    if (statusFilter === 'overdue')   return buckets.overdue;
    if (statusFilter === 'due_soon')  return buckets.dueSoon;
    if (statusFilter === 'pending')   return buckets.pending;
    if (statusFilter === 'completed') return buckets.completed;
    if (statusFilter === 'cancelled') return buckets.cancelled;
    return allTasks;
  }, [statusFilter, allTasks, buckets]);

  // Clear selection whenever the visible list changes (filter switch
  // or refetch).  Stale ids would otherwise sit in state and could
  // target rows that are no longer visible — confusing UX and a small
  // risk of bulk-acting on the wrong tasks.
  useEffect(() => {
    setSelectedIds(prev => {
      if (prev.size === 0) return prev;
      const visible = new Set(tasks.map(t => t.id));
      const next = new Set<number>();
      for (const id of prev) if (visible.has(id)) next.add(id);
      return next.size === prev.size ? prev : next;
    });
  }, [tasks]);

  // Final columns array — prepended checkbox column when in list mode,
  // omitted in calendar mode (which has its own click target per chip).
  const columns: AnyColumn[] = useMemo(() => {
    const allVisibleIds = tasks.map(t => t.id);
    const allSelected = allVisibleIds.length > 0
      && allVisibleIds.every(id => selectedIds.has(id));
    const checkboxCol: AnyColumn = {
      key: '_select',
      label: '',
      sortable: false,
      render: (_v, row) => {
        const t = row as MaintenanceTask;
        const checked = selectedIds.has(t.id);
        return (
          <input
            type="checkbox"
            checked={checked}
            // Stop click propagation so the row-click → edit-sidebar
            // doesn't fire when the user is just toggling selection.
            onClick={e => e.stopPropagation()}
            onChange={e => {
              setSelectedIds(prev => {
                const next = new Set(prev);
                if (e.target.checked) next.add(t.id);
                else next.delete(t.id);
                return next;
              });
            }}
            className="cursor-pointer accent-primary"
            aria-label={`Select task ${t.id}`}
          />
        );
      },
    };
    // The label cell needs the "select all visible" header but
    // AnyColumn.label is just a string.  Render a special header by
    // tucking the toggle in the cell key — we approximate "select all"
    // via the parent's header bar instead, defined below in the JSX.
    // (Keeps the checkboxCol shape compatible with DataTable's
    // ColumnDef header generation.)
    void allSelected;
    return [checkboxCol, ...baseColumns];
  }, [tasks, selectedIds]);

  // Bulk action handlers — POST to the new /tasks/bulk/* routes.
  const handleBulkComplete = async () => {
    if (selectedIds.size === 0) return;
    const ok = window.confirm(
      `Mark ${selectedIds.size} task${selectedIds.size === 1 ? '' : 's'} complete?\n\n`
      + 'You will be recorded as the attester for each one. '
      + 'Recurring tasks will auto-spawn their next instance.',
    );
    if (!ok) return;
    try {
      const res = await apiJSON<{ updated: number; spawned_ids: number[] }>(
        '/maintenance/tasks/bulk/status',
        { method: 'POST', body: { task_ids: Array.from(selectedIds), status: 'completed' } },
      );
      toast.success(
        `Marked ${res.updated} complete`
        + (res.spawned_ids.length ? ` · ${res.spawned_ids.length} recurring follow-up${res.spawned_ids.length === 1 ? '' : 's'} created` : ''),
      );
      setSelectedIds(new Set());
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk update failed');
    }
  };

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    const ok = window.confirm(
      `Delete ${selectedIds.size} task${selectedIds.size === 1 ? '' : 's'}?\n\n`
      + 'This cannot be undone.',
    );
    if (!ok) return;
    try {
      const res = await apiJSON<{ deleted: number }>(
        '/maintenance/tasks/bulk/delete',
        { method: 'POST', body: { task_ids: Array.from(selectedIds) } },
      );
      toast.success(`Deleted ${res.deleted} task${res.deleted === 1 ? '' : 's'}`);
      setSelectedIds(new Set());
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk delete failed');
    }
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    // Client-side mirror of the API's model_validator: at least one
    // trigger (date / miles / engine_hours) is required so the task can
    // actually become overdue.  Fail-fast here keeps the round-trip out
    // of the common case (user just forgot to set one).
    if (!fDueDate && !fDueMiles && !fDueEngineHours) {
      setError('Set a period for date, miles, or engine hours — otherwise this task will never become overdue.');
      return;
    }
    // Convert period inputs to the absolute values the API expects.
    // Miles & hours fall back to "period as absolute" when we don't
    // know the current reading (vehicle without telemetry).
    const dueDateAbs = _periodDaysToDueDate(fDueDate) || undefined;
    const dueMilesAbs = fDueMiles
      ? (fOdometer != null
          ? Math.round(fOdometer) + Number(fDueMiles)
          : Number(fDueMiles))
      : undefined;
    // When the live engine-hours baseline is available (warehouse
    // returned a reading), submit as ``current + period``.  Without a
    // baseline, fall back to "period as absolute" — the same
    // degradation the miles path uses.
    const dueEngineHoursAbs = fDueEngineHours
      ? (fEngineHours != null
          ? Math.round(fEngineHours) + Number(fDueEngineHours)
          : Number(fDueEngineHours))
      : undefined;
    setSaving(true); setError('');
    try {
      await apiJSON('/maintenance/tasks', { method: 'POST', body: {
        vehicle_name: fVehicle,
        task_type: fType,
        description: fDesc,
        priority: fPriority,
        due_date: dueDateAbs,
        due_miles: dueMilesAbs,
        due_engine_hours: dueEngineHoursAbs,
      }});
      setShowAdd(false);
      setFVehicle(''); setFDesc(''); setFDueDate(''); setFDueMiles('');
      setFDueEngineHours(''); setFPriority('medium'); setFOdometer(null); setFEngineHours(null);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleUpdate = async () => {
    if (!selected) return;
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
    if (eStatus !== selected.status) body.status = eStatus;
    if (eDesc !== selected.description) body.description = eDesc;
    if (dueDateAbs !== (selected.due_date || '')) {
      body.due_date = dueDateAbs || null;
    }
    if (dueMilesAbs !== (selected.due_miles ?? null)) {
      body.due_miles = dueMilesAbs;
    }
    if (ePriority !== (selected.priority || 'medium')) body.priority = ePriority;
    if (dueEngineHoursAbs !== (selected.due_engine_hours ?? null)) {
      body.due_engine_hours = dueEngineHoursAbs;
    }
    if (Object.keys(body).length === 0) return;
    // Completion confirmation — flipping a task to "completed" stamps
    // the current user as the attester (server-side, see the API route
    // handler).  Make that explicit so the user understands they're
    // signing off, not just updating a field.  Matches the
    // window.confirm pattern used elsewhere in the dashboard
    // (PoiLayerPanel, Coaching).
    if (body.status === 'completed' && selected.status !== 'completed') {
      const ok = window.confirm(
        'Mark this task complete?\n\n'
        + 'This will record you as the attester for DOT audit purposes. '
        + 'The completion timestamp and your identity are stored permanently.',
      );
      if (!ok) return;
    }
    setSaving(true); setError('');
    try {
      const res = await apiJSON<{ ok: boolean; spawned_id?: number | null }>(
        '/maintenance/tasks/' + selected.id, { method: 'PUT', body },
      );
      // Surface recurring auto-spawn so users see the chain is alive.
      // Quietly skips when the parent had no recurrence interval.
      if (res?.spawned_id) {
        toast.success(`Marked complete — next occurrence created (#${res.spawned_id}).`);
      }
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleDelete = async (id: number) => {
    // Delete is irreversible — every other destructive action in this
    // file already prompts (bulk-delete at L283, mark-overdue at L307,
    // export-csv-bulk at L373) so this button was the odd one out.
    // A misclick on a maintenance row could wipe a service record the
    // shop later needs for the DOT binder, so the friction is cheap.
    const taskLabel = selected?.task_type
      ? selected.task_type.replace(/_/g, ' ')
      : `task #${id}`;
    const vehicleLabel = selected?.vehicle_name ? ` for #${selected.vehicle_name}` : '';
    const ok = window.confirm(
      `Delete the "${taskLabel}"${vehicleLabel}?\n\nThis can't be undone.`,
    );
    if (!ok) return;
    try {
      await apiJSON('/maintenance/tasks/' + id, { method: 'DELETE' });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  // DOT compliance binder download.  Same auth-aware blob pattern as
  // exportTasksCsv but routes through the dialog so the user can pick
  // window + vehicle before generation kicks off (heavy compute on
  // large fleets — we don't want a single button to drop a 200-page
  // PDF without warning).
  const generateDotBinder = async () => {
    setBinderGenerating(true);
    try {
      const params = new URLSearchParams({ days: binderDays });
      if (binderVehicle.trim()) params.set('vehicle', binderVehicle.trim());
      const res = await apiFetch(
        `/maintenance/dot-binder?${params.toString()}`,
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
      const vehiclePart = binderVehicle.trim() ? `-${binderVehicle.trim()}` : '';
      a.href = url;
      a.download = `dot-binder-${datePart}${vehiclePart}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success('DOT binder downloaded');
      setBinderDialogOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Binder generation failed');
    } finally {
      setBinderGenerating(false);
    }
  };

  // CSV download.  The browser's native download UI doesn't carry our
  // Bearer token, so a plain <a href="/api/…csv"> would 401.  Pull the
  // bytes via the auth-aware client, blob them, and trigger a synthetic
  // download.  Same pattern used by the reports export elsewhere in the
  // app.
  const exportTasksCsv = async () => {
    try {
      const res = await apiFetch('/maintenance/tasks.csv');
      if (!res.ok) {
        toast.error(`Export failed: ${res.statusText}`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `maintenance-tasks-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed');
    }
  };

  return (
    <div>
      <PageHeader
        icon={Wrench}
        title={t('pages.maintenance_title')}
        description={t('pages.maintenance_desc')}
        actions={
          <div className="flex items-center gap-2">
            {/* List / Calendar toggle.  Persists to localStorage so a
                fleet manager who lives in calendar view stays there. */}
            <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md" role="group" aria-label="View mode">
              <button
                type="button"
                onClick={() => setViewMode('list')}
                aria-pressed={viewMode === 'list'}
                aria-label="List view"
                title="List view"
                className={`p-1.5 rounded ${viewMode === 'list'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'}`}
              >
                <List size={14} />
              </button>
              <button
                type="button"
                onClick={() => setViewMode('calendar')}
                aria-pressed={viewMode === 'calendar'}
                aria-label="Calendar view"
                title="Calendar view"
                className={`p-1.5 rounded ${viewMode === 'calendar'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'}`}
              >
                <CalendarDays size={14} />
              </button>
            </div>
            {/* CSV export — hits /api/maintenance/tasks.csv with the
                current Bearer token via apiFetch, then forces a download
                by injecting a temporary <a> with object-URL.  Native
                browser download doesn't carry our JWT, so we can't just
                <a href="…">. */}
            <button
              type="button"
              onClick={() => exportTasksCsv()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
              title="Download maintenance tasks as CSV"
            >
              <Download size={13} />
              Export CSV
            </button>
            {/* DOT binder — opens a dialog so the user can pick a
                window + optional single vehicle before the heavy PDF
                gen runs.  Sits next to Export CSV since both are
                compliance-flavoured exports. */}
            <button
              type="button"
              onClick={() => setBinderDialogOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
              title="Generate a DOT compliance binder PDF"
            >
              <FileText size={13} />
              DOT Binder
            </button>
            <button onClick={() => { setShowAdd(!showAdd); setError(''); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition">
              <Plus size={13} />
              {showAdd ? 'Cancel' : 'New task'}
            </button>
          </div>
        }
      />

      {/* : filter chip row with derived counts.  Click cycles
          the active chip; clicking the active chip clears back to "All".
          Counts pull from the same memo as the rendered list, so they
          stay accurate even when the underlying query refetches. */}
      <div className="flex flex-wrap gap-2 mb-4">
        {([
          { key: '',          label: 'All',       count: allTasks.length,            dot: 'bg-muted-foreground/40' },
          { key: 'overdue',   label: 'Overdue',   count: buckets.overdue.length,     dot: 'bg-red-500'    },
          { key: 'due_soon',  label: 'Due Soon',  count: buckets.dueSoon.length,     dot: 'bg-orange-500' },
          { key: 'pending',   label: 'Pending',   count: buckets.pending.length,     dot: 'bg-blue-500'   },
          { key: 'completed', label: 'Completed', count: buckets.completed.length,   dot: 'bg-green-500'  },
        ] as const).map(chip => {
          const active = statusFilter === chip.key;
          return (
            <button
              key={chip.key || 'all'}
              onClick={() => setStatusFilter(active ? '' : chip.key)}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition border ${
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-card hover:bg-muted text-foreground border-border'
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${chip.dot}`} />
              {chip.label}
              <span className={`tabular-nums ${active ? 'opacity-80' : 'text-muted-foreground'}`}>
                {chip.count}
              </span>
            </button>
          );
        })}
      </div>

      {(error || fetchError) && (
        <div className="mb-3">
          <ErrorState message={error || fetchError} />
        </div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Vehicle</span>
            <VehiclePicker
              value={fVehicle}
              vehicles={fleetVehicles}
              loading={fleetLoading}
              onChange={(name, vehicle) => {
                setFVehicle(name);
                setFOdometer(null);
                setFEngineHours(null);
                if (vehicle) fetchOdometer(vehicle.name);
              }}
            />
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Type</span>
            <select value={fType} onChange={e => setFType(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
              {TASK_TYPE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Description</span>
            {/* minLength=3 mirrors the API's Pydantic constraint so users
                fail-fast at the field level instead of via a toast. */}
            <input
              required
              minLength={3}
              maxLength={500}
              value={fDesc}
              onChange={e => setFDesc(e.target.value)}
              placeholder="e.g. Oil + filter, full synthetic 15W-40"
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Priority</span>
            <select
              value={fPriority}
              onChange={e => setFPriority(e.target.value as Priority)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring capitalize"
            >
              {PRIORITY_OPTIONS.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Due Date</span>
            <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
              <span title="Today's date — the period below is added to this">Today: {_todayLabel()}</span>
              <span className="text-primary">
                Due: {fDueDate ? _formatDate(_periodDaysToDueDate(fDueDate)) : '—'}
              </span>
            </div>
            <DaysPicker value={fDueDate} onChange={setFDueDate} />
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Due Miles</span>
            <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
              <span>
                Current:{' '}
                {fOdometerLoading
                  ? 'fetching…'
                  : fOdometer != null
                    ? `${Math.round(fOdometer).toLocaleString()} mi`
                    : '—'}
              </span>
              <span className="text-primary">
                Due:{' '}
                {fDueMiles
                  ? `${(fOdometer != null ? Math.round(fOdometer) + Number(fDueMiles) : Number(fDueMiles)).toLocaleString()} mi`
                  : '—'}
              </span>
            </div>
            <MilesPicker
              value={fDueMiles}
              onChange={setFDueMiles}
              mode={fOdometer != null ? 'period' : 'absolute'}
            />
          </label>
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Due Engine Hours</span>
            {fEngineHours != null ? (
              <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
                <span>Current: {Math.round(fEngineHours).toLocaleString()} h</span>
                <span className="text-primary">
                  Due:{' '}
                  {fDueEngineHours
                    ? `${(Math.round(fEngineHours) + Number(fDueEngineHours)).toLocaleString()} h`
                    : '—'}
                </span>
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground mb-1">
                {fOdometerLoading
                  ? 'fetching telemetry…'
                  : fVehicle
                    ? null  /* picker now renders its own no-telemetry hint */
                    : 'Pick a vehicle to see its current engine hours.'}
              </p>
            )}
            <HoursPicker
              value={fDueEngineHours}
              onChange={setFDueEngineHours}
              mode={fEngineHours != null ? 'period' : 'absolute'}
            />
          </label>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="w-full px-4 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition">
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </form>
      )}

      {loading && tasks.length === 0 ? (
        <TableSkeleton rows={6} cols={7} />
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title={statusFilter ? `No ${statusFilter.replace(/_/g, ' ')} tasks` : 'No maintenance tasks yet'}
          description="Create your first task — set a due date, due miles, or both, and we'll alert you as it approaches."
          action={
            <button onClick={() => setShowAdd(true)} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition">
              <Plus size={13} />
              New task
            </button>
          }
        />
      ) : viewMode === 'calendar' ? (
        // Calendar view — same dataset, different visualisation.  Click
        // a chip to open the edit sidebar with that task selected
        // (matches the list-row click affordance).
        <CalendarMonth
          tasks={tasks}
          onTaskClick={openTaskForEdit}
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={tasks as unknown as Record<string, unknown>[]}
            searchKey="vehicle_name"
            onRowClick={(row) => openTaskForEdit(row as unknown as MaintenanceTask)}
          />
          {/* Result count footer.  Always shows the filtered count
              followed by what's hidden, so the user understands they're
              not seeing the whole list when a chip is active. */}
          <p className="text-xs text-muted-foreground mt-2">
            {tasks.length} task{tasks.length !== 1 ? 's' : ''}
            {statusFilter && allTasks.length !== tasks.length
              ? ` · ${allTasks.length - tasks.length} hidden by filter`
              : ''}
            {!statusFilter && buckets.overdue.length > 0
              ? ` · ${buckets.overdue.length} overdue`
              : ''}
          </p>
        </>
      )}

      {/* Bulk-action floating bar — only shown when 1+ rows are
          selected in list mode.  Position fixed at the bottom so it
          stays visible even when the user scrolls the table.  The
          handlers prompt for confirmation before hitting the backend
          (same pattern as the single-task completion dialog). */}
      {viewMode === 'list' && selectedIds.size > 0 && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-4 py-2 bg-card border border-border rounded-full shadow-lg">
          <span className="text-sm font-medium">
            {selectedIds.size} selected
          </span>
          <span className="text-muted-foreground">·</span>
          <button
            type="button"
            onClick={handleBulkComplete}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-green-600 hover:bg-green-700 text-white rounded-full text-xs font-medium transition"
          >
            <CheckSquare size={13} />
            Mark complete
          </button>
          <button
            type="button"
            onClick={handleBulkDelete}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-destructive/80 hover:bg-destructive text-destructive-foreground rounded-full text-xs font-medium transition"
          >
            <Trash2 size={13} />
            Delete
          </button>
          <button
            type="button"
            onClick={() => setSelectedIds(new Set())}
            className="text-muted-foreground hover:text-foreground p-1"
            aria-label="Clear selection"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {selected && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={() => setSelected(null)}>
          <div className="w-96 bg-card border-l border-border p-6 overflow-y-auto" onClick={e => e.stopPropagation()}>
            {/* Header: vehicle + task type for disambiguation when
                multiple tabs/drawers are juggled. History sits as a
                clear chip rather than a faint inline link. */}
            <div className="flex items-start justify-between gap-2 mb-4">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold truncate">
                  {selected.vehicle_name}
                  <span className="text-muted-foreground font-normal">
                    {' · '}
                    <span className="capitalize">{selected.task_type.replace(/_/g, ' ')}</span>
                  </span>
                </h2>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  type="button"
                  onClick={() => setHistoryVehicle(selected.vehicle_name)}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-muted hover:bg-muted/80 border border-border rounded-md transition"
                  title="View service history"
                >
                  <History size={12} />
                  History
                </button>
                <button onClick={() => setSelected(null)} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1"><X size={16} /></button>
              </div>
            </div>
            {/* Auto-renewal breadcrumb — shown at the top of the
                sidebar so users instantly understand why this task
                exists when it was machine-created.  Only renders when
                ``spawned_from_id`` is set (legacy or user-created tasks
                show nothing here). */}
            {selected.spawned_from_id && (
              <div className="mb-4 px-3 py-2 bg-blue-500/10 border border-blue-500/30 rounded text-xs text-blue-700 dark:text-blue-400 inline-flex items-center gap-1.5">
                <span aria-hidden>↻</span>
                Auto-renewed from task #{selected.spawned_from_id}
              </div>
            )}
            {/* Work-order cross-link — when this task was closed by a
                shop visit, surface a clickable badge that opens the
                work-order page.  Uses ``window.location`` to traverse
                the SPA so the maintenance sidebar can close cleanly
                without router context plumbing.  Read-only — editing
                lives on the work-order page itself. */}
            {selected.work_order_id && (
              <a
                href={`/work-orders/${selected.work_order_id}`}
                className="mb-4 px-3 py-2 bg-green-500/10 border border-green-500/30 rounded text-xs text-green-700 dark:text-green-400 inline-flex items-center gap-1.5 hover:bg-green-500/20"
              >
                <span aria-hidden>📄</span>
                Closed by Work Order #{selected.work_order_id}
              </a>
            )}
            {/* Immutable facts only — editable fields (status,
                description, due triggers, priority) live in the form
                below so the same value never appears twice. */}
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-muted-foreground">Created</dt><dd>{_formatDate(selected.created_at)}</dd></div>
              {selected.completed_at && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Completed</dt>
                  <dd>{_formatDate(selected.completed_at)}</dd>
                </div>
              )}
              {selected.recur_interval_days && <div className="flex justify-between"><dt className="text-muted-foreground">Recurrence</dt><dd>Every {selected.recur_interval_days} days</dd></div>}
              {/* Attestation: surfaces the audit trail.  Renders inside
                  the dl block so it's visually grouped with the other
                  task metadata.  Multi-line because the name+date can
                  wrap on narrow sidebars. */}
              {selected.attested_at && (
                <div className="pt-2 border-t border-border/50">
                  <dt className="text-muted-foreground text-xs mb-1">Attestation</dt>
                  <dd className="text-xs text-green-700 dark:text-green-400">
                    <span aria-hidden>✓</span>{' '}
                    <span className="font-medium">
                      {selected.attested_by_name || `user ${selected.attested_by}`}
                    </span>
                    {' '}confirmed on{' '}
                    {new Date(selected.attested_at).toLocaleString()}
                  </dd>
                </div>
              )}
            </dl>
            <div className="space-y-3">
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Status</span>
                <select value={eStatus} onChange={e => setEStatus(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                  {STATUS_OPTIONS.map(s => <option key={s} value={s}>{STATUS_LABELS[s] || s}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Priority</span>
                <select
                  value={ePriority}
                  onChange={e => setEPriority(e.target.value as Priority)}
                  className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring capitalize"
                >
                  {PRIORITY_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
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
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Due Date</span>
                <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
                  <span title="Today's date — the period below is added to this">Today: {_todayLabel()}</span>
                  <span className="text-primary">
                    Due: {eDueDate ? _formatDate(_periodDaysToDueDate(eDueDate)) : '—'}
                  </span>
                </div>
                <DaysPicker value={eDueDate} onChange={setEDueDate} />
              </label>
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Due Miles</span>
                <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
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
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Due Engine Hours</span>
                {eEngineHours != null ? (
                  <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground mb-1">
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
              {/* Primary actions: Cancel + Update side-by-side so the
                  back-out option is obvious even with Esc/click-outside
                  available. */}
              <div className="flex items-center gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  disabled={saving}
                  className="px-3 py-2 bg-muted hover:bg-muted/80 border border-border rounded text-sm font-medium transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdate}
                  disabled={saving}
                  className="flex-1 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium transition"
                >
                  {saving ? 'Saving...' : 'Update Task'}
                </button>
              </div>
              {/* Danger zone — visually separated so a misclick on the
                  red button is harder.  The confirm() in handleDelete
                  is the real guard, this is the second line of defence. */}
              <div className="mt-4 pt-3 border-t border-border/60">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5">
                  Danger zone
                </p>
                <button
                  onClick={() => handleDelete(selected.id)}
                  className="w-full py-2 bg-destructive/80 hover:bg-destructive rounded text-sm font-medium transition inline-flex items-center justify-center gap-1.5"
                >
                  <Trash2 size={13} />
                  Delete Task
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {historyVehicle && (
        <ServiceHistoryModal
          vehicleName={historyVehicle}
          onClose={() => setHistoryVehicle(null)}
        />
      )}

      {binderDialogOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-50 flex justify-center items-start pt-24"
          onClick={() => !binderGenerating && setBinderDialogOpen(false)}
        >
          <div
            className="w-[420px] bg-card border border-border rounded-xl p-6 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold inline-flex items-center gap-2">
                  <FileText size={18} className="text-muted-foreground" />
                  Generate DOT Binder
                </h2>
                <p className="text-xs text-muted-foreground mt-1">
                  One printable PDF covering maintenance + work orders +
                  attestations for the selected window.
                </p>
              </div>
              <button
                onClick={() => !binderGenerating && setBinderDialogOpen(false)}
                aria-label="Close"
                className="text-muted-foreground hover:text-foreground p-1"
              >
                <X size={16} />
              </button>
            </div>

            <div className="space-y-3 mb-5">
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">
                  Coverage window
                </span>
                <select
                  value={binderDays}
                  onChange={e => setBinderDays(e.target.value)}
                  disabled={binderGenerating}
                  className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
                >
                  <option value="30">Last 30 days</option>
                  <option value="90">Last 90 days</option>
                  <option value="180">Last 6 months</option>
                  <option value="365">Last 12 months (DOT default)</option>
                  <option value="730">Last 24 months</option>
                </select>
              </label>
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">
                  Single vehicle (optional)
                </span>
                <input
                  type="text"
                  value={binderVehicle}
                  onChange={e => setBinderVehicle(e.target.value)}
                  disabled={binderGenerating}
                  placeholder="Leave empty for the whole fleet"
                  className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  Useful when only one truck is being audited or sold.
                </p>
              </label>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setBinderDialogOpen(false)}
                disabled={binderGenerating}
                className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium border border-border"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={generateDotBinder}
                disabled={binderGenerating}
                className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
              >
                <FileText size={13} />
                {binderGenerating ? 'Generating PDF…' : 'Generate PDF'}
              </button>
            </div>

            {binderGenerating && (
              <p className="text-xs text-muted-foreground mt-3 text-center">
                Compiling per-vehicle records — this may take up to 30 seconds
                for large fleets.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
