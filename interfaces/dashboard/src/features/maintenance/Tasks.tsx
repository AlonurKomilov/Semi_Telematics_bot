import { useState, useRef, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { usePreference } from '../../preferences';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Wrench,
  Plus,
  List,
  CalendarDays,
  Trash2,
  CheckSquare,
  Archive,
  RefreshCw,
  ClipboardList,
} from 'lucide-react';
import { apiJSON } from '../../api/client';
import { usePublishContext } from '../ai/PageContext';
import DataGrid, { type DataGridSegment, type BulkAction } from '../../components/datagrid';
import {
  useMaintenanceTasksQuery, makeUrgencyClassifier, classifyTaskBuckets,
} from './useMaintenanceTasks';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type { MaintenanceTask, AnyColumn } from '../../types';
import { type VehicleSummary } from './pickers';
import { CalendarMonth } from './CalendarMonth';
import { undoableToast } from '../../lib/undoable';
import { ServiceHistoryModal } from './ServiceHistoryModal';
import { TaskActivityDialog } from './TaskActivityDialog';
import { TemplatesModal } from './TemplatesModal';
import type { MaintenanceTemplate } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { useTaskLabels } from '../service-tasks/useTaskLabels';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { makeColumns } from './columns';
import AddTaskDialog from './AddTaskDialog';
import { TourHost } from '../../components/tour';
import TaskDetailSheet from './TaskDetailSheet';
import {
  STATUS_OPTIONS,
  STATUS_LABELS,
  CLOSED_STATUSES,
  _periodDaysToDueDate,
  _dueDateToPeriodDays,
  _formatDate,
  _todayLabel,
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
  // Company outlives the add form: usePublishContext below hands it to
  // the AI assistant as the page's context filter, so it cannot live
  // inside a dialog that unmounts when closed.
  const [fCompany, setFCompany] = useState('');
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

  // Single-trigger mode for the add form — new users were confused
  // by three trigger inputs (date/miles/hours) all visible at once.
  // We now show one at a time; the segmented control above the
  // trigger picks which one.



  // Current odometer / engine-hours snapshot for the truck this task
  // is attached to — drives the "current: 245,678 mi" hint and lets
  // the +3k/+5k preset buttons add to the real odometer instead of to
  // whatever stale value is already in the field.
  const odometerFor = useRef<number | null>(null);

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
                  : 'text-muted-foreground hover:text-foreground'} min-h-tap min-w-tap`}
              >
                <List className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setViewMode('calendar')}
                aria-pressed={viewMode === 'calendar'}
                aria-label="Calendar view"
                title="Calendar view"
                className={`inline-flex size-7 items-center justify-center rounded ${viewMode === 'calendar'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'} min-h-tap min-w-tap`}
              >
                <CalendarDays className="size-3.5" />
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
              <ClipboardList className="size-3.5" />
              Templates
            </button>
            {/* DOT Binder button moved to Reports module (Reports →
                DOT Binder tab) in 2026-06 — the binder is a
                stakeholder-facing compliance PDF, not a maintenance-
                editing surface. */}
            <button data-tour="maintenance.new-task" onClick={() => { setShowAdd(!showAdd); setError(''); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary-hover rounded-md text-xs font-medium text-primary-foreground transition min-h-tap">
              <Plus className="size-3.5" />
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

      {/* The add form lives in its own component now (stage 2 of the
          split).  Rendered only while OPEN so closing it discards the
          form state — which is why the old fourteen-line manual reset is
          gone. */}
      {/* Rendered ONLY while open — that is what discards the form state,
          and it is load-bearing.  ``if (!open) return null`` inside the
          component does NOT do it: the hooks have already run, so React
          keeps the state and the next open shows the last half-typed
          task.  ``handleAdd``'s fourteen-line manual reset used to cover
          for that and was deleted with this move. */}
      {/* Tours for this page — at most one offer per visit, decided at
          mount so a dialog never pops over someone's half-filled form.
          The engine lives in components/tour; this page only says
          who it is and what it can see. */}
      <TourHost
        feature="maintenance"
        // canCreate: adding a TASK is gated by the page permission
        // itself (can_maintenance_*) — anyone standing here can press
        // "New task".  `canCreateTasks` above is a different right
        // (defining new service-task TYPES in the picker) and would
        // wrongly hide the tour from most maintenance users.
        ctx={{ count: allTasks.length, canCreate: true }}
      />

      {showAdd && (
      <AddTaskDialog
        open={showAdd}
        onOpenChange={setShowAdd}
        onCreated={load}
        onError={setError}
        canCreateTasks={canCreateTasks}
        // The page already runs these queries for the grid and the
        // detail sheet; passing them down beats a second fetch.
        vehicleList={vehicleList}
        fleetLoading={fleetLoading}
        templates={templates}
        templateItems={templateItems}
        tz={tz}
        company={fCompany}
        onCompanyChange={setFCompany}
      />
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
            <button onClick={() => setShowAdd(true)} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary-hover transition min-h-tap">
              <Plus className="size-3.5" />
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
          onTaskClick={setSelected}
        />
      ) : (
        <>
          <DataGrid
            tableId="maintenance-tasks"
            columns={columns}
            // OPENS ON URGENCY.  Maintenance is a work queue, and the
            // question an operator brings to it is "what is due first?" —
            // so the first paint answers it instead of showing whatever
            // order the database returned.
            //
            // ``due_miles`` sorts by REMAINING miles, not by odometer:
            // the column's sortKey is ``due_miles - last_odometer``, so
            // ascending puts the truck with the fewest miles to go on
            // top, and trucks with no mileage threshold sink via
            // +Infinity.  See columns.tsx.
            //
            // defaultSorting, not `sorting`: the latter is CONTROLLED, so
            // a constant there would freeze every column header. This
            // seeds the grid once and leaves it clickable — and a saved
            // tab's own sort still wins, because that is a choice the
            // user actually made.
            defaultSorting={[{ id: 'due_miles', desc: false }]}
            segments={TASK_SEGMENTS}
            // Personal scope tabs sit after Active/Archive (e.g. a saved
            // "Critical" or "Oil changes" view), per-user + isolated.
            savedTabs
            data={gridTasks as unknown as Record<string, unknown>[]}
            searchKey={['vehicle_name', 'company_code', 'description', 'task_type']}
            searchPlaceholder="Search…"
            onRowClick={(row) => setSelected(row as unknown as MaintenanceTask)}
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
      {/* Stage 3: the detail sheet is its own component.  Rendered only
          while a task is open, so switching tasks REMOUNTS it and no
          field can carry over from the previous one. */}
      {selected && (
        <TaskDetailSheet
          task={selected}
          onClose={() => setSelected(null)}
          onSaved={load}
          onError={setError}
          onTaskChanged={setSelected}
          // TWO callbacks, not one.  They were two distinct actions
          // before the split and collapsing them fired BOTH dialogs at
          // once — the vehicle's service history stacked on top of the
          // task's activity trail.
          onShowServiceHistory={() => setHistoryVehicle(selected.vehicle_name)}
          onShowActivityTrail={() => setHistoryOpen(true)}
          canCreateTasks={canCreateTasks}
          customTypeLabelByValue={customTypeLabelByValue}
          tz={tz}
        />
      )}

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
