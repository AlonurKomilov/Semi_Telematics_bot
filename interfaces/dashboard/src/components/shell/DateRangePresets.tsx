import { useEffect, useRef, useState } from 'react';
import { Calendar, ChevronDown, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';

/**
 * Quick-select day-range picker with optional custom-range calendar.
 *
 * Mirrors the competitor dashboard's `Today / Yesterday / Last 7 / 14 /
 * 30 / 60 / 90` dropdown plus a two-month calendar for picking arbitrary
 * start/end dates. Writes back to a single `days` integer so existing
 * API endpoints (which expect `days=N` from-today semantics) keep
 * working unchanged.
 *
 * Custom-range UX caveat: the underlying scoring backend computes
 * windows as "last N days from today", so picking a custom start date
 * is interpreted as `days = today - start_date`. The calendar's end
 * date is informational and shown back to the user, but the API call
 * always rounds the window to end at "today".
 */

export interface DateRangePresetsProps {
  value: number;
  onChange: (days: number) => void;
  /** Allowed presets — defaults to the same set the competitor exposes. */
  options?: { label: string; days: number }[];
  /**
   * When true, render a subtle spinner inside the trigger so the user
   * sees feedback while the new period's data is being fetched.  Pages
   * pass React Query's ``isFetching`` here.  Without this the only
   * cue that a 7→90 day switch did anything was the eventual table
   * re-render, which on a slow Samsara fallback looked frozen.
   */
  isFetching?: boolean;
}

const DEFAULT_OPTIONS = [
  { label: 'Today', days: 1 },
  { label: 'Yesterday', days: 2 },
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 14 days', days: 14 },
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 60 days', days: 60 },
  { label: 'Last 90 days', days: 90 },
];

function startOfDay(d: Date): Date {
  const c = new Date(d);
  c.setHours(0, 0, 0, 0);
  return c;
}

function daysBetween(from: Date, to: Date): number {
  const ms = startOfDay(to).getTime() - startOfDay(from).getTime();
  return Math.max(1, Math.round(ms / (24 * 60 * 60 * 1000)));
}

function fmtIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtNice(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function labelFor(value: number, opts: { label: string; days: number }[]): string {
  const match = opts.find((o) => o.days === value);
  if (match) return match.label;
  return `Last ${value} days`;
}

// ── Mini-calendar for picking a start date ───────────────────

interface CalendarMonthProps {
  monthStart: Date;
  selected: Date | null;
  hover: Date | null;
  rangeEnd: Date;
  onPick: (d: Date) => void;
  onHover: (d: Date | null) => void;
}

function CalendarMonth({ monthStart, selected, hover, rangeEnd, onPick, onHover }: CalendarMonthProps) {
  const today = startOfDay(new Date());
  const year = monthStart.getFullYear();
  const month = monthStart.getMonth();
  const first = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0).getDate();
  const startWeekday = first.getDay();

  const cells: (Date | null)[] = [];
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= lastDay; d++) cells.push(new Date(year, month, d));
  while (cells.length % 7 !== 0) cells.push(null);

  const inRange = (d: Date): boolean => {
    if (!selected) return false;
    const end = hover && hover > selected ? hover : rangeEnd;
    return d >= startOfDay(selected) && d <= startOfDay(end);
  };

  return (
    <div className="w-56">
      <p className="text-xs font-semibold text-foreground text-center mb-2">
        {monthStart.toLocaleString(undefined, { month: 'long', year: 'numeric' })}
      </p>
      <div className="grid grid-cols-7 gap-0.5 text-[10px] text-muted-foreground text-center mb-1">
        {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map((d) => <div key={d}>{d}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((d, i) => {
          if (!d) return <div key={i} />;
          const future = d > today;
          const isSelected = selected && fmtIso(d) === fmtIso(selected);
          const ranged = inRange(d) && !isSelected;
          return (
            <button
              key={i}
              disabled={future}
              onClick={() => onPick(d)}
              onMouseEnter={() => onHover(d)}
              onMouseLeave={() => onHover(null)}
              className={`text-xs h-7 rounded transition ${
                future
                  ? 'text-muted-foreground/30 cursor-not-allowed'
                  : isSelected
                  ? 'bg-primary text-primary-foreground font-semibold'
                  : ranged
                  ? 'bg-primary/15 text-foreground'
                  : 'text-foreground/80 hover:bg-muted'
              }`}
            >
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function DateRangePresets({
  value,
  onChange,
  options = DEFAULT_OPTIONS,
  isFetching = false,
}: DateRangePresetsProps) {
  const [open, setOpen] = useState(false);
  const [showCal, setShowCal] = useState(false);
  const [pickerMonth, setPickerMonth] = useState<Date>(() => {
    const d = new Date();
    d.setDate(1);
    return d;
  });
  const [pickedStart, setPickedStart] = useState<Date | null>(null);
  const [hoverDate, setHoverDate] = useState<Date | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setShowCal(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const today = startOfDay(new Date());

  const applyCustom = () => {
    if (!pickedStart) return;
    // Backend composite endpoint caps at 90 days; clamp here so the
    // calendar can't generate API errors. Larger ranges silently fall
    // back to the maximum supported window.
    const days = Math.max(1, Math.min(90, daysBetween(pickedStart, today)));
    onChange(days);
    setOpen(false);
    setShowCal(false);
    setPickedStart(null);
  };

  const isCustom = !options.some((o) => o.days === value);

  const monthBack = () => {
    const d = new Date(pickerMonth);
    d.setMonth(d.getMonth() - 1);
    setPickerMonth(d);
  };
  const monthFwd = () => {
    const d = new Date(pickerMonth);
    d.setMonth(d.getMonth() + 1);
    setPickerMonth(d);
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => { setOpen((o) => !o); setShowCal(false); }}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-background border border-border rounded-md text-sm text-foreground/80 hover:bg-muted transition"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-busy={isFetching}
      >
        {isFetching ? (
          <Loader2 size={13} className="animate-spin text-primary" aria-label="Loading" />
        ) : (
          <Calendar size={13} className="text-muted-foreground" />
        )}
        {isCustom ? `Last ${value} days` : labelFor(value, options)}
        <ChevronDown size={12} className="text-muted-foreground" />
      </button>

      {open && !showCal && (
        <ul
          role="listbox"
          className="absolute right-0 top-full mt-1 w-48 max-h-80 overflow-y-auto bg-card border border-border rounded-md shadow-xl z-50 py-1"
        >
          {options.map((opt) => {
            const active = opt.days === value;
            return (
              <li key={opt.days}>
                <button
                  onClick={() => { onChange(opt.days); setOpen(false); }}
                  className={`w-full flex items-center justify-between px-3 py-1.5 text-sm transition ${
                    active
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-foreground/80 hover:bg-muted'
                  }`}
                >
                  <span>{opt.label}</span>
                  {active && <span className="text-[10px] text-primary">●</span>}
                </button>
              </li>
            );
          })}
          <li className="border-t border-border mt-1 pt-1">
            <button
              onClick={() => setShowCal(true)}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-foreground/80 hover:bg-muted transition"
            >
              <Calendar size={12} className="text-muted-foreground" />
              Custom range…
            </button>
          </li>
        </ul>
      )}

      {open && showCal && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-card border border-border rounded-md shadow-xl z-50 p-3">
          <div className="flex items-center justify-between mb-2">
            <button
              onClick={monthBack}
              className="p-1 rounded hover:bg-muted text-muted-foreground"
              aria-label="Previous month"
            >
              <ChevronLeft size={14} />
            </button>
            <p className="text-xs text-muted-foreground">Pick a start date</p>
            <button
              onClick={monthFwd}
              className="p-1 rounded hover:bg-muted text-muted-foreground"
              aria-label="Next month"
            >
              <ChevronRight size={14} />
            </button>
          </div>
          <CalendarMonth
            monthStart={pickerMonth}
            selected={pickedStart}
            hover={hoverDate}
            rangeEnd={today}
            onPick={setPickedStart}
            onHover={setHoverDate}
          />
          <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>
              {pickedStart
                ? `${fmtNice(pickedStart)} → ${fmtNice(today)} (${daysBetween(pickedStart, today)}d)`
                : 'Range ends today'}
            </span>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={() => { setShowCal(false); setPickedStart(null); }}
              className="px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              onClick={applyCustom}
              disabled={!pickedStart}
              className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
