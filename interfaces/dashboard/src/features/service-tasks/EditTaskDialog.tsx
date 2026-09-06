/**
 * Edit one service task.
 *
 * Everything the task stores lives here, so a task can be revised
 * rather than only created and destroyed — including the parent, which
 * is the only place subtasks can be assigned.
 *
 * A STANDARD task's name is locked: its canonical key is the identity
 * shared by every account, so a rename in one fleet would quietly fork
 * the shared vocabulary. The dialog says that outright and offers the
 * honest alternative rather than greying the field out in silence.
 *
 * Renaming onto a name that already exists is the moment a duplicate
 * is born, so that's exactly where we offer Merge instead.
 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '../../lib/toast';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import { useAssemblies } from '../parts/useAssemblies';
import { useQuery } from '@tanstack/react-query';
import {
  SERVICE_TASKS_KEY, SYSTEMS_KEY, fetchTaskSystems, updateServiceTask,
  type ServiceTask,
} from './api';

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'text-foreground focus:outline-none focus:border-ring';

export default function EditTaskDialog({
  task, allTasks, onClose, onMergeInstead,
}: {
  task: ServiceTask | null;
  /** Every task, for the parent picker + collision lookup. */
  allTasks: ServiceTask[];
  onClose: () => void;
  /** Called when the user hits a name collision and chooses to merge. */
  onMergeInstead: (target: ServiceTask) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [hours, setHours] = useState('');
  const [vehicleType, setVehicleType] = useState('');
  const [parentId, setParentId] = useState('');
  const [systemKey, setSystemKey] = useState('');
  const [assemblyKey, setAssemblyKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [clash, setClash] = useState<ServiceTask | null>(null);

  const isStandard = !!task?.canonical_key;

  useEffect(() => {
    if (!task) return;
    setName(task.name);
    setDescription(task.description ?? '');
    setHours(task.expected_labor_hours ? String(task.expected_labor_hours) : '');
    setVehicleType(task.vehicle_type ?? '');
    setParentId(task.parent_id ? String(task.parent_id) : '');
    setSystemKey(task.system_key ?? '');
    setAssemblyKey(task.assembly_key ?? '');
    setClash(null);
  }, [task]);

  // A valid parent is top-level, isn't this task, and isn't one of this
  // task's own children (nesting is one level deep). Offering an
  // invalid option and then rejecting it would just be rude.
  const { data: systemsData } = useQuery({
    queryKey: SYSTEMS_KEY, queryFn: fetchTaskSystems, staleTime: 5 * 60_000,
  });
  const systems = systemsData?.systems ?? [];
  const { forSystem } = useAssemblies();
  const assemblyOptions = forSystem(systemKey);

  const parentOptions = allTasks.filter(
    (t) => !t.parent_id && t.id !== task?.id && t.status === 'active',
  );

  const save = async () => {
    if (!task) return;
    const trimmed = name.trim();
    if (!trimmed) return;

    // Catch the collision locally so we can offer merge without a
    // round trip; the server enforces it regardless.
    if (!isStandard) {
      const key = trimmed.replace(/\s+/g, ' ').toLowerCase();
      const hit = allTasks.find(
        (t) => t.id !== task.id && t.name.replace(/\s+/g, ' ').toLowerCase() === key,
      );
      if (hit) { setClash(hit); return; }
    }

    setSaving(true);
    try {
      await updateServiceTask(task.id, {
        ...(isStandard ? {} : { name: trimmed }),
        description,
        expected_labor_hours: Number(hours) || 0,
        vehicle_type: vehicleType as ServiceTask['vehicle_type'],
        system_key: systemKey,
        assembly_key: assemblyKey,
        // 0 detaches — see the API's parent_id contract.
        parent_id: parentId ? Number(parentId) : 0,
      });
      qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
      toast.success('Service task updated');
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={!!task} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Edit service task</DialogTitle>
          <DialogDescription>
            Changes apply everywhere this task is used — maintenance
            schedules, work orders and reports all read the same list.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">Name</span>
            <input
              value={name}
              disabled={isStandard}
              onChange={(e) => { setName(e.target.value); setClash(null); }}
              className={`${inputCls} disabled:opacity-60`}
            />
            {isStandard && (
              <span className="mt-1 block text-2xs text-muted-foreground">
                Standard tasks keep their name so spending stays comparable
                across fleets. Need a different name? Archive this one and
                add your own.
              </span>
            )}
          </label>

          {clash && (
            <div className={`rounded-md border px-2.5 py-2 text-xs ${toneClasses('warn')}`}>
              <p>“{clash.name}” already exists. Keeping both spellings splits
                every report.</p>
              <button
                type="button"
                onClick={() => { onMergeInstead(clash); onClose(); }}
                className="mt-1 font-medium underline"
              >
                Merge this task into it instead
              </button>
            </div>
          )}

          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              Description (optional)
            </span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="What this job involves"
              className={inputCls}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Estimated labor hours
              </span>
              <input
                type="number" min="0" step="0.25" inputMode="decimal"
                value={hours}
                onChange={(e) => setHours(e.target.value)}
                placeholder="0"
                className={`${inputCls} tabular-nums`}
              />
            </label>
            <label className="block">
              <span className="block text-xs text-muted-foreground mb-1">
                Applies to
              </span>
              <select
                value={vehicleType}
                onChange={(e) => setVehicleType(e.target.value)}
                className={inputCls}
              >
                <option value="">Any vehicle</option>
                <option value="truck">Trucks only</option>
                <option value="trailer">Trailers only</option>
              </select>
            </label>
          </div>

          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              System
            </span>
            <select
              value={systemKey}
              disabled={isStandard}
              onChange={(e) => {
                // The pair rule: an assembly lives under ONE system,
                // so moving the system clears the assembly rather
                // than letting the save bounce off the server.
                setSystemKey(e.target.value);
                setAssemblyKey('');
              }}
              className={`${inputCls} disabled:opacity-60`}
            >
              <option value="">Unassigned</option>
              {systems.map((sy) => (
                <option key={sy.key} value={sy.key}>{sy.label}</option>
              ))}
            </select>
            <span className="mt-1 block text-2xs text-muted-foreground">
              {isStandard
                ? 'Groups this task for spend reporting. Set centrally on a standard task, for the same reason the name is — a system that means Brakes in one fleet and Other in another makes the comparison worthless.'
                : 'Groups this task for spend reporting — “what are brakes costing us?”'}
            </span>
          </label>

          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              Assembly (optional)
            </span>
            <select
              value={assemblyKey}
              disabled={isStandard || !systemKey}
              onChange={(e) => setAssemblyKey(e.target.value)}
              className={`${inputCls} disabled:opacity-60`}
            >
              <option value="">No assembly (most tasks)</option>
              {assemblyOptions.map((a) => (
                <option key={a.key} value={a.key}>{a.label}</option>
              ))}
            </select>
            <span className="mt-1 block text-2xs text-muted-foreground">
              {isStandard
                ? 'Set centrally, like the system — it decides which assembly this task\'s LABOR files under in the drill-down.'
                : systemKey
                  ? 'Only for assembly-specific tasks (Water Pump Replacement → Water Pump). It routes this task\'s LABOR in the assembly drill-down; parts always keep their own.'
                  : 'Pick a system first — an assembly lives under one.'}
            </span>
          </label>

          <label className="block">
            <span className="block text-xs text-muted-foreground mb-1">
              Part of a bigger job (optional)
            </span>
            <select
              value={parentId}
              onChange={(e) => setParentId(e.target.value)}
              className={inputCls}
            >
              <option value="">Not a subtask</option>
              {parentOptions.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <span className="mt-1 block text-2xs text-muted-foreground">
              Subtasks group under their parent in the task picker.
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
