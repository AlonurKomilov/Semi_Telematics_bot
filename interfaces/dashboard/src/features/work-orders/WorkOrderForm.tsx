import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { WO_PREFILL_STATE_KEY, type WorkOrderPrefill } from './createFrom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  FileText, Save, ArrowLeft, Trash2, Plus, Paperclip,
  Receipt, X, Link as LinkIcon, Image as ImageIcon, Loader2,
} from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { PageHeader, ErrorState } from '../../components/shell';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { toneClasses } from '../../lib/status';
import TypePicker from '../maintenance/TypePicker';
import { TASK_TYPE_OPTIONS } from '../maintenance/badges';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import type {
  WorkOrder, WorkOrderDetail, WorkOrderAttachment,
  MaintenanceTask, CompanyInfo,
} from '../../types';
import { VehiclePicker, type VehicleSummary } from '../maintenance/pickers';
import { VendorPicker } from '../vendors/VendorPicker';
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
  vendor_id: null,
  service_date: '',
  odometer_at_service: null,
  engine_hours_at_service: null,
  labor_cost: 0,
  parts_cost: 0,
  tax_amount: 0,
  total_cost: 0,
  invoice_number: '',
  payment_method: '',
  payment_status: 'unpaid',
  status: 'draft',
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
    queryFn: () => apiJSON('/work-orders/parts-catalog'),
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
    setTaskGroups(Array.from(new Set(
      detail.parts.map(p => (p as { service_task?: string }).service_task || '').filter(Boolean),
    )));
    setAttachments(detail.attachments);
    setLinkedTasks(detail.linked_tasks);
  }, [detail]);

  // Auto-totals: parts_cost = sum(parts.total_cost), total_cost =
  // labor + parts + tax.  Recomputed as a derived value so the
  // displayed numbers always match what'll be sent.
  const partsCostComputed = useMemo(
    () => parts.reduce((acc, p) => acc + (Number(p.total_cost) || 0), 0),
    [parts],
  );
  const totalCostComputed = useMemo(
    () => (Number(wo.labor_cost) || 0) + partsCostComputed + (Number(wo.tax_amount) || 0),
    [wo.labor_cost, wo.tax_amount, partsCostComputed],
  );

  // ── Field helpers ──────────────────────────────────────────────
  const setField = <K extends keyof WorkOrder>(key: K, value: WorkOrder[K] | null) =>
    setWo(prev => ({ ...prev, [key]: value }));

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
  const canManageTaskTypes = has('can_maintenance_all');

  const entriesFor = (task: string) =>
    parts.map((pt, idx) => ({ pt, idx })).filter(e => (e.pt.service_task || '') === task);
  const groupSubtotal = (task: string) =>
    entriesFor(task).reduce((acc, e) => acc + (e.pt.total_cost || 0), 0);

  const addTaskGroup = () => {
    // Default the new group to the first built-in type not in use —
    // the operator immediately changes it via the header picker.
    const used = new Set(taskGroups);
    const next = TASK_TYPE_OPTIONS
      .map(o => o.value)
      .filter(v => v !== 'custom')
      .find(v => !used.has(v));
    if (!next) return;   // every built-in already open — unlikely
    setTaskGroups(prev => [...prev, next]);
  };

  const renameTaskGroup = (from: string, to: string) => {
    if (!to || to === from) return;
    // Same value picked twice → the two groups merge (Set dedupes).
    setTaskGroups(prev => Array.from(new Set(prev.map(g => (g === from ? to : g)))));
    setParts(prev => prev.map(pt => (pt.service_task === from ? { ...pt, service_task: to } : pt)));
  };

  const removeTaskGroup = (task: string) => {
    // Never deletes part lines — they fall back to General (untagged).
    setTaskGroups(prev => prev.filter(g => g !== task));
    setParts(prev => prev.map(pt => (pt.service_task === task ? { ...pt, service_task: '' } : pt)));
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
                  <input
                    type="number" min="0" step="1"
                    value={pt.quantity}
                    onChange={e => updatePart(idx, { quantity: Number(e.target.value) || 0 })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <input
                    type="number" min="0" step="0.01"
                    value={pt.unit_cost}
                    onChange={e => updatePart(idx, { unit_cost: Number(e.target.value) || 0 })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <input
                    type="number" min="0" step="0.01"
                    value={pt.total_cost}
                    onChange={e => updatePart(idx, { total_cost: Number(e.target.value) || 0 })}
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums font-medium focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2">
                  <input
                    type="number" min="0" step="1"
                    value={pt.warranty_months}
                    onChange={e => updatePart(idx, { warranty_months: Number(e.target.value) || 0 })}
                    placeholder="0"
                    className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                  />
                </td>
                <td className="py-1.5 px-2 text-right">
                  <button
                    type="button"
                    onClick={() => removePart(idx)}
                    className="text-muted-foreground hover:text-destructive p-1"
                    title={t('work_orders_page.remove_part')}
                    aria-label={t('work_orders_page.remove_part')}
                  >
                    <Trash2 size={14} />
                  </button>
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
  const statusItems = ['draft', 'submitted', 'paid', 'void'].map((s) => ({ value: s, label: s }));
  // Reason-for-repair class.  '' = unclassified (the honest default for
  // rows created before this field, or when the operator hasn't tagged it).
  const priorityItems = [
    { value: '', label: t('work_orders_page.priority_unset', { defaultValue: 'Unclassified' }) },
    { value: 'scheduled', label: t('work_orders_page.priority_scheduled', { defaultValue: 'Scheduled' }) },
    { value: 'non_scheduled', label: t('work_orders_page.priority_non_scheduled', { defaultValue: 'Non-scheduled' }) },
    { value: 'emergency', label: t('work_orders_page.priority_emergency', { defaultValue: 'Emergency' }) },
  ];
  const paymentStatusItems = ['unpaid', 'paid', 'partial', 'void'].map((s) => ({ value: s, label: s }));
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
              onClick={() => navigate('/work-orders')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
            >
              <ArrowLeft size={14} />
              {t('work_orders_page.back')}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
            >
              <Save size={14} />
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

      {/* ── Vehicle + status block ─────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
          <Receipt size={14} className="text-muted-foreground" />
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
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_status')}>
            <Select value={wo.status || 'draft'} onValueChange={(v) => setField('status', v)} items={statusItems}>
              <SelectTrigger className="w-full capitalize" aria-label={t('work_orders_page.field_status')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {statusItems.map((it) => <SelectItem key={it.value} value={it.value} className="capitalize">{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t('work_orders_page.field_repair_priority', { defaultValue: 'Repair priority' })}>
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
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            />
            {asOfNote && (
              <p className="mt-1 flex items-center gap-1 text-2xs text-muted-foreground">
                {asOfLoading && <Loader2 size={12} className="animate-spin" />}
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
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
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
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_address')}>
            <input
              type="text"
              value={wo.vendor_address || ''}
              onChange={e => setField('vendor_address', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_address')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* ── Parts editor — grouped by service task ──────────────
          Task → parts hierarchy: each group header is a TypePicker
          (same vocabulary as Maintenance, built-ins + the account's
          custom types) and the lines under it are that task's parts.
          Grouping is just the ``service_task`` tag on each line, so
          cost reports can sum per task while a mixed invoice still
          splits correctly.  Untagged lines live in the trailing
          General section. */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold">{t('work_orders_page.section_parts')}</h3>
            <p className="text-2xs text-muted-foreground mt-0.5">
              ↻ {t('work_orders_page.parts_hint')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={addTaskGroup}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded"
            >
              <Plus size={12} />
              {t('work_orders_page.add_task_group')}
            </button>
            <button
              type="button"
              onClick={() => setParts(prev => [...prev, blankPart()])}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded"
            >
              <Plus size={12} />
              {t('work_orders_page.add_part')}
            </button>
          </div>
        </div>
        {/* Shared suggestion list for every part-name input (native
            datalist: zero JS, browser-rendered dropdown). */}
        <datalist id="wo-parts-catalog">
          {catalogParts.map(cp => <option key={cp.id} value={cp.name} />)}
        </datalist>
        {parts.length === 0 && taskGroups.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('work_orders_page.no_parts')}</p>
        ) : (
          <div className="space-y-4">
            {taskGroups.map((gv) => (
              <div key={gv} className="border border-border rounded-lg overflow-hidden">
                <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-muted/60 border-b border-border">
                  <TypePicker
                    value={gv}
                    onChange={(next) => renameTaskGroup(gv, next)}
                    canCreate={canManageTaskTypes}
                    className="w-56"
                  />
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t('work_orders_page.group_subtotal')}: ${groupSubtotal(gv).toFixed(2)}
                  </span>
                  <span className="ml-auto inline-flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setParts(prev => [...prev, blankPart(gv)])}
                      className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-card hover:bg-muted border border-border rounded"
                    >
                      <Plus size={12} />
                      {t('work_orders_page.add_part')}
                    </button>
                    <button
                      type="button"
                      onClick={() => removeTaskGroup(gv)}
                      title={t('work_orders_page.remove_task_group')}
                      aria-label={t('work_orders_page.remove_task_group')}
                      className="text-muted-foreground hover:text-destructive p-1"
                    >
                      <X size={14} />
                    </button>
                  </span>
                </div>
                {renderPartsTable(entriesFor(gv))}
              </div>
            ))}
            {(entriesFor('').length > 0 || taskGroups.length === 0) && (
              <div className="border border-border rounded-lg overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2 bg-muted/60 border-b border-border">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    {t('work_orders_page.group_general')}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t('work_orders_page.group_subtotal')}: ${groupSubtotal('').toFixed(2)}
                  </span>
                </div>
                {renderPartsTable(entriesFor(''))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── Cost summary block ─────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_costs')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label={t('work_orders_page.field_labor')}>
            <input
              type="number" min="0" step="0.01"
              value={wo.labor_cost ?? 0}
              onChange={e => setField('labor_cost', Number(e.target.value) || 0)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_tax')}>
            <input
              type="number" min="0" step="0.01"
              value={wo.tax_amount ?? 0}
              onChange={e => setField('tax_amount', Number(e.target.value) || 0)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
        <div className="mt-3 pt-3 border-t border-border/50 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-muted-foreground">{t('work_orders_page.sum_parts')}: <span className="font-medium tabular-nums text-foreground">${partsCostComputed.toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_labor')}: <span className="font-medium tabular-nums text-foreground">${(Number(wo.labor_cost) || 0).toFixed(2)}</span></span>
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
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:border-ring"
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
          <Field label={t('work_orders_page.field_payment_status')}>
            <Select value={wo.payment_status || 'unpaid'} onValueChange={(v) => setField('payment_status', v)} items={paymentStatusItems}>
              <SelectTrigger className="w-full capitalize" aria-label={t('work_orders_page.field_payment_status')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {paymentStatusItems.map((it) => <SelectItem key={it.value} value={it.value} className="capitalize">{it.label}</SelectItem>)}
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
          <Paperclip size={16} className="shrink-0 mt-0.5" />
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
              <Paperclip size={14} className="text-muted-foreground" />
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
            <LinkIcon size={14} className="text-muted-foreground" />
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
    </div>
  );
}

// ── Small subcomponents ────────────────────────────────────────

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  // Wrap children inside <label> so the input/select/textarea is
  // implicitly associated — screen readers announce the label when the
  // field receives focus, and clicking the text focuses the control.
  return (
    <label className="block">
      <span className="block text-xs text-muted-foreground mb-1">
        {label}{required && <span className="text-destructive ml-0.5">*</span>}
      </span>
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
      <Plus size={12} />
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
        {isImage ? <ImageIcon size={16} className="text-muted-foreground" /> : <FileText size={16} className="text-muted-foreground" />}
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
        className="text-xs text-primary hover:underline"
      >
        {t('work_orders_page.download')}
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="text-muted-foreground hover:text-destructive p-1"
        title={t('work_orders_page.delete_attachment')}
        aria-label={t('work_orders_page.delete_attachment')}
      >
        <X size={14} />
      </button>
    </li>
  );
}
