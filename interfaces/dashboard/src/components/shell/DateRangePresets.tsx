import { useEffect, useRef, useState } from 'react';
import { Calendar, ChevronDown, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

/**
 * Quick-select day-range picker with optional custom-range calendar —
 * THE single source of truth for "pick a time window" (same idea as
 * DataGrid for tables).  Never hand-roll a days dropdown, a chip row,
 * or a numeric days input on a page.
 *
 * Two visual forms, one contract (`value`/`onChange` on a single
 * `days` integer, so `days=N` from-today endpoints work unchanged):
 *
 *   * ``variant="dropdown"`` (default) — the Today / Yesterday /
 *     Last 7 / 14 / 30 / 60 / 90 listbox + "Custom range…" calendar.
 *     Right for page toolbars.
 *   * ``variant="segments"`` — an inline chip row (pass a compact
 *     ``options`` list like 7/30/90) + a calendar chip for custom.
 *     Right for chart/section headers where a dropdown is too heavy.
 *
 * END-DATE support is per-page and FAIL-CLOSED: most backends only
 * understand "last N days from today", so by default the calendar
 * picks a START date and the range ends today.  A page whose backend
 * honors an explicit end (e.g. /events' `end=` param) passes
 * ``onApplyRange`` + ``end`` — then the calendar becomes a true
 * two-click start→end picker.  Never enable it for a page whose API
 * ignores the end date: the label would promise a window the data
 * doesn't match.
 */

export interface DateRangePresetsProps {
  value: number;
  onChange: (days: number) => void;
  /** Allowed presets — defaults to the same set the competitor exposes. */
  options?: { label: string; days: number }[];
  /** Visual form — one contract, two shapes (see the component doc). */
  variant?: 'dropdown' | 'segments';
  /** Custom-calendar clamp — how far back the backend accepts.
   *  Defaults to 90 (the composite scoring endpoints' cap); pages
   *  backed by longer-window endpoints (DOT binder: 24 months) raise it. */
  maxDays?: number;
  /** Lock the control (e.g. while a report is generating). */
  disabled?: boolean;
  /** Current custom END day (``YYYY-MM-DD``), null/undefined = today.
   *  Only meaningful together with ``onApplyRange``. */
  end?: string | null;
  /** Enables TRUE start→end picking in the calendar.  Pass ONLY when
   *  the page's backend honors an explicit end date; called with the
   *  window length and the end day (null = ends today).  Presets keep
   *  firing plain ``onChange`` — reset your end state there. */
  onApplyRange?: (days: number, end: string | null) => void;
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

function fmtNice(d: Date, tz?: string): string {
  return formatDay(d, { timeZone: tz, intl: { month: 'short', day: 'numeric', year: 'numeric' } });
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
  /** Picked END day (two-click mode); null while only the start is set. */
  selectedEnd?: Date | null;
  hover: Date | null;
  rangeEnd: Date;
  onPick: (d: Date) => void;
  onHover: (d: Date | null) => void;
}

function CalendarMonth({ monthStart, selected, selectedEnd = null, hover, rangeEnd, onPick, onHover }: CalendarMonthProps) {
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
    // Highlight priority: a committed end wins; while the end is still
    // unpicked, the hover previews it; otherwise the range runs to
    // rangeEnd (today in start-only mode).
    const end = selectedEnd ?? (hover && hover > selected ? hover : rangeEnd);
    return d >= startOfDay(selected) && d <= startOfDay(end);
  };

  return (
    <div className="w-56">
      <p className="text-xs font-semibold text-foreground text-center mb-2">
        {monthStart.toLocaleString(undefined, { month: 'long', year: 'numeric' })}
      </p>
      <div className="grid grid-cols-7 gap-0.5 text-3xs text-muted-foreground text-center mb-1">
        {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map((d) => <div key={d}>{d}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((d, i) => {
          if (!d) return <div key={i} />;
          const future = d > today;
          const isSelected = (selected && fmtIso(d) === fmtIso(selected))
            || (selectedEnd && fmtIso(d) === fmtIso(selectedEnd));
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
  variant = 'dropdown',
  maxDays = 90,
  disabled = false,
  end = null,
  onApplyRange,
  isFetching = false,
}: DateRangePresetsProps) {
  const rangeCapable = onApplyRange !== undefined;
  const tz = useTimezone();
  const [open, setOpen] = useState(false);
  const [showCal, setShowCal] = useState(false);
  const [pickerMonth, setPickerMonth] = useState<Date>(() => {
    const d = new Date();
    d.setDate(1);
    return d;
  });
  const [pickedStart, setPickedStart] = useState<Date | null>(null);
  const [pickedEnd, setPickedEnd] = useState<Date | null>(null);
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

  // Two-click picking (range-capable pages only): first click = start;
  // a later click = end; clicking before the start restarts the range.
  const pickDay = (d: Date) => {
    if (!rangeCapable || !pickedStart || d < pickedStart) {
      setPickedStart(d);
      setPickedEnd(null);
      return;
    }
    setPickedEnd(d);
  };

  const resetPicks = () => { setPickedStart(null); setPickedEnd(null); };

  const applyCustom = () => {
    if (!pickedStart) return;
    const effEnd = pickedEnd ?? today;
    // Clamp to the page's backend window cap so the calendar can't
    // generate API errors; larger picks silently fall back to the max.
    const days = Math.max(1, Math.min(maxDays, daysBetween(pickedStart, effEnd)));
    if (rangeCapable && onApplyRange) {
      const endsToday = fmtIso(effEnd) === fmtIso(today);
      onApplyRange(days, endsToday ? null : fmtIso(effEnd));
    } else {
      onChange(days);
    }
    setOpen(false);
    setShowCal(false);
    resetPicks();
  };

  // An explicit end day makes the selection custom even when the
  // window LENGTH matches a preset ("last 7 ending Jun 20" ≠ "Last 7").
  const isCustom = end != null || !options.some((o) => o.days === value);
  const endDate = end ? startOfDay(new Date(`${end}T00:00:00`)) : null;
  const rangeLabel = endDate
    ? `${fmtNice(new Date(endDate.getTime() - value * 86_400_000), tz)} – ${fmtNice(endDate, tz)}`
    : null;

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

  const segChip = (active: boolean) =>
    `px-2.5 py-1 rounded-md text-xs font-medium border transition ${
      active
        ? 'bg-primary text-primary-foreground border-primary'
        : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
    }`;

  return (
    <div ref={ref} className="relative">
      {variant === 'segments' ? (
        // Chip row: presets inline, custom behind the calendar chip.
        <div className="inline-flex items-center gap-1" aria-busy={isFetching}>
          {options.map((opt) => (
            <button
              key={opt.days}
              disabled={disabled}
              onClick={() => { onChange(opt.days); setOpen(false); setShowCal(false); }}
              className={`${segChip(opt.days === value)} disabled:opacity-50`}
            >
              {opt.label}
            </button>
          ))}
          <button
            disabled={disabled}
            onClick={() => { setOpen(true); setShowCal(true); }}
            className={`inline-flex items-center gap-1 ${segChip(isCustom)} disabled:opacity-50`}
            aria-label="Custom range"
          >
            {isFetching
              ? <Loader2 size={12} className="animate-spin" aria-label="Loading" />
              : <Calendar size={12} />}
            {isCustom ? `${value}d` : null}
          </button>
        </div>
      ) : (
        <button
          disabled={disabled}
          onClick={() => { setOpen((o) => !o); setShowCal(false); }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-background border border-border rounded-md text-sm text-foreground/80 hover:bg-muted transition disabled:opacity-50 disabled:cursor-not-allowed"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-busy={isFetching}
        >
          {isFetching ? (
            <Loader2 size={14} className="animate-spin text-primary" aria-label="Loading" />
          ) : (
            <Calendar size={14} className="text-muted-foreground" />
          )}
          {rangeLabel ?? (isCustom ? `Last ${value} days` : labelFor(value, options))}
          <ChevronDown size={12} className="text-muted-foreground" />
        </button>
      )}

      {variant === 'dropdown' && open && !showCal && (
        <ul
          role="listbox"
          className="absolute right-0 top-full mt-1 w-44 max-h-80 overflow-y-auto bg-card border border-border rounded-md shadow-xl z-50 py-1"
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
                  {active && <span className="text-3xs text-primary">●</span>}
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
        <div className="absolute right-0 top-full mt-1 w-80 bg-card border border-border rounded-md shadow-xl z-50 p-3">
          <div className="flex items-center justify-between mb-2">
            <button
              onClick={monthBack}
              className="inline-flex size-7 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
              aria-label="Previous month"
            >
              <ChevronLeft size={14} />
            </button>
            <p className="text-xs text-muted-foreground">
              {!rangeCapable ? 'Pick a start date'
                : !pickedStart ? 'Pick a start date'
                : !pickedEnd ? 'Now pick the end date'
                : 'Range selected'}
            </p>
            <button
              onClick={monthFwd}
              className="inline-flex size-7 items-center justify-center rounded-md hover:bg-muted text-muted-foreground"
              aria-label="Next month"
            >
              <ChevronRight size={14} />
            </button>
          </div>
          <CalendarMonth
            monthStart={pickerMonth}
            selected={pickedStart}
            selectedEnd={pickedEnd}
            hover={hoverDate}
            rangeEnd={today}
            onPick={pickDay}
            onHover={setHoverDate}
          />
          <div className="mt-3 flex items-center justify-between text-2xs text-muted-foreground">
            <span>
              {pickedStart
                ? `${fmtNice(pickedStart, tz)} → ${fmtNice(pickedEnd ?? today, tz)} (${daysBetween(pickedStart, pickedEnd ?? today)}d)`
                : rangeCapable ? 'Pick start, then end' : 'Range ends today'}
            </span>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={() => { setShowCal(false); resetPicks(); }}
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
