// ── Add a maintenance task ──────────────────────────────────────────
//
// Stage 2 of splitting Tasks.tsx.  This form owned SIXTEEN of the page's
// ~60 state hooks plus three functions, every one prefixed ``f``/``F``
// to keep them apart from the detail sheet's ``e`` set living beside
// them — a naming convention that existed only because two forms shared
// one component's scope.  They don't any more.
//
// The page keeps what the page owns (the query, the grid, which dialog
// is open); this owns everything about filling the form in.
//
// One thing got SIMPLER rather than just moved: the old ``handleAdd``
// ended with fourteen lines resetting every field by hand, because the
// form never unmounted.  The page renders this only while it is open
// now, so closing discards the state and the reset is gone — a
// half-typed task no longer reappears on the next open.

import { useEffect, useState } from 'react';
import { ClipboardList } from 'lucide-react';
import { toast } from '../../lib/toast';

import { apiJSON } from '../../api/client';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../components/ui/select';
import ServiceTaskPicker from '../service-tasks/ServiceTaskPicker';
import {
  VehiclePicker, MilesPicker, HoursPicker, DaysPicker, type VehicleSummary,
} from './pickers';
import { type Priority } from './badges';
import {
  PRIORITY_ITEMS, _formatDate, _periodDaysToDueDate, _todayLabel,
  type TriggerMode,
} from './taskFields';
import type { MaintenanceTemplate } from '../../types';
import { Card } from '@/components/ui/card';

/** Fan-outs above this pause for a count confirmation even with no
 *  duplicates — "Create 27 tasks?" is a decision, not a click. */
const BULK_CONFIRM_THRESHOLD = 5;

export interface AddTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Refetch the list — the page owns the query, so it owns the reload. */
  onCreated: () => void;
  /** Surface a failure on the page's error strip rather than in here. */
  onError: (message: string) => void;
  /** Whether the operator may create a new service task from the picker. */
  canCreateTasks: boolean;
  /** Fleet + templates come from the PAGE's queries — it already runs
   *  them for the grid and the detail sheet, so re-fetching here would
   *  duplicate work and let the two lists disagree. */
  vehicleList: VehicleSummary[];
  fleetLoading: boolean;
  templates: MaintenanceTemplate[];
  templateItems: { value: string; label: string }[];
  /** Account timezone — dates render in it, never in UTC. */
  tz: string;
  /** ⚠️ Company is the one field that did NOT move.  The page publishes
   *  it as the AI assistant's context filter (``usePublishContext``), so
   *  it has a reader outside this form and has to outlive it.
   *  Worth a look later — the assistant arguably wants the GRID's company
   *  filter, not a half-typed value from the add form — but changing that
   *  is a behaviour change, not a refactor. */
  company: string;
  onCompanyChange: (value: string) => void;
}

export default function AddTaskDialog({
  open, onOpenChange, onCreated, onError, canCreateTasks,
  vehicleList, fleetLoading, templates, templateItems, tz,
  company: fCompany, onCompanyChange: setFCompany,
}: AddTaskDialogProps) {
  const [saving, setSaving] = useState(false);

  // The pre-create pause.  Set when the submit needs a human decision
  // first — a big fan-out, or vehicles already covered by an open task
  // of the same type (the SERVER's answer, deliberately: the page's
  // own task list is capped at one page and would under-count).
  // null = no pause pending.
  const [confirm, setConfirm] = useState<{
    targets: string[]; duplicates: string[];
  } | null>(null);

  // Add form
  const [fVehicle, setFVehicle] = useState('');
  // Company the picked vehicle belongs to.  Persists through the POST
  // body as ``company_code`` so the task row knows WHICH "103" the
  // user chose when two companies under the account share a vehicle
  // name.  Cleared whenever the vehicle text is cleared or no
  // matching fleet entry is found.
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
  // W3 from review: a confirmation describes the form AS CONFIRMED.
  // Editing anything that changes what would be created — vehicles,
  // type, mode — makes that description stale, so the panel dismisses
  // itself and the next submit re-asks with fresh numbers.  Cheaper
  // and more honest than locking every field.
  useEffect(() => {
    setConfirm(null);
  }, [fType, fVehicle, fMultiMode, fMultiVehicles]);
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
      onError('Set a value for the chosen trigger so the task can become overdue.');
      return;
    }
    if (fMultiMode && fMultiVehicles.size === 0) {
      onError('Pick at least one vehicle for the bulk-create.');
      return;
    }
    // The pre-create pause.  Two hazards earn one: a fan-out past the
    // threshold ("Create 27 tasks" is a decision, not a click), and
    // vehicles ALREADY covered by an open task of this type — asked of
    // the server, because the page's own list is capped at one page.
    // The preflight failing must not block creation (fail open, no
    // duplicate info); the create call re-checks anyway when skipping.
    if (saving) return;            // double-Enter during the preflight
    setSaving(true);
    const targets = fMultiMode ? Array.from(fMultiVehicles) : [fVehicle];
    let duplicates: string[] = [];
    try {
      const pf = await apiJSON<{ duplicates: string[] }>(
        '/maintenance/tasks/bulk/preflight',
        { method: 'POST', body: { task_type: fType, vehicle_names: targets } },
      );
      duplicates = pf.duplicates;
    } catch { /* fail open — the hazard check is a courtesy */ }
    if (duplicates.length > 0
        || (fMultiMode && targets.length > BULK_CONFIRM_THRESHOLD)) {
      setSaving(false);            // the pause hands control back
      setConfirm({ targets, duplicates });
      return;
    }
    await doCreate(false);
  };

  const doCreate = async (skipDuplicates: boolean) => {
    setConfirm(null);
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
    setSaving(true); onError('');
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
          skipped: string[];
        }>('/maintenance/tasks/bulk/create', {
          method: 'POST',
          body: {
            ...shared,
            vehicle_names: Array.from(fMultiVehicles),
            skip_duplicates: skipDuplicates,
          },
        });
        toast.success(
          `Created ${res.created.length} task${res.created.length === 1 ? '' : 's'}`
          + (res.skipped?.length
              ? ` · ${res.skipped.length} already covered` : '')
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
      onOpenChange(false);
      setFVehicle(''); setFCompany(''); setFDesc(''); setFDueDate(''); setFDueMiles('');
      setFDueEngineHours(''); setFPriority('medium'); setFOdometer(null); setFEngineHours(null);
      setFMultiMode(false); setFMultiVehicles(new Set());
      setFTriggerMode('date');
      setFRepeat(false);
      setFRecurValue('');
      onCreated();
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  // NOTE: no ``if (!open) return null`` here.  It reads like it discards
  // the form, and it does not — the hooks above have already run, so
  // React keeps every value and the next open restores a half-typed
  // task.  The PAGE not rendering this component is what actually
  // clears it; ``open`` below is only for the Dialog primitive's own
  // open/close animation and focus handling.

  return (

        <Card className="mb-6 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3" render={<form />} onSubmit={handleAdd}>
          {/* Apply-template dropdown — only shown when at least one
              template exists.  Selecting fills the rest of the form
              with the template's defaults; the user picks a vehicle
              and clicks Create. */}
          {templates.length > 0 && (
            <label className="col-span-full block">
              <span className="block text-xs text-muted-foreground mb-1 inline-flex items-center gap-1">
                <ClipboardList className="size-3" />
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
          <label data-tour="maintenance.multi-toggle" className="col-span-full inline-flex items-center gap-2 text-xs text-muted-foreground">
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
              <div data-tour="maintenance.vehicle-chips" data-tour-count={fMultiVehicles.size} className="max-h-40 overflow-y-auto bg-muted border border-border rounded p-2 flex flex-wrap gap-1">
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
                      } min-h-tap`}
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
                  className="mt-1 text-2xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
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
                    } min-h-tap`}
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
            {confirm === null ? (
              <button data-tour="maintenance.create" type="submit" disabled={saving} className="w-full px-4 py-1.5 bg-primary hover:bg-primary-hover disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition min-h-tap">
                {saving ? 'Saving...' : 'Create'}
              </button>
            ) : (() => {
              // The pause the submit asked for.  Skip-those is the
              // default (create only where nothing is open); "anyway"
              // is a real choice, spelled out, never pre-selected.
              const fresh = confirm.targets.length - confirm.duplicates.length;
              const many = confirm.targets.length !== 1;
              return (
                <div className="w-full rounded-md border border-warn-bd bg-warn-bg p-3 text-xs">
                  <p className="font-medium text-foreground">
                    {many
                      ? `This will create ${confirm.targets.length} tasks — one per selected vehicle.`
                      : `“${confirm.targets[0]}” already has an open task of this type.`}
                  </p>
                  {many && confirm.duplicates.length > 0 && (
                    <p className="mt-1 text-muted-foreground">
                      {confirm.duplicates.length} of these vehicles already
                      {confirm.duplicates.length === 1 ? ' has' : ' have'} an
                      open task of this type: {confirm.duplicates.slice(0, 6).join(', ')}
                      {confirm.duplicates.length > 6 ? '…' : ''}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-2">
                    {fresh > 0 && confirm.duplicates.length > 0 && (
                      <button data-tour="maintenance.create" type="button" disabled={saving}
                        onClick={() => doCreate(true)}
                        className="px-3 py-1.5 bg-primary hover:bg-primary-hover disabled:opacity-50 rounded text-xs font-medium text-primary-foreground transition min-h-tap">
                        Create {fresh} — skip {confirm.duplicates.length} existing
                      </button>
                    )}
                    {fresh > 0 && confirm.duplicates.length === 0 && (
                      <button data-tour="maintenance.create" type="button" disabled={saving}
                        onClick={() => doCreate(false)}
                        className="px-3 py-1.5 bg-primary hover:bg-primary-hover disabled:opacity-50 rounded text-xs font-medium text-primary-foreground transition min-h-tap">
                        Create {confirm.targets.length} tasks
                      </button>
                    )}
                    {confirm.duplicates.length > 0 && (
                      <button type="button" disabled={saving}
                        onClick={() => doCreate(false)}
                        {...(fresh === 0
                          // Every target is already covered, so no primary
                          // renders — the tour's anchor rides the only
                          // committing button left, or the engine would
                          // read the pause as the step completing.
                          ? { 'data-tour': 'maintenance.create' } : {})}
                        className="px-3 py-1.5 rounded border border-border text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition min-h-tap">
                        Create {many ? `all ${confirm.targets.length} ` : ''}anyway
                      </button>
                    )}
                    <button type="button" disabled={saving}
                      onClick={() => setConfirm(null)}
                      className="px-3 py-1.5 rounded text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition min-h-tap">
                      Back
                    </button>
                  </div>
                </div>
              );
            })()}
          </div>
        </Card>
  );
}
