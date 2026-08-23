import { useState, useMemo, useEffect } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import type { MaintenanceTask } from '../../types';
import { toneClasses } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { Dialog, DialogContent } from '../../components/ui/dialog';

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

// Parse a due_date that may be a bare YYYY-MM-DD or a full ISO string.
// Bare YYYY-MM-DD lands on UTC midnight when fed straight to Date(),
// which renders one calendar day early in negative timezones (US, etc).
// Pinning to local midnight keeps the calendar slot accurate.
function parseDueDate(value: string): Date {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(value) ? value + 'T00:00:00' : value;
  return new Date(iso);
}

// Build the 6×7 grid (always 42 cells so calendar height is stable).
function buildMonthCells(viewDate: Date): Date[] {
  const first = startOfMonth(viewDate);
  const firstWeekday = first.getDay();
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - firstWeekday);
  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart);
    d.setDate(gridStart.getDate() + i);
    cells.push(d);
  }
  return cells;
}

// Priority → dot fill, via the shared status tones (low→neutral,
// medium→info, high→warn, critical→danger — see lib/status.ts).
// Neutral has no dedicated hue, so the "no signal" low dot uses the
// muted-foreground fill that ``toneText('neutral')`` resolves to.
const PRIORITY_DOT: Record<string, string> = {
  low:      'bg-muted-foreground',
  medium:   'bg-info',
  high:     'bg-warn',
  critical: 'bg-danger',
};

export function CalendarMonth({
  tasks,
  onTaskClick,
}: {
  tasks: MaintenanceTask[];
  onTaskClick: (t: MaintenanceTask) => void;
}) {
  const tz = useTimezone();
  const [viewDate, setViewDate] = useState<Date>(() => new Date());
  // Day-detail popover state — when a day has more than 3 tasks, the
  // operator clicks "+ N more" and the full list opens here.  ``null``
  // when closed; ``{ date, tasks }`` while open.
  const [dayDetail, setDayDetail] = useState<{
    date: Date;
    tasks: MaintenanceTask[];
  } | null>(null);

  // Escape closes the day-detail popover.  Matches the Tasks page
  // pattern so the calendar feels consistent with the rest of the SPA.
  useEffect(() => {
    if (!dayDetail) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDayDetail(null);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [dayDetail]);

  const tasksByDay = useMemo(() => {
    // Hard date pinned by the operator wins.  Mileage-only tasks fall
    // back to ``projected_due_date`` (server-computed from the
    // vehicle's recent average daily miles) so they land on the day
    // they're actually expected to come due.  The render below
    // dashes the dot for projected entries so the operator can tell
    // them apart from hard-scheduled tasks.
    const m = new Map<string, MaintenanceTask[]>();
    for (const t of tasks) {
      const dateStr = t.due_date || t.projected_due_date;
      if (!dateStr) continue;
      const d = parseDueDate(dateStr);
      if (Number.isNaN(d.getTime())) continue;
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const existing = m.get(key);
      if (existing) existing.push(t);
      else m.set(key, [t]);
    }
    return m;
  }, [tasks]);

  // Footer panel: tasks with NO date at all (neither hard nor
  // projected).  Usually means we don't have enough recent telemetry
  // to project a date — newly-onboarded trucks, or trucks that
  // haven't moved in 30 days.  Honest about it: surface them so
  // they're not lost, but make it clear the calendar can't place
  // them yet.  Sorted so closest-to-due bubble up.
  const noDateTasks = useMemo(() => {
    const items = tasks.filter(t => !t.due_date && !t.projected_due_date
      && (t.due_miles != null || t.due_engine_hours != null));
    const remaining = (t: MaintenanceTask): number => {
      if (t.due_miles != null && t.last_odometer != null) {
        return t.due_miles - t.last_odometer;
      }
      if (t.due_engine_hours != null && t.last_engine_hours != null) {
        return t.due_engine_hours - t.last_engine_hours;
      }
      // No telemetry — sink to the bottom of the list.
      return Number.POSITIVE_INFINITY;
    };
    return [...items].sort((a, b) => remaining(a) - remaining(b));
  }, [tasks]);

  const cells = useMemo(() => buildMonthCells(viewDate), [viewDate]);
  const today = new Date();
  const monthLabel = viewDate.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/40">
        <h3 className="text-sm font-semibold">{monthLabel}</h3>
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            onClick={() => setViewDate(d => addMonths(d, -1))}
            className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
            aria-label="Previous month"
          >
            <ChevronLeft className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => setViewDate(new Date())}
            className="px-2 py-1 text-xs hover:bg-muted rounded text-muted-foreground hover:text-foreground min-h-tap"
          >
            Today
          </button>
          <button
            type="button"
            onClick={() => setViewDate(d => addMonths(d, 1))}
            className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
            aria-label="Next month"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 border-b border-border bg-muted/20">
        {WEEKDAY_LABELS.map(w => (
          <div key={w} className="text-xs text-muted-foreground px-2 py-1.5 text-center font-medium">
            {w}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7">
        {cells.map((cell, i) => {
          const inMonth = cell.getMonth() === viewDate.getMonth();
          const isToday = isSameDay(cell, today);
          const key = `${cell.getFullYear()}-${cell.getMonth()}-${cell.getDate()}`;
          const dayTasks = tasksByDay.get(key) ?? [];
          const visible = dayTasks.slice(0, 3);
          const overflow = dayTasks.length - visible.length;
          return (
            <div
              key={i}
              className={`min-h-[88px] p-1.5 border-r border-b border-border ${
                inMonth ? 'bg-card' : 'bg-muted/20'
              } ${i % 7 === 6 ? 'border-r-0' : ''}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className={`text-xs ${
                  isToday ? 'inline-flex items-center justify-center w-5 h-5 rounded-full bg-primary text-primary-foreground font-semibold'
                  : inMonth ? 'text-foreground' : 'text-muted-foreground/60'
                }`}>
                  {cell.getDate()}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                {visible.map(task => {
                  const dotCls = PRIORITY_DOT[task.priority || 'medium'] ?? PRIORITY_DOT.medium;
                  // Projected (mileage-derived) entries get a dashed
                  // outline + italic vehicle name so the operator can
                  // tell "I scheduled this" from "the system guessed
                  // when it'll come due".  Tooltip discloses the
                  // velocity assumption that drove the projection.
                  const isProjected = !task.due_date && Boolean(task.projected_due_date);
                  // Build a richer projection tooltip when the new
                  // velocity provenance fields are populated.  Older
                  // backends that don't send them fall through to the
                  // original "mi/day avg" string so the calendar
                  // never loses its disclosure during rollout.
                  let projTitle = '';
                  if (isProjected && task.velocity_avg_daily_miles) {
                    const v = task.velocity_avg_daily_miles.toLocaleString();
                    const window = task.velocity_window_days;
                    const driveDays = task.velocity_drive_days;
                    if (window && driveDays) {
                      projTitle = ` (projected from ${v} mi/day median over `
                        + `${driveDays} drive day${driveDays === 1 ? '' : 's'} in `
                        + `the last ${window} days)`;
                    } else {
                      projTitle = ` (projected from ${v} mi/day avg)`;
                    }
                  }
                  return (
                    <button
                      key={task.id}
                      type="button"
                      onClick={() => onTaskClick(task)}
                      className={`flex items-center gap-1 text-left px-1.5 py-0.5 text-3xs rounded truncate ${
                        isProjected
                          ? 'bg-card hover:bg-muted border border-dashed border-border'
                          : 'bg-muted/60 hover:bg-muted'
                      } min-h-tap`}
                      title={`#${task.vehicle_name} · ${task.task_type} · ${task.description || 'no description'}${projTitle}`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}`} />
                      <span className={`shrink-0 ${isProjected ? 'italic' : ''}`}>{task.vehicle_name}</span>
                      <span className="text-muted-foreground truncate">{task.task_type}</span>
                    </button>
                  );
                })}
                {overflow > 0 && (
                  <button
                    type="button"
                    onClick={() => setDayDetail({ date: cell, tasks: dayTasks })}
                    className="text-3xs text-muted-foreground hover:text-foreground text-left px-1.5 py-1 -my-1 min-h-tap"
                  >
                    + {overflow} more
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* No-date tasks footer — mileage-only / hours-only tasks that
          have no calendar position but still need to be visible in
          the planning view.  Renders only when there are any, so the
          footer doesn't take up space for date-only fleets. */}
      {noDateTasks.length > 0 && (
        <div className="border-t border-border bg-muted/20 px-4 py-3">
          <p className="text-xs font-medium text-muted-foreground mb-2">
            Mileage / hours-only tasks ({noDateTasks.length}) — no calendar date
          </p>
          <div className="flex flex-wrap gap-1.5">
            {noDateTasks.slice(0, 12).map(task => {
              const dotCls = PRIORITY_DOT[task.priority || 'medium'] ?? PRIORITY_DOT.medium;
              const remaining = task.due_miles != null && task.last_odometer != null
                ? `${Math.max(0, Math.round(task.due_miles - task.last_odometer)).toLocaleString()} mi to go`
                : task.due_engine_hours != null && task.last_engine_hours != null
                  ? `${Math.max(0, Math.round(task.due_engine_hours - task.last_engine_hours)).toLocaleString()} h to go`
                  : task.due_miles != null
                    ? `due ${Number(task.due_miles).toLocaleString()} mi`
                    : `due ${Number(task.due_engine_hours).toLocaleString()} h`;
              return (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => onTaskClick(task)}
                  className="inline-flex items-center gap-1.5 px-2 py-1 text-2xs rounded bg-card hover:bg-muted border border-border min-h-tap"
                  title={`#${task.vehicle_name} · ${task.task_type} · ${task.description || 'no description'}`}
                >
                  <span aria-hidden className={`w-1.5 h-1.5 rounded-full ${dotCls}`} />
                  <span>{task.vehicle_name}</span>
                  <span className="text-muted-foreground">·</span>
                  <span className="text-muted-foreground">{remaining}</span>
                </button>
              );
            })}
            {noDateTasks.length > 12 && (
              <span className="self-center text-2xs text-muted-foreground">
                + {noDateTasks.length - 12} more (visible in list view)
              </span>
            )}
          </div>
        </div>
      )}

      {/* Was a hand-rolled backdrop + panel — click-away only, so no focus
          trap, no Escape, no ``aria-modal``, no background scroll lock.
          <Dialog> brings all four and does the scrolling too. */}
      <Dialog open={!!dayDetail} onOpenChange={(o) => { if (!o) setDayDetail(null); }}>
        <DialogContent showCloseButton={false} className="sm:max-w-lg p-5">
          {dayDetail && (
          <>
            <div className="flex items-start justify-between mb-3">
              <div>
                <h3 className="text-base font-semibold">
                  {formatDay(dayDetail.date, {
                    timeZone: tz,
                    intl: { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' },
                  })}
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {dayDetail.tasks.length} task{dayDetail.tasks.length === 1 ? '' : 's'} due
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDayDetail(null)}
                aria-label="Close"
                className="text-muted-foreground hover:text-foreground p-1"
              >
                <X className="size-4" />
              </button>
            </div>
            <ul className="space-y-2">
              {dayDetail.tasks.map(task => {
                const dotCls = PRIORITY_DOT[task.priority || 'medium'] ?? PRIORITY_DOT.medium;
                return (
                  <li key={task.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setDayDetail(null);
                        onTaskClick(task);
                      }}
                      className="w-full text-left p-2.5 rounded-lg bg-muted/40 hover:bg-muted border border-border/60 transition flex items-start gap-2"
                    >
                      <span className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${dotCls}`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium flex items-center gap-2">
                          <span>#{task.vehicle_name}</span>
                          <span className="text-muted-foreground capitalize">
                            {(task.task_type || '').replace(/_/g, ' ')}
                          </span>
                          {task.status === 'overdue' && (
                            <span className={`text-3xs uppercase tracking-wide px-1.5 py-0.5 rounded-md ${toneClasses('danger')}`}>
                              Overdue
                            </span>
                          )}
                        </div>
                        {task.description && (
                          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                            {task.description}
                          </p>
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
