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
import {
  ArrowLeft, ClipboardList, Lock, Package, Pencil,
} from 'lucide-react';
import TaskPartsDialog from './TaskPartsDialog';
import MergeTaskDialog from './MergeTaskDialog';
import EditTaskDialog from './EditTaskDialog';
import { EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import {
  SERVICE_TASKS_KEY, SYSTEMS_KEY, deleteServiceTask, fetchServiceTasks,
  fetchTaskParts, fetchTaskSystems, updateServiceTask, type ServiceTask,
} from './api';

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

export default function ServiceTaskDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { has } = useViewPermissions();
  const canManage = has('can_service_tasks');

  const [partsOpen, setPartsOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeWinner, setMergeWinner] = useState<number | null>(null);

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
      <button
        type="button"
        onClick={() => navigate('/service-tasks')}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3"
      >
        <ArrowLeft size={14} aria-hidden /> Service Tasks
      </button>

      {/* ── Identity ─────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold text-foreground">{task.name}</h1>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(shared ? 'info' : 'neutral')}`}>
              {shared ? 'Shared' : 'Mine'}
            </span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(task.status === 'archived' ? 'neutral' : 'ok')}`}>
              {task.status === 'archived' ? 'Archived' : 'Active'}
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            {shared
              ? 'A shared task — every account has it under the same key, which is what lets "what does a brake job cost" compare honestly between fleets. You can still change how it behaves for your fleet below.'
              : 'Your own task. Rename, retune or delete it freely — delete only while nothing references it, so history never loses its label.'}
          </p>
        </div>
        {canManage && (
          <div className="flex items-center gap-2 shrink-0">
            <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
              <Pencil size={14} /> Edit
            </Button>
            <Button size="sm" variant="outline" onClick={() => setPartsOpen(true)}>
              <Package size={14} /> Usual parts
            </Button>
          </div>
        )}
      </div>

      {/* ── Facts ────────────────────────────────────────────────── */}
      <div className="rounded-lg border border-border bg-card p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Field label="System">
            {systemLabel || task.system_key || (
              <span className="text-muted-foreground">Unassigned</span>
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
          <Field label="Name">
            {shared ? (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <Lock size={12} aria-hidden /> Locked
              </span>
            ) : 'Yours to change'}
          </Field>
        </div>
        {task.description && (
          <div className="mt-4 pt-4 border-t border-border">
            <Field label="Description">{task.description}</Field>
          </div>
        )}
      </div>

      {/* ── What a work order pre-fills from this task ───────────── */}
      <div className="rounded-lg border border-border bg-card p-4 mb-4">
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
          <p className="text-sm text-muted-foreground">No parts linked yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {links.map((p) => (
              <li key={p.part_id} className="flex items-center justify-between py-2 text-sm">
                <span className="text-foreground">{p.part_name}</span>
                <span className="tabular-nums text-muted-foreground">
                  ×{p.quantity}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Lifecycle ────────────────────────────────────────────── */}
      {canManage && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-base font-semibold text-foreground mb-1">Lifecycle</h2>
          <p className="text-sm text-muted-foreground mb-3">
            {shared
              ? 'Archiving hides this task from your pickers without touching anyone else — it stays available to every other account, and your own history keeps its label.'
              : 'Archiving hides it from pickers. Merging folds it into another task and moves its history there; deleting is only possible while nothing references it.'}
          </p>
          <div className="flex flex-wrap items-center gap-2">
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
                  onClick={() => act('Task deleted', async () => {
                    await deleteServiceTask(task.id);
                    navigate('/service-tasks');
                  })}
                >
                  Delete
                </Button>
              </>
            )}
          </div>
        </div>
      )}

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
    </div>
  );
}
