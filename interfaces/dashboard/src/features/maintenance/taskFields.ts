// ── How a maintenance task's fields READ ────────────────────────────
//
// The vocabulary (status / priority labels and options) and the date +
// urgency maths, lifted out of Tasks.tsx — which had grown to 2,310
// lines and was carrying its own data dictionary at the top.
//
// Everything here is pure: no React, no queries, no component scope. It
// is shared by the columns, the add form and the detail sheet, all three
// of which used to reach for the same private helpers in one file.
// Being pure also makes it the first part of this feature that can be
// unit-tested without rendering a page.
//
// The leading underscores are kept from the original names so the diff
// that moved them stays readable; they no longer mean "private".

import type { MaintenanceTask } from '../../types';
import { PRIORITY_OPTIONS } from './badges';
import { formatDay } from '../../utils/datetime';

// Status dropdown options. Labels are computed via STATUS_LABELS so
// the on-screen text is properly capitalised ("In Progress", not
// "in progress") regardless of the wire value.
export const STATUS_OPTIONS = ['pending', 'in_progress', 'completed', 'cancelled'] as const;
export const STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
  overdue: 'Overdue',
};

// Item lists for the shared Select. Priority carries lowercase wire
// values; title-case the label so the picker reads "Low / Medium / …"
// (matches the old CSS-``capitalize`` presentation).
export const PRIORITY_ITEMS = PRIORITY_OPTIONS.map((p) => ({
  value: p,
  label: p.charAt(0).toUpperCase() + p.slice(1),
}));
export const STATUS_ITEMS = STATUS_OPTIONS.map((s) => ({
  value: s,
  label: STATUS_LABELS[s] || s,
}));

// Lifecycle split for the grid's segment tabs.  Tabs answer "is this
// still work, or history?" — the urgency chips above the grid keep
// answering "how urgent is the work?" within Active.  Completed AND
// cancelled both live in Archive: they're closed tickets either way,
// and Cancelled stays reachable via the Status column filter there.
export const CLOSED_STATUSES = new Set(['completed', 'cancelled']);

/** Which dimension a task is scheduled against.  Both forms pick one and
 *  send only that dimension's value, so the API never stores a stale
 *  number from a mode the operator switched away from. */
export type TriggerMode = 'date' | 'miles' | 'hours';

// Convert a period in days (e.g. "30") to a YYYY-MM-DD due date by
// adding to today's calendar day. Returns empty when the period is
// empty/non-numeric (caller treats that as "no date trigger").
export function _periodDaysToDueDate(periodDays: string): string {
  if (!periodDays) return '';
  const n = Number(periodDays);
  if (!Number.isFinite(n)) return '';
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + Math.round(n));
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

// Inverse of _periodDaysToDueDate — pulls the period out of an
// existing absolute due date so the Edit drawer can pre-fill the
// period input with "remaining days until due".
export function _dueDateToPeriodDays(due: string | null | undefined): string {
  if (!due) return '';
  const target = new Date(due + 'T00:00:00');
  if (Number.isNaN(target.getTime())) return '';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
  return String(diff);
}

// Single date formatter used everywhere in the drawer so Created /
// Completed / Current / Due all read the same way ("May 17, 2026")
// instead of mixing numeric and long-form locale defaults.
export const DATE_FMT: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'short', day: 'numeric' };

export function _formatDate(input: string | null | undefined, tz?: string): string {
  if (!input) return '';
  // YYYY-MM-DD inputs land at midnight UTC if parsed bare; pin to
  // local midnight so the rendered day matches the user's calendar.
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(input) ? input + 'T00:00:00' : input;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return formatDay(d, { timeZone: tz, intl: DATE_FMT });
}

export function _todayLabel(tz?: string): string {
  return formatDay(new Date(), { timeZone: tz, intl: DATE_FMT });
}

// Snooze Alerts is only meaningful when the task is actually nagging
// the operator — overdue right now, or close enough to the threshold
// that the pre-overdue warning would fire.  At create time / for
// far-future tasks, the buttons are clutter, so we hide them.
// Thresholds match the backend ``detect_upcoming_warnings`` defaults:
// 7 days / 500 mi / 50 engine hours.
export function _isOverdueOrApproaching(t: MaintenanceTask): boolean {
  if (t.status === 'overdue') return true;
  if (t.status !== 'pending') return false;
  if (t.due_date) {
    const due = /^\d{4}-\d{2}-\d{2}$/.test(t.due_date)
      ? new Date(t.due_date + 'T00:00:00')
      : new Date(t.due_date);
    if (!Number.isNaN(due.getTime())) {
      const now = new Date();
      const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
      const startOfDue   = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime();
      const days = Math.round((startOfDue - startOfToday) / 86_400_000);
      if (days <= 7) return true;
    }
  }
  if (t.due_miles != null && t.last_odometer != null
      && t.last_odometer + 500 >= t.due_miles) {
    return true;
  }
  if (t.due_engine_hours != null && t.last_engine_hours != null
      && t.last_engine_hours + 50 >= t.due_engine_hours) {
    return true;
  }
  return false;
}
