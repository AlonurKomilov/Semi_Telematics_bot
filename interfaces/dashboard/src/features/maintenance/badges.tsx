import type { ElementType } from 'react';
import {
  Wrench, Droplet, Droplets, Circle, Cog, Zap, Flame,
  ClipboardCheck, Landmark, OctagonAlert,
} from 'lucide-react';
import type { MaintenanceTask } from '../../types';
import { statusTone, toneClasses, type Tone } from '../../lib/status';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

export const PRIORITY_OPTIONS = ['low', 'medium', 'high', 'critical'] as const;
export type Priority = typeof PRIORITY_OPTIONS[number];

export function PriorityBadge({ value }: { value: unknown }) {
  const v = String(value || 'medium').toLowerCase();
  // Colour comes from the shared status system: low→neutral,
  // medium→info, high→warn, critical→danger (see lib/status.ts).
  // Unknown values fall back to the medium tone, matching the old
  // ``?? PRIORITY_CLASSES.medium`` default, while still showing the
  // raw text.
  const known = (PRIORITY_OPTIONS as readonly string[]).includes(v) ? v : 'medium';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium capitalize ${toneClasses(statusTone(known))}`}>
      {v}
    </span>
  );
}

// Consistent empty-state for the three "due-by" cells (Due Date /
// Mileage / Engine Hours).  Muted em-dash so a row with no value
// reads as "no value" instead of a missing cell that looks like a
// rendering bug.
export function EmptyDueCell() {
  return (
    <span className="text-muted-foreground/50 text-xs">—</span>
  );
}

// Once a task closes, the live "X hrs to go" counter is meaningless —
// the work is done.  Show a frozen "completed at X hrs" line instead,
// with a filled-green bar so the row reads as "this finished" at a
// glance.  Same idea for the Mileage and DueDate cells below.
function isClosed(status: string | undefined | null): boolean {
  return status === 'completed' || status === 'done' || status === 'cancelled';
}

export function EngineHoursProgress({ row }: { row: MaintenanceTask }) {
  if (row.due_engine_hours == null) return <EmptyDueCell />;
  if (row.last_engine_hours == null) {
    return (
      <span className="text-muted-foreground text-xs">
        Due {Number(row.due_engine_hours).toLocaleString()} hrs
      </span>
    );
  }
  // Closed tasks (completed / cancelled) freeze the readout — no live
  // "X hrs to go" since there's no future event to count down to.
  if (isClosed(row.status)) {
    return (
      <div className="flex flex-col gap-1.5 min-w-[120px]">
        <div className="text-xs text-muted-foreground">
          Completed at {Number(row.last_engine_hours).toLocaleString()} hrs
        </div>
        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div className="h-full bg-ok/40" style={{ width: '100%' }} />
        </div>
      </div>
    );
  }
  // Period-relative progress: the bar shows how much of the recur
  // interval has been consumed, not the absolute current/due ratio.
  // A task with due=1,000 hrs and recur=500 hrs that's at last=510
  // should render as 2% (just past the period start) — NOT 51%
  // (which is misleading "absolute" progress).  Falls back to
  // absolute when no recur is set so one-time tasks still get a
  // sensible-ish bar.
  const period = row.recur_interval_engine_hours ?? null;
  let pct: number;
  if (period && period > 0) {
    const periodStart = row.due_engine_hours - period;
    pct = ((row.last_engine_hours - periodStart) / period) * 100;
  } else {
    pct = (row.last_engine_hours / row.due_engine_hours) * 100;
  }
  pct = Math.max(0, Math.min(120, Math.round(pct)));
  const overdue = pct >= 100;
  const remaining = Math.round(row.due_engine_hours - row.last_engine_hours);
  return (
    <div className="flex flex-col gap-1.5 min-w-[120px]">
      <div className="text-xs">
        {Number(row.last_engine_hours).toLocaleString()} / {Number(row.due_engine_hours).toLocaleString()} hrs
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full ${overdue ? 'bg-danger' : pct >= 90 ? 'bg-warn' : 'bg-ok'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className={`text-xs ${overdue ? 'text-danger font-medium' : 'text-muted-foreground'}`}>
        {overdue
          ? `${Math.abs(remaining).toLocaleString()} hrs overdue`
          : `${remaining.toLocaleString()} hrs to go`}
      </div>
    </div>
  );
}

// Task-type options — MUST match capabilities/maintenance/service.py:TASK_TYPES
// (the SSOT used by the bot wizard and the AI tool).
//
// Labels stay text-only here.  HTML ``<select>`` options can't render
// SVGs reliably across browsers, and the emoji prefixes the form used
// before rendered as missing-glyph placeholders on some platforms
// (the user's screenshot showed several broken).  The table cell
// renders proper lucide-react icons via ``TaskTypeCell`` — that's
// where the visual icon belongs, not in the picker dropdown.
const TYPE_ICON_COMPONENTS: Record<string, ElementType> = {
  oil:            Droplet,
  tires:          Circle,
  brakes:         OctagonAlert,
  inspection:     ClipboardCheck,
  transmission:   Cog,
  electrical:     Zap,
  dot_inspection: Landmark,
  dpf_regen:      Flame,
  def_refill:     Droplets,
  custom:         Wrench,
};
const TYPE_ICON_COLORS: Record<string, string> = {
  oil:            'text-amber-600 dark:text-amber-400',
  tires:          'text-slate-600 dark:text-slate-300',
  brakes:         'text-red-600 dark:text-red-400',
  inspection:     'text-blue-600 dark:text-blue-400',
  transmission:   'text-purple-600 dark:text-purple-400',
  electrical:     'text-yellow-600 dark:text-yellow-400',
  dot_inspection: 'text-cyan-600 dark:text-cyan-400',
  dpf_regen:      'text-orange-600 dark:text-orange-400',
  def_refill:     'text-emerald-600 dark:text-emerald-400',
  custom:         'text-muted-foreground',
};
export function TaskTypeCell({
  type,
  customLabel,
}: {
  type: string;
  // The resolved display name from the account's service_tasks list
  // (``useTaskLabels`` in the service-tasks feature) — the SSOT.  The
  // parent passes it so the cell doesn't query React-Query per row.
  // The hardcoded TYPE_LABELS map that used to answer first is gone:
  // it had drifted ("Brake Inspection" for a task really named
  // "Brake Service") — a second copy of a vocabulary always does.
  customLabel?: string;
}) {
  const Icon = TYPE_ICON_COMPONENTS[type] ?? Wrench;
  const colour = TYPE_ICON_COLORS[type] ?? 'text-muted-foreground';
  // Fallback chain:
  //   1. The SSOT label passed in by the caller.
  //   2. ``custom_<slug>`` → strip prefix + de-kebab so cells stay
  //      readable before the service-tasks fetch returns.
  //   3. Raw value with underscores → spaces.
  let label = customLabel;
  if (!label) {
    const isCustom = (type || '').startsWith('custom_');
    label = isCustom
      ? type.slice('custom_'.length).replace(/-/g, ' ')
      : String(type || '').replace(/_/g, ' ');
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon size={14} className={`shrink-0 ${colour}`} />
      <span className="capitalize">{label}</span>
    </span>
  );
}

// due-date urgency chip. Buckets mirror the bot's overdue-alert scheduler.
// ``status`` (optional) lets the chip flip into a "closed ticket"
// presentation: a "completed Mon DD" pill with no urgency suffix, so
// the row reads as done rather than "Xd overdue" against a stale
// target.
// ``recurDays`` (optional) is the task's ``recur_interval_days``;
// when present the chip gains a small inline progress bar underneath
// showing how much of the period has elapsed — matches the period-
// relative bars on Mileage / Engine Hours.
export function DueDateChip({
  value,
  status,
  recurDays,
}: {
  value: unknown;
  status?: string;
  recurDays?: number | null;
}) {
  const tz = useTimezone();
  if (!value) return <EmptyDueCell />;
  // Pin bare YYYY-MM-DD to local midnight; otherwise Date() treats it
  // as UTC midnight, which renders one day early in negative timezones
  // (US, etc). Same pattern Tasks.tsx uses in _formatDate.
  const raw = String(value);
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw + 'T00:00:00' : raw;
  const due = new Date(iso);
  if (Number.isNaN(due.getTime())) {
    return <EmptyDueCell />;
  }
  const dateStr = formatDay(due, { timeZone: tz });

  // Closed ticket → frozen pill, no urgency arithmetic.
  if (isClosed(status)) {
    return (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses('neutral')}`}>
        {dateStr}
      </span>
    );
  }

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfDue   = new Date(due.getFullYear(), due.getMonth(), due.getDate());
  const days = Math.round((startOfDue.getTime() - startOfToday.getTime()) / 86_400_000);

  let tone: Tone = 'neutral';
  let suffix = '';
  if (days < 0) {
    tone = 'danger';
    suffix = ` · ${Math.abs(days)}d overdue`;
  } else if (days <= 7) {
    tone = 'warn';
    suffix = days === 0 ? ' · today' : ` · in ${days}d`;
  } else if (days <= 30) {
    // Within-a-week (was orange) and within-a-month (was yellow) were
    // both warn-family hues — they collapse to the single ``warn``
    // tone; the suffix text still carries the exact day count.
    tone = 'warn';
    suffix = ` · in ${days}d`;
  }

  // Period-relative progress bar.  Mirrors the Mileage / Engine Hours
  // cells so all three "due by" reads share the same mental model:
  // bar = how much of the recur interval has been consumed.  No bar
  // when recur_interval_days isn't set — the chip alone is enough
  // for one-time date-based tasks.
  if (recurDays && recurDays > 0) {
    const consumed = recurDays - days;
    const rawPct = (consumed / recurDays) * 100;
    const pct = Math.max(0, Math.min(120, Math.round(rawPct)));
    const overdue = pct >= 100;
    const barTone =
      overdue ? 'bg-danger'
      : pct >= 90 ? 'bg-warn'
      : 'bg-ok';
    return (
      <div className="flex flex-col gap-1.5 min-w-[140px]">
        <span className={`inline-flex items-center self-start px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(tone)}`}>
          {dateStr}{suffix}
        </span>
        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div
            className={`h-full ${barTone}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
      </div>
    );
  }

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-medium ${toneClasses(tone)}`}>
      {dateStr}{suffix}
    </span>
  );
}

export function MileageProgress({ row }: { row: MaintenanceTask }) {
  if (row.due_miles == null) return <EmptyDueCell />;
  if (row.last_odometer == null) {
    return (
      <span
        className="text-muted-foreground text-xs underline decoration-dotted decoration-muted-foreground/40 cursor-help"
        title="No odometer telemetry from this truck — the target is shown without a progress bar.  Connect Samsara or another telematics provider to enable real-time mileage tracking."
      >
        Due {Number(row.due_miles).toLocaleString()} mi
      </span>
    );
  }
  // Closed tasks: freeze the readout to the completion-time odometer.
  // The "X mi to go / overdue" arithmetic doesn't apply once the work
  // is done — show "Completed at X mi" so the row reads as a closed
  // ticket rather than a stale tracking metric.
  if (isClosed(row.status)) {
    return (
      <div className="flex flex-col gap-1.5 min-w-[140px]">
        <div className="text-xs text-muted-foreground">
          Completed at {Number(row.last_odometer).toLocaleString()} mi
        </div>
        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
          <div className="h-full bg-ok/40" style={{ width: '100%' }} />
        </div>
      </div>
    );
  }
  // Period-relative progress — see EngineHoursProgress for the
  // rationale.  An oil change with due=163,289 and recur=40,000 at
  // last=124,074 should read as 2%, not 76%.  Falls back to absolute
  // when no recur_interval_miles is set.
  const period = row.recur_interval_miles ?? null;
  let pct: number;
  if (period && period > 0) {
    const periodStart = row.due_miles - period;
    pct = ((row.last_odometer - periodStart) / period) * 100;
  } else {
    pct = (row.last_odometer / row.due_miles) * 100;
  }
  pct = Math.max(0, Math.min(120, Math.round(pct)));
  const overdue = pct >= 100;
  const remaining = Math.round(row.due_miles - row.last_odometer);
  return (
    <div className="flex flex-col gap-1.5 min-w-[140px]">
      <div className="text-xs">
        {Number(row.last_odometer).toLocaleString()} / {Number(row.due_miles).toLocaleString()} mi
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full ${overdue ? 'bg-danger' : pct >= 90 ? 'bg-warn' : 'bg-ok'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className={`text-xs ${overdue ? 'text-danger font-medium' : 'text-muted-foreground'}`}>
        {overdue
          ? `${Math.abs(remaining).toLocaleString()} mi overdue`
          : `${remaining.toLocaleString()} mi to go`}
      </div>
    </div>
  );
}
