/**
 * One service task, opened from a row on the Service Tasks grid.
 *
 * The grid answers "which tasks exist"; this page answers "what does
 * this one mean" — the full description the grid truncates, the usual
 * parts a work order will pre-fill from it, and, on a Shared task, WHY
 * the name is locked. Mutations reuse the same dialogs the grid uses,
 * so there is one edit contract rather than two that can drift.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { History as HistoryIcon,
  ArrowLeft, ClipboardList, Lock, Package, Pencil,
} from 'lucide-react';
import TaskPartsDialog from './TaskPartsDialog';
import MergeTaskDialog from './MergeTaskDialog';
import EditTaskDialog from './EditTaskDialog';
import DataGrid from '../../components/datagrid';
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from '../../components/shell';
import { ActivityTrailDialog } from '../../components/activity-trail/ActivityTrailDialog';
import { Button } from '../../components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { toneClasses } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useAssemblies } from '../parts/useAssemblies';
import type { AnyColumn } from '../../types';
import {
  SERVICE_TASKS_KEY, SYSTEMS_KEY, deleteServiceTask, fetchServiceTasks,
  fetchTaskParts, fetchTaskSystems, updateServiceTask, type ServiceTask,
} from './api';
import { Card } from '@/components/ui/card';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
        {label}
      </div>
      <div className="text-sm text-foreground">{children}</div>
    </div>
  );
}

const dash = <span className="text-muted-foreground">—</span>;

const partColumns: AnyColumn[] = [
  { key: 'part_name', label: 'Part', sortable: true },
  {
    key: 'part_number', label: 'Part #', sortable: true,
    render: (v) => (v ? <span className="text-sm">{String(v)}</span> : dash),
  },
  {
    key: 'quantity', label: 'Default qty', sortable: true,
    render: (v) => <span className="tabular-nums">×{Number(v) || 1}</span>,
  },
];

export default function ServiceTaskDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { has } = useViewPermissions();
  const canManage = has('can_service_tasks');

  const [partsOpen, setPartsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeWinner, setMergeWinner] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Same query key as the grid, so opening a row is a cache hit and
  // any edit here refreshes both surfaces at once.
  const { data, isLoading, error } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, 'all'],
    queryFn: () => fetchServiceTasks(true),
  });
  const { data: systemsData } = useQuery({
    queryKey: SYSTEMS_KEY, queryFn: fetchTaskSystems, staleTime: 5 * 60_000,
  });

  const task = (data?.service_tasks ?? []).find(
    (t: ServiceTask) => String(t.id) === id,
  );

  const { data: partsData } = useQuery({
    queryKey: ['service-task-parts', task?.id],
    queryFn: () => fetchTaskParts(task!.id),
    enabled: !!task,
  });

  // ⚠️ ABOVE the early returns, with every other hook.  It used to sit
  // below them, after ``if (isLoading) return <TableSkeleton/>`` — so on
  // a COLD cache the first render bailed before calling it and the
  // second render called it, changing the hook count between renders.
  // React's answer to that is to throw ("Rendered more hooks than during
  // the previous render") and take the page down.
  //
  // It survived because ``useAssemblies`` sets ``staleTime: 5min``: come
  // in from another page that already fetched, ``isLoading`` is false on
  // the very first render, the counts agree and nothing breaks.  Hard-
  // refresh ON this page and it crashes.  The only eslint ERROR in the
  // codebase, and it was pointing at a real one.
  const { labelOf: assemblyLabel } = useAssemblies();

  const refresh = () => {
    qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
  };

  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      refresh();
      toast.success(label);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    }
  };

  if (isLoading) return <TableSkeleton />;
  if (error) return <ErrorState message={(error as Error).message} />;
  if (!task) {
    return (
      <EmptyState
        icon={ClipboardList}
        title="Task not found"
        description="It may have been deleted or merged into another task."
        action={<Button size="sm" onClick={() => navigate('/service-tasks')}>Back to Service Tasks</Button>}
      />
    );
  }

  const shared = !!task.canonical_key;
  const systemLabel = (systemsData?.systems ?? [])
    .find((s) => s.key === task.system_key)?.label;
  const links = partsData?.parts ?? [];

  return (
    <div>
      {/* Same shell as VendorProfile / PartDetail: PageHeader carries
          the entity name, the explanation sits behind the title's ⓘ,
          badges ride in `meta`, and back-nav lives in `actions` — a
          third hand-rolled detail header is how siblings drift. */}
      <PageHeader
        icon={ClipboardList}
        title={task.name}
        description={shared
          ? 'A shared task — every account has it under the same key, which is what lets "what does a brake job cost" compare honestly between fleets. Its name and system are set centrally to keep that true; the labor estimate, description and applies-to are yours to tune and are never overwritten.'
          : 'Your own task. Rename, retune or delete it freely — delete only while nothing references it, so history never loses its label.'}
        meta={(
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(shared ? 'info' : 'neutral')}`}>
              {shared ? 'Shared' : 'Mine'}
            </span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(task.status === 'archived' ? 'neutral' : 'ok')}`}>
              {task.status === 'archived' ? 'Archived' : 'Active'}
            </span>
          </div>
        )}
        actions={(
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => navigate('/service-tasks')}>
              <ArrowLeft /> All service tasks
            </Button>
            {canManage && (
              <>
                <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
                  <Pencil /> Edit
                </Button>
                <Button size="sm" variant="outline" onClick={() => setPartsOpen(true)}>
                  <Package /> Usual parts
                </Button>
              </>
            )}
          </div>
        )}
      />

      {/* ── Facts ────────────────────────────────────────────────── */}
      <Card className="mb-4">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <Field label="System">
            {systemLabel || task.system_key || (
              <span className="text-muted-foreground">Unassigned</span>
            )}
          </Field>
          <Field label="Assembly">
            {task.assembly_key ? (
              assemblyLabel.get(task.assembly_key) ?? task.assembly_key
            ) : (
              <span className="text-muted-foreground">None — most tasks</span>
            )}
          </Field>
          <Field label="Est. labor">
            {task.expected_labor_hours
              ? <span className="tabular-nums">{task.expected_labor_hours} h</span>
              : dash}
          </Field>
          <Field label="Applies to">
            {task.vehicle_type
              ? <span className="capitalize">{task.vehicle_type}s only</span>
              : 'Any vehicle'}
          </Field>
          <Field label="Set centrally">
            {shared ? (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <Lock className="size-3" aria-hidden /> Name and system
              </span>
            ) : (
              <span className="text-muted-foreground">Nothing — it's yours</span>
            )}
          </Field>
        </div>
        {task.description && (
          <div className="mt-4 pt-4 border-t border-border">
            <Field label="Description">{task.description}</Field>
          </div>
        )}
      </Card>

      {/* ── What a work order pre-fills from this task ───────────── */}
      <Card className="mb-4">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-base font-semibold text-foreground">Usual parts</h2>
          {canManage && (
            <Button size="sm" variant="ghost" onClick={() => setPartsOpen(true)}>
              Manage
            </Button>
          )}
        </div>
        <p className="text-sm text-muted-foreground mb-3">
          What a work order pre-fills when someone picks this task, so a
          shop visit starts from the usual bill of materials instead of
          being retyped. Quantities are defaults — the invoice still
          decides what actually went on the truck.
        </p>
        {links.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No parts linked yet — link the parts this job usually needs so
            new work orders start pre-filled.
          </p>
        ) : (
          <DataGrid
            tableId="service-task-usual-parts"
            columns={partColumns}
            data={links as unknown as Record<string, unknown>[]}
            enableToolbar={false}
            enablePagination={false}
          />
        )}
      </Card>

      {/* ── Lifecycle ────────────────────────────────────────────── */}
      {canManage && (
        <Card>
          <h2 className="text-base font-semibold text-foreground mb-1">Lifecycle</h2>
          <p className="text-sm text-muted-foreground mb-3">
            {shared
              ? 'Archiving hides this task from your pickers without touching anyone else — it stays available to every other account, and your own history keeps its label.'
              : 'Archiving hides it from pickers. Merging folds it into another task and moves its history there; deleting is only possible while nothing references it.'}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setHistoryOpen(true)}>
              <HistoryIcon /> History
            </Button>
            {task.status === 'active' ? (
              <Button size="sm" variant="outline" onClick={() => act('Task archived', () => updateServiceTask(task.id, { status: 'archived' }))}>
                Archive
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => act('Task restored', () => updateServiceTask(task.id, { status: 'active' }))}>
                Restore
              </Button>
            )}
            {!shared && (
              <>
                <Button size="sm" variant="outline" onClick={() => setMergeOpen(true)}>
                  Merge into…
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="text-destructive"
                  onClick={() => setConfirmDelete(true)}
                >
                  Delete
                </Button>
              </>
            )}
          </div>
        </Card>
      )}

      <Dialog open={confirmDelete} onOpenChange={(o) => { if (!o) setConfirmDelete(false); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete “{task.name}”?</DialogTitle>
            <DialogDescription>
              The task definition is removed permanently. Nothing references
              it, so no history is touched — but there is no undo.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              size="sm" variant="destructive"
              onClick={() => {
                setConfirmDelete(false);
                void act('Task deleted', async () => {
                  await deleteServiceTask(task.id);
                  navigate('/service-tasks');
                });
              }}
            >
              Delete task
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <TaskPartsDialog
        task={partsOpen ? task : null}
        onClose={() => setPartsOpen(false)}
        canManage={canManage}
      />
      <EditTaskDialog
        task={editOpen ? task : null}
        allTasks={data?.service_tasks ?? []}
        onClose={() => setEditOpen(false)}
        onMergeInstead={(target) => {
          // Renaming onto a name that already exists → merge instead of
          // keeping both spellings.  THIS task is the one that goes
          // away, so the merge dialog opens with `target` pre-selected.
          setEditOpen(false);
          setMergeWinner(target.id);
          setMergeOpen(true);
        }}
      />
      <MergeTaskDialog
        task={mergeOpen ? task : null}
        presetWinnerId={mergeWinner}
        onClose={() => { setMergeOpen(false); setMergeWinner(null); }}
      />
      <ActivityTrailDialog
        entityType="service_task"
        entityId={task?.id ?? null}
        title={`${task?.name ?? 'Service task'} — activity history`}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />
    </div>
  );
}
