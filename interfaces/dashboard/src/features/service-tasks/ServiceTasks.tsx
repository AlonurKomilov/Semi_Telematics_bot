/**
 * Service Tasks — the shared task list behind Maintenance and Work
 * Orders (features/service_tasks).
 *
 * Shared tasks ship with every account and carry a cross-account
 * key, so "what does a brake job cost" compares honestly between
 * fleets; they're archive-only and name-locked for exactly that
 * reason.  Your own tasks are free to rename, archive or delete —
 * delete only while nothing references them, so history never loses
 * its label.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { undoableToast } from '../../lib/undoable';
import { Check, ClipboardList, Plus } from 'lucide-react';
import TaskPartsDialog from './TaskPartsDialog';
import MergeTaskDialog from './MergeTaskDialog';
import EditTaskDialog from './EditTaskDialog';
import DataGrid, { type DataGridSegment } from '../../components/datagrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton,
} from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import type { MenuAction } from '../../components/ui/context-menu';
import { toneClasses } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useAssemblies } from '../parts/useAssemblies';
import type { AnyColumn } from '../../types';
import {
  SERVICE_TASKS_KEY, SYSTEMS_KEY, createServiceTask, deleteServiceTask,
  fetchServiceTasks, fetchTaskSystems, updateServiceTask,
  type ServiceTask,
} from './api';

// My tasks / Shared are PAGE tabs, the same My-vs-Shared binary
// every cross-account feature uses (owner call: one vocabulary, not
// a different noun per feature), so the grid keeps only the lifecycle
// segments.  Truck/trailer narrowing lives on the "Applies to" column
// filter, not as tabs.
//
// Archived can't simply be dropped even though it's usually empty: an
// account CAN archive a shared task it doesn't do (its own call, and a
// fan-out won't undo it), so without the tab those rows would vanish
// with no way back.  It's shown only when it actually holds something
// — see `segments` at the call site.
const SEGMENTS: DataGridSegment[] = [
  { key: 'all', label: 'All' },
  { key: 'archived', label: 'Archived', match: (r) => r.status === 'archived' },
];
const ACTIVE_ONLY: DataGridSegment[] = [];

export default function ServiceTasks() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { has } = useViewPermissions();
  const canManage = has('can_service_tasks');

  // Page-level surface tabs, like Vendors/Parts.
  const [tab, setTab] = useState<'mine' | 'standard'>('mine');
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState('');
  const [hours, setHours] = useState('');
  const [addDescription, setAddDescription] = useState('');
  const [addVehicleType, setAddVehicleType] = useState('');
  const [addSystem, setAddSystem] = useState('');
  const [saving, setSaving] = useState(false);
  // Row → its linked-parts editor (what a work order pre-fills).
  const [partsFor, setPartsFor] = useState<ServiceTask | null>(null);
  // Row → the merge dialog (fold a duplicate into the canonical one).
  const [mergeFor, setMergeFor] = useState<ServiceTask | null>(null);
  // Row → the edit dialog (the only place a parent is assigned).
  const [editFor, setEditFor] = useState<ServiceTask | null>(null);
  // Pre-selected merge target when the user came from a name clash.
  const [mergeWinner, setMergeWinner] = useState<number | null>(null);
  // Row → the delete confirm.  Danger styling is not consent: a menu
  // misclick must not erase a task definition with no way back.
  const [deleteFor, setDeleteFor] = useState<ServiceTask | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, 'all'],
    queryFn: () => fetchServiceTasks(true),
  });

  // Fetched, never hardcoded — a second copy in the frontend is how
  // the old task vocabulary drifted.
  const { data: systemsData } = useQuery({
    queryKey: SYSTEMS_KEY, queryFn: fetchTaskSystems, staleTime: 5 * 60_000,
  });
  const systems = systemsData?.systems ?? [];
  const systemLabel = (k: unknown) =>
    systems.find((sy) => sy.key === k)?.label ?? '';
  const { labelOf: assemblyLabel } = useAssemblies();

  const rows = useMemo(
    () => ((data?.service_tasks ?? []) as unknown as Record<string, unknown>[])
      .filter((r) => (tab === 'mine' ? !r.canonical_key : !!r.canonical_key)),
    [data, tab],
  );

  const refresh = () => {
    qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
  };

  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      // A call that returns a trail group (deletes) gets an Undo
      // affordance for free; everything else toasts as before.
      const res = await fn() as { group_id?: string } | undefined;
      refresh();
      undoableToast({
        message: label, groupId: res?.group_id, onRestored: refresh,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    }
  };

  const add = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const created = await createServiceTask({
        name: name.trim(),
        description: addDescription,
        expected_labor_hours: Number(hours) || 0,
        vehicle_type: addVehicleType,
        system_key: addSystem,
      });
      setAddOpen(false);
      // A task you add is always your own — land on the tab that holds
      // it, or adding from Shared looks like nothing happened.
      setTab('mine');
      setName('');
      setHours('');
      setAddDescription('');
      setAddVehicleType('');
      setAddSystem('');
      refresh();
      // Linking parts needs the task to exist, so it can't live in this
      // dialog without creating the row behind the user's back.  Offer
      // the jump instead.
      toast.success('Service task added', {
        action: {
          label: 'Add usual parts',
          onClick: () => setPartsFor(created),
        },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add that task');
    } finally {
      setSaving(false);
    }
  };

  const columns: AnyColumn[] = [
    { key: 'name', label: 'Task', sortable: true, filterable: true },
    {
      key: 'expected_labor_hours', label: 'Est. labor', sortable: true,
      aggregable: true, aggFns: ['avg', 'max'],
      render: (v) => (Number(v)
        ? <span className="tabular-nums">{Number(v)} h</span>
        : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'system_key', label: 'System', sortable: true, filterable: true,
      render: (v, row) => {
        if (v) return <span className="text-sm">{systemLabel(v) || String(v)}</span>;
        const t = row as unknown as ServiceTask;
        // Suggest-confirm fill: the server guessed from the name; one
        // click applies it, nothing happens without the click.
        if (t.suggested_system && canManage && !t.canonical_key) {
          return (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                void act('System set', () =>
                  updateServiceTask(t.id, { system_key: t.suggested_system }));
              }}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs font-medium transition hover:brightness-110 ${toneClasses('info')}`}
            >
              <Check size={12} aria-hidden />
              {systemLabel(t.suggested_system) || t.suggested_system}?
            </button>
          );
        }
        return <span className="text-muted-foreground">Unassigned</span>;
      },
    },
    {
      key: 'assembly_key', label: 'Assembly', sortable: true, filterable: true,
      filterValue: (row) => String((row as unknown as ServiceTask).assembly_key ?? ''),
      filterLabel: (row) => {
        const k = String((row as unknown as ServiceTask).assembly_key ?? '');
        return k ? (assemblyLabel.get(k) ?? k) : '(none)';
      },
      render: (v) => (v
        ? <span className="text-sm">{assemblyLabel.get(String(v)) ?? String(v)}</span>
        : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'vehicle_type', label: 'Applies to', sortable: true, filterable: true,
      render: (v) => (v
        ? <span className="capitalize text-sm">{String(v)}s only</span>
        : <span className="text-muted-foreground">Any vehicle</span>),
    },
    { key: 'description', label: 'Description', filterable: true,
      render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>) },
    {
      key: 'status', label: 'Status', sortable: true, filterable: true,
      render: (v) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(v === 'archived' ? 'neutral' : 'ok')}`}>
          {v === 'archived' ? 'Archived' : 'Active'}
        </span>
      ),
    },
  ];

  const rowActions = (row: Record<string, unknown>) => {
        const t = row as unknown as ServiceTask;
        const actions: MenuAction[] = [{
          key: 'parts', label: 'Usual parts…',
          onSelect: () => setPartsFor(t),
        }];
        if (!canManage) return actions;
        actions.unshift({
          key: 'edit', label: 'Edit…',
          onSelect: () => setEditFor(t),
        });
        if (t.status === 'active') {
          actions.push({
            key: 'archive', label: 'Archive',
            onSelect: () => act('Task archived', () =>
              updateServiceTask(t.id, { status: 'archived' })),
          });
        } else {
          actions.push({
            key: 'restore', label: 'Restore',
            onSelect: () => act('Task restored', () =>
              updateServiceTask(t.id, { status: 'active' })),
          });
        }
        // Narrowing a mixed fleet's picker: tag the task for one kind
        // of unit, or clear it back to "any vehicle".
        for (const vt of ['truck', 'trailer', ''] as const) {
          if (t.vehicle_type === vt) continue;
          actions.push({
            key: `vt-${vt || 'any'}`,
            label: vt ? `Applies to ${vt}s only` : 'Applies to any vehicle',
            separatorBefore: vt === 'truck',
            onSelect: () => act('Updated', () =>
              updateServiceTask(t.id, { vehicle_type: vt })),
          });
        }
        if (!t.canonical_key) {
          actions.push({
            key: 'merge', label: 'Merge into…', separatorBefore: true,
            onSelect: () => { setMergeWinner(null); setMergeFor(t); },
          });
          actions.push({
            key: 'delete', label: 'Delete', danger: true,
            onSelect: () => setDeleteFor(t),
          });
        }
        return actions;
      };

  return (
    <div>
      <PageHeader
        icon={ClipboardList}
        title="Service Tasks"
        description="The shared list of work your fleet does — used by both maintenance schedules and work orders, so the same job is named the same way everywhere. System is the reporting axis ('what are brakes costing us?'); labor always follows the task's system, and an assembly-specific task also gives its labor an assembly."
        actions={canManage ? (
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add service task
          </Button>
        ) : undefined}
      />

      <div role="tablist" aria-label="Service task sections" className="flex gap-1 mb-4 border-b border-border">
        {([
          { key: 'mine' as const, label: 'My tasks' },
          { key: 'standard' as const, label: 'Shared' },
        ]).map(({ key, label }) => {
          const sel = tab === key;
          return (
            <button
              key={key}
              role="tab"
              aria-selected={sel}
              onClick={() => setTab(key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                sel
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {error && <ErrorState message={(error as Error).message} />}
      {isLoading && <TableSkeleton />}
      {!isLoading && !error && rows.length === 0 && (
        tab === 'mine' ? (
          <EmptyState
            icon={ClipboardList}
            title="No tasks of your own yet"
            description="The Shared tab covers the common jobs — add your own here for work specific to your fleet."
          />
        ) : (
          <EmptyState
            icon={ClipboardList}
            title="No shared tasks"
            description="Shared tasks are added automatically — if this list is empty, try reloading."
          />
        )
      )}
      {!isLoading && !error && rows.length > 0 && (
        <DataGrid
          data={rows}
          columns={columns}
          segments={rows.some((r) => r.status === 'archived') ? SEGMENTS : ACTIVE_ONLY}
          savedTabs
          rowActions={rowActions}
          onRowClick={(row) => navigate(`/service-tasks/${(row as unknown as ServiceTask).id}`)}
          searchKey={['name', 'description']}
          searchPlaceholder="Search service tasks…"
          tableId={tab === 'mine' ? 'service-tasks-mine' : 'service-tasks-standard'}
        />
      )}

      <EditTaskDialog
        task={editFor}
        allTasks={(data?.service_tasks ?? [])}
        onClose={() => setEditFor(null)}
        onMergeInstead={(target) => {
          // Renaming onto an existing name → merge the two instead of
          // keeping both spellings. The task being edited is the one
          // that disappears; `target` keeps its name, pre-selected so
          // the user doesn't have to find it again.
          setMergeWinner(target.id);
          setMergeFor(editFor);
        }}
      />

      <MergeTaskDialog
        task={mergeFor}
        presetWinnerId={mergeWinner}
        onClose={() => { setMergeFor(null); setMergeWinner(null); }}
      />

      <TaskPartsDialog
        task={partsFor}
        canManage={canManage}
        onClose={() => setPartsFor(null)}
      />

      <Dialog open={!!deleteFor} onOpenChange={(o) => { if (!o) setDeleteFor(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete “{deleteFor?.name}”?</DialogTitle>
            <DialogDescription>
              The task definition is removed permanently. Nothing references
              it, so no history is touched — but there is no undo.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="ghost" size="sm" onClick={() => setDeleteFor(null)}>
              Cancel
            </Button>
            <Button
              size="sm" variant="destructive"
              onClick={() => {
                const t = deleteFor;
                setDeleteFor(null);
                if (t) void act('Task deleted', () => deleteServiceTask(t.id));
              }}
            >
              Delete task
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={addOpen} onOpenChange={(o) => { if (!o) setAddOpen(false); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add service task</DialogTitle>
            <DialogDescription>
              Names are unique — if the task already exists, use it instead of
              adding a second spelling (two spellings split every report).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">Name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Kingpin Service"
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Description (optional)
              </span>
              <textarea
                value={addDescription}
                onChange={(e) => setAddDescription(e.target.value)}
                rows={2}
                placeholder="What this job involves"
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                System
              </span>
              <select
                value={addSystem}
                onChange={(e) => setAddSystem(e.target.value)}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              >
                <option value="">Unassigned</option>
                {systems.map((sy) => (
                  <option key={sy.key} value={sy.key}>{sy.label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Applies to
              </span>
              <select
                value={addVehicleType}
                onChange={(e) => setAddVehicleType(e.target.value)}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              >
                <option value="">Any vehicle</option>
                <option value="truck">Trucks only</option>
                <option value="trailer">Trailers only</option>
              </select>
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Estimated labor hours (optional)
              </span>
              <input
                type="number" min="0" step="0.25" inputMode="decimal"
                value={hours}
                onChange={(e) => setHours(e.target.value)}
                placeholder="0"
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums text-foreground focus:outline-none focus:border-ring"
              />
            </label>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={add} disabled={saving || !name.trim()}>
              {saving ? 'Adding…' : 'Add task'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
