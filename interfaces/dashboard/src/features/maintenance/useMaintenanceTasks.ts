/**
 * Shared maintenance-task data layer — ONE query + ONE bucket
 * computation used by both the Tasks page and the topbar
 * MaintenanceHero.  Sharing the react-query key means the hero and
 * the page read the same cache entry, and sharing the classifier
 * means their counts are identical BY CONSTRUCTION — no server/client
 * or page/shell drift possible (the same drift class we previously
 * fixed between the chip counts and the per-row badges).
 *
 * "Due soon" thresholds:
 *   • date  — within 7 days
 *   • miles — within 5,000 mi
 *   • hours — within 100 engine hours
 * These mirror the typical service-interval warning windows in the
 * industry (oil-change comes "due soon" ~5k miles out, DOT
 * inspections ~1 week out, PTO hours ~100h out) and MUST stay in
 * sync with ``classify_task_urgency`` in features/maintenance/
 * service.py (the backend's overdue-marker jobs use the same lines).
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import type { MaintenanceTask } from '../../types';

export const DUE_SOON_DAYS = 7;
export const DUE_SOON_MILES = 5_000;
export const DUE_SOON_HOURS = 100;

// A big service needs more warning than a small one.
//
// A flat 5,000 miles is ~10 days of driving.  That is fine for an oil
// change you can book anywhere, and thin for a 200,000-mile transmission
// service that needs parts ordered, a bay booked and the truck off the
// road.  So the window scales with the INTERVAL and never shrinks below
// the flat floor:
//
//     30,000 mi oil change   -> max(5,000,  1,500) =  5,000   unchanged
//    100,000 mi service      -> max(5,000,  5,000) =  5,000   unchanged
//    200,000 mi transmission -> max(5,000, 10,000) = 10,000   ~3 weeks
//
// Only LONG intervals move, so nothing that was due-soon yesterday stops
// being due-soon today.
export const DUE_SOON_INTERVAL_FRACTION = 0.05;

export const dueSoonMilesFor = (interval: number | null | undefined): number =>
  Math.max(DUE_SOON_MILES, (interval ?? 0) * DUE_SOON_INTERVAL_FRACTION);

export const dueSoonHoursFor = (interval: number | null | undefined): number =>
  Math.max(DUE_SOON_HOURS, (interval ?? 0) * DUE_SOON_INTERVAL_FRACTION);

/** Full task set, one fetch per session (placeholderData keeps the
 *  previous page rendered during refetches).  A typical account has
 *  <500 tasks; the filter chips / hero chips just narrow client-side. */
export function useMaintenanceTasksQuery() {
  return useQuery({
    queryKey: ['maintenance-tasks'],
    queryFn: () => apiJSON<{ tasks: MaintenanceTask[] }>('/maintenance/tasks?page_size=200'),
    placeholderData: (prev) => prev,
  });
}

/** Per-task urgency: 'overdue' | 'due_soon' | null (calendar-day
 *  basis, so a "due today" task is due-soon, not overdue).  Returned
 *  as a factory so the "today" boundary is captured once per render
 *  pass instead of re-read per task. */
export function makeUrgencyClassifier(): (t: MaintenanceTask) => 'overdue' | 'due_soon' | null {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return (t: MaintenanceTask) => {
    if (t.due_date) {
      const due = new Date(t.due_date);
      if (!Number.isNaN(due.getTime())) {
        const startOfDue = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
        const days = Math.round((startOfDue - startOfToday) / 86_400_000);
        if (days < 0) return 'overdue';
        if (days <= DUE_SOON_DAYS) return 'due_soon';
      }
    }
    if (t.due_miles != null && t.last_odometer != null) {
      const remaining = t.due_miles - t.last_odometer;
      if (remaining < 0) return 'overdue';
      if (remaining <= dueSoonMilesFor(t.recur_interval_miles)) return 'due_soon';
    }
    if (t.due_engine_hours != null && t.last_engine_hours != null) {
      const remaining = t.due_engine_hours - t.last_engine_hours;
      if (remaining < 0) return 'overdue';
      if (remaining <= dueSoonHoursFor(t.recur_interval_engine_hours)) return 'due_soon';
    }
    return null;
  };
}

export interface TaskBuckets {
  overdue: MaintenanceTask[];
  dueSoon: MaintenanceTask[];
  pending: MaintenanceTask[];
  completed: MaintenanceTask[];
  cancelled: MaintenanceTask[];
}

/** Bucket each task exactly once (counts sum to the total).
 *  Classification order per open task: overdue → due_soon → pending,
 *  checking date, then mileage, then engine-hours — the first axis
 *  that pins the task wins, and the backend's status='overdue' flag
 *  forces the overdue bucket regardless of axes. */
export function classifyTaskBuckets(allTasks: MaintenanceTask[]): TaskBuckets {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const overdue: MaintenanceTask[] = [];
  const dueSoon: MaintenanceTask[] = [];
  const pending: MaintenanceTask[] = [];
  const completed: MaintenanceTask[] = [];
  const cancelled: MaintenanceTask[] = [];
  for (const t of allTasks) {
    if (t.status === 'completed') { completed.push(t); continue; }
    if (t.status === 'cancelled') { cancelled.push(t); continue; }
    const isOverdueStatus = t.status === 'overdue';
    let placed = false;
    // 1. Date axis — overdue first (lets the backend's status='overdue'
    //    flag also force this), then "due in next 7 days".
    if (t.due_date) {
      const due = new Date(t.due_date);
      if (!Number.isNaN(due.getTime())) {
        const startOfDue = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
        const days = Math.round((startOfDue - startOfToday) / 86_400_000);
        if (days < 0 || isOverdueStatus) { overdue.push(t); placed = true; }
        else if (days <= DUE_SOON_DAYS) { dueSoon.push(t); placed = true; }
      }
    }
    if (!placed && isOverdueStatus) { overdue.push(t); placed = true; }
    // 2. Mileage axis — only when no date pinned the task already.
    if (!placed && t.due_miles != null && t.last_odometer != null) {
      const remaining = t.due_miles - t.last_odometer;
      if (remaining < 0) { overdue.push(t); placed = true; }
      else if (remaining <= DUE_SOON_MILES) { dueSoon.push(t); placed = true; }
    }
    // 3. Engine hours axis — same shape as mileage.
    if (!placed && t.due_engine_hours != null && t.last_engine_hours != null) {
      const remaining = t.due_engine_hours - t.last_engine_hours;
      if (remaining < 0) { overdue.push(t); placed = true; }
      else if (remaining <= DUE_SOON_HOURS) { dueSoon.push(t); placed = true; }
    }
    if (!placed) { pending.push(t); }
  }
  return { overdue, dueSoon, pending, completed, cancelled };
}
