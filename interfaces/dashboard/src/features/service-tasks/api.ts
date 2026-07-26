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
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
}

/** The value stored on a maintenance task / work-order line. */
export const taskValue = (t: ServiceTask): string =>
  t.canonical_key || t.name;

export const SERVICE_TASKS_KEY = ['service-tasks'] as const;

export async function fetchServiceTasks(
  includeArchived = false,
): Promise<{ service_tasks: ServiceTask[]; count: number }> {
  return apiJSON(
    `/service-tasks${includeArchived ? '?include_archived=true' : ''}`,
  );
}

export async function createServiceTask(body: {
  name: string;
  description?: string;
  expected_labor_hours?: number;
}): Promise<ServiceTask> {
  return apiJSON('/service-tasks', { method: 'POST', body });
}

export async function updateServiceTask(
  id: number,
  body: Partial<Pick<ServiceTask,
    'name' | 'description' | 'expected_labor_hours' | 'status'>>,
): Promise<ServiceTask> {
  return apiJSON(`/service-tasks/${id}`, { method: 'PUT', body });
}

export async function deleteServiceTask(id: number): Promise<void> {
  await apiJSON(`/service-tasks/${id}`, { method: 'DELETE' });
}
