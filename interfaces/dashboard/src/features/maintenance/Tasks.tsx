import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Wrench, Plus, X, Download, History, FileText,
  List, CalendarDays, Trash2, CheckSquare, BellOff, Bell,
  Paperclip, Image as ImageIcon, Upload, ClipboardList,
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
  PRIORITY_OPTIONS,
  type Priority,
} from './badges';
import TypePicker from './TypePicker';
import { VehiclePicker, MilesPicker, HoursPicker, DaysPicker, type VehicleSummary } from './pickers';
import { CalendarMonth } from './CalendarMonth';
import { toneClasses } from '@/lib/status';
import { ServiceHistoryModal } from './ServiceHistoryModal';
import { TemplatesModal } from './TemplatesModal';
import type { MaintenanceTemplate } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatDay } from '../../utils/datetime';

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

function _formatDate(input: string | null | undefined, tz?: string): string {
  if (!input) return '';
  // YYYY-MM-DD inputs land at midnight UTC if parsed bare; pin to
  // local midnight so the rendered day matches the user's calendar.
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(input) ? input + 'T00:00:00' : input;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return formatDay(d, { timeZone: tz, intl: DATE_FMT });
}

function _todayLabel(tz?: string): string {
  return formatDay(new Date(), { timeZone: tz, intl: DATE_FMT });
}

// Snooze Alerts is only meaningful when the task is actually nagging
// the operator — overdue right now, or close enough to the threshold
// that the pre-overdue warning would fire.  At create time / for
// far-future tasks, the buttons are clutter, so we hide them.
// Thresholds match the backend ``detect_upcoming_warnings`` defaults:
// 7 days / 500 mi / 50 engine hours.
function _isOverdueOrApproaching(t: MaintenanceTask): boolean {
  if (t.status === 'overdue') return true;
  if (t.status !== 'pending') return false;
  if (t.due_date) {
    const due = /^\d{4}-\d{2}-\d{2}$/.test(t.due_date)
      ? new Date(t.due_date + 'T00:00:00')
      : new Date(t.due_date);
    if (!Number.isNaN(due.getTime())) {
      const now = new Date();
      const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
      const startOfDue   = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
      const days = Math.round((startOfDue - startOfToday) / 86_400_000);
      if (days <= 7) return true;
    }
  }
  if (t.due_miles != null && t.last_odometer != null
      && t.last_odometer + 500 >= t.due_miles) {
    return true;
  }
  if (t.due_engine_hours != null && t.last_engine_hours != null
      && t.last_engine_hours + 50 >= t.due_engine_hours) {
    return true;
  }
  return false;
}

// Base column set, excluding the bulk-select checkbox.  The component
// composes the final columns array by prepending a selection column
// whose render closes over the page-level ``selectedIds`` state.
const baseColumns: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  // Company column lets operators tell trucks apart when two
  // companies under the same account each have a "103" or "101".
  // Mirrors the Vehicles list column for visual parity.  Renders the
  // raw company_code (e.g. G1 / OSY / PTG) — short, scannable.
  { key: 'company_code', label: 'Company', sortable: true,
    render: (v) => {
      const s = String(v || '').trim();
      return s
        ? <span className="text-xs">{s}</span>
        : <span className="text-muted-foreground text-xs">—</span>;
    } },
  // Priority badge — first column after vehicle so it carries the most
  // visual weight.  ``sortKey`` ranks critical→high→medium→low so the
  // sort matches operator expectations (alphabetical would put
  // critical AFTER low, which is wrong).
  { key: 'priority', label: 'Priority', sortable: true,
    sortKey: (row) => {
      const rank: Record<string, number> = {
        critical: 0, high: 1, medium: 2, low: 3,
      };
      const r = row as MaintenanceTask;
      return rank[(r.priority || 'medium').toLowerCase()] ?? 99;
    },
    render: (v) => <PriorityBadge value={v} /> },
  { key: 'task_type', label: 'Type', sortable: true, render: (v) => <TaskTypeCell type={String(v || 'custom')} /> },
  { key: 'description', label: 'Description', render: (v) => {
    const s = String(v || '');
    return s.length > 60 ? <span title={s}>{s.slice(0, 60)}…</span> : s;
  }},
  { key: 'due_date', label: 'Due Date', sortable: true, render: (v, row) => {
    const r = row as MaintenanceTask;
    return <DueDateChip value={v} status={r.status} recurDays={r.recur_interval_days} />;
  } },
  // Combined "Mileage Progress" replaces the bare "Due Miles" cell so the
  // operator can see how close each truck is to the next service without
  // doing the arithmetic in their head.
  //
  // Sortable by URGENCY, not by raw due_miles.  ``due_miles -
  // last_odometer`` is the canonical "miles to go": negative means
  // overdue (most-negative = most-overdue), small positive means due
  // soon, large positive means far from due.  ASC click puts the most
  // urgent truck at the top — what the operator wants when scanning
  // "what do I need to service this week?".  Rows with no due_miles
  // threshold sink to the bottom via +Infinity.
  { key: 'due_miles', label: 'Mileage', sortable: true,
    sortKey: (row) => {
      const r = row as MaintenanceTask;
      if (r.due_miles == null) return Number.POSITIVE_INFINITY;
      const last = r.last_odometer ?? 0;
      return Number(r.due_miles) - Number(last);
    },
    render: (_v, row) => <MileageProgress row={row as MaintenanceTask} /> },
  // Engine-hours parallel to mileage.  Same urgency-based sort:
  // ``due_engine_hours - last_engine_hours`` ascending, with rows
  // missing the threshold sinking to the bottom.
  { key: 'due_engine_hours', label: 'Engine Hours', sortable: true,
    sortKey: (row) => {
      const r = row as MaintenanceTask;
      if (r.due_engine_hours == null) return Number.POSITIVE_INFINITY;
      const last = r.last_engine_hours ?? 0;
      return Number(r.due_engine_hours) - Number(last);
    },
    render: (_v, row) => <EngineHoursProgress row={row as MaintenanceTask} /> },
  { key: 'status', label: 'Status', sortable: true, render: (v) => <StatusBadge status={String(v)} /> },
  // Updated column shows date + time so multiple same-day edits are
  // distinguishable.  Short locale format keeps it readable without
  // dominating the row width.
  { key: 'updated_at', label: 'Updated', sortable: true, render: (v) => v
    ? new Date(String(v)).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
      })
    : '—' },
];

// ── Main component ─────────────────────────────────────────────

export default function Tasks() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tz = useTimezone();
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

  // Account's custom task types — drives the "Type" column's display
  // label for rows that use a custom type, plus the TypePicker
  // dropdown in the add / edit forms.  Lookup map is built once per
  // fetch so the table render closures don't reduce on every row.
  const { data: customTypesData } = useQuery({
    queryKey: ['maintenance-custom-types'],
    queryFn: () => apiJSON<{ types: { value: string; label: string }[] }>(
      '/maintenance/task-types',
    ),
    staleTime: 60_000,
  });
  const customTypeLabelByValue = useMemo(() => {
    const map: Record<string, string> = {};
    for (const t of customTypesData?.types ?? []) map[t.value] = t.label;
    return map;
  }, [customTypesData]);

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
  //
  // "Due soon" thresholds:
  //   • date     — within 7 days
  //   • miles    — within 5,000 mi
  //   • hours    — within 100 engine hours
  // These mirror the typical service-interval warning windows in the
  // industry (oil-change comes "due soon" ~5k miles out, DOT
  // inspections ~1 week out, PTO hours ~100h out).  A task lands in
  // dueSoon if ANY axis is within its window; pending otherwise.
  // Without this the Due Soon chip stayed at 0 for fleets that schedule
  // by mileage — every "1,922 mi to go" task got bucketed as pending.
  // Same helper drives the Status column's "due_soon" override so the
  // chip count and the per-row badge can't drift.
  const dueSoonClassify = useMemo(() => {
    const DUE_SOON_DAYS = 7;
    const DUE_SOON_MILES = 5_000;
    const DUE_SOON_HOURS = 100;
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    return (t: MaintenanceTask): 'overdue' | 'due_soon' | null => {
      if (t.due_date) {
        const due = new Date(t.due_date);
        if (!Number.isNaN(due.getTime())) {
          const startOfDue = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
          const days = Math.round((startOfDue - startOfToday) / 86_400_000);
          if (days < 0) return 'overdue';
          if (days <= DUE_SOON_DAYS) return 'due_soon';
        }
      }
      if (t.due_miles != null && t.last_odometer != null) {
        const remaining = t.due_miles - t.last_odometer;
        if (remaining < 0) return 'overdue';
        if (remaining <= DUE_SOON_MILES) return 'due_soon';
      }
      if (t.due_engine_hours != null && t.last_engine_hours != null) {
        const remaining = t.due_engine_hours - t.last_engine_hours;
        if (remaining < 0) return 'overdue';
        if (remaining <= DUE_SOON_HOURS) return 'due_soon';
      }
      return null;
    };
  }, []);
  const DUE_SOON_DAYS = 7;
  const DUE_SOON_MILES = 5_000;
  const DUE_SOON_HOURS = 100;
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
      // 1. Date axis — overdue first (lets the backend's status='overdue'
      //    flag also force this), then "due in next 7 days".
      if (t.due_date) {
        const due = new Date(t.due_date);
        if (!Number.isNaN(due.getTime())) {
          const startOfDue = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
          const days = Math.round((startOfDue - startOfToday) / 86_400_000);
          if (days < 0 || isOverdueStatus) { overdue.push(t); placed = true; }
          else if (days <= DUE_SOON_DAYS) { dueSoon.push(t); placed = true; }
        }
      }
      if (!placed && isOverdueStatus) { overdue.push(t); placed = true; }
      // 2. Mileage axis — only check when no date pinned the task to
      //    another bucket already.  Past due (negative remaining) goes
      //    to overdue; within threshold goes to dueSoon.
      if (!placed && t.due_miles != null && t.last_odometer != null) {
        const remaining = t.due_miles - t.last_odometer;
        if (remaining < 0) { overdue.push(t); placed = true; }
        else if (remaining <= DUE_SOON_MILES) { dueSoon.push(t); placed = true; }
      }
      // 3. Engine hours axis — same shape as mileage.
      if (!placed && t.due_engine_hours != null && t.last_engine_hours != null) {
        const remaining = t.due_engine_hours - t.last_engine_hours;
        if (remaining < 0) { overdue.push(t); placed = true; }
        else if (remaining <= DUE_SOON_HOURS) { dueSoon.push(t); placed = true; }
      }
      if (!placed) { pending.push(t); }
    }
    return { overdue, dueSoon, pending, completed, cancelled };
  }, [allTasks]);

  // statusFilter values: '' (all), 'overdue', 'due_soon', 'pending',
  // 'completed', 'cancelled' — client-side bucket keys, not API query
  // params.  Per-vehicle narrowing is handled by the DataTable search
  // box (it indexes vehicle_name, description, task_type) so we don't
  // need a separate vehicle dropdown.
  const tasks = useMemo(() => {
    if (statusFilter === 'overdue')   return buckets.overdue;
    if (statusFilter === 'due_soon')  return buckets.dueSoon;
    if (statusFilter === 'pending')   return buckets.pending;
    if (statusFilter === 'completed') return buckets.completed;
    if (statusFilter === 'cancelled') return buckets.cancelled;
    // Default "All" view = OPEN work only.  Completed and cancelled
    // tasks are closed tickets; surfacing them in the main list
    // clutters the "what do I need to do next?" read.  They stay
    // reachable via the Completed chip, and per-vehicle they're
    // available in the History button on the drawer.  The recurring-
    // task auto-spawn (see capabilities/maintenance/service.py:
    // spawn_recurring_if_completed) already creates the NEXT task
    // when a recurring one closes, so the list stays populated with
    // the freshly-spawned children.
    return [
      ...buckets.overdue,
      ...buckets.dueSoon,
      ...buckets.pending,
    ];
  }, [statusFilter, buckets]);

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
    // Override the Status column's render so a pending row whose
    // due_date / due_miles / due_engine_hours puts it in the dueSoon
    // bucket displays "due soon" in the badge instead of "pending".
    // Keeps the per-row badge consistent with the Due Soon chip count;
    // otherwise the row shows "pending" while the chip says the same
    // task is "due soon" — two reads of the same task, confusing.
    // Backend ``status`` column is untouched; only the display label
    // flips based on the same urgency check the bucket uses.
    const enrichedBase = baseColumns.map(col => {
      if (col.key === 'status') {
        return {
          ...col,
          render: (v: unknown, row: Record<string, unknown>) => {
            const t = row as unknown as MaintenanceTask;
            const stored = String(v);
            if (stored === 'pending' && dueSoonClassify(t) === 'due_soon') {
              return <StatusBadge status="due_soon" />;
            }
            return <StatusBadge status={stored} />;
          },
        };
      }
      if (col.key === 'task_type') {
        // Override the static render so custom types resolve to the
        // operator-supplied label instead of the kebab-cased value.
        return {
          ...col,
          render: (v: unknown) => {
            const type = String(v || 'inspection');
            return (
              <TaskTypeCell
                type={type}
                customLabel={customTypeLabelByValue[type]}
              />
            );
          },
        };
      }
      if (col.key === 'updated_at') {
        // Override the static render so the Updated timestamp formats
        // in the account-effective timezone (the module-level
        // ``baseColumns`` definition can't reach the ``useTimezone``
        // hook).  Same short date+time shape as before.
        return {
          ...col,
          render: (v: unknown) => v
            ? formatDate(String(v), {
                timeZone: tz,
                intl: { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
              })
            : '—',
        };
      }
      return col;
    });
    return [checkboxCol, ...enrichedBase];
  }, [tasks, selectedIds, dueSoonClassify, customTypeLabelByValue, tz]);

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

  // Bulk-flip the selected tasks to 'in_progress'.  Useful for
  // "shop visit booked next Friday — mark these 8 as in progress".
  // Skips the attestation/recur-spawn path that 'completed' takes,
  // since in-progress isn't a terminal state.
  const handleBulkInProgress = async () => {
    if (selectedIds.size === 0) return;
    try {
      const res = await apiJSON<{ updated: number }>(
        '/maintenance/tasks/bulk/status',
        { method: 'POST', body: { task_ids: Array.from(selectedIds), status: 'in_progress' } },
      );
      toast.success(`Marked ${res.updated} as in progress`);
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
              <Download size={14} />
              Export CSV
            </button>
            {/* DOT binder — opens a dialog so the user can pick a
                window + optional single vehicle before the heavy PDF
                gen runs.  Sits next to Export CSV since both are
                compliance-flavoured exports. */}
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
              <select
                onChange={e => {
                  const id = Number(e.target.value);
                  const t = templates.find(x => x.id === id);
                  if (t) applyTemplate(t);
                  // Reset the select so re-picking the same one
                  // re-applies it.
                  e.target.value = '';
                }}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
              >
                <option value="">— pick a template —</option>
                {templates.map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
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
            <span className="block text-xs text-muted-foreground mb-1">Type</span>
            <TypePicker value={fType} onChange={setFType} />
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

      {/* Filter chips live HERE, above the conditional, so they stay
          visible in every state — table view, empty state, calendar
          view, loading skeleton.  Putting them inside DataTable's
          ``headerToolbar`` slot worked when there were rows but
          disappeared the moment a chip resolved to 0 results (the
          EmptyState branch replaced the whole DataTable, taking the
          chips with it — leaving the user stranded with no way to
          click back to "All"). */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        {([
          // "All" counts OPEN work only — same as what the default
          // view renders.  Completed/cancelled rows live behind their
          // own chip + the per-vehicle History button on the drawer.
          { key: '',          label: 'All',       count: buckets.overdue.length + buckets.dueSoon.length + buckets.pending.length, dot: 'bg-muted-foreground/40' },
          { key: 'overdue',   label: 'Overdue',   count: buckets.overdue.length,   dot: 'bg-danger' },
          { key: 'due_soon',  label: 'Due Soon',  count: buckets.dueSoon.length,   dot: 'bg-warn'   },
          { key: 'pending',   label: 'Pending',   count: buckets.pending.length,   dot: 'bg-info'   },
          { key: 'completed', label: 'Completed', count: buckets.completed.length, dot: 'bg-ok'     },
        ] as const).map(chip => {
          const active = statusFilter === chip.key;
          return (
            <button
              key={chip.key || 'all'}
              type="button"
              onClick={() => setStatusFilter(active ? '' : chip.key)}
              aria-pressed={active}
              aria-label={`${chip.label}, ${chip.count} ${chip.count === 1 ? 'task' : 'tasks'}${active ? ', selected' : ''}`}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition border ${
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-card hover:bg-muted text-foreground border-border'
              }`}
            >
              <span aria-hidden className={`w-2 h-2 rounded-full ${chip.dot}`} />
              {chip.label}
              <span className={`tabular-nums ${active ? 'opacity-80' : 'text-muted-foreground'}`}>
                {chip.count}
              </span>
            </button>
          );
        })}
      </div>

      {loading && tasks.length === 0 ? (
        <TableSkeleton rows={6} cols={7} />
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title={statusFilter ? `No ${statusFilter.replace(/_/g, ' ')} tasks` : 'No maintenance tasks yet'}
          description={statusFilter
            ? "Pick another chip above to see other states, or clear the filter to come back to the default list."
            : "Create your first task — set a due date, due miles, or both, and we'll alert you as it approaches."}
          action={statusFilter
            ? (
                <button
                  type="button"
                  onClick={() => setStatusFilter('')}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 text-foreground rounded-md text-xs font-medium border border-border transition"
                >
                  Clear filter
                </button>
              )
            : (
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
          <DataTable
            columns={columns}
            data={tasks as unknown as Record<string, unknown>[]}
            searchKey={['vehicle_name', 'company_code', 'description', 'task_type']}
            searchPlaceholder="Filter this list…"
            onRowClick={(row) => openTaskForEdit(row as unknown as MaintenanceTask)}
          />
          {/* Result count footer.  Always shows the filtered count
              followed by what's hidden, so the user understands they're
              not seeing the whole list when a chip is active. */}
          <p className="text-xs text-muted-foreground mt-2">
            {tasks.length} {tasks.length === 1 ? 'task' : 'tasks'}
            {statusFilter && allTasks.length !== tasks.length
              ? ` · ${allTasks.length - tasks.length} hidden by filter`
              : ''}
            {/* On the default (no-filter) view we explicitly tell the
                user that closed tickets aren't included.  Otherwise
                the count looks lower than what the "Completed" chip
                shows and people wonder where their finished tasks
                went.  Clickable hint takes them to the Completed
                chip in one step. */}
            {!statusFilter && (buckets.completed.length + buckets.cancelled.length) > 0 && (
              <>
                {' · '}
                <button
                  type="button"
                  onClick={() => setStatusFilter('completed')}
                  className="text-muted-foreground underline decoration-dotted hover:text-foreground"
                >
                  {buckets.completed.length + buckets.cancelled.length} completed/cancelled hidden
                </button>
              </>
            )}
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
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-ok text-white rounded-full text-xs font-medium transition"
          >
            <CheckSquare size={14} />
            Mark complete
          </button>
          <button
            type="button"
            onClick={handleBulkInProgress}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-info text-white rounded-full text-xs font-medium transition"
          >
            In progress
          </button>
          <button
            type="button"
            onClick={handleBulkDelete}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-destructive/80 hover:bg-destructive text-destructive-foreground rounded-full text-xs font-medium transition"
          >
            <Trash2 size={14} />
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
            </dl>
            <div className="space-y-3">
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Status</span>
                <select value={eStatus} onChange={e => setEStatus(e.target.value)} className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring">
                  {STATUS_OPTIONS.map(s => <option key={s} value={s}>{STATUS_LABELS[s] || s}</option>)}
                </select>
              </label>
              {/* Type selector — lets the operator FIX a mistyped task
                  type after creation.  Without this they had to delete
                  the row and re-create just to flip e.g. "Oil Change"
                  to "Inspection".  Uses the same TypePicker the add
                  form does so custom types created here also surface
                  on the create form (and vice versa). */}
              <label className="block">
                <span className="block text-xs text-muted-foreground mb-1">Type</span>
                <TypePicker value={eType} onChange={setEType} />
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
          </div>
        </div>
      )}

      {historyVehicle && (
        <ServiceHistoryModal
          vehicleName={historyVehicle}
          onClose={() => setHistoryVehicle(null)}
        />
      )}

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
