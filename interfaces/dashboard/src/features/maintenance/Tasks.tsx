import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { usePreference } from '../../preferences';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Wrench, Plus, X, History, FileText,
  List, CalendarDays, Trash2, CheckSquare, BellOff, Bell, Archive, RefreshCw,
  Paperclip, Image as ImageIcon, Upload, ClipboardList,
} from 'lucide-react';
import { Sheet, SheetContent, SheetBody } from '../../components/ui/sheet';
import { apiJSON, apiFetch } from '../../api/client';
import { usePublishContext } from '../ai/PageContext';
import DataGrid, { type DataGridSegment, type BulkAction } from '../../components/datagrid';
import {
  useMaintenanceTasksQuery, makeUrgencyClassifier, classifyTaskBuckets,
} from './useMaintenanceTasks';
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
  PRIORITY_OPTIONS,
  type Priority,
} from './badges';
import ServiceTaskPicker from '../service-tasks/ServiceTaskPicker';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { VehiclePicker, MilesPicker, HoursPicker, DaysPicker, type VehicleSummary } from './pickers';
import { CalendarMonth } from './CalendarMonth';
import { toneClasses } from '@/lib/status';
import { undoableToast } from '../../lib/undoable';
import { ServiceHistoryModal } from './ServiceHistoryModal';
import { TaskActivityDialog } from './TaskActivityDialog';
import { TemplatesModal } from './TemplatesModal';
import type { MaintenanceTemplate } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatDay } from '../../utils/datetime';
import { useTaskLabels } from '../service-tasks/useTaskLabels';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { makeColumns } from './columns';
import {
  STATUS_OPTIONS, STATUS_LABELS, PRIORITY_ITEMS, STATUS_ITEMS, CLOSED_STATUSES,
  _periodDaysToDueDate, _dueDateToPeriodDays, _formatDate, _todayLabel,
  _isOverdueOrApproaching,
} from './taskFields';

const TASK_SEGMENTS: DataGridSegment[] = [
  {
    key: 'active',
    label: 'Active',
    match: (r) => !CLOSED_STATUSES.has(String(r.status ?? '')),
  },
  {
    key: 'archive',
    label: 'Archive',
    match: (r) => CLOSED_STATUSES.has(String(r.status ?? '')),
  },
];


// ── Main component ─────────────────────────────────────────────

export default function Tasks() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tz = useTimezone();
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [selected, setSelected] = useState<MaintenanceTask | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Service-history modal — null when closed; vehicle_name when open.
  // Kept separate from ``selected`` so the user can open history without
  // losing their place in the edit sidebar.
  const [historyVehicle, setHistoryVehicle] = useState<string | null>(null);
  // View mode toggle: list (default) or calendar.  Persisted to
  // localStorage so a fleet manager who lives in calendar view doesn't
  // need to flip every session.
  // Per-user preference (synced): a fleet manager who lives in calendar
  // view shouldn't re-pick it on another machine.  Storage + default +
  // the legacy '4truck.maintenance.viewMode' key live in the registry.
  const { value: viewMode, setValue: setViewMode } = usePreference('maintenance.viewMode');
  // Bulk selection — list of task ids the user has multi-selected for a
  // batch operation.  Cleared whenever the visible task list changes
  // (filter chip flip, refetch) so stale ids never get sent to the
  // server.  Kept as Set for O(1) toggle.
  // Mirror of DataGrid's bulk selection — DataGrid owns the checkbox
  // set + the action bar now; this copy exists only to feed the AI
  // page-context (usePublishContext below).
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  // Keyboard accessibility: Escape closes the open detail sidebar or
  // history modal (whichever is on top).  Native pattern — sighted
  // users expect this on every modal/drawer.  selectedIds tracked
  // separately — bulk-action bar stays visible on Escape (user might
  // want to refine the selection, not abandon it); cleared via the
  // dedicated Clear button instead.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (historyVehicle)   { setHistoryVehicle(null); return; }
        if (selected)         { setSelected(null); return; }
        return;
      }
      // 'n' opens the New Task form — but only when the user isn't
      // typing into an input/textarea/contenteditable, otherwise it
      // would eat the letter.  Skip while a modal/drawer is open so it
      // doesn't conflict with whatever the user is doing in there.
      if (e.key === 'n' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tgt = document.activeElement as HTMLElement | null;
        const tag = tgt?.tagName?.toLowerCase();
        const isEditing = tag === 'input' || tag === 'textarea' || tag === 'select'
          || (tgt?.isContentEditable ?? false);
        if (isEditing) return;
        if (selected || historyVehicle) return;
        setShowAdd(s => !s);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selected, historyVehicle]);

  // Add form
  const [fVehicle, setFVehicle] = useState('');
  // Company the picked vehicle belongs to.  Persists through the POST
  // body as ``company_code`` so the task row knows WHICH "103" the
  // user chose when two companies under the account share a vehicle
  // name.  Cleared whenever the vehicle text is cleared or no
  // matching fleet entry is found.
  const [fCompany, setFCompany] = useState('');
  const [fType, setFType] = useState('inspection');

  // Copilot page context — tells the assistant what's on screen so
  // "why is this overdue?" / "these tasks" resolve to this page.  One
  // hook call, in the feature's own file; no shared code touched.
  usePublishContext({
    feature: 'maintenance',
    label: t('nav.maintenance'),
    filters: { company: fCompany || undefined },
    selectedIds,
    focus: selected
      ? { kind: 'maintenance task', id: selected.id, label: `Truck ${selected.vehicle_name}` }
      : undefined,
  });
  const [fDesc, setFDesc] = useState('');
  const [fDueDate, setFDueDate] = useState('');
  const [fDueMiles, setFDueMiles] = useState('');
  // Priority + engine-hours form fields.
  const [fPriority, setFPriority] = useState<Priority>('medium');
  const [fDueEngineHours, setFDueEngineHours] = useState('');
  const [fOdometer, setFOdometer] = useState<number | null>(null);
  const [fEngineHours, setFEngineHours] = useState<number | null>(null);
  const [fOdometerLoading, setFOdometerLoading] = useState(false);
  // Multi-vehicle bulk-create mode — when on, the single VehiclePicker
  // is replaced by a chip-list multi-select and the submit hits the
  // bulk-create endpoint.  Useful for "onboard 10 trucks, all need
  // the same oil schedule".
  const [fMultiMode, setFMultiMode] = useState(false);
  const [fMultiVehicles, setFMultiVehicles] = useState<Set<string>>(new Set());

  // Single-trigger mode for the add form — new users were confused
  // by three trigger inputs (date/miles/hours) all visible at once.
  // We now show one at a time; the segmented control above the
  // trigger picks which one.
  type TriggerMode = 'date' | 'miles' | 'hours';
  const [fTriggerMode, setFTriggerMode] = useState<TriggerMode>('date');
  // Single "repeat after completion" checkbox — replaces three
  // recurrence inputs.  When checked, the next auto-spawned task
  // reuses the SAME interval the user entered for the current trigger
  // (date period → recur_interval_days, miles period → recur_interval_miles,
  // hours period → recur_interval_engine_hours).  Mirrors the trigger
  // mode so the user only thinks about one number per task.
  const [fRepeat, setFRepeat] = useState(false);
  // Separate recurrence-period input on the create form — mirrors the
  // edit drawer.  Defaults to the due-period value when the user
  // flips the checkbox on; freely editable so an operator can set
  // "due in 39,250 mi, repeat every 40,000 mi".
  const [fRecurValue, setFRecurValue] = useState('');

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

  // Re-seed the Repeat checkbox whenever the active edit-trigger mode
  // changes — the loaded task's per-dimension recurrence is the source
  // of truth, so switching from Date → Miles reveals whatever the
  // existing miles-recurrence state was without mixing dimensions.
  useEffect(() => {
    if (!selected) return;
    setERepeat(
      eTriggerMode === 'date'  ? selected.recur_interval_days != null
      : eTriggerMode === 'miles' ? selected.recur_interval_miles != null
      : selected.recur_interval_engine_hours != null,
    );
    // Re-seed the recur input from the row's stored interval for the
    // active trigger.  Falls back to '' when no interval is set;
    // the checkbox handler below seeds from the due-period when the
    // user flips it on without a pre-existing interval.
    const stored =
      eTriggerMode === 'date'  ? selected.recur_interval_days
      : eTriggerMode === 'miles' ? selected.recur_interval_miles
      : selected.recur_interval_engine_hours;
    setERecurValue(stored != null ? String(stored) : '');
  }, [eTriggerMode, selected]);

  // Cost is held as a dollars string in the form (free-text decimal),
  // converted to integer cents on submit.  Vendor is plain text.
  const [eCost, setECost] = useState('');
  const [eVendor, setEVendor] = useState('');
  // Current odometer / engine-hours snapshot for the truck this task
  // is attached to — drives the "current: 245,678 mi" hint and lets
  // the +3k/+5k preset buttons add to the real odometer instead of to
  // whatever stale value is already in the field.
  const [eOdometer, setEOdometer] = useState<number | null>(null);
  const [eEngineHours, setEEngineHours] = useState<number | null>(null);

  // Fleet vehicle list for the vehicle picker — only fetched once the
  // add form opens (the picker is the only consumer). Cached for 60s by
  // the global QueryClient default so reopening the form is instant.
  // Walks all pages — backend caps page_size at 200, so the picker
  // used to silently truncate on fleets >200 vehicles.  Sequential
  // paging keeps the simple-fleet case (<200) at one round-trip.
  const { data: vehiclesData, isLoading: fleetLoading } = useQuery({
    queryKey: ['maintenance-vehicles'],
    queryFn: async () => {
      const all: VehicleSummary[] = [];
      let page = 1;
      while (true) {
        const res = await apiJSON<{
          vehicles: VehicleSummary[];
          total_pages: number;
        }>(`/vehicles?page_size=200&page=${page}`);
        all.push(...(res.vehicles ?? []));
        if (page >= (res.total_pages ?? 1)) break;
        page++;
      }
      return { vehicles: all };
    },
    enabled: showAdd,
  });
  const vehicleList = vehiclesData?.vehicles ?? [];

  // Templates dropdown — fetched whenever the add form is open so
  // applying a template doesn't need an extra round-trip.  Same cache
  // key the TemplatesModal uses, so editing in the modal refreshes
  // here automatically.
  const { data: templatesData } = useQuery({
    queryKey: ['maintenance-templates'],
    queryFn: () => apiJSON<{ templates: MaintenanceTemplate[] }>('/maintenance/templates'),
    enabled: showAdd,
  });
  const templates = templatesData?.templates ?? [];
  // Items for the apply-template Select — id coerced to a string value
  // (base-ui Select requires string values); the handler coerces back.
  const templateItems = useMemo(
    () => templates.map((t) => ({ value: String(t.id), label: t.name })),
    [templates],
  );

  // Task display labels come from the account's service_tasks list —
  // the SSOT — replacing the deprecated /maintenance/task-types shim
  // (which served customs only; standards leaned on a hardcoded map
  // that had drifted).  Archived included: history keeps its label.
  const { byValue: customTypeLabelByValue } = useTaskLabels();
  // Inline task creation is gated by can_service_tasks — the picker's
  // backend gate.  Offering the "+ Add" option to a role the server
  // will 403 teaches the permission by failure.
  const { has: hasPerm } = useViewPermissions();
  const canCreateTasks = hasPerm('can_service_tasks');

  // Apply a template's defaults into the open add-form fields.  Only
  // touches the fields the template actually sets, so the user can
  // tweak one field after applying without losing the others.
  const applyTemplate = (t: MaintenanceTemplate) => {
    setFType(t.task_type || 'inspection');
    setFDesc(t.description || '');
    setFPriority((t.priority || 'medium') as Priority);
    setFDueDate(t.due_in_days ? String(t.due_in_days) : '');
    setFDueMiles(t.due_in_miles ? String(t.due_in_miles) : '');
    setFDueEngineHours(t.due_in_hours ? String(t.due_in_hours) : '');
    // Pick the active trigger view from whatever the template sets
    // (date > miles > hours preference), and turn on Repeat if the
    // template carries a recurrence for that same dimension.
    const primary: TriggerMode =
      t.due_in_days  ? 'date'
      : t.due_in_miles ? 'miles'
      : t.due_in_hours ? 'hours'
      : 'date';
    setFTriggerMode(primary);
    setFRepeat(
      primary === 'date'  ? t.recur_interval_days != null
      : primary === 'miles' ? t.recur_interval_miles != null
      : t.recur_interval_engine_hours != null,
    );
    toast.success(`Applied "${t.name}" — pick a vehicle to finish.`);
  };

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
    setEType(t.task_type || 'inspection');
    setEDesc(t.description);
    setEDueDate(_dueDateToPeriodDays(t.due_date));
    setEPriority(((t.priority || 'medium') as Priority));
    // Infer the initial trigger view from what the task actually has.
    // Preference order: date > miles > hours.  Falls back to date so
    // the drawer always opens with a sane view.
    const initialMode: TriggerMode =
      t.due_date            ? 'date'
      : t.due_miles != null ? 'miles'
      : t.due_engine_hours != null ? 'hours'
      : 'date';
    setETriggerMode(initialMode);
    // Initial repeat-checkbox state reflects the existing recurrence
    // for the chosen dimension.  Switching tabs after this will
    // re-derive via the effect below.
    setERepeat(
      initialMode === 'date'  ? t.recur_interval_days != null
      : initialMode === 'miles' ? t.recur_interval_miles != null
      : t.recur_interval_engine_hours != null,
    );
    // Cost — display as dollars (cents / 100) with up to 2 decimals.
    setECost(
      t.cost_cents != null
        ? (t.cost_cents / 100).toFixed(2).replace(/\.00$/, '')
        : '',
    );
    setEVendor(t.vendor_name || '');
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
  const { data: tasksData, isLoading: loading, error: queryError } = useMaintenanceTasksQuery();
  const allTasks = tasksData?.tasks ?? [];
  const fetchError = queryError instanceof Error ? queryError.message : '';
  const load = () => qc.invalidateQueries({ queryKey: ['maintenance-tasks'] });

  // Urgency classification + buckets live in useMaintenanceTasks.ts,
  // SHARED with the topbar MaintenanceHero so the hero chips, the
  // filter chips, and the per-row status badges all derive from the
  // same computation and can't drift.  Calendar-day basis (a "due
  // today" task is due-soon, not overdue) — same boundary as
  // ``DueDateChip``.
  const dueSoonClassify = useMemo(() => makeUrgencyClassifier(), []);
  const columns: AnyColumn[] = useMemo(
    () => makeColumns({ dueSoonClassify, customTypeLabelByValue, tz }),
    [dueSoonClassify, customTypeLabelByValue, tz],
  );
  const buckets = useMemo(() => classifyTaskBuckets(allTasks), [allTasks]);

  // OPEN work only (overdue → due-soon → pending), used by the
  // calendar view, the select-all checkbox, and the footer count.
  // Completed and cancelled tasks are closed tickets; they live in
  // the grid's Archive segment tab, and per-vehicle they're
  // available in the History button on the drawer.  The recurring-
  // task auto-spawn (see capabilities/maintenance/service.py:
  // spawn_recurring_if_completed) already creates the NEXT task
  // when a recurring one closes, so the list stays populated with
  // the freshly-spawned children.  Urgency SLICING is no longer a
  // page concern — the grid's Status column filter offers the same
  // derived Overdue / Due Soon options, and the topbar hero shows
  // the live counts.
  const tasks = useMemo(() => [
    ...buckets.overdue,
    ...buckets.dueSoon,
    ...buckets.pending,
  ], [buckets]);

  // The GRID gets the FULL set including closed tickets — its
  // Active / Archive segment tabs own the lifecycle split.
  const gridTasks = allTasks;

  // (Selection clearing on filter change is handled inside DataGrid.)


  // Bulk actions — DataGrid owns the selection + the top action bar +
  // the confirm; each handler receives the selected task rows and POSTs
  // to the /tasks/bulk/* routes.  DataGrid clears the selection when the
  // action resolves.  ("Archive" isn't a flag — the Active/Archive
  // segment is derived from status, so archiving = status 'cancelled'.)
  const idsOf = (rows: Record<string, unknown>[]) =>
    rows.map(r => (r as unknown as MaintenanceTask).id);

  const bulkSetStatus = (status: string) => async (rows: Record<string, unknown>[]) => {
    try {
      const res = await apiJSON<{ updated: number; spawned_ids?: number[] }>(
        '/maintenance/tasks/bulk/status',
        { method: 'POST', body: { task_ids: idsOf(rows), status } },
      );
      const spawned = res.spawned_ids?.length ?? 0;
      toast.success(
        `Updated ${res.updated} task${res.updated === 1 ? '' : 's'}`
        + (spawned ? ` · ${spawned} recurring follow-up${spawned === 1 ? '' : 's'} created` : ''),
      );
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk update failed');
    }
  };

  const bulkActions: BulkAction[] = [
    { label: 'Mark complete', icon: CheckSquare,
      confirm: (n) => `Mark ${n} task${n === 1 ? '' : 's'} complete?\n\n`
        + 'You will be recorded as the attester for each one. '
        + 'Recurring tasks will auto-spawn their next instance.',
      onRun: bulkSetStatus('completed') },
    // No confirm: a status picker reads like a Select, not a
    // destructive action (Delete/Archive keep their confirms).
    { label: 'Change status', icon: RefreshCw,
      options: STATUS_OPTIONS.map((s) => ({ value: s, label: STATUS_LABELS[s] || s })),
      onRun: (rows, value) => {
        // Every menu item passes a real status; a missing value means a
        // wiring bug — fail loudly rather than silently downgrading.
        if (!value) { toast.error('No status chosen'); return; }
        return bulkSetStatus(value)(rows);
      } },
    { label: 'Archive', icon: Archive,
      confirm: (n) => `Archive ${n} task${n === 1 ? '' : 's'}? They move to the Archive tab (status "cancelled").`,
      onRun: bulkSetStatus('cancelled') },
    { label: 'Delete', icon: Trash2, tone: 'danger',
      // Honest copy: deletions ARE recoverable now — from the Undo
      // toast for 15s, and from each task's History indefinitely.
      confirm: (n) => `Delete ${n} task${n === 1 ? '' : 's'}?\n\nYou can undo this right after, or restore them later from the task history.`,
      onRun: async (rows) => {
        try {
          const res = await apiJSON<{ deleted: number; group_id?: string }>(
            '/maintenance/tasks/bulk/delete',
            { method: 'POST', body: { task_ids: idsOf(rows) } },
          );
          undoableToast({
            message: `Deleted ${res.deleted} task${res.deleted === 1 ? '' : 's'}`,
            groupId: res.group_id,
            onRestored: load,
          });
          load();
        } catch (e) {
          toast.error(e instanceof Error ? e.message : 'Bulk delete failed');
        }
      } },
  ];

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    // Single-trigger validation: the selected dimension must have a
    // value.  We compute only the chosen trigger's absolute value and
    // leave the other two undefined so the API doesn't store stale
    // values from a previous mode the user switched away from.
    const activeValue =
      fTriggerMode === 'date'  ? fDueDate
      : fTriggerMode === 'miles' ? fDueMiles
      : fDueEngineHours;
    if (!activeValue) {
      setError('Set a value for the chosen trigger so the task can become overdue.');
      return;
    }
    if (fMultiMode && fMultiVehicles.size === 0) {
      setError('Pick at least one vehicle for the bulk-create.');
      return;
    }
    // Convert ONLY the active trigger's period to an absolute value.
    // The other two stay undefined so the API receives a clean,
    // single-trigger task.  Miles & hours fall back to "period as
    // absolute" when telemetry is missing (no odometer baseline).
    const dueDateAbs = fTriggerMode === 'date'
      ? (_periodDaysToDueDate(fDueDate) || undefined)
      : undefined;
    const dueMilesAbs = fTriggerMode === 'miles' && fDueMiles
      ? (fOdometer != null
          ? Math.round(fOdometer) + Number(fDueMiles)
          : Number(fDueMiles))
      : undefined;
    const dueEngineHoursAbs = fTriggerMode === 'hours' && fDueEngineHours
      ? (fEngineHours != null
          ? Math.round(fEngineHours) + Number(fDueEngineHours)
          : Number(fDueEngineHours))
      : undefined;
    setSaving(true); setError('');
    try {
      // Single-checkbox recurrence: when "Repeat after completion" is
      // on, the next instance reuses the SAME interval as the trigger
      // the user just configured.  Only the active dimension's
      // recurrence column is set; the other two stay undefined so the
      // task ships with a single, coherent trigger + repeat policy.
      // Recurrence picks the user-typed ``fRecurValue`` first; falls
      // back to the due-period when the recur input was left blank
      // (preserves the "they're the same" old default).
      const recurNum = fRecurValue.trim() ? Number(fRecurValue) : NaN;
      const recurFor = (fallback: string) =>
        fRepeat
          ? (Number.isFinite(recurNum) ? recurNum
            : fallback ? Number(fallback) : undefined)
          : undefined;
      const recurDays  = fTriggerMode === 'date'  ? recurFor(fDueDate) : undefined;
      const recurMiles = fTriggerMode === 'miles' ? recurFor(fDueMiles) : undefined;
      const recurHours = fTriggerMode === 'hours' ? recurFor(fDueEngineHours) : undefined;
      const shared = {
        task_type: fType,
        description: fDesc,
        priority: fPriority,
        due_date: dueDateAbs,
        due_miles: dueMilesAbs,
        due_engine_hours: dueEngineHoursAbs,
        recur_interval_days: recurDays,
        recur_interval_miles: recurMiles,
        recur_interval_engine_hours: recurHours,
      };
      if (fMultiMode) {
        const res = await apiJSON<{
          created: { id: number }[];
          failed: { vehicle_name: string; error: string }[];
        }>('/maintenance/tasks/bulk/create', {
          method: 'POST',
          body: { ...shared, vehicle_names: Array.from(fMultiVehicles) },
        });
        toast.success(
          `Created ${res.created.length} task${res.created.length === 1 ? '' : 's'}`
          + (res.failed.length ? ` · ${res.failed.length} failed` : ''),
        );
        if (res.failed.length) {
          // Show the first failure verbatim so the user can fix the
          // root cause (usually a bad vehicle name).
          toast.error(`First failure: ${res.failed[0].vehicle_name} — ${res.failed[0].error}`);
        }
      } else {
        await apiJSON('/maintenance/tasks', { method: 'POST', body: {
          ...shared,
          vehicle_name: fVehicle,
          // ``company_code`` lets the backend store WHICH "103" the
          // user picked when two companies share a vehicle name.
          // Empty string flows through harmlessly when the user
          // free-typed the name without picking a fleet row.
          company_code: fCompany,
        }});
      }
      setShowAdd(false);
      setFVehicle(''); setFCompany(''); setFDesc(''); setFDueDate(''); setFDueMiles('');
      setFDueEngineHours(''); setFPriority('medium'); setFOdometer(null); setFEngineHours(null);
      setFMultiMode(false); setFMultiVehicles(new Set());
      setFTriggerMode('date');
      setFRepeat(false);
      setFRecurValue('');
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
    if (eType !== (selected.task_type || 'inspection')) body.task_type = eType;
    if (eDesc !== selected.description) body.description = eDesc;
    // Only patch the trigger field matching the current view —
    // switching modes in the segmented control doesn't wipe a
    // previously-set trigger of another type.  A multi-trigger task
    // stays multi-trigger until the user explicitly opens that
    // dimension's view and clears it.
    if (eTriggerMode === 'date' && dueDateAbs !== (selected.due_date || '')) {
      body.due_date = dueDateAbs || null;
    }
    if (eTriggerMode === 'miles' && dueMilesAbs !== (selected.due_miles ?? null)) {
      body.due_miles = dueMilesAbs;
    }
    if (ePriority !== (selected.priority || 'medium')) body.priority = ePriority;
    if (eTriggerMode === 'hours' && dueEngineHoursAbs !== (selected.due_engine_hours ?? null)) {
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
      if (want !== (selected.recur_interval_days ?? null)) {
        body.recur_interval_days = want;
      }
    }
    if (eTriggerMode === 'miles') {
      const fallback = eDueMiles ? Number(eDueMiles) : null;
      const want = eRepeat
        ? (Number.isFinite(recurNum) ? recurNum : fallback)
        : null;
      if (want !== (selected.recur_interval_miles ?? null)) {
        body.recur_interval_miles = want;
      }
    }
    if (eTriggerMode === 'hours') {
      const fallback = eDueEngineHours ? Number(eDueEngineHours) : null;
      const want = eRepeat
        ? (Number.isFinite(recurNum) ? recurNum : fallback)
        : null;
      if (want !== (selected.recur_interval_engine_hours ?? null)) {
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
        setError('Cost must be a positive number.');
        return;
      }
      costCentsAbs = Math.round(parsed * 100);
    }
    if (costCentsAbs !== (selected.cost_cents ?? null)) {
      body.cost_cents = costCentsAbs;
    }
    const trimmedVendor = eVendor.trim();
    if ((trimmedVendor || null) !== (selected.vendor_name || null)) {
      body.vendor_name = trimmedVendor || null;
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

  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  // Snooze and Delete expand inline within the action area instead of
  // shouting from above the fold.  Each starts collapsed (just a
  // button); clicking reveals the picker (Snooze) or the destructive
  // confirmation (Delete).  Both reset to collapsed every time the
  // drawer reopens so a stale "are you sure?" never greets the user.
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  useEffect(() => {
    // Whenever the open task changes, collapse both panels.
    setSnoozeOpen(false);
    setDeleteOpen(false);
  }, [selected?.id]);

  // Upload a receipt/photo to the open task.  Backend stamps the
  // single attachment metadata; previous file is replaced on the
  // object store side too (folder-keyed by task id).
  const handleAttachmentUpload = async (file: File) => {
    if (!selected) return;
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
        '/maintenance/tasks/' + selected.id + '/attachment',
        { method: 'POST', body: form },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        toast.error(typeof err.detail === 'string' ? err.detail : 'Upload failed');
        return;
      }
      toast.success('Attachment uploaded');
      load();
      // Refetch the open task so the sidebar shows the new attachment
      // without forcing the user to reopen the drawer.
      try {
        const refreshed = await apiJSON<MaintenanceTask>(
          '/maintenance/tasks/' + selected.id,
        );
        setSelected(refreshed);
      } catch { /* non-fatal; list refresh above will sync next open */ }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploadingAttachment(false);
    }
  };

  const handleAttachmentDelete = async () => {
    if (!selected) return;
    const ok = window.confirm(
      'Remove the attached file?\n\nThis can\'t be undone.',
    );
    if (!ok) return;
    try {
      await apiJSON('/maintenance/tasks/' + selected.id + '/attachment', {
        method: 'DELETE',
      });
      toast.success('Attachment removed');
      load();
      try {
        const refreshed = await apiJSON<MaintenanceTask>(
          '/maintenance/tasks/' + selected.id,
        );
        setSelected(refreshed);
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
    if (!selected) return;
    try {
      await apiJSON('/maintenance/tasks/' + selected.id, {
        method: 'PUT', body: { status: 'completed' },
      });
      toast.success('Marked complete');
      setSelected(null);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Mark complete failed');
    }
  };

  const handleSnooze = async (hours: number | null) => {
    if (!selected) return;
    const until = hours === null
      ? null
      : new Date(Date.now() + hours * 3600_000).toISOString();
    try {
      await apiJSON('/maintenance/tasks/' + selected.id + '/snooze', {
        method: 'POST', body: { until },
      });
      toast.success(
        until
          ? `Snoozed until ${formatDate(until, { timeZone: tz })}`
          : 'Snooze cleared',
      );
      setSelected(null);
      load();
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
    if (!selected) return;
    try {
      await apiJSON('/maintenance/tasks/' + selected.id, { method: 'DELETE' });
      setSelected(null); load();
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed'); }
  };

  // DOT Binder generation moved to the Reports module (Reports →
  // DOT Binder tab, /reports/dot-binder) in 2026-06.  The button was
  // removed from this page because the binder is a stakeholder-facing
  // compliance PDF, not a maintenance-editing surface — see
  // docs/architecture/reports-hierarchy-audit.md.  The backend
  // endpoint /api/maintenance/dot-binder is still served (one release
  // cycle of backward compatibility) but the dashboard now calls the
  // canonical /api/reports/dot-binder URL.

  // The server-side CSV download (/maintenance/tasks.csv) was removed
  // from this page along with its header button — DataGrid's toolbar
  // Export (Current page / All rows) is the single export path and
  // honours the operator's live filters + column layout, which the
  // server dump never did.  The API endpoint itself is still served
  // for bot/automation callers.

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
                className={`inline-flex size-7 items-center justify-center rounded ${viewMode === 'list'
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
                className={`inline-flex size-7 items-center justify-center rounded ${viewMode === 'calendar'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'}`}
              >
                <CalendarDays size={14} />
              </button>
            </div>
            {/* The page-level "Export CSV" button was removed — the
                grid's own toolbar Export (Current page / All rows,
                honouring live filters + column layout) covers it and
                the two side by side read as duplication. */}
            <button
              type="button"
              onClick={() => setTemplatesOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
              title="Manage re-usable task templates"
            >
              <ClipboardList size={14} />
              Templates
            </button>
            {/* DOT Binder button moved to Reports module (Reports →
                DOT Binder tab) in 2026-06 — the binder is a
                stakeholder-facing compliance PDF, not a maintenance-
                editing surface. */}
            <button onClick={() => { setShowAdd(!showAdd); setError(''); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition">
              <Plus size={14} />
              {showAdd ? 'Cancel' : 'New task'}
            </button>
          </div>
        }
      />

      {/* Filter chips moved INLINE next to the table search box (see
          ``headerToolbar`` prop below) so the "narrow this list"
          controls live in one zone.  Keeping the chip-render code
          here as a memoised JSX block keeps the props readable. */}

      {(error || fetchError) && (
        <div className="mb-3">
          <ErrorState message={error || fetchError} />
        </div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-card border border-border rounded-xl p-4 mb-6 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
          {/* Apply-template dropdown — only shown when at least one
              template exists.  Selecting fills the rest of the form
              with the template's defaults; the user picks a vehicle
              and clicks Create. */}
          {templates.length > 0 && (
            <label className="col-span-full block">
              <span className="block text-xs text-muted-foreground mb-1 inline-flex items-center gap-1">
                <ClipboardList size={12} />
                Apply template (optional)
              </span>
              {/* Value stays pinned to '' so the picker always shows the
                  placeholder and re-picking the same template re-applies
                  it. */}
              <Select
                value=""
                onValueChange={(v) => {
                  const id = Number(v);
                  const t = templates.find(x => x.id === id);
                  if (t) applyTemplate(t);
                }}
                items={templateItems}
              >
                <SelectTrigger className="w-full" aria-label="Apply template">
                  <SelectValue placeholder="— pick a template —" />
                </SelectTrigger>
                <SelectContent>
                  {templateItems.map(it => (
                    <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          )}
          {/* Multi-vehicle toggle — checkbox at the top so the user
              sees it before they start filling fields.  Spans the
              full row so it doesn't get lost. */}
          <label className="col-span-full inline-flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={fMultiMode}
              onChange={e => {
                setFMultiMode(e.target.checked);
                if (!e.target.checked) setFMultiVehicles(new Set());
              }}
              className="accent-primary cursor-pointer"
            />
            Apply to multiple vehicles at once
          </label>
          {fMultiMode ? (
            <div className="col-span-2 md:col-span-3 xl:col-span-4">
              <span className="block text-xs text-muted-foreground mb-1">
                Vehicles ({fMultiVehicles.size} selected)
              </span>
              <div className="max-h-40 overflow-y-auto bg-muted border border-border rounded p-2 flex flex-wrap gap-1">
                {fleetLoading && (
                  <span className="text-xs text-muted-foreground">Loading…</span>
                )}
                {!fleetLoading && vehicleList.length === 0 && (
                  <span className="text-xs text-muted-foreground">No vehicles found.</span>
                )}
                {vehicleList.map(v => {
                  const on = fMultiVehicles.has(v.name);
                  return (
                    <button
                      key={v.name}
                      type="button"
                      onClick={() => {
                        setFMultiVehicles(prev => {
                          const next = new Set(prev);
                          if (on) next.delete(v.name); else next.add(v.name);
                          return next;
                        });
                      }}
                      className={`px-2 py-0.5 rounded-full border text-xs transition ${
                        on
                          ? 'bg-primary text-primary-foreground border-primary'
                          : 'bg-card border-border hover:bg-muted'
                      }`}
                    >
                      {on ? '✓ ' : ''}#{v.name}
                    </button>
                  );
                })}
              </div>
              {fMultiVehicles.size > 0 && (
                <button
                  type="button"
                  onClick={() => setFMultiVehicles(new Set())}
                  className="mt-1 text-2xs text-muted-foreground hover:text-foreground"
                >
                  Clear selection
                </button>
              )}
            </div>
          ) : (
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">Vehicle</span>
              <VehiclePicker
                value={fVehicle}
                vehicles={vehicleList}
                loading={fleetLoading}
                onChange={(name, vehicle) => {
                  setFVehicle(name);
                  // ``vehicle`` is non-null only when the user clicked
                  // a row in the picker dropdown — that's the
                  // unambiguous signal of WHICH company's "103" they
                  // picked.  Free-typed names leave fCompany blank,
                  // which the backend treats as "unknown" and the
                  // server-side enrichment leaves the company chip
                  // empty rather than guessing.
                  setFCompany(vehicle?.company ?? '');
                  setFOdometer(null);
                  setFEngineHours(null);
                  if (vehicle) fetchOdometer(vehicle.name);
                }}
              />
            </label>
          )}
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Service task</span>
            <ServiceTaskPicker value={fType} onChange={setFType} canCreate={canCreateTasks} />
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
            <Select value={fPriority} onValueChange={(v) => setFPriority(v as Priority)} items={PRIORITY_ITEMS}>
              <SelectTrigger className="w-full" aria-label="Priority"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PRIORITY_ITEMS.map(it => (
                  <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {/* Single-trigger picker — segmented control + the
              corresponding period input.  Plain text labels (no
              emoji) for a cleaner look. */}
          <div className="col-span-full">
            <span className="block text-xs text-muted-foreground mb-1">
              Due by
            </span>
            <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md mb-2" role="group" aria-label="Due by">
              {([
                { k: 'date',  label: 'Date'  },
                { k: 'miles', label: 'Miles' },
                { k: 'hours', label: 'Hours' },
              ] as const).map(opt => {
                const active = fTriggerMode === opt.k;
                return (
                  <button
                    key={opt.k}
                    type="button"
                    onClick={() => setFTriggerMode(opt.k)}
                    aria-pressed={active}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition ${
                      active
                        ? 'bg-card text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            {fTriggerMode === 'date' && (
              <label className="block">
                <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
                  <span title="Today's date — the period below is added to this">Today: {_todayLabel(tz)}</span>
                  <span className="text-primary">
                    Due: {fDueDate ? _formatDate(_periodDaysToDueDate(fDueDate), tz) : '—'}
                  </span>
                </div>
                <DaysPicker value={fDueDate} onChange={setFDueDate} />
              </label>
            )}
            {fTriggerMode === 'miles' && (
              <label className="block">
                <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
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
            )}
            {fTriggerMode === 'hours' && (
              <label className="block">
                {fEngineHours != null ? (
                  <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground mb-1">
                    <span>Current: {Math.round(fEngineHours).toLocaleString()} h</span>
                    <span className="text-primary">
                      Due:{' '}
                      {fDueEngineHours
                        ? `${(Math.round(fEngineHours) + Number(fDueEngineHours)).toLocaleString()} h`
                        : '—'}
                    </span>
                  </div>
                ) : (
                  <p className="text-2xs text-muted-foreground mb-1">
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
            )}
          </div>
          {/* Single recurrence checkbox — one line, mirrors active
              trigger.  Phrasing varies depending on whether a value
              has been entered yet ("Repeat every 90 days after
              completion" vs the plain "Repeat after completion"). */}
          <div className="col-span-full flex flex-wrap items-center gap-2 text-xs">
            <label className="inline-flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={fRepeat}
                onChange={(e) => {
                  const on = e.target.checked;
                  setFRepeat(on);
                  // Seed from the due-period when the user flips it
                  // on and hasn't set a separate recur value yet —
                  // matches today's "same as due" default while
                  // still letting them override.
                  if (on && !fRecurValue.trim()) {
                    const seed =
                      fTriggerMode === 'date'  ? fDueDate
                      : fTriggerMode === 'miles' ? fDueMiles
                      : fDueEngineHours;
                    if (seed) setFRecurValue(String(seed));
                  }
                }}
                className="accent-primary cursor-pointer"
              />
              <span className="text-foreground">Repeat every</span>
            </label>
            <input
              type="number"
              min={0}
              value={fRecurValue}
              onChange={e => setFRecurValue(e.target.value)}
              disabled={!fRepeat}
              placeholder={
                fTriggerMode === 'date' ? '30'
                : fTriggerMode === 'miles' ? '5000'
                : '500'
              }
              className="w-20 bg-muted border border-border rounded px-2 py-1 text-xs focus:outline-none focus:border-ring disabled:opacity-50"
            />
            <span className="text-foreground">
              {fTriggerMode === 'date' ? 'days' : fTriggerMode === 'miles' ? 'mi' : 'h'} after completion
            </span>
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={saving} className="w-full px-4 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition">
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </form>
      )}

      {/* The urgency chip row that used to live here (All / Overdue /
          Due Soon / Pending) is gone: the topbar MaintenanceHero now
          owns those live counts, and urgency FILTERING moved into the
          grid's Status column filter (whose options are the same
          derived statuses the badges show).  Lifecycle stays on the
          grid's Active / Archive segment tabs. */}
      {loading && tasks.length === 0 ? (
        <TableSkeleton rows={6} cols={7} />
      // Only a truly empty account gets the onboarding state — if the
      // only tasks left are closed ones, the grid still renders so
      // the Archive tab stays reachable (an empty Active view shows
      // the in-grid "No data" row with the tabs visible).
      ) : allTasks.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No maintenance tasks yet"
          description="Create your first task — set a due date, due miles, or both, and we'll alert you as it approaches."
          action={(
            <button onClick={() => setShowAdd(true)} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition">
              <Plus size={14} />
              New task
            </button>
          )}
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
          <DataGrid
            tableId="maintenance-tasks"
            columns={columns}
            segments={TASK_SEGMENTS}
            // Personal scope tabs sit after Active/Archive (e.g. a saved
            // "Critical" or "Oil changes" view), per-user + isolated.
            savedTabs
            data={gridTasks as unknown as Record<string, unknown>[]}
            searchKey={['vehicle_name', 'company_code', 'description', 'task_type']}
            searchPlaceholder="Search…"
            onRowClick={(row) => openTaskForEdit(row as unknown as MaintenanceTask)}
            // Bulk selection + action bar are DataGrid's now (the SSOT):
            // it owns the checkbox column and the floating bar rendered
            // from bulkActions.  onBulkSelectionChange mirrors the set
            // out for the AI page-context.
            bulkSelection
            bulkActions={bulkActions}
            bulkRowLabel={(r) => `task on ${(r as unknown as MaintenanceTask).vehicle_name}`}
            onBulkSelectionChange={(rows) =>
              setSelectedIds(rows.map(r => (r as unknown as MaintenanceTask).id))}
          />
          {/* No count footer — the topbar hero carries the live
              Overdue / Due Soon / Pending / Completed counts and the
              grid's pagination footer shows the row totals. */}
        </>
      )}

      {/* Bulk-action bar is rendered by DataGrid from ``bulkActions``. */}

      {/* Was a hand-rolled backdrop + panel: no focus trap, no Escape, no
          ``aria-modal``, no background scroll lock.  <Sheet> brings all
          four; <SheetBody> makes the body a real scroll region. */}
      <Sheet open={!!selected} onOpenChange={(o) => { if (!o) setSelected(null); }}>
        <SheetContent side="right" className="p-0"
        size="lg">
          {selected && (
          <SheetBody label="Maintenance task details" className="p-6">
            {/* Header: vehicle + task type for disambiguation when
                multiple tabs/drawers are juggled. History sits as a
                clear chip rather than a faint inline link. */}
            <div className="flex items-start justify-between gap-2 mb-4">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold truncate">
                  {selected.vehicle_name}
                  {selected.company_code && (
                    <span
                      title="Company this truck belongs to — disambiguates when two companies share a vehicle name"
                      className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-3xs align-middle"
                    >
                      {selected.company_code}
                    </span>
                  )}
                  <span className="text-muted-foreground font-normal">
                    {' · '}
                    <span>{customTypeLabelByValue[selected.task_type] ?? selected.task_type.replace(/_/g, ' ')}</span>
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
              <div className={`mb-4 px-3 py-2 rounded text-xs inline-flex items-center gap-1.5 ${toneClasses('info')}`}>
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
                className={`mb-4 px-3 py-2 rounded text-xs inline-flex items-center gap-1.5 ${toneClasses('ok')}`}
              >
                <span aria-hidden>📄</span>
                Closed by Work Order #{selected.work_order_id}
              </a>
            )}
            {/* Active-snooze banner — when the task has a future
                ``snoozed_until``, surface it prominently so the user
                doesn't think the system is broken when overdue tasks
                stop generating alerts.  One-click Resume clears it. */}
            {selected.snoozed_until
              && new Date(selected.snoozed_until).getTime() > Date.now() && (
              <div className={`mb-4 px-3 py-2 rounded text-xs flex items-center gap-2 ${toneClasses('warn')}`}>
                <BellOff size={14} className="shrink-0" />
                <span className="flex-1">
                  Snoozed until {formatDate(selected.snoozed_until, { timeZone: tz })}
                </span>
                <button
                  type="button"
                  onClick={() => handleSnooze(null)}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-warn-bg hover:bg-warn-bg rounded text-warn"
                >
                  <Bell size={12} />
                  Resume
                </button>
              </div>
            )}
            {/* Immutable facts only — editable fields (status,
                description, due triggers, priority) live in the form
                below so the same value never appears twice. */}
            <dl className="space-y-3 text-sm mb-6">
              <div className="flex justify-between"><dt className="text-muted-foreground">Created</dt><dd>{_formatDate(selected.created_at, tz)}</dd></div>
              {selected.completed_at && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Completed</dt>
                  <dd>{_formatDate(selected.completed_at, tz)}</dd>
                </div>
              )}
              {(selected.recur_interval_days
                || selected.recur_interval_miles
                || selected.recur_interval_engine_hours) && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Recurrence</dt>
                  <dd className="text-right">
                    {[
                      selected.recur_interval_days
                        ? `every ${selected.recur_interval_days} days` : null,
                      selected.recur_interval_miles
                        ? `every ${Number(selected.recur_interval_miles).toLocaleString()} mi` : null,
                      selected.recur_interval_engine_hours
                        ? `every ${Number(selected.recur_interval_engine_hours).toLocaleString()} h` : null,
                    ].filter(Boolean).join(' · ')}
                  </dd>
                </div>
              )}
              {/* Attestation: surfaces the audit trail.  Renders inside
                  the dl block so it's visually grouped with the other
                  task metadata.  Multi-line because the name+date can
                  wrap on narrow sidebars. */}
              {selected.attested_at && (
                <div className="pt-2 border-t border-border">
                  <dt className="text-muted-foreground text-xs mb-1">Attestation</dt>
                  <dd className="text-xs text-ok">
                    <span aria-hidden>✓</span>{' '}
                    <span className="font-medium">
                      {selected.attested_by_name || `user ${selected.attested_by}`}
                    </span>
                    {' '}confirmed on{' '}
                    {formatDate(selected.attested_at, { timeZone: tz })}
                  </dd>
                </div>
              )}
              {/* The record's own activity trail — who did what, with
                  before→after values (capabilities/activity_trail). */}
              <div className="pt-2 border-t border-border">
                <button
                  type="button"
                  onClick={() => setHistoryOpen(true)}
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <History size={14} /> View activity history
                </button>
              </div>
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
                        }`}
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
              {(selected.status === 'completed' || eStatus === 'completed') && (<>
                <div className="border-t border-border/40 pt-3 -mx-0" aria-hidden />
                <div className="block">
                  <span className="block text-xs text-muted-foreground mb-1 inline-flex items-center gap-1">
                    <Paperclip size={12} />
                    Receipt / Photo
                  </span>
                  {selected.attachment_name ? (
                    <div className="flex items-center gap-2 p-2 bg-muted/40 border border-border rounded text-xs">
                      {(selected.attachment_content_type || '').startsWith('image/')
                        ? <ImageIcon size={14} className="text-muted-foreground shrink-0" />
                        : <FileText size={14} className="text-muted-foreground shrink-0" />}
                      <a
                        href={'/api/maintenance/tasks/' + selected.id + '/attachment'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-1 truncate hover:underline"
                        title={selected.attachment_name}
                      >
                        {selected.attachment_name}
                      </a>
                      <label className="cursor-pointer text-muted-foreground hover:text-foreground" title="Replace">
                        <Upload size={14} />
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
                        className="text-muted-foreground hover:text-destructive"
                        aria-label="Remove attachment"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <label className="flex items-center justify-center gap-1.5 p-2 bg-muted/40 hover:bg-muted border border-dashed border-border rounded text-xs cursor-pointer text-muted-foreground hover:text-foreground">
                      <Upload size={14} />
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
                  onClick={() => setSelected(null)}
                  disabled={saving}
                  className="px-3 py-2 bg-muted hover:bg-muted/80 border border-border rounded text-sm font-medium transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdate}
                  disabled={saving}
                  className="flex-1 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition"
                >
                  {saving ? 'Saving...' : 'Update Task'}
                </button>
              </div>

              {/* Mark complete — one-click parity with the bulk-select
                  toolbar's "Mark complete" pill.  Hidden when the
                  task is already completed or cancelled (it would be
                  a no-op) so the action area stays uncluttered. */}
              {selected.status !== 'completed' && selected.status !== 'cancelled' && (
                <button
                  type="button"
                  onClick={handleMarkComplete}
                  disabled={saving}
                  className={`mt-2 w-full py-1.5 rounded text-xs font-medium transition inline-flex items-center justify-center gap-1.5 disabled:opacity-50 ${toneClasses('ok')}`}
                >
                  <CheckSquare size={12} />
                  Mark complete
                </button>
              )}

              {/* Snooze pill — only shows when a snooze would actually
                  do something (task is overdue or close to threshold).
                  Collapsed: subtle button.  Expanded: inline 48h/7d/30d
                  picker with a close-X. */}
              {_isOverdueOrApproaching(selected) && (
                snoozeOpen ? (
                  <div className="flex items-center gap-1.5 pt-2">
                    <span className="text-2xs text-muted-foreground inline-flex items-center gap-1 mr-1">
                      <BellOff size={12} />
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
                      <X size={14} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setSnoozeOpen(true)}
                    className="mt-2 w-full py-1.5 bg-muted/60 hover:bg-muted border border-border rounded text-xs font-medium text-foreground transition inline-flex items-center justify-center gap-1.5"
                  >
                    <BellOff size={12} />
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
                      className="flex-1 py-1.5 bg-destructive hover:bg-destructive/90 rounded text-xs font-medium inline-flex items-center justify-center gap-1.5"
                    >
                      <Trash2 size={12} />
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
                  <Trash2 size={12} />
                  Delete
                </button>
              )}
            </div>
          </SheetBody>
          )}
        </SheetContent>
      </Sheet>

      {historyVehicle && (
        <ServiceHistoryModal
          vehicleName={historyVehicle}
          onClose={() => setHistoryVehicle(null)}
        />
      )}
      <TaskActivityDialog
        taskId={selected?.id ?? null}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />

      {templatesOpen && (
        <TemplatesModal
          onClose={() => setTemplatesOpen(false)}
          // No explicit onChange — the modal invalidates the
          // ['maintenance-templates'] query key directly, which the
          // dropdown above subscribes to.  Keeps the modal decoupled
          // from this page's state.
        />
      )}

      {/* DOT Binder dialog removed — the binder generator now lives
          at /reports/dot-binder as its own page inside the Reports
          module shell. */}
    </div>
  );
}
