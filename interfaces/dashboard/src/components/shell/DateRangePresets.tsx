import { useEffect, useRef, useState } from 'react';

import { formatClock } from '../../utils/datetime';
import { Calendar, ChevronDown, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';

/**
 * The time-window picker — THE single source of truth for "pick a time
 * window" (same idea as DataGrid for tables).  Never hand-roll a days
 * dropdown, a chip row, or a numeric days input on a page.
 *
 * Layout: the industry-standard two-pane popover — a presets rail on
 * the left (click applies instantly) beside an always-visible
 * two-month calendar (one month under ``md``), with a live range
 * summary in the footer.  No "Custom range…" hop: presets and the
 * calendar are one surface.
 *
 * Two trigger forms, one contract (`value`/`onChange` on a single
 * `days` integer, so `days=N` from-today endpoints work unchanged):
 *
 *   * ``variant="dropdown"`` (default) — labeled trigger button.
 *     Right for page toolbars and forms.
 *   * ``variant="segments"`` — inline chip row (pass a compact
 *     ``options`` list like 7/30/90) + a calendar chip opening the
 *     same panel.  Right for chart/section headers.
 *
 * END-DATE support is per-page and FAIL-CLOSED: most backends only
 * understand "last N days from today", so by default the calendar
 * picks a START date and the range ends today.  A page whose backend
 * honors an explicit end (e.g. /events' `end=` param) passes
 * ``onApplyRange`` + ``end`` — then the calendar is a true two-click
 * start→end picker.  Never enable it for a page whose API ignores the
 * end date: the label would promise a window the data doesn't match.
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
  /** Which trigger edge the panel hangs from.  Default 'end'
   *  (right-aligned — correct for pickers at a toolbar's right edge).
   *  Pass 'start' when the trigger is the LEFTMOST control on the
   *  page, otherwise the panel overflows into the sidebar. */
  align?: 'start' | 'end';
  /** Current custom END day (``YYYY-MM-DD``), null/undefined = today.
   *  Only meaningful together with ``onApplyRange``. */
  end?: string | null;
  /** Enables TRUE start→end picking in the calendar.  Pass ONLY when
   *  the page's backend honors an explicit end date; called with the
   *  window length and the end day (null = ends today).  Presets keep
   *  firing plain ``onChange`` — reset your end state there. */
  onApplyRange?: (
    days: number,
    end: string | null,
    times?: { start: string | null; end: string | null },
  ) => void;
  /** Show start/end TIME selectors (HOUR granularity) in the
   *  custom-range calendar.  The
   *  applied times ride the third ``onApplyRange`` argument
   *  ("HH:MM" or null = whole day).  Presets always clear times.
   *  Pass ONLY when the page's backend accepts date-times — the
   *  Mileage tab's precision ladder is the reference consumer. */
  withTime?: boolean;
  /** The APPLIED times (the page owns that state) — echoed into the
   *  trigger label so the control describes the whole query it made,
   *  not just the date half. */
  appliedTimes?: { start: string | null; end: string | null };
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

// ── One month grid ───────────────────────────────────────────

interface CalendarMonthProps {
  monthStart: Date;
  /** Picked range start. */
  start: Date | null;
  /** Picked END day (two-click mode); null while unpicked. */
  endPicked: Date | null;
  hover: Date | null;
  /** Where the highlight band runs to when no end is picked (today in
   *  start-only mode; hover preview while picking an end). */
  bandEnd: Date;
  /** True on pages that can pick an explicit end — enables the
   *  either-direction hover preview and the hover-endpoint highlight. */
  rangeCapable: boolean;
  onPick: (d: Date) => void;
  onHover: (d: Date | null) => void;
}

function CalendarMonth({ monthStart, start, endPicked, hover, bandEnd, rangeCapable, onPick, onHover }: CalendarMonthProps) {
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

  // The far end of the visual range.  A committed end wins; mid-pick,
  // the hover previews it in EITHER direction (so dragging back from
  // the start highlights just like dragging forward — the fix); a
  // start-only page has no pickable end, so its band always runs
  // start→today (bandEnd).  The band is then ordered [lo, hi] so it
  // renders correctly whichever way it points.
  const far = endPicked ?? (rangeCapable && start && hover ? hover : bandEnd);
  const lo = start && far ? (startOfDay(start) <= startOfDay(far) ? start : far) : null;
  const hi = start && far ? (startOfDay(start) <= startOfDay(far) ? far : start) : null;

  const inBand = (d: Date): boolean =>
    !!lo && !!hi && d > startOfDay(lo) && d < startOfDay(hi);

  return (
    <div className="w-60">
      <p className="text-xs font-semibold text-foreground text-center mb-2 h-7 leading-7">
        {monthStart.toLocaleString(undefined, { month: 'long', year: 'numeric' })}
      </p>
      <div className="grid grid-cols-7 text-3xs text-muted-foreground text-center mb-1">
        {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map((d) => <div key={d}>{d}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-y-0.5">
        {cells.map((d, i) => {
          if (!d) return <div key={i} className="h-8" />;
          const future = d > today;
          const iso = fmtIso(d);
          const isStart = !!start && iso === fmtIso(start);
          const isEnd = !!endPicked && iso === fmtIso(endPicked);
          // The day the cursor is on while the end is still open — the
          // range's other endpoint-to-be.  Marked so both forward and
          // backward drags feel like grabbing an endpoint.
          const isHoverEnd = rangeCapable && !!start && !endPicked
            && !!hover && iso === fmtIso(hover) && iso !== fmtIso(start);
          const isToday = iso === fmtIso(today);
          const banded = inBand(d);
          const cls = future
            ? 'text-muted-foreground/30 cursor-not-allowed'
            : isStart || isEnd
              ? 'bg-primary text-primary-foreground font-semibold rounded-md'
              : isHoverEnd
                ? 'bg-primary/30 text-foreground font-medium rounded-md'
                : banded
                  ? 'bg-primary/10 text-foreground'
                  : `text-foreground/80 hover:bg-muted rounded-md${
                      isToday ? ' ring-1 ring-inset ring-primary/50 text-primary font-medium' : ''
                    }`;
          return (
            <button
              key={i}
              disabled={future}
              onClick={() => onPick(d)}
              onMouseEnter={() => onHover(d)}
              onMouseLeave={() => onHover(null)}
              className={`h-8 w-full text-xs transition ${cls}`}
            >
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── The picker ───────────────────────────────────────────────

export default function DateRangePresets({
  value,
  onChange,
  options = DEFAULT_OPTIONS,
  variant = 'dropdown',
  maxDays = 90,
  disabled = false,
  align = 'end',
  end = null,
  onApplyRange,
  withTime = false,
  appliedTimes,
  isFetching = false,
}: DateRangePresetsProps) {
  const rangeCapable = onApplyRange !== undefined;
  const tz = useTimezone();
  const [open, setOpen] = useState(false);
  const [pickerMonth, setPickerMonth] = useState<Date>(() => {
    const d = new Date();
    d.setDate(1);
    // Panel shows [pickerMonth, pickerMonth+1] — land with the CURRENT
    // month on the right, so recent days are immediately clickable.
    d.setMonth(d.getMonth() - 1);
    return d;
  });
  const [pickedStart, setPickedStart] = useState<Date | null>(null);
  const [pickedEnd, setPickedEnd] = useState<Date | null>(null);
  const [hoverDate, setHoverDate] = useState<Date | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const resetPicks = () => { setPickedStart(null); setPickedEnd(null); };

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        resetPicks();
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const today = startOfDay(new Date());

  // Two-click picking.  Start-only pages just re-anchor on every click.
  // Range pages: the FIRST click (or a click after a completed range)
  // opens a new range; the SECOND click closes it — and the pair is
  // ORDERED, so clicking an earlier day second reads as [earlier,
  // later] instead of restarting from it (the user-comfort fix; you
  // can now sweep backward from the anchor).
  const pickDay = (d: Date) => {
    if (!rangeCapable) {
      setPickedStart(d);
      setPickedEnd(null);
      return;
    }
    if (!pickedStart || pickedEnd) {
      setPickedStart(d);
      setPickedEnd(null);
      return;
    }
    if (d < pickedStart) {
      setPickedEnd(pickedStart);
      setPickedStart(d);
    } else {
      setPickedEnd(d);
    }
  };

  // Time-of-day (withTime only) — HOUR granularity by design: the
  // warehouse answers from 5-minute snapshots and banked hourly
  // readings, so offering minutes would imply precision the data
  // can't back.  Defaults are VISIBLE ("00:00" → "24:00") so the
  // whole-day behavior is stated, not guessed from an empty "--:--".
  const [timeStart, setTimeStart] = useState('00:00');
  const [timeEnd, setTimeEnd] = useState('24:00');
  const timesAreDefault = timeStart === '00:00' && timeEnd === '24:00';

  const applyCustom = () => {
    if (!pickedStart) return;
    const effEnd = pickedEnd ?? today;
    // Clamp to the page's backend window cap so the calendar can't
    // generate API errors; larger picks silently fall back to the max.
    const days = Math.max(1, Math.min(maxDays, daysBetween(pickedStart, effEnd)));
    if (rangeCapable && onApplyRange) {
      const endsToday = fmtIso(effEnd) === fmtIso(today);
      onApplyRange(
        days,
        endsToday ? null : fmtIso(effEnd),
        withTime
          ? {
              start: timeStart === '00:00' ? null : timeStart,
              end: timeEnd === '24:00' ? null : timeEnd,
            }
          : undefined,
      );
    } else {
      onChange(days);
    }
    setOpen(false);
    resetPicks();
  };

  const applyPreset = (days: number) => {
    onChange(days);
    setOpen(false);
    resetPicks();
  };

  // An explicit end day makes the selection custom even when the
  // window LENGTH matches a preset ("last 7 ending Jun 20" ≠ "Last 7").
  const isCustom = end != null || !options.some((o) => o.days === value);
  const endDate = end ? startOfDay(new Date(`${end}T00:00:00`)) : null;
  const appliedClock =
    appliedTimes && (appliedTimes.start || appliedTimes.end)
      ? ` · ${formatClock(appliedTimes.start ?? '00:00')}–${formatClock(appliedTimes.end ?? '23:59')}`
      : '';
  const rangeLabel = endDate
    ? `${fmtNice(new Date(endDate.getTime() - value * 86_400_000), tz)} – ${fmtNice(endDate, tz)}${appliedClock}`
    : null;

  const monthShift = (delta: number) => {
    const d = new Date(pickerMonth);
    d.setMonth(d.getMonth() + delta);
    setPickerMonth(d);
  };
  const secondMonth = (() => {
    const d = new Date(pickerMonth);
    d.setMonth(d.getMonth() + 1);
    return d;
  })();

  const segChip = (active: boolean) =>
    `px-2.5 py-1 rounded-md text-xs font-medium border transition ${
      active
        ? 'bg-primary text-primary-foreground border-primary'
        : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
    }`;

  // Footer tracks the pick live — including the hover preview and its
  // direction — so it agrees with the calendar band as you sweep.
  const previewFar = pickedEnd
    ?? (rangeCapable && pickedStart && hoverDate ? hoverDate : today);
  const [sumLo, sumHi] = pickedStart
    ? (startOfDay(pickedStart) <= startOfDay(previewFar)
        ? [pickedStart, previewFar] : [previewFar, pickedStart])
    : [null, null];
  const draftClock = withTime && !timesAreDefault
    ? ` · ${formatClock(timeStart)}–${timeEnd === '24:00' ? '24:00' : formatClock(timeEnd)}`
    : '';
  const footerSummary = sumLo && sumHi
    ? `${fmtNice(sumLo, tz)} → ${fmtNice(sumHi, tz)} · ${daysBetween(sumLo, sumHi)} days${draftClock}`
    : rangeCapable
      ? 'Click a start date, then an end date'
      : 'Click a start date — the range ends today';

  return (
    <div ref={ref} className="relative inline-block">
      {variant === 'segments' ? (
        // Chip row: presets inline, the calendar chip opens the panel.
        <div className="inline-flex items-center gap-1" aria-busy={isFetching}>
          {options.map((opt) => (
            <button
              key={opt.days}
              disabled={disabled}
              onClick={() => applyPreset(opt.days)}
              className={`${segChip(opt.days === value && end == null)} disabled:opacity-50`}
            >
              {opt.label}
            </button>
          ))}
          <button
            disabled={disabled}
            onClick={() => setOpen((o) => !o)}
            className={`inline-flex items-center gap-1 ${segChip(isCustom)} disabled:opacity-50`}
            aria-label="Custom range"
            aria-expanded={open}
          >
            {isFetching
              ? <Loader2 size={12} className="animate-spin" aria-label="Loading" />
              : <Calendar size={12} />}
            {isCustom ? (rangeLabel ?? `${value}d`) : null}
          </button>
        </div>
      ) : (
        <button
          disabled={disabled}
          onClick={() => { setOpen((o) => !o); }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-background border border-border rounded-md text-sm text-foreground/80 hover:bg-muted transition disabled:opacity-50 disabled:cursor-not-allowed"
          aria-haspopup="dialog"
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

      {open && (
        <div className={`absolute ${align === 'start' ? 'left-0' : 'right-0'} top-full mt-1 z-50 bg-card border border-border rounded-lg shadow-xl flex overflow-hidden`}>
          {/* ── presets rail ── */}
          <ul className="w-36 shrink-0 border-r border-border py-1.5 max-h-96 overflow-y-auto" role="listbox">
            {options.map((opt) => {
              const active = opt.days === value && end == null;
              return (
                <li key={opt.days}>
                  <button
                    onClick={() => applyPreset(opt.days)}
                    className={`w-full text-left px-3 py-1.5 text-sm transition ${
                      active
                        ? 'bg-primary/10 text-primary font-medium'
                        : 'text-foreground/80 hover:bg-muted'
                    }`}
                  >
                    {opt.label}
                  </button>
                </li>
              );
            })}
          </ul>

          {/* ── calendar pane ── */}
          <div className="p-3">
            <div className="relative">
              <button
                onClick={() => monthShift(-1)}
                className="absolute left-0 top-0 inline-flex size-7 items-center justify-center rounded-md hover:bg-muted text-muted-foreground z-10"
                aria-label="Previous month"
              >
                <ChevronLeft size={14} />
              </button>
              <button
                onClick={() => monthShift(1)}
                className="absolute right-0 top-0 inline-flex size-7 items-center justify-center rounded-md hover:bg-muted text-muted-foreground z-10"
                aria-label="Next month"
              >
                <ChevronRight size={14} />
              </button>
              <div className="flex gap-5">
                <div className="hidden md:block">
                  <CalendarMonth
                    monthStart={pickerMonth}
                    start={pickedStart}
                    endPicked={pickedEnd}
                    hover={hoverDate}
                    bandEnd={today}
                    rangeCapable={rangeCapable}
                    onPick={pickDay}
                    onHover={setHoverDate}
                  />
                </div>
                <CalendarMonth
                  monthStart={secondMonth}
                  start={pickedStart}
                  endPicked={pickedEnd}
                  hover={hoverDate}
                  bandEnd={today}
                  rangeCapable={rangeCapable}
                  onPick={pickDay}
                  onHover={setHoverDate}
                />
              </div>
            </div>

            {/* ── time-of-day row (withTime pages only) ── */}
            {withTime && (
              <div className="mt-3 pt-2.5 border-t border-border flex items-center gap-3">
                <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                  Time
                </span>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  from
                  <select
                    value={timeStart}
                    onChange={(e) => setTimeStart(e.target.value)}
                    className="h-7 px-2 rounded-md border border-border bg-background text-foreground text-xs"
                    aria-label="Start time (hour)"
                  >
                    {Array.from({ length: 24 }, (_, h) => {
                      const v = `${String(h).padStart(2, '0')}:00`;
                      return (
                        <option key={v} value={v}>{formatClock(v)}</option>
                      );
                    })}
                  </select>
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  to
                  <select
                    value={timeEnd}
                    onChange={(e) => setTimeEnd(e.target.value)}
                    className="h-7 px-2 rounded-md border border-border bg-background text-foreground text-xs"
                    aria-label="End time (hour)"
                  >
                    {Array.from({ length: 24 }, (_, i) => {
                      const h = i + 1;
                      const v = h === 24 ? '24:00' : `${String(h).padStart(2, '0')}:00`;
                      return (
                        <option key={v} value={v}>
                          {h === 24 ? '24:00' : formatClock(v)}
                        </option>
                      );
                    })}
                  </select>
                </label>
                {!timesAreDefault && (
                  <button
                    type="button"
                    onClick={() => { setTimeStart('00:00'); setTimeEnd('24:00'); }}
                    className="text-2xs text-muted-foreground hover:text-foreground"
                  >
                    reset
                  </button>
                )}
              </div>
            )}

            {/* ── footer: live summary + actions ── */}
            <div className="mt-3 pt-2.5 border-t border-border flex items-center justify-between gap-3">
              <span className="text-2xs text-muted-foreground">{footerSummary}</span>
              <span className="flex items-center gap-2">
                <button
                  onClick={() => { setOpen(false); resetPicks(); }}
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
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
