import type { ElementType } from 'react';
import {
  Wrench, Droplet, Droplets, Circle, Cog, Zap, Flame,
  ClipboardCheck, Landmark, OctagonAlert,
} from 'lucide-react';
import type { MaintenanceTask } from '../../types';

export const PRIORITY_OPTIONS = ['low', 'medium', 'high', 'critical'] as const;
export type Priority = typeof PRIORITY_OPTIONS[number];

const PRIORITY_CLASSES: Record<Priority, string> = {
  low:      'bg-slate-500/15  text-slate-600  dark:text-slate-300  border-slate-500/30',
  medium:   'bg-blue-500/15   text-blue-700   dark:text-blue-400   border-blue-500/30',
  high:     'bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30',
  critical: 'bg-red-500/15    text-red-700    dark:text-red-400    border-red-500/30',
};

export function PriorityBadge({ value }: { value: unknown }) {
  const v = (String(value || 'medium').toLowerCase()) as Priority;
  const cls = PRIORITY_CLASSES[v] ?? PRIORITY_CLASSES.medium;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium capitalize ${cls}`}>
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

export function EngineHoursProgress({ row }: { row: MaintenanceTask }) {
  if (row.due_engine_hours == null) return <EmptyDueCell />;
  if (row.last_engine_hours == null) {
    return (
      <span className="text-muted-foreground text-xs">
        Due {Number(row.due_engine_hours).toLocaleString()} hrs
      </span>
    );
  }
  const pct = Math.max(0, Math.min(120, Math.round((row.last_engine_hours / row.due_engine_hours) * 100)));
  const overdue = pct >= 100;
  const remaining = Math.round(row.due_engine_hours - row.last_engine_hours);
  return (
    <div className="flex flex-col gap-1.5 min-w-[120px]">
      <div className="text-xs">
        {Number(row.last_engine_hours).toLocaleString()} / {Number(row.due_engine_hours).toLocaleString()} hrs
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full ${overdue ? 'bg-red-500' : pct >= 90 ? 'bg-orange-500' : 'bg-purple-500'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className={`text-xs ${overdue ? 'text-red-500 font-medium' : 'text-muted-foreground'}`}>
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
export const TASK_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'inspection',     label: 'General Inspection' },
  { value: 'oil',            label: 'Oil Change' },
  { value: 'tires',          label: 'Tire Service' },
  { value: 'brakes',         label: 'Brake Inspection' },
  { value: 'transmission',   label: 'Transmission' },
  { value: 'electrical',     label: 'Electrical' },
  { value: 'dot_inspection', label: 'DOT Inspection' },
  { value: 'dpf_regen',      label: 'DPF Regen' },
  { value: 'def_refill',     label: 'DEF Refill' },
  { value: 'custom',         label: 'Custom' },
];

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
const TYPE_LABELS: Record<string, string> = {
  oil:            'Oil Change',
  tires:          'Tire Service',
  brakes:         'Brake Inspection',
  inspection:     'Inspection',
  transmission:   'Transmission',
  electrical:     'Electrical',
  dot_inspection: 'DOT Inspection',
  dpf_regen:      'DPF Regen',
  def_refill:     'DEF Refill',
  custom:         'Custom',
};

export function TaskTypeCell({ type }: { type: string }) {
  const Icon = TYPE_ICON_COMPONENTS[type] ?? Wrench;
  const colour = TYPE_ICON_COLORS[type] ?? 'text-muted-foreground';
  const label = TYPE_LABELS[type] ?? String(type || '').replace(/_/g, ' ');
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon size={14} className={`shrink-0 ${colour}`} />
      <span className="capitalize">{label}</span>
    </span>
  );
}

// due-date urgency chip. Buckets mirror the bot's overdue-alert scheduler.
export function DueDateChip({ value }: { value: unknown }) {
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
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfDue   = new Date(due.getFullYear(), due.getMonth(), due.getDate());
  const days = Math.round((startOfDue.getTime() - startOfToday.getTime()) / 86_400_000);
  const dateStr = due.toLocaleDateString();

  let cls = 'bg-muted text-muted-foreground border-border/50';
  let suffix = '';
  if (days < 0) {
    cls = 'bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30';
    suffix = ` · ${Math.abs(days)}d overdue`;
  } else if (days <= 7) {
    cls = 'bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30';
    suffix = days === 0 ? ' · today' : ` · in ${days}d`;
  } else if (days <= 30) {
    cls = 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30';
    suffix = ` · in ${days}d`;
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${cls}`}>
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
  const pct = Math.max(0, Math.min(120, Math.round((row.last_odometer / row.due_miles) * 100)));
  const overdue = pct >= 100;
  const remaining = Math.round(row.due_miles - row.last_odometer);
  return (
    <div className="flex flex-col gap-1.5 min-w-[140px]">
      <div className="text-xs">
        {Number(row.last_odometer).toLocaleString()} / {Number(row.due_miles).toLocaleString()} mi
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full ${overdue ? 'bg-red-500' : pct >= 90 ? 'bg-orange-500' : 'bg-blue-500'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <div className={`text-xs ${overdue ? 'text-red-500 font-medium' : 'text-muted-foreground'}`}>
        {overdue
          ? `${Math.abs(remaining).toLocaleString()} mi overdue`
          : `${remaining.toLocaleString()} mi to go`}
      </div>
    </div>
  );
}
