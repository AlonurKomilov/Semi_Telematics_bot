// Service tasks — the shared vocabulary Maintenance and Work Orders
// both pick from.  One list, one owner: before this feature the same
// vocabulary was a hardcoded array here, another in the maintenance AI
// tool, and a third in the work-order matcher, and they had drifted.
//
// A task's STORED value (what lands in maintenance_tasks.task_type and
// the work-order line tags) is `canonical_key || name` — the server's
// resolver accepts either, so the picker never has to know which.
import { apiJSON } from '../../api/client';

export interface ServiceTask {
  id: number;
  account_id: number;
  name: string;
  name_key: string;
  /** Set ⇒ a standard task: shared across accounts, archive-only,
   *  name locked. Empty ⇒ this account's own. */
  canonical_key: string;
  description: string;
  expected_labor_hours: number;
  parent_id: number | null;
  /** '' = any vehicle; narrows the picker on a mixed fleet. */
  vehicle_type: '' | 'truck' | 'trailer';
  /** The reporting axis above a task ("what are brakes costing us?").
   *  '' = unassigned. Ours, not VMRS — see the storage module. */
  system_key: string;
  /** Level 2 for LABOR — set only on assembly-specific tasks
   *  (Water Pump Replacement → water_pump); '' is the common,
   *  correct value.  Operator-owned on Shared tasks, like system. */
  assembly_key: string;
  /** Server-side best guess for an UNASSIGNED task — shown as a
   *  one-click confirm chip, never auto-applied. */
  suggested_system?: string;
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
}

/** The value stored on a maintenance task / work-order line. */
export const taskValue = (t: ServiceTask): string =>
  t.canonical_key || t.name;

export const SERVICE_TASKS_KEY = ['service-tasks'] as const;

export async function fetchServiceTasks(
  includeArchived = false, vehicleType = '',
): Promise<{ service_tasks: ServiceTask[]; count: number }> {
  const q = new URLSearchParams();
  if (includeArchived) q.set('include_archived', 'true');
  if (vehicleType) q.set('vehicle_type', vehicleType);
  const qs = q.toString();
  return apiJSON(`/service-tasks${qs ? `?${qs}` : ''}`);
}

export async function createServiceTask(body: {
  name: string;
  description?: string;
  expected_labor_hours?: number;
  vehicle_type?: string;
  system_key?: string;
}): Promise<ServiceTask> {
  return apiJSON('/service-tasks', { method: 'POST', body });
}

export async function updateServiceTask(
  id: number,
  body: Partial<Pick<ServiceTask,
    'name' | 'description' | 'expected_labor_hours' | 'status'
    | 'vehicle_type' | 'parent_id' | 'system_key' | 'assembly_key'>>,
): Promise<ServiceTask> {
  return apiJSON(`/service-tasks/${id}`, { method: 'PUT', body });
}

export async function deleteServiceTask(
  id: number,
): Promise<{ deleted: boolean; group_id?: string }> {
  // Returns the trail group so callers can offer Undo (the delete is
  // restorable from the task's history regardless).
  return apiJSON(`/service-tasks/${id}`, { method: 'DELETE' });
}

export interface LinkedPart {
  id: number;
  part_id: number;
  quantity: number;
  part_name: string;
  part_number: string;
}

export async function fetchTaskParts(
  taskId: number,
): Promise<{ parts: LinkedPart[] }> {
  return apiJSON(`/service-tasks/${taskId}/parts`);
}

export async function linkTaskPart(
  taskId: number, part_id: number, quantity: number,
): Promise<LinkedPart> {
  return apiJSON(`/service-tasks/${taskId}/parts`, {
    method: 'POST', body: { part_id, quantity },
  });
}

export async function unlinkTaskPart(
  taskId: number, linkId: number,
): Promise<void> {
  await apiJSON(`/service-tasks/${taskId}/parts/${linkId}`, { method: 'DELETE' });
}

/** Fold a duplicate task into the canonical one. Destructive: the
 *  loser row is gone afterwards, its history repointed at the winner. */
export async function mergeServiceTasks(
  loser_id: number, winner_id: number,
): Promise<void> {
  await apiJSON('/service-tasks/merge', {
    method: 'POST', body: { loser_id, winner_id },
  });
}

export interface TaskSystem { key: string; label: string }

/** The system vocabulary, FETCHED not hardcoded — a second copy in the
 *  frontend is exactly how the old task vocabulary drifted into three
 *  disagreeing lists. */
export const SYSTEMS_KEY = ['service-task-systems'] as const;

export async function fetchTaskSystems(): Promise<{ systems: TaskSystem[] }> {
  return apiJSON('/service-tasks/systems');
}
