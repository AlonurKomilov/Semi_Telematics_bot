import { useState, useMemo } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { MaintenanceTask } from '../../types';

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

const PRIORITY_DOT: Record<string, string> = {
  low:      'bg-slate-400',
  medium:   'bg-blue-500',
  high:     'bg-orange-500',
  critical: 'bg-red-500',
};

export function CalendarMonth({
  tasks,
  onTaskClick,
}: {
  tasks: MaintenanceTask[];
  onTaskClick: (t: MaintenanceTask) => void;
}) {
  const [viewDate, setViewDate] = useState<Date>(() => new Date());

  const tasksByDay = useMemo(() => {
    const m = new Map<string, MaintenanceTask[]>();
    for (const t of tasks) {
      if (!t.due_date) continue;
      const d = new Date(t.due_date);
      if (Number.isNaN(d.getTime())) continue;
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const existing = m.get(key);
      if (existing) existing.push(t);
      else m.set(key, [t]);
    }
    return m;
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
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            onClick={() => setViewDate(new Date())}
            className="px-2 py-1 text-xs hover:bg-muted rounded text-muted-foreground hover:text-foreground"
          >
            Today
          </button>
          <button
            type="button"
            onClick={() => setViewDate(d => addMonths(d, 1))}
            className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
            aria-label="Next month"
          >
            <ChevronRight size={16} />
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
                  return (
                    <button
                      key={task.id}
                      type="button"
                      onClick={() => onTaskClick(task)}
                      className="flex items-center gap-1 text-left px-1.5 py-0.5 text-[10px] rounded bg-muted/60 hover:bg-muted truncate"
                      title={`#${task.vehicle_name} · ${task.task_type} · ${task.description || 'no description'}`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}`} />
                      <span className="font-mono shrink-0">{task.vehicle_name}</span>
                      <span className="text-muted-foreground truncate">{task.task_type}</span>
                    </button>
                  );
                })}
                {overflow > 0 && (
                  <button
                    type="button"
                    onClick={() => onTaskClick(dayTasks[3])}
                    className="text-[10px] text-muted-foreground hover:text-foreground text-left px-1.5"
                  >
                    + {overflow} more
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
