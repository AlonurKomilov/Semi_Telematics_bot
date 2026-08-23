import { useState, useEffect, useMemo, useRef, type InputHTMLAttributes } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { WO_PREFILL_STATE_KEY, type WorkOrderPrefill } from './createFrom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { InfoTip, Tip } from '../../components/tooltip';
import {
  FileText, Save, ArrowLeft, Trash2, Plus, Paperclip,
  Receipt, X, Link as LinkIcon, Image as ImageIcon, Loader2,
  Sparkles,
} from 'lucide-react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '../../components/ui/dialog';
import {
  buildScanPatch, type InvoiceExtract, type ScanConflict,
} from './scanInvoice';
import {
  priceKey, verdictFor, summaryFor, detailFor,
  type PriceContextMap,
} from './priceContext';
import { apiJSON, apiFetch } from '../../api/client';
import { PageHeader, ErrorState } from '../../components/shell';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { toneClasses, toneText } from '../../lib/status';
import ServiceTaskPicker from '../service-tasks/ServiceTaskPicker';
import {
  SERVICE_TASKS_KEY, fetchServiceTasks, taskValue,
} from '../service-tasks/api';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import type {
  WorkOrder, WorkOrderDetail, WorkOrderAttachment,
  MaintenanceTask, CompanyInfo,
} from '../../types';
import { VehiclePicker, type VehicleSummary } from '../maintenance/pickers';
import { VendorPicker } from '../vendors/VendorPicker';
import { DIRECTORY_DISCLOSURE } from '../vendors/directoryCopy';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay, todayInTimeZone } from '../../utils/datetime';

// ── Empty-state factories ────────────────────────────────────────
//
// New work orders start with the bare minimum: vehicle, status=draft,
// everything else zero/blank.  The cost totals auto-update from parts
// + labor + tax as the user types.

const blankWorkOrder = (): Partial<WorkOrder> => ({
  vehicle_name: '',
  vehicle_type: '',
  company_code: '',
  vendor_name: '',
  vendor_address: '',
  vendor_phone: '',
  vendor_email: '',
  vendor_id: null,
  service_date: '',
  odometer_at_service: null,
  engine_hours_at_service: null,
  labor_cost: 0,
  parts_cost: 0,
  tax_amount: 0,
  fee_amount: 0,
  total_cost: 0,
  invoice_number: '',
  payment_method: '',
  payment_status: 'unpaid',
  status: 'open',
  repair_priority: '',
  complaint: '',
  cause: '',
  correction: '',
  notes: '',
  assigned_to: '',
});

// Display an integer with thousands separators (e.g. 496483 → "496,483").
// Empty when the value is null/undefined so the placeholder shows.
const fmtInt = (n: number | null | undefined): string =>
  n == null ? '' : n.toLocaleString('en-US');

// Parse a possibly-formatted integer string back to a number — strips
// commas/spaces and any stray non-digits.  Empty → null.
const parseInt0 = (s: string): number | null => {
  const digits = s.replace(/[^0-9]/g, '');
  return digits ? Number(digits) : null;
};

// Format a YYYY-MM-DD (or ISO timestamp) as a numeric date in the
// ACCOUNT's timezone (the single source of truth), routed through the
// shared formatter.  A bare calendar date is anchored at noon UTC so a
// tz conversion can never roll it to an adjacent day; a full timestamp
// passes through and renders on its account-local day.
const fmtDay = (s: string, tz?: string): string => {
  if (!s) return s;
  const v = s.length === 10 ? `${s}T12:00:00Z` : s;
  return formatDay(v, {
    timeZone: tz,
    intl: { year: 'numeric', month: 'numeric', day: 'numeric' },
  });
};

// ── Parts editor row state ──────────────────────────────────────
//
// Parts that haven't been saved yet have id=undefined; the form posts
// them after the work-order create.  Existing parts loaded from the
// server keep their numeric ids so we can DELETE individual rows.

interface DraftPart {
  id?: number;
  part_name: string;
  part_number: string;
  quantity: number;
  unit_cost: number;
  total_cost: number;
  warranty_months: number;
  /** Task-type slug this line belongs to ('brakes', 'custom_…');
   *  '' = the General (untagged) group.  Drives the task→parts
   *  grouping in the editor and the per-task cost report. */
  service_task: string;
  notes: string;
}

/**
 * Numeric input that keeps its own display STRING so the field can be
 * genuinely empty (never a stuck "0" you can't delete), typed freely
 * (no leading zero from a pre-filled 0 -> no "0100"), and still accept
 * decimals ("0.5") - while the model stays a number (empty => 0).  The
 * external value re-syncs the display only when it changes to something
 * the current text doesn't already represent (auto-computed totals,
 * loading a saved WO), so it never fights active typing.
 */
function NumberInput({
  value, onValueChange,
  ...rest
}: {
  value: number;
  onValueChange: (n: number) => void;
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'>) {
  const [text, setText] = useState(() => (value ? String(value) : ''));
  useEffect(() => {
    const cur = text.trim() === '' ? 0 : Number(text);
    if (!Number.isNaN(cur) && cur === value) return;
    setText(value ? String(value) : '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      {...rest}
      type="number"
      inputMode="decimal"
      value={text}
      placeholder={rest.placeholder ?? '0'}
      onChange={(e) => {
        setText(e.target.value);
        const n = e.target.value.trim() === '' ? 0 : Number(e.target.value);
        onValueChange(Number.isNaN(n) ? 0 : n);
      }}
    />
  );
}

const blankPart = (serviceTask = ''): DraftPart => ({
  part_name: '',
  part_number: '',
  quantity: 1,
  unit_cost: 0,
  total_cost: 0,
  warranty_months: 0,
  service_task: serviceTask,
  notes: '',
});

interface DraftLabor {
  id?: number;
  description: string;
  hours: number;
  rate: number;
  total_cost: number;
  service_task: string;
}

const blankLabor = (serviceTask = ''): DraftLabor => ({
  description: '', hours: 0, rate: 0, total_cost: 0, service_task: serviceTask,
});

// ── Component ────────────────────────────────────────────────────

export default function WorkOrderForm() {
  const { t } = useTranslation();
  const { id: idParam } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  // Account-effective timezone is the single source of truth for every
  // date the form reads or renders (what "today" is, how an as-of day
  // is shown) — never the browser locale or UTC.
  const tz = useTimezone();
  const isEdit = Boolean(idParam && idParam !== 'new');
  const workOrderId = isEdit ? Number(idParam) : null;

  // Cross-feature pre-fill (create mode only): a fault code or a failed
  // inspection item can hand us ``location.state.woPrefill`` — vehicle +
  // a complaint description + a priority guess.  Seeded once into the
  // initial state; the rest of the form (and the POST body it spreads)
  // treats those exactly like operator-typed values.  Ignored in edit
  // mode, where the server copy is authoritative.
  const location = useLocation();
  const [wo, setWo] = useState<Partial<WorkOrder>>(() => {
    const base = blankWorkOrder();
    if (isEdit) return base;
    const seed = (location.state as { [WO_PREFILL_STATE_KEY]?: WorkOrderPrefill } | null)
      ?.[WO_PREFILL_STATE_KEY];
    return seed ? { ...base, ...seed } : base;
  });
  const [parts, setParts] = useState<DraftPart[]>([]);
  // Itemized labor (Tier-2 B1) — optional; when any lines exist the
  // labor_cost scalar becomes their derived sum (server re-enforces).
  const [laborLines, setLaborLines] = useState<DraftLabor[]>([]);
  // Ordered service-task group keys shown in the parts editor (the
  // '' General group is implicit and always renders last when it has
  // rows).  A group can exist before it has parts so the operator can
  // "open" a task then add lines under it.
  const [taskGroups, setTaskGroups] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<WorkOrderAttachment[]>([]);
  const [linkedTasks, setLinkedTasks] = useState<MaintenanceTask[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [uploadingFile, setUploadingFile] = useState(false);

  // ── Scan invoice (AI pre-fill) ─────────────────────────────────
  // The picked file is HELD here in create mode (no WO id yet) and
  // auto-uploaded as the `invoice` attachment right after Save creates
  // the WO; edit mode uploads immediately.  `aiFilled` drives the
  // per-field highlight — a key leaves the set the moment the user
  // edits that field, so the marker always means "AI wrote this,
  // human hasn't touched it yet".
  const [scanning, setScanning] = useState(false);
  const scanFileRef = useRef<File | null>(null);
  const scanInputRef = useRef<HTMLInputElement | null>(null);
  const [aiFilled, setAiFilled] = useState<Set<string>>(() => new Set());
  const [scanSummary, setScanSummary] = useState<{
    filled: number; partsAdded: number; laborAdded: number;
    warnings: string[]; notes: string; linesSkipped: boolean;
  } | null>(null);
  // Overwrite-confirm dialog: non-empty fields the invoice disagrees
  // with.  NOTHING overwrites silently (owner contract) — each row is
  // a checkbox the user approves.
  const [scanConflicts, setScanConflicts] = useState<{
    list: ScanConflict[];
    overwrites: Record<string, unknown>;
    checked: Record<string, boolean>;
  } | null>(null);

  // Hydrate from server in edit mode.  React Query gives us the loading
  // affordance + auto-refetch on revisit.
  const { data: detail, isLoading: detailLoading } = useQuery<WorkOrderDetail>({
    queryKey: ['work-order', workOrderId],
    queryFn: () => apiJSON<WorkOrderDetail>(`/work-orders/${workOrderId}`),
    enabled: isEdit,
  });

  // Company roster for the picker — the WO stores a company_code; the
  // form shows the full names and lets the operator set/change it.
  const { data: companiesData } = useQuery<{ companies: CompanyInfo[] }>({
    queryKey: ['admin-companies'],
    queryFn: () => apiJSON<{ companies: CompanyInfo[] }>('/admin/companies'),
    staleTime: 5 * 60_000,
  });
  const companies = companiesData?.companies ?? [];

  // Parts catalog for the editor's autocomplete (native <datalist> —
  // suggestions only; the server resolves/creates the catalog link
  // from whatever name is saved, picked or free-typed).
  const { data: catalogData } = useQuery<{ parts: Array<{ id: number; name: string }> }>({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON('/parts'),
    staleTime: 60_000,
  });
  const catalogParts = catalogData?.parts ?? [];

  // Account fleet for the Vehicle picker (registry-backed, already
  // permission/company-scoped server-side).  Same paged shape +
  // VehicleSummary the Maintenance picker uses, so picking a vehicle can
  // auto-fill type / company / odometer / hours.
  const { data: fleetData, isLoading: fleetLoading } = useQuery({
    queryKey: ['fleet-vehicles'],
    queryFn: async () => {
      const all: VehicleSummary[] = [];
      let page = 1;
      while (true) {
        const res = await apiJSON<{ vehicles: VehicleSummary[]; total_pages: number }>(
          `/vehicles?page_size=200&page=${page}`,
        );
        all.push(...(res.vehicles ?? []));
        if (page >= (res.total_pages ?? 1)) break;
        page++;
      }
      return { vehicles: all };
    },
    staleTime: 60_000,
  });
  const fleetVehicles = fleetData?.vehicles ?? [];

  // Small note under the odometer explaining where the reading came from.
  const [asOfNote, setAsOfNote] = useState('');
  // True while the as-of reading is being fetched — drives a "checking…"
  // hint so the field doesn't look frozen during the round trip.
  const [asOfLoading, setAsOfLoading] = useState(false);
  // The picked vehicle's CURRENT reading — so a date with NO history can
  // fall back to it instead of getting stuck on a previous date's value.
  const [pickedCurrent, setPickedCurrent] =
    useState<{ odometer: number | null; hours: number | null } | null>(null);
  // Monotonic request id — newest as-of fetch wins (see fetchAsOf).
  const asOfSeq = useRef(0);

  // "Today" in the ACCOUNT's timezone — UTC here would flip the date a
  // day early/late near midnight and wrongly decide whether a service
  // date is "past".  Shared helper so every site agrees on "today".
  const todayStr = () => todayInTimeZone(tz);

  // Pull the odometer/engine-hours AS OF a date and apply them — so a
  // back-dated WO shows the mileage the truck had then, not today's.
  // ``current`` is the vehicle's present reading; when the date has no
  // history we reset to it (never leave a stale other-date value).
  const fetchAsOf = async (
    name: string, date: string,
    current: { odometer: number | null; hours: number | null } | null,
  ) => {
    if (!name.trim() || !date) return;
    // Stale-guard: rapid date changes fire overlapping requests; only the
    // newest one may apply its result (else a slow earlier reply clobbers
    // the value the user actually picked).
    const seq = ++asOfSeq.current;
    const isStale = () => seq !== asOfSeq.current;
    setAsOfLoading(true);
    setAsOfNote('Checking reading for that date…');
    try {
      const r = await apiJSON<{
        odometer_miles: number | null;
        engine_hours: number | null;
        as_of: string | null;
        telematics_linked?: boolean;
        coverage_start?: string | null;
        coverage_end?: string | null;
      }>(`/vehicles/${encodeURIComponent(name)}/reading-as-of?date=${date}`);
      if (isStale()) return;
      if (r.odometer_miles != null || r.engine_hours != null) {
        setWo(prev => ({
          ...prev,
          odometer_at_service: r.odometer_miles != null
            ? Math.round(r.odometer_miles) : prev.odometer_at_service,
          engine_hours_at_service: r.engine_hours != null
            ? Math.round(r.engine_hours) : prev.engine_hours_at_service,
        }));
        // Clarify when the reading we found is OLDER than the service
        // date: the truck hasn't reported since ``as_of``, so every
        // service date after it resolves to the same reading.  Without
        // this the note ("as of 6/8") looks stuck/wrong next to a 6/15
        // service date.
        if (!r.as_of) {
          setAsOfNote('');
        } else if (r.as_of.slice(0, 10) === date) {
          setAsOfNote(`Odometer/hours as of ${fmtDay(r.as_of, tz)}`);
        } else {
          setAsOfNote(
            `Last reading before ${fmtDay(date, tz)}: ${fmtDay(r.as_of, tz)} `
            + '(no newer telematics yet)',
          );
        }
        return;
      }
      // No snapshot for that date.  Reset to the vehicle's CURRENT reading
      // (so the field reflects THIS vehicle, never a stale earlier date),
      // and explain WHY no historical reading exists.
      const hasCurrent = current && (current.odometer != null || current.hours != null);
      if (hasCurrent) {
        setWo(prev => ({
          ...prev,
          odometer_at_service: current!.odometer != null
            ? Math.round(current!.odometer) : prev.odometer_at_service,
          engine_hours_at_service: current!.hours != null
            ? Math.round(current!.hours) : prev.engine_hours_at_service,
        }));
      }
      const tail = hasCurrent ? " — showing the vehicle's current reading." : '.';
      let why: string;
      if (r.telematics_linked === false) {
        why = 'No telematics history for this vehicle — enter the odometer manually';
      } else if (r.coverage_start && date < r.coverage_start.slice(0, 10)) {
        // The date predates the earliest reading we hold.
        why = `Only tracked since ${fmtDay(r.coverage_start, tz)}`;
      } else {
        why = 'No reading stored for that date';
      }
      setAsOfNote(why + tail);
    } catch {
      if (!isStale()) setAsOfNote('');
    } finally {
      if (!isStale()) setAsOfLoading(false);
    }
  };

  // Service date change → re-pull the reading as of the new date for the
  // selected vehicle (back-dated WOs).
  const onServiceDateChange = (date: string) => {
    setField('service_date', date || null);
    if (wo.vehicle_name && date) fetchAsOf(wo.vehicle_name, date, pickedCurrent);
    else setAsOfNote('');
  };

  // Vehicle field change.  Two cases:
  //  • free typing (v === null) → only update the name, touch nothing else.
  //  • explicit PICK (v set) → REFRESH the vehicle-derived fields to that
  //    vehicle (overwrite, since the vehicle changed — picking B must not
  //    leave A's odometer/company stuck).  A vehicle that reports no
  //    odometer/hours clears those so a previous vehicle's reading never
  //    lingers.  Service date defaults to today only if still unset.
  const onVehiclePick = (name: string, v: VehicleSummary | null) => {
    setWo(prev => {
      const next: Partial<WorkOrder> = { ...prev, vehicle_name: name };
      if (v) {
        next.vehicle_type = v.vehicle_type || '';
        next.company_code = v.company || '';
        next.odometer_at_service = v.odometer_miles != null
          ? Math.round(v.odometer_miles) : null;
        next.engine_hours_at_service = v.engine_hours != null
          ? Math.round(v.engine_hours) : null;
        if (!prev.service_date) next.service_date = todayStr();
      }
      return next;
    });
    if (!v) {
      // Free-typed name — no vehicle to anchor a current reading to.
      setPickedCurrent(null);
      return;
    }
    // Remember the vehicle's CURRENT reading so a date with no history
    // can fall back to it (and never get stuck on an earlier date).
    const current = {
      odometer: v.odometer_miles ?? null,
      hours: v.engine_hours ?? null,
    };
    setPickedCurrent(current);
    // For a back-dated WO, refine odometer/hours to that date's reading.
    const d = wo.service_date || todayStr();
    if (d < todayStr()) fetchAsOf(name, d, current);
    else setAsOfNote('');
  };

  useEffect(() => {
    if (!detail) return;
    setWo(detail.work_order);
    setParts(detail.parts.map(p => ({ service_task: '', ...p })));
    // Seed the task groups from the loaded parts, first-seen order.
    setTaskGroups(Array.from(new Set([
      ...detail.parts.map(p => (p as { service_task?: string }).service_task || ''),
      ...(detail.labor ?? []).map(l => l.service_task || ''),
    ].filter(Boolean))));
    setLaborLines((detail.labor ?? []) as DraftLabor[]);
    setAttachments(detail.attachments);
    setLinkedTasks(detail.linked_tasks);
  }, [detail]);

  // ── Unsaved-changes guard (UX audit P5) ──────────────────────────
  // Snapshot the form ONCE it's populated (new mode: at mount; edit
  // mode: when the server copy arrives); any later divergence marks it
  // dirty.  Protects long line-item/labor edits from an accidental
  // back / refresh / tab-close.
  const initialSnapRef = useRef<string | null>(null);
  const formSnapshot = useMemo(
    () => JSON.stringify({ wo, parts, laborLines }),
    [wo, parts, laborLines],
  );
  useEffect(() => {
    if (initialSnapRef.current !== null) return;
    if (isEdit && !detail) return;               // wait for the server copy
    initialSnapRef.current = formSnapshot;
  }, [isEdit, detail, formSnapshot]);
  const dirty = initialSnapRef.current !== null
    && formSnapshot !== initialSnapRef.current && !saving;

  // Browser-level unload (close / refresh / external nav).
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

  // In-app nav (this component router has no data-router blocker) —
  // confirm on the back affordance when there are unsaved edits.
  const leaveGuard = (to: string) => {
    if (dirty && !window.confirm(t('work_orders_page.unsaved_confirm', {
      defaultValue: 'You have unsaved changes. Leave without saving?',
    }))) return;
    navigate(to);
  };

  // Auto-totals: parts + labor are the line items (task groups above);
  // fee + tax are the header add-ons.  total = labor+parts+fee+tax,
  // recomputed as a derived value so the displayed numbers always
  // match what'll be sent.
  const partsCostComputed = useMemo(
    () => parts.reduce((acc, p) => acc + (Number(p.total_cost) || 0), 0),
    [parts],
  );
  const laborCostComputed = useMemo(
    () => laborLines.reduce((acc, l) => acc + (Number(l.total_cost) || 0), 0),
    [laborLines],
  );
  // Labor comes from the task-group line items (or a legacy scalar on
  // old records); it's display-only in Costs now — a Fee field took the
  // old editable Labor slot.
  const effectiveLaborCost = laborLines.length
    ? laborCostComputed
    : (Number(wo.labor_cost) || 0);
  const totalCostComputed = useMemo(
    () => effectiveLaborCost + partsCostComputed
      + (Number(wo.fee_amount) || 0) + (Number(wo.tax_amount) || 0),
    [effectiveLaborCost, wo.fee_amount, wo.tax_amount, partsCostComputed],
  );

  // ── Field helpers ──────────────────────────────────────────────
  const setField = <K extends keyof WorkOrder>(key: K, value: WorkOrder[K] | null) => {
    setWo(prev => ({ ...prev, [key]: value }));
    // A human edit retires the "AI-filled" marker for that field.
    setAiFilled(prev => {
      if (!prev.has(key as string)) return prev;
      const next = new Set(prev);
      next.delete(key as string);
      return next;
    });
  };

  /** Highlight class for a field the scan filled and the user hasn't
   *  touched yet — cleared by setField on first human edit. */
  const aiCls = (key: string) =>
    aiFilled.has(key) ? ' ring-1 ring-primary/40 border-primary/40' : '';

  const updatePart = (idx: number, patch: Partial<DraftPart>) =>
    setParts(prev => prev.map((p, i) => {
      if (i !== idx) return p;
      const merged = { ...p, ...patch };
      // Auto-fill total_cost = qty × unit_cost ONLY if the user hasn't
      // manually typed something different.  We detect "hasn't typed"
      // by checking if the prior total_cost equals prior qty × unit.
      const prevAuto = (p.quantity || 0) * (p.unit_cost || 0);
      const prevWasAuto = Math.abs((p.total_cost || 0) - prevAuto) < 0.01;
      if (prevWasAuto && patch.total_cost === undefined) {
        merged.total_cost = (merged.quantity || 0) * (merged.unit_cost || 0);
      }
      return merged;
    }));

  const removePart = async (idx: number) => {
    const target = parts[idx];
    if (target?.id && workOrderId) {
      try {
        await apiJSON(`/work-orders/${workOrderId}/parts/${target.id}`, { method: 'DELETE' });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_part_delete_failed'));
        return;
      }
    }
    setParts(prev => prev.filter((_, i) => i !== idx));
  };

  // ── Labor line helpers ────────────────────────────────────────

  const updateLaborLine = (idx: number, patch: Partial<DraftLabor>) =>
    setLaborLines(prev => prev.map((l, i) => {
      if (i !== idx) return l;
      const merged = { ...l, ...patch };
      // hours × rate auto-fills the total while both are set; a flat
      // total typed directly (hours/rate 0) stays untouched.
      if (('hours' in patch || 'rate' in patch) && merged.hours && merged.rate) {
        merged.total_cost = Math.round(merged.hours * merged.rate * 100) / 100;
      }
      return merged;
    }));

  const removeLaborLine = async (idx: number) => {
    const target = laborLines[idx];
    if (target?.id && workOrderId) {
      try {
        await apiJSON(`/work-orders/${workOrderId}/labor/${target.id}`, { method: 'DELETE' });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_save_failed'));
        return;
      }
    }
    setLaborLines(prev => prev.filter((_, i) => i !== idx));
  };

  // ── Service-task grouping helpers ─────────────────────────────
  //
  // The editor shows parts grouped under task headers (task → parts
  // hierarchy).  Groups are just distinct ``service_task`` values —
  // no separate entity — so "renaming" a group retags its parts and
  // "removing" one untags them into the trailing General section.
  const { has } = useViewPermissions();
  // Creating a NEW custom task type goes through the maintenance
  // endpoint gated by can_maintenance_all — hide the inline creator
  // from anyone who'd 403 on it.
  // Matches the backend gate on POST /service-tasks — it was
  // can_maintenance_all before, which both offered the option to roles
  // the server refuses AND hid it from roles the server allows.
  const canManageTaskTypes = has('can_service_tasks');

  const entriesFor = (task: string) =>
    parts.map((pt, idx) => ({ pt, idx })).filter(e => (e.pt.service_task || '') === task);
  const laborEntriesFor = (task: string) =>
    laborLines.map((l, idx) => ({ l, idx })).filter(e => (e.l.service_task || '') === task);
  const groupSubtotal = (task: string) =>
    entriesFor(task).reduce((acc, e) => acc + (e.pt.total_cost || 0), 0);
  const groupLaborSubtotal = (task: string) =>
    laborEntriesFor(task).reduce((acc, e) => acc + (e.l.total_cost || 0), 0);

  // "Is this price normal?" — from the account's OWN buying history.
  // Answers the question at the moment it's actionable (invoice in
  // hand) rather than on a part profile nobody visits mid-entry.
  // Debounced on the set of part NAMES, so typing a name doesn't
  // fire a request per keystroke.
  const [priceCtx, setPriceCtx] = useState<PriceContextMap>({});
  const partNamesKey = useMemo(
    () => Array.from(new Set(
      parts.map(p => priceKey(p.part_name)).filter(Boolean),
    )).sort().join('|'),
    [parts],
  );
  useEffect(() => {
    if (!partNamesKey) { setPriceCtx({}); return; }
    let alive = true;
    const timer = setTimeout(() => {
      apiJSON<{ context: PriceContextMap }>('/parts/price-context', {
        method: 'POST', body: { names: partNamesKey.split('|') },
      })
        .then(r => { if (alive) setPriceCtx(r.context ?? {}); })
        .catch(() => { /* context is a bonus, never a blocker */ });
    }, 400);
    return () => { alive = false; clearTimeout(timer); };
  }, [partNamesKey]);

  // Service-task vocabulary — the shared list Maintenance and this form
  // both pick from (features/service-tasks).  Also what "Add service
  // task" walks to open the next unused group.
  const { data: serviceTasksData } = useQuery({
    queryKey: SERVICE_TASKS_KEY,
    queryFn: () => fetchServiceTasks(),
    staleTime: 60_000,
  });
  const serviceTasks = serviceTasksData?.service_tasks;

  /**
   * Pull a task's DEFAULTS onto its group: the parts it usually needs
   * and its labor estimate.  Fill-empty-only — a group that already
   * has lines is left alone, so picking a task can never overwrite
   * what someone typed (the same contract Scan invoice follows).  The
   * toast keeps it from being a silent change.
   */
  const applyTaskDefaults = async (task: string) => {
    if (!task) return;
    if (entriesFor(task).length || laborEntriesFor(task).length) return;
    let defaults: {
      found: boolean; name?: string; expected_labor_hours: number;
      parts: Array<{ part_name: string; part_number: string; quantity: number }>;
    };
    try {
      defaults = await apiJSON(
        `/service-tasks/defaults?task=${encodeURIComponent(task)}`,
      );
    } catch {
      return;   // defaults are a convenience, never a blocker
    }
    if (!defaults?.found) return;
    const newParts = (defaults.parts ?? []).map(p => ({
      ...blankPart(task),
      part_name: p.part_name,
      part_number: p.part_number || '',
      quantity: Number(p.quantity) || 1,
    }));
    const hours = Number(defaults.expected_labor_hours) || 0;
    const newLabor = hours > 0
      ? [{ ...blankLabor(task), description: defaults.name || task, hours }]
      : [];
    if (!newParts.length && !newLabor.length) return;
    if (newParts.length) setParts(prev => [...prev, ...newParts]);
    if (newLabor.length) setLaborLines(prev => [...prev, ...newLabor]);
    toast.success(t('work_orders_page.task_defaults_applied', {
      defaultValue: 'Added this task\u2019s usual parts — check the amounts.',
    }));
  };

  const addTaskGroup = () => {
    // Default the new group to the first built-in type not in use —
    // the operator immediately changes it via the header picker.
    const used = new Set(taskGroups);
    const next = (serviceTasks ?? [])
      .map(taskValue)
      .find(v => !used.has(v));
    if (!next) return;   // every task already open — unlikely
    setTaskGroups(prev => [...prev, next]);
    void applyTaskDefaults(next);
  };

  const renameTaskGroup = (from: string, to: string) => {
    if (!to || to === from) return;
    // Same value picked twice → the two groups merge (Set dedupes).
    setTaskGroups(prev => Array.from(new Set(prev.map(g => (g === from ? to : g)))));
    setParts(prev => prev.map(pt => (pt.service_task === from ? { ...pt, service_task: to } : pt)));
    setLaborLines(prev => prev.map(l => (l.service_task === from ? { ...l, service_task: to } : l)));
    void applyTaskDefaults(to);
  };

  const removeTaskGroup = (task: string) => {
    // Never deletes part lines — they fall back to General (untagged).
    setTaskGroups(prev => prev.filter(g => g !== task));
    setParts(prev => prev.map(pt => (pt.service_task === task ? { ...pt, service_task: '' } : pt)));
    setLaborLines(prev => prev.map(l => (l.service_task === task ? { ...l, service_task: '' } : l)));
  };

  // One parts table per task group.  Entries carry the ORIGINAL index
  // into ``parts`` so updatePart / removePart address the right row
  // regardless of grouping.  (Raw <table>: the sanctioned form-embedded
  // line-item-editor exception to the DataGrid rule.)
  const renderPartsTable = (entries: Array<{ pt: DraftPart; idx: number }>) => {
    if (entries.length === 0) {
      return (
        <p className="px-3 py-2.5 text-xs text-muted-foreground">
          {t('work_orders_page.group_empty_hint')}
        </p>
      );
    }
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[680px]">
          <thead>
            <tr className="text-xs text-muted-foreground border-b border-border">
              <th className="text-left font-medium py-1.5 px-2">{t('work_orders_page.col_part')}</th>
              <th className="text-left font-medium py-1.5 px-2 w-28">{t('work_orders_page.col_part_number')}</th>
              <th className="text-right font-medium py-1.5 px-2 w-16">{t('work_orders_page.col_qty')}</th>
              <th className="text-right font-medium py-1.5 px-2 w-24">{t('work_orders_page.col_unit')}</th>
              <th className="text-right font-medium py-1.5 px-2 w-24">{t('work_orders_page.col_total')}</th>
              <th className="text-right font-medium py-1.5 px-2 w-20">{t('work_orders_page.col_warranty')}</th>
              <th className="py-1.5 px-2 w-8"></th>
            </tr>
          </thead>
          <tbody>
            {entries.map(({ pt, idx }) => (
              <tr key={pt.id ?? `new-${idx}`} className="border-b border-border/40">
                <td className="py-1.5 px-2">
                  <input
                    type="text"
                    list="wo-parts-catalog"
                    value={pt.part_name}
                    onChange={e => updatePart(idx, { part_name: e.target.value })}
                    placeholder={t('work_orders_page.ph_part_name')}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm focus:outline-none focus:bg-muted/40 rounded"
                  />
                  {(() => {
                    const c = priceCtx[priceKey(pt.part_name)];
                    if (!c) return null;
                    const v = verdictFor(pt.unit_cost, c);
                    return (
                      <Tip label={detailFor(c)}>
                        <span className={`mt-0.5 block text-2xs cursor-help ${
                          v === 'above' ? toneText('warn') : 'text-muted-foreground'
                        }`}>
                          {v === 'above' ? '\u2191 ' : ''}{summaryFor(c)}
                        </span>
                      </Tip>
                    );
                  })()}
                </td>
                <td className="py-1.5 px-2">
                  <input
                    type="text"
                    value={pt.part_number}
                    onChange={e => updatePart(idx, { part_number: e.target.value })}
                    placeholder={t('work_orders_page.ph_part_number')}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-xs font-mono focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="1"
                    value={pt.quantity}
                    onValueChange={v => updatePart(idx, { quantity: v })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="0.01"
                    value={pt.unit_cost}
                    onValueChange={v => updatePart(idx, { unit_cost: v })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="0.01"
                    value={pt.total_cost}
                    onValueChange={v => updatePart(idx, { total_cost: v })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums font-medium focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="1"
                    value={pt.warranty_months}
                    onValueChange={v => updatePart(idx, { warranty_months: v })}
                    placeholder="0"
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2 text-right">
                  <Tip label={t('work_orders_page.remove_part')}>
                    <button
                      type="button"
                      onClick={() => removePart(idx)}
                      className="text-muted-foreground hover:text-destructive p-1"
                      aria-label={t('work_orders_page.remove_part')}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </Tip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  // ── Save ──────────────────────────────────────────────────────
  //
  // Labor sub-table for one task group — same sanctioned raw-table
  // exception as parts.  The group defines service_task, so lines
  // carry no per-row picker.
  const renderLaborTable = (entries: Array<{ l: DraftLabor; idx: number }>) => {
    if (entries.length === 0) return null;
    return (
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full text-sm min-w-[560px]">
          <thead>
            <tr className="text-left text-xs text-muted-foreground border-b border-border">
              <th className="py-1.5 px-2 font-medium">
                {t('work_orders_page.labor_desc', { defaultValue: 'Labor' })}
              </th>
              <th className="py-1.5 px-2 font-medium w-20 text-right">{t('work_orders_page.labor_hours', { defaultValue: 'Hours' })}</th>
              <th className="py-1.5 px-2 font-medium w-24 text-right">{t('work_orders_page.labor_rate', { defaultValue: 'Rate' })}</th>
              <th className="py-1.5 px-2 font-medium w-24 text-right">{t('work_orders_page.labor_total', { defaultValue: 'Total' })}</th>
              <th className="py-1.5 pl-2 w-8" />
            </tr>
          </thead>
          <tbody>
            {entries.map(({ l, idx }) => (
              <tr key={l.id ?? `new-${idx}`} className="border-b border-border/50 last:border-0">
                <td className="py-1.5 px-2">
                  <input
                    type="text"
                    value={l.description}
                    onChange={e => updateLaborLine(idx, { description: e.target.value })}
                    placeholder={t('work_orders_page.labor_desc_ph', { defaultValue: 'e.g. Brake job labor' })}
                    className="w-full bg-muted border border-border rounded px-2 py-1 text-sm text-foreground focus:outline-none focus:border-ring"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="0.25"
                    value={l.hours}
                    onValueChange={v => updateLaborLine(idx, { hours: v })}
                    className="w-full bg-muted border border-border rounded px-2 py-1 text-sm text-right tabular-nums text-foreground focus:outline-none focus:border-ring"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="0.01"
                    value={l.rate}
                    onValueChange={v => updateLaborLine(idx, { rate: v })}
                    className="w-full bg-muted border border-border rounded px-2 py-1 text-sm text-right tabular-nums text-foreground focus:outline-none focus:border-ring"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <NumberInput
                    min="0" step="0.01"
                    value={l.total_cost}
                    onValueChange={v => updateLaborLine(idx, { total_cost: v })}
                    className="w-full bg-muted border border-border rounded px-2 py-1 text-sm text-right tabular-nums text-foreground focus:outline-none focus:border-ring"
                  />
                </td>
                <td className="py-1.5 pl-2 text-right">
                  <Tip label={t('work_orders_page.remove_labor', { defaultValue: 'Remove labor line' })}>
                    <button
                      type="button"
                      onClick={() => removeLaborLine(idx)}
                      aria-label={t('work_orders_page.remove_labor', { defaultValue: 'Remove labor line' })}
                      className="text-muted-foreground hover:text-destructive p-1"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </Tip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  // ── Scan invoice: extract → prefill → review ──────────────────
  //
  // Transient extract call (nothing persists server-side); the mapper
  // enforces the overwrite contract — empty fields fill silently,
  // non-empty fields go to the confirm dialog.  Edit mode archives the
  // file as the WO's invoice attachment immediately; create mode holds
  // it and handleSave uploads once the id exists.

  const onScanFile = async (file: File) => {
    if (file.size > 10 * 1024 * 1024) {
      toast.error(t('work_orders_page.scan_too_big', {
        defaultValue: 'File is over the 10 MB limit.',
      }));
      return;
    }
    setScanning(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await apiFetch('/work-orders/extract-invoice', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '' }));
        toast.error(typeof err.detail === 'string' && err.detail
          ? err.detail
          : t('work_orders_page.scan_failed', {
              defaultValue: 'Could not read this invoice — try a clearer photo.',
            }));
        return;
      }
      const extract = await res.json() as InvoiceExtract;
      const r = buildScanPatch(extract, wo as Record<string, unknown>, {
        vehicles: fleetVehicles.map(v => ({ name: v.name, vehicle_type: v.vehicle_type })),
        hasExistingLines: parts.length > 0 || laborLines.length > 0,
      });
      if (Object.keys(r.patch).length) setWo(prev => ({ ...prev, ...r.patch }));
      if (r.parts.length) setParts(prev => [...prev, ...r.parts]);
      if (r.labor.length) setLaborLines(prev => [...prev, ...r.labor]);
      setAiFilled(new Set(r.filled));
      setScanSummary({
        filled: r.filled.length,
        partsAdded: r.parts.length,
        laborAdded: r.labor.length,
        warnings: extract.warnings ?? [],
        notes: extract.notes ?? '',
        linesSkipped: r.linesSkipped,
      });
      if (r.conflicts.length) {
        setScanConflicts({
          list: r.conflicts,
          overwrites: r.overwrites,
          checked: Object.fromEntries(r.conflicts.map(c => [c.key, true])),
        });
      }
      if (isEdit && workOrderId) {
        void handleUpload(file, 'invoice');
      } else {
        scanFileRef.current = file;
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.scan_failed', {
        defaultValue: 'Could not read this invoice — try a clearer photo.',
      }));
    } finally {
      setScanning(false);
    }
  };

  // Overwrite-confirm dialog "apply": ONLY ticked rows change; the
  // human decided per-field, so applied keys get the AI marker too.
  const applyScanOverwrites = () => {
    if (!scanConflicts) return;
    const chosen: Record<string, unknown> = {};
    const keys: string[] = [];
    for (const c of scanConflicts.list) {
      if (scanConflicts.checked[c.key]) {
        chosen[c.key] = scanConflicts.overwrites[c.key];
        keys.push(c.key);
      }
    }
    if (keys.length) {
      setWo(prev => ({ ...prev, ...chosen }));
      setAiFilled(prev => new Set([...prev, ...keys]));
    }
    setScanConflicts(null);
  };

  const scanFieldLabel = (key: string): string => ({
    vendor_name: t('work_orders_page.field_vendor_name'),
    vendor_phone: t('work_orders_page.field_vendor_phone'),
    vendor_email: t('work_orders_page.field_vendor_email', { defaultValue: 'Vendor Email' }),
    vendor_address: t('work_orders_page.field_vendor_address'),
    invoice_number: t('work_orders_page.field_invoice_number'),
    service_date: t('work_orders_page.field_service_date'),
    odometer_at_service: t('work_orders_page.field_odometer'),
    fee_amount: t('work_orders_page.field_fee', { defaultValue: 'Fee $' }),
    tax_amount: t('work_orders_page.field_tax'),
    vehicle_name: t('work_orders_page.field_vehicle'),
  } as Record<string, string>)[key] ?? key;

  // Two-phase save in CREATE mode: first POST the work order to get an
  // id, then POST each unsaved part.  Edit mode is simpler — PUT the
  // work order, POST any newly-added parts (existing parts already
  // exist server-side).  Both modes keep ``parts_cost`` and
  // ``total_cost`` aligned with the computed values so the report
  // aggregations stay accurate.

  const handleSave = async () => {
    if (!wo.vehicle_name?.trim()) {
      setError(t('work_orders_page.toast_vehicle_required'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      const payload = {
        ...wo,
        labor_cost: effectiveLaborCost,
        parts_cost: partsCostComputed,
        total_cost: totalCostComputed,
      };
      let savedId = workOrderId;
      if (isEdit && workOrderId) {
        await apiJSON(`/work-orders/${workOrderId}`, { method: 'PUT', body: payload });
      } else {
        const res = await apiJSON<{ id: number }>('/work-orders', { method: 'POST', body: payload });
        savedId = res.id;
      }
      for (const p of parts) {
        if (p.id || !savedId) continue;
        if (!p.part_name.trim()) continue;
        const { id: _id, ...partPayload } = p;
        await apiJSON(`/work-orders/${savedId}/parts`, { method: 'POST', body: partPayload as Record<string, unknown> });
      }
      for (const l of laborLines) {
        if (l.id || !savedId) continue;
        if (!l.description.trim()) continue;
        const { id: _lid, ...laborPayload } = l;
        await apiJSON(`/work-orders/${savedId}/labor`, { method: 'POST', body: laborPayload as Record<string, unknown> });
      }
      // A scanned invoice picked in CREATE mode was held on the device
      // (no WO id existed) — archive it as the invoice attachment now.
      // Non-fatal: the WO is already saved; a failed upload just asks
      // the user to attach from the WO page.
      if (!isEdit && savedId && scanFileRef.current) {
        try {
          const fd = new FormData();
          fd.append('file', scanFileRef.current);
          const up = await apiFetch(
            `/work-orders/${savedId}/attachments?kind=invoice`,
            { method: 'POST', body: fd },
          );
          if (!up.ok) throw new Error(String(up.status));
          scanFileRef.current = null;
        } catch {
          toast.warning(t('work_orders_page.scan_attach_failed', {
            defaultValue: 'Work order saved, but the invoice file could not be attached — add it from the work order page.',
          }));
        }
      }
      qc.invalidateQueries({ queryKey: ['work-orders'] });
      if (savedId) qc.invalidateQueries({ queryKey: ['work-order', savedId] });
      toast.success(isEdit
        ? t('work_orders_page.toast_wo_updated')
        : t('work_orders_page.toast_wo_created', { id: savedId }));
      navigate(`/work-orders/${savedId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('work_orders_page.toast_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  // ── Attachment upload ─────────────────────────────────────────
  //
  // Only available after the work order exists (we need its id to
  // build the storage folder path).  In CREATE mode the user has to
  // save the draft first before they can attach files.

  const handleUpload = async (file: File, kind: string) => {
    if (!workOrderId) {
      toast.error(t('work_orders_page.toast_save_first'));
      return;
    }
    setUploadingFile(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await apiFetch(
        `/work-orders/${workOrderId}/attachments?kind=${encodeURIComponent(kind)}`,
        { method: 'POST', body: fd },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        toast.error(typeof err.detail === 'string' ? err.detail : t('work_orders_page.toast_upload_failed'));
        return;
      }
      toast.success(t('work_orders_page.toast_uploaded', { name: file.name }));
      qc.invalidateQueries({ queryKey: ['work-order', workOrderId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_upload_failed'));
    } finally {
      setUploadingFile(false);
    }
  };

  const handleDeleteAttachment = async (att: WorkOrderAttachment) => {
    if (!workOrderId) return;
    if (!window.confirm(t('work_orders_page.confirm_delete_attachment', { name: att.file_name }))) return;
    try {
      await apiJSON(`/work-orders/${workOrderId}/attachments/${att.id}`, { method: 'DELETE' });
      qc.invalidateQueries({ queryKey: ['work-order', workOrderId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_delete_failed'));
    }
  };

  if (isEdit && detailLoading) {
    return (
      <div>
        <PageHeader icon={FileText} title={t('work_orders_page.loading_title')} description={t('work_orders_page.loading_desc')} />
      </div>
    );
  }

  // Select item lists — ``items`` lets SelectValue render the label and
  // keeps the dropdown options in sync with the same source arrays.
  const vehicleTypeItems = [
    { value: '', label: t('work_orders_page.vehicle_type_unset') },
    { value: 'truck', label: t('work_orders_page.vehicle_type_truck') },
    { value: 'trailer', label: t('work_orders_page.vehicle_type_trailer') },
  ];
  // Lifecycle only — money state lives in payment_status (the old
  // status='paid' was folded away by migration 154).
  // Fleetio-standard lifecycle: Open → In Progress → Completed.
  const statusItems = [
    { value: 'open', label: t('work_orders_page.status_open', { defaultValue: 'Open' }) },
    { value: 'in_progress', label: t('work_orders_page.status_in_progress', { defaultValue: 'In Progress' }) },
    { value: 'completed', label: t('work_orders_page.status_completed', { defaultValue: 'Completed' }) },
  ];
  // Reason-for-repair class.  '' = unclassified (the honest default for
  // rows created before this field, or when the operator hasn't tagged it).
  const priorityItems = [
    { value: '', label: t('work_orders_page.priority_unset', { defaultValue: 'Not sure yet' }) },
    { value: 'scheduled', label: t('work_orders_page.priority_scheduled', { defaultValue: 'Scheduled' }) },
    { value: 'non_scheduled', label: t('work_orders_page.priority_non_scheduled', { defaultValue: 'Non-scheduled' }) },
    { value: 'emergency', label: t('work_orders_page.priority_emergency', { defaultValue: 'Emergency' }) },
  ];
  // 'void' renders as "Written off" so it never collides with the
  // Status field's own "Void" on the same screen (UX audit C1): the
  // stored VALUE stays 'void' (contract unchanged), only the label
  // diverges.
  const paymentStatusItems = [
    { value: 'unpaid', label: t('work_orders_page.pay_unpaid', { defaultValue: 'Unpaid' }) },
    { value: 'paid', label: t('work_orders_page.pay_paid', { defaultValue: 'Paid' }) },
    { value: 'partial', label: t('work_orders_page.pay_partial', { defaultValue: 'Partial' }) },
    { value: 'void', label: t('work_orders_page.pay_void', { defaultValue: 'Written off' }) },
  ];
  const companyItems = [
    { value: '', label: '— none —' },
    ...companies.map((c) => ({ value: c.code, label: c.display_name || c.code })),
  ];

  return (
    <div>
      <PageHeader
        icon={FileText}
        title={isEdit ? t('work_orders_page.form_title_edit', { id: workOrderId }) : t('work_orders_page.form_title_new')}
        description={isEdit ? t('work_orders_page.form_desc_edit') : t('work_orders_page.form_desc_new')}
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => leaveGuard('/work-orders')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
            >
              <ArrowLeft className="size-3.5" />
              {t('work_orders_page.back')}
            </button>
            {/* AI pre-fill from an invoice photo/PDF.  Transient
                extraction; the human reviews before anything saves. */}
            <input
              ref={scanInputRef}
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0];
                e.target.value = '';
                if (f) void onScanFile(f);
              }}
            />
            <button
              type="button"
              onClick={() => scanInputRef.current?.click()}
              disabled={scanning}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 disabled:opacity-50 rounded-md text-xs font-medium text-foreground transition border border-border"
            >
              {scanning
                ? <Loader2 className="animate-spin size-3.5" />
                : <Sparkles className="text-primary size-3.5" />}
              {scanning
                ? t('work_orders_page.scanning', { defaultValue: 'Reading invoice…' })
                : t('work_orders_page.scan_invoice', { defaultValue: 'Scan invoice' })}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition min-h-tap"
            >
              <Save className="size-3.5" />
              {saving ? t('work_orders_page.saving') : isEdit ? t('work_orders_page.save_changes') : t('work_orders_page.create_draft')}
            </button>
          </div>
        }
      />

      {error && (
        <div className="mb-4">
          <ErrorState message={error} />
        </div>
      )}

      {/* Scan result banner — what the AI filled and what to check.
          Highlights stay on the fields until the user edits them. */}
      {scanSummary && (
        <div className="mb-4 bg-primary/5 border border-primary/20 rounded-xl p-3 text-sm flex items-start gap-2.5">
          <Sparkles className="text-primary mt-0.5 shrink-0 size-4" />
          <div className="flex-1 min-w-0">
            <p className="font-medium text-foreground">
              {t('work_orders_page.scan_banner_title', {
                defaultValue: 'Filled from invoice — review before saving',
              })}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t('work_orders_page.scan_banner_counts', {
                defaultValue: '{{fields}} fields, {{parts}} part lines and {{labor}} labor lines were filled. Highlighted fields came from the scan.',
                fields: scanSummary.filled,
                parts: scanSummary.partsAdded,
                labor: scanSummary.laborAdded,
              })}
            </p>
            {scanSummary.linesSkipped && (
              <p className="text-xs text-muted-foreground mt-1">
                {t('work_orders_page.scan_lines_skipped', {
                  defaultValue: 'Invoice line items were not added because this work order already has lines — add them by hand if needed.',
                })}
              </p>
            )}
            {scanSummary.warnings.some(w => w.startsWith('totals_mismatch')) && (
              <p className={`text-xs mt-1 ${toneText('warn')}`}>
                {t('work_orders_page.scan_totals_mismatch', {
                  defaultValue: 'The line items don’t add up to the invoice’s printed total — double-check the amounts.',
                })}
              </p>
            )}
            {scanSummary.notes && (
              <p className="text-xs text-muted-foreground mt-1 italic">{scanSummary.notes}</p>
            )}
          </div>
          <button
            type="button"
            aria-label={t('work_orders_page.scan_banner_dismiss', { defaultValue: 'Dismiss' })}
            onClick={() => setScanSummary(null)}
            className="text-muted-foreground hover:text-foreground transition shrink-0"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}

      {/* ── Vehicle + status block ─────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
          <Receipt className="text-muted-foreground size-3.5" />
          {t('work_orders_page.section_shop_visit')}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label={t('work_orders_page.field_vehicle')} required>
            {/* Registry-backed picker — search any vehicle in the account
                (permission/company-scoped); selecting one auto-fills type,
                company, odometer + hours below.  Still free-typeable for a
                vehicle that isn't in the registry yet. */}
            <VehiclePicker
              value={wo.vehicle_name || ''}
              onChange={onVehiclePick}
              vehicles={fleetVehicles}
              loading={fleetLoading}
            />
          </Field>
          <Field label={t('work_orders_page.field_vehicle_type')}>
            <Select value={wo.vehicle_type || ''} onValueChange={(v) => setField('vehicle_type', v)} items={vehicleTypeItems}>
              <SelectTrigger className="w-full" aria-label={t('work_orders_page.field_vehicle_type')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {vehicleTypeItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t('work_orders_page.field_service_date')}>
            <input
              type="date"
              value={(wo.service_date || '').slice(0, 10)}
              onChange={e => onServiceDateChange(e.target.value)}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring${aiCls('service_date')}`}
            />
          </Field>
          <Field label={t('work_orders_page.field_status')}
            tip={t('work_orders_page.tip_status', { defaultValue:
              'The repair\u2019s lifecycle: Open = created / work not done, In Progress = being worked, Completed = finished. Whether it\u2019s paid lives in Payment status below.' })}>
            <Select value={wo.status || 'open'} onValueChange={(v) => setField('status', v)} items={statusItems}>
              <SelectTrigger className="w-full" aria-label={t('work_orders_page.field_status')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {statusItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t('work_orders_page.field_repair_priority', { defaultValue: 'Repair priority' })}
            tip={t('work_orders_page.tip_repair_priority', { defaultValue:
              'How this repair came about: Scheduled = planned maintenance, Non-scheduled = unplanned but not urgent, Emergency = roadside/breakdown. Not sure? Leave it “Not sure yet” — you can set it later. Used to break down repair spend by plannability.' })}>
            <Select value={wo.repair_priority || ''} onValueChange={(v) => setField('repair_priority', v)} items={priorityItems}>
              <SelectTrigger className="w-full" aria-label={t('work_orders_page.field_repair_priority', { defaultValue: 'Repair priority' })}><SelectValue /></SelectTrigger>
              <SelectContent>
                {priorityItems.map((it) => <SelectItem key={it.value || 'unset'} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t('work_orders_page.field_odometer')}>
            <input
              type="text" inputMode="numeric"
              value={fmtInt(wo.odometer_at_service)}
              onChange={e => setField('odometer_at_service', parseInt0(e.target.value))}
              placeholder={t('work_orders_page.ph_odometer')}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring${aiCls('odometer_at_service')}`}
            />
            {asOfNote && (
              <p className="mt-1 flex items-center gap-1 text-2xs text-muted-foreground">
                {asOfLoading && <Loader2 className="animate-spin size-3" />}
                {asOfNote}
              </p>
            )}
          </Field>
          <Field label={t('work_orders_page.field_engine_hours')}>
            <input
              type="text" inputMode="numeric"
              value={fmtInt(wo.engine_hours_at_service)}
              onChange={e => setField('engine_hours_at_service', parseInt0(e.target.value))}
              placeholder={t('work_orders_page.ph_engine_hours')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            />
          </Field>
          {/* Company — full names from the account's roster; stored as the
              compact code (shown on the list).  Synced WOs are matched by
              MC number, but the operator can set/override it here. */}
          <Field label="Company">
            <Select value={wo.company_code || ''} onValueChange={(v) => setField('company_code', v)} items={companyItems}>
              <SelectTrigger className="w-full" aria-label="Company"><SelectValue /></SelectTrigger>
              <SelectContent>
                {companyItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Assigned to">
            <input
              type="text"
              value={wo.assigned_to || ''}
              onChange={e => setField('assigned_to', e.target.value)}
              placeholder="e.g. Pat Driver"
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* ── Vendor block ───────────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_vendor')}</h3>
        {/* Contact fields feed the vendor REGISTRY (and, once complete,
            the public-directory review queue) — filling them here is
            what makes the shop usable across reports and the map. */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <Field label={t('work_orders_page.field_vendor_name')}>
            {/* Registry-first picker: choosing a vendor links vendor_id
                + auto-fills contact; free-typing a new name still saves
                (the server resolves-or-creates the registry row). */}
            <VendorPicker
              value={wo.vendor_name || ''}
              onChange={(name, vendor) => {
                setWo(prev => ({
                  ...prev,
                  vendor_name: name,
                  vendor_id: vendor ? vendor.id : null,
                  ...(vendor ? {
                    vendor_phone: prev.vendor_phone || vendor.phone,
                    vendor_address: prev.vendor_address || vendor.address,
                    vendor_email: prev.vendor_email || vendor.email,
                  } : {}),
                }));
              }}
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_phone')}>
            <input
              type="tel"
              value={wo.vendor_phone || ''}
              onChange={e => setField('vendor_phone', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_phone')}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring${aiCls('vendor_phone')}`}
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_email', { defaultValue: 'Vendor Email' })}>
            <input
              type="email"
              value={wo.vendor_email || ''}
              onChange={e => setField('vendor_email', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_email', { defaultValue: 'shop@example.com' })}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring${aiCls('vendor_email')}`}
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_address')} tip={DIRECTORY_DISCLOSURE}>
            <input
              type="text"
              value={wo.vendor_address || ''}
              onChange={e => setField('vendor_address', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_address')}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring${aiCls('vendor_address')}`}
            />
          </Field>
        </div>
      </section>

      {/* ── Parts editor — grouped by service task ──────────────
          Task → parts hierarchy: each group header is a ServiceTaskPicker
          (same vocabulary as Maintenance, built-ins + the account's
          custom types) and the lines under it are that task's parts.
          Grouping is just the ``service_task`` tag on each line, so
          cost reports can sum per task while a mixed invoice still
          splits correctly.  Untagged lines live in the trailing
          General section. */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold">{t('work_orders_page.section_parts_labor', { defaultValue: 'Parts & labor' })}</h3>
            <p className="text-2xs text-muted-foreground mt-0.5">
              ↻ {t('work_orders_page.parts_hint')}
            </p>
          </div>
          {/* Only "Add service task" lives at the section level — it
              creates a new task group.  Parts and labor are added
              INSIDE each task (including the General bucket below), so
              a line always has an owning task. */}
          <button
            type="button"
            onClick={addTaskGroup}
            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded min-h-tap"
          >
            <Plus className="size-3" />
            {t('work_orders_page.add_task_group')}
          </button>
        </div>
        {/* Shared suggestion list for every part-name input (native
            datalist: zero JS, browser-rendered dropdown). */}
        <datalist id="wo-parts-catalog">
          {catalogParts.map(cp => <option key={cp.id} value={cp.name} />)}
        </datalist>
        <div className="space-y-4">
            {taskGroups.map((gv) => (
              <div key={gv} className="border border-border rounded-lg overflow-hidden">
                <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-muted/60 border-b border-border">
                  <ServiceTaskPicker
                    value={gv}
                    onChange={(next) => renameTaskGroup(gv, next)}
                    canCreate={canManageTaskTypes}
                    vehicleType={wo.vehicle_type || ''}
                    className="w-56"
                  />
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t('work_orders_page.group_subtotal')}: ${groupSubtotal(gv).toFixed(2)}
                    {groupLaborSubtotal(gv) > 0 && (
                      <> · {t('work_orders_page.group_labor', { defaultValue: 'labor' })} ${groupLaborSubtotal(gv).toFixed(2)}</>
                    )}
                  </span>
                  <span className="ml-auto inline-flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setParts(prev => [...prev, blankPart(gv)])}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-card hover:bg-muted border border-border rounded min-h-tap"
                    >
                      <Plus className="size-3" />
                      {t('work_orders_page.add_part')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setLaborLines(prev => [...prev, blankLabor(gv)])}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-card hover:bg-muted border border-border rounded min-h-tap"
                    >
                      <Plus className="size-3" />
                      {t('work_orders_page.add_labor', { defaultValue: 'Add labor' })}
                    </button>
                    <Tip label={t('work_orders_page.remove_task_group')}>
                      <button
                        type="button"
                        onClick={() => removeTaskGroup(gv)}
                        aria-label={t('work_orders_page.remove_task_group')}
                        className="text-muted-foreground hover:text-destructive p-1"
                      >
                        <X className="size-3.5" />
                      </button>
                    </Tip>
                  </span>
                </div>
                {renderPartsTable(entriesFor(gv))}
                {renderLaborTable(laborEntriesFor(gv))}
              </div>
            ))}
            {(entriesFor('').length > 0 || laborEntriesFor('').length > 0 || taskGroups.length === 0) && (
              <div className="border border-border rounded-lg overflow-hidden">
                <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-muted/60 border-b border-border">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    {t('work_orders_page.group_general')}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t('work_orders_page.group_subtotal')}: ${groupSubtotal('').toFixed(2)}
                    {groupLaborSubtotal('') > 0 && (
                      <> · {t('work_orders_page.group_labor', { defaultValue: 'labor' })} ${groupLaborSubtotal('').toFixed(2)}</>
                    )}
                  </span>
                  {/* General bucket gets its own Add part / Add labor,
                      same as every named task. */}
                  <span className="ml-auto inline-flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setParts(prev => [...prev, blankPart('')])}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-card hover:bg-muted border border-border rounded min-h-tap"
                    >
                      <Plus className="size-3" />
                      {t('work_orders_page.add_part')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setLaborLines(prev => [...prev, blankLabor('')])}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-card hover:bg-muted border border-border rounded min-h-tap"
                    >
                      <Plus className="size-3" />
                      {t('work_orders_page.add_labor', { defaultValue: 'Add labor' })}
                    </button>
                  </span>
                </div>
                {renderPartsTable(entriesFor(''))}
                {renderLaborTable(laborEntriesFor(''))}
              </div>
            )}
          </div>
      </section>

      {/* ── Cost summary block ─────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_costs')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label={t('work_orders_page.field_fee', { defaultValue: 'Fee $' })}
            tip={t('work_orders_page.tip_fee', { defaultValue:
              'An extra charge beyond the parts and labor entered above — shop supplies, environmental, or a call-out fee. Parts and labor come from the line items; fee and tax are added on top.' })}>
            <NumberInput
              min="0" step="0.01"
              value={wo.fee_amount ?? 0}
              onValueChange={v => setField('fee_amount', v)}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums text-foreground focus:outline-none focus:border-ring${aiCls('fee_amount')}`}
            />
          </Field>
          <Field label={t('work_orders_page.field_tax')}>
            <NumberInput
              min="0" step="0.01"
              value={wo.tax_amount ?? 0}
              onValueChange={v => setField('tax_amount', v)}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums focus:outline-none focus:border-ring${aiCls('tax_amount')}`}
            />
          </Field>
        </div>
        <div className="mt-3 pt-3 border-t border-border/50 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-muted-foreground">{t('work_orders_page.sum_parts')}: <span className="font-medium tabular-nums text-foreground">${partsCostComputed.toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_labor')}: <span className="font-medium tabular-nums text-foreground">${effectiveLaborCost.toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_fee', { defaultValue: 'Fee' })}: <span className="font-medium tabular-nums text-foreground">${(Number(wo.fee_amount) || 0).toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_tax')}: <span className="font-medium tabular-nums text-foreground">${(Number(wo.tax_amount) || 0).toFixed(2)}</span></span>
          <span className="text-foreground font-semibold ml-auto">{t('work_orders_page.sum_total')}: <span className="tabular-nums">${totalCostComputed.toFixed(2)}</span></span>
        </div>
      </section>

      {/* ── Repair documentation (3C) ──────────────────────────────
          The Complaint / Cause / Correction triplet — the DOT- and
          warranty-standard way to record a repair (reported → found →
          fixed).  All optional; a shop invoice with just costs stays
          valid, but filling these gives an audit-ready paper trail. */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-1">{t('work_orders_page.section_diagnosis', { defaultValue: 'Repair documentation' })}</h3>
        <p className="text-xs text-muted-foreground mb-3">{t('work_orders_page.diagnosis_hint', { defaultValue: 'The 3C format auditors and warranty claims expect. Optional.' })}</p>
        <div className="flex flex-col gap-3">
          <Field label={t('work_orders_page.field_complaint', { defaultValue: 'Complaint — what was reported' })}>
            <textarea
              rows={2}
              value={wo.complaint || ''}
              onChange={e => setField('complaint', e.target.value)}
              placeholder={t('work_orders_page.ph_complaint', { defaultValue: 'e.g. Driver reported grinding noise when braking' })}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_cause', { defaultValue: 'Cause — what was found' })}>
            <textarea
              rows={2}
              value={wo.cause || ''}
              onChange={e => setField('cause', e.target.value)}
              placeholder={t('work_orders_page.ph_cause', { defaultValue: 'e.g. Front brake pads worn below minimum, rotor scored' })}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_correction', { defaultValue: 'Correction — what was done' })}>
            <textarea
              rows={2}
              value={wo.correction || ''}
              onChange={e => setField('correction', e.target.value)}
              placeholder={t('work_orders_page.ph_correction', { defaultValue: 'e.g. Replaced front pads and rotors, road-tested' })}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* ── Invoice / payment block ────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_invoice')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label={t('work_orders_page.field_invoice_number')}>
            <input
              type="text"
              value={wo.invoice_number || ''}
              onChange={e => setField('invoice_number', e.target.value)}
              placeholder={t('work_orders_page.ph_invoice_number')}
              className={`w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:border-ring${aiCls('invoice_number')}`}
            />
          </Field>
          <Field label={t('work_orders_page.field_payment_method')}>
            <input
              type="text"
              value={wo.payment_method || ''}
              onChange={e => setField('payment_method', e.target.value)}
              placeholder={t('work_orders_page.ph_payment_method')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_payment_status')}
            tip={t('work_orders_page.tip_payment_status', { defaultValue:
              'The money state: has this invoice been paid? \u201cWritten off\u201d = the charge was cancelled and nothing is owed \u2014 different from voiding the record itself (Status). Synced invoices update to Paid automatically when the integration reports a cleared balance.' })}>
            <Select value={wo.payment_status || 'unpaid'} onValueChange={(v) => setField('payment_status', v)} items={paymentStatusItems}>
              <SelectTrigger className="w-full" aria-label={t('work_orders_page.field_payment_status')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {paymentStatusItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className="mt-3">
          <Field label={t('work_orders_page.field_notes')}>
            <textarea
              rows={3}
              value={wo.notes || ''}
              onChange={e => setField('notes', e.target.value)}
              placeholder={t('work_orders_page.ph_notes')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* In create mode the attachments section can't render yet (no
          work-order id → no folder path).  Show a prominent banner
          where the section would be so the user understands why the
          upload UI is missing and doesn't scroll past hunting for it. */}
      {!isEdit && (
        <section className={`mb-5 p-4 rounded-xl text-sm inline-flex items-start gap-2 w-full ${toneClasses('info')}`}>
          <Paperclip className="shrink-0 mt-0.5 size-4" />
          <div>
            <p className="font-medium">{t('work_orders_page.attachments_unlock_title')}</p>
            <p className="text-xs mt-0.5 text-info">
              {t('work_orders_page.attachments_unlock_desc')}
            </p>
          </div>
        </section>
      )}

      {/* ── Attachments (edit mode only) ──────────────────────── */}
      {isEdit && (
        <section className="bg-card border border-border rounded-xl p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold inline-flex items-center gap-1.5">
              <Paperclip className="text-muted-foreground size-3.5" />
              {t('work_orders_page.section_attachments')}
            </h3>
            <div className="flex items-center gap-2">
              <AttachUploadButton label={t('work_orders_page.add_invoice')} kind="invoice" onUpload={handleUpload} disabled={uploadingFile} accept=".pdf,image/*" />
              <AttachUploadButton label={t('work_orders_page.add_photo')} kind="photo" onUpload={handleUpload} disabled={uploadingFile} accept="image/*" />
            </div>
          </div>
          {attachments.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('work_orders_page.no_attachments')}</p>
          ) : (
            <ul className="space-y-2">
              {attachments.map(att => (
                <AttachmentRow
                  key={att.id}
                  attachment={att}
                  workOrderId={workOrderId!}
                  onDelete={() => handleDeleteAttachment(att)}
                />
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ── Linked maintenance tasks (edit mode) ──────────────── */}
      {isEdit && linkedTasks.length > 0 && (
        <section className="bg-card border border-border rounded-xl p-5 mb-5">
          <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
            <LinkIcon className="text-muted-foreground size-3.5" />
            {t('work_orders_page.section_linked_tasks')}
          </h3>
          <ul className="space-y-1.5">
            {linkedTasks.map(task => (
              <li key={task.id} className="text-sm flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">#{task.id}</span>
                <span className="capitalize">{(task.task_type || '').replace(/_/g, ' ')}</span>
                <span className="text-muted-foreground">— {task.description || t('work_orders_page.no_description')}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!isEdit && (
        <p className="text-xs text-muted-foreground mb-4">
          💡 {t('work_orders_page.save_first_hint')}
        </p>
      )}

      {/* ── Scan overwrite confirmation ───────────────────────────
          Owner contract: the scan NEVER silently replaces a value the
          user already typed — each disagreement is a checkbox here. */}
      <Dialog open={!!scanConflicts} onOpenChange={(o) => { if (!o) setScanConflicts(null); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {t('work_orders_page.scan_conflicts_title', { defaultValue: 'Overwrite existing values?' })}
            </DialogTitle>
            <DialogDescription>
              {t('work_orders_page.scan_conflicts_desc', {
                defaultValue: 'The invoice disagrees with fields already filled in. Tick what to overwrite — unticked fields keep their current value.',
              })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 max-h-72 overflow-y-auto">
            {scanConflicts?.list.map(c => (
              <label
                key={c.key}
                className="flex items-start gap-2.5 p-2 rounded-md border border-border bg-muted/40 cursor-pointer"
              >
                <input
                  type="checkbox"
                  className="mt-0.5 accent-primary"
                  checked={!!scanConflicts.checked[c.key]}
                  onChange={e => setScanConflicts(sc => sc
                    ? { ...sc, checked: { ...sc.checked, [c.key]: e.target.checked } }
                    : sc)}
                />
                <span className="text-xs min-w-0">
                  <span className="font-medium text-foreground">{scanFieldLabel(c.key)}</span>
                  <span className="block mt-0.5">
                    <span className="text-muted-foreground line-through">{c.current}</span>
                    <span className="text-muted-foreground"> → </span>
                    <span className="text-foreground font-medium">{c.proposed}</span>
                  </span>
                </span>
              </label>
            ))}
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setScanConflicts(null)}
              className="inline-flex items-center px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
            >
              {t('work_orders_page.scan_keep_current', { defaultValue: 'Keep current values' })}
            </button>
            <button
              type="button"
              onClick={applyScanOverwrites}
              className="inline-flex items-center px-3 py-1.5 bg-primary hover:bg-primary/90 rounded-md text-xs font-medium text-primary-foreground transition min-h-tap"
            >
              {t('work_orders_page.scan_apply', { defaultValue: 'Overwrite selected' })}
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Small subcomponents ────────────────────────────────────────

function Field({ label, required, tip, children }: { label: string; required?: boolean; tip?: React.ReactNode; children: React.ReactNode }) {
  // Wrap children inside <label> so the input/select/textarea is
  // implicitly associated — screen readers announce the label when the
  // field receives focus, and clicking the text focuses the control.
  const caption = (
    <span className="block text-xs text-muted-foreground mb-1">
      {label}{required && <span className="text-destructive ml-0.5">*</span>}
      {tip && <span className="ml-1 inline-flex align-middle"><InfoTip size={12} label={tip} /></span>}
    </span>
  );
  // With a tip, the InfoTip <button> must not precede the control in
  // DOM order — the implicit label targets its FIRST labelable
  // descendant, and a button before the input steals that association
  // (clicking the label text would toggle the tip, not focus the
  // field).  col-reverse keeps the control first while rendering the
  // caption on top.
  if (tip) {
    return (
      <label className="flex flex-col-reverse">
        {children}
        {caption}
      </label>
    );
  }
  return (
    <label className="block">
      {caption}
      {children}
    </label>
  );
}

function AttachUploadButton({
  label, kind, onUpload, disabled, accept,
}: {
  label: string;
  kind: string;
  onUpload: (file: File, kind: string) => Promise<void>;
  disabled: boolean;
  accept: string;
}) {
  // Native <input type=file> hidden behind a styled label so the click
  // affordance is a real button.  Resets the input value after upload
  // so the same file can be re-attached if the user deletes + re-adds.
  return (
    <label className={`inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
      <Plus className="size-3" />
      {label}
      <input
        type="file"
        accept={accept}
        className="hidden"
        disabled={disabled}
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) await onUpload(f, kind);
          e.target.value = '';
        }}
      />
    </label>
  );
}

function AttachmentRow({
  attachment, workOrderId, onDelete,
}: {
  attachment: WorkOrderAttachment;
  workOrderId: number;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const tz = useTimezone();
  const isImage = attachment.content_type?.startsWith('image/');
  const handleDownload = async () => {
    try {
      const res = await apiFetch(`/work-orders/${workOrderId}/attachments/${attachment.id}`);
      if (!res.ok) {
        toast.error(`${t('work_orders_page.toast_download_failed')}: ${res.statusText}`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = attachment.file_name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_download_failed'));
    }
  };

  return (
    <li className="flex items-center gap-3 p-2.5 bg-muted/40 border border-border rounded-lg">
      <div className="w-8 h-8 rounded bg-muted flex items-center justify-center shrink-0">
        {isImage ? <ImageIcon className="text-muted-foreground size-4" /> : <FileText className="text-muted-foreground size-4" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{attachment.file_name}</p>
        <p className="text-xs text-muted-foreground">
          <span className="capitalize">{attachment.kind}</span>
          {' · '}
          {(attachment.file_size / 1024).toFixed(1)} KB
          {attachment.uploaded_by_name && ` · ${t('work_orders_page.uploaded_by', { name: attachment.uploaded_by_name })}`}
          {' · '}
          {formatDay(attachment.uploaded_at, { timeZone: tz })}
        </p>
      </div>
      <button
        type="button"
        onClick={handleDownload}
        className="text-xs text-primary hover:underline py-1 -my-1 min-h-tap"
      >
        {t('work_orders_page.download')}
      </button>
      <Tip label={t('work_orders_page.delete_attachment')}>
        <button
          type="button"
          onClick={onDelete}
          className="text-muted-foreground hover:text-destructive p-1 min-h-tap"
          aria-label={t('work_orders_page.delete_attachment')}
        >
          <X className="size-3.5" />
        </button>
      </Tip>
    </li>
  );
}
