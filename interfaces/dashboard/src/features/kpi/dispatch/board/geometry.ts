/**
 * The board's BAR GEOMETRY — which day renders which piece of which
 * load's bar, and how far a label may run.
 *
 * These rules used to be closures inside `BoardRow`, unreachable by
 * any test: every bar defect (a clamped date printed as a delivery, a
 * strip drawn on the wrong lane, a label colliding with the next
 * chip) had to be caught by eye on a screenshot.  They are pure here,
 * so the rules can be argued with in `geometry.test.ts` instead.
 *
 * Lives under the KPI feature by the feature-centric rule — the board
 * is not a shared component, and nothing outside `features/kpi` may
 * import it.
 *
 * The one distinction the whole file turns on:
 *
 * - ``deliveryEnd`` — the REAL delivery day.  Everything a human READS
 *   (labels, aria, tooltips, "day N of TOTAL") uses this.
 * - ``spanEnd`` — that day CLAMPED to the run window.  Pixels only.
 *   Printing it would say "delivers <period end>" — a clamp wearing a
 *   date's clothes.
 */
import type { RunLoad } from '../../api';
import { nextDay } from '../explain';

/** One rolling (non-pickup) day of a load's bar. */
export interface TransitInfo {
  load: RunLoad;
  /** Position in the REAL trip — "day {dayNo} of {total}". */
  dayNo: number;
  /** Length of the real trip in days (never the clamped slice). */
  total: number;
  /** Last VISIBLE day of this load's span (clamped) — geometry only. */
  last: string;
}

export interface BoardGeometry {
  /** Pickup day → the loads that START there, in render order. */
  byDay: Map<string, RunLoad[]>;
  /** Day → its rolling-load piece (absent on pickup days). */
  transitInfo: Map<string, TransitInfo>;
  inWindow(day: string): boolean;
  /** The load whose STRIP this day renders — null when the day draws
   *  something of its own (a pickup, a mark, a suggestion) or falls
   *  outside the window.  The oracle every label's runway trusts:
   *  arithmetic said "5 days", the board rendered 2, and text ran
   *  under the next chip. */
  stripLoadAt(day: string): RunLoad | null;
  /** Consecutive strip days after `from` — the chip label's runway. */
  stripRun(load: RunLoad, from: string): number;
  /** The REAL delivery day — for words. */
  deliveryEnd(load: RunLoad): string;
  /** The last visible day — for pixels, never for words. */
  spanEnd(load: RunLoad): string;
  /** Which line the load sits on: its index among its pickup day's
   *  chips.  A strip must draw on ITS load's lane, or a second load's
   *  bar appears to continue from the first one's label. */
  laneOf(load: RunLoad): number;
}

/** Inclusive ISO day range — pure calendar strings, no timezone. */
export function dayRange(start: string, end: string): string[] {
  const out: string[] = [];
  const d = new Date(`${start.slice(0, 10)}T00:00:00Z`);
  const stop = new Date(`${end.slice(0, 10)}T00:00:00Z`);
  while (d <= stop && out.length < 120) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

export const prevDay = (iso: string) =>
  new Date(Date.parse(`${iso}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);

/** The REAL delivery day of one load (pickup when it has none, or when
 *  the delivery date is not after the pickup). */
export function deliveryEnd(load: RunLoad): string {
  const a = (load.pickup_date || '').slice(0, 10);
  const b = (load.delivery_date || '').slice(0, 10);
  return b && b > a ? b : a;
}

export function boardGeometry({ loads, windowStart, windowEnd, marks, suggested }: {
  loads: readonly RunLoad[] | undefined;
  windowStart: string;
  windowEnd: string;
  /** date → reason, from the row's inactive_dates. */
  marks: ReadonlyMap<string, string>;
  /** date → suggestion; only membership matters here. */
  suggested: ReadonlyMap<string, unknown>;
}): BoardGeometry {
  const lo = windowStart.slice(0, 10);
  const hi = windowEnd.slice(0, 10);
  const inWindow = (day: string) => day >= lo && day <= hi;
  const spanEnd = (load: RunLoad): string => {
    const end = deliveryEnd(load);
    return end > hi ? hi : end;
  };

  const byDay = new Map<string, RunLoad[]>();
  for (const l of loads ?? []) {
    const d = l.pickup_date;
    byDay.set(d, [...(byDay.get(d) ?? []), l]);
  }

  // Days a load COVERS without starting there — the truck is rolling
  // (picked up earlier, delivering later), not idle.  Each rolling day
  // keeps its load and position, so a strip reads as a PIECE of that
  // load's bar ("→ $5,800 · Wilmi… — day 2 of 3"), never an anonymous
  // arrow.  First load to claim a day keeps it.
  const transitInfo = new Map<string, TransitInfo>();
  for (const l of loads ?? []) {
    const a = (l.pickup_date || '').slice(0, 10);
    if (!a) continue;
    const end = spanEnd(l);
    if (end <= a) continue;
    const real = deliveryEnd(l);
    let total = 1;
    for (let d = a; d < real; d = nextDay(d)) total += 1;
    let dayNo = 2;
    for (let d = nextDay(a); d <= end; d = nextDay(d), dayNo += 1) {
      if (!transitInfo.has(d)) transitInfo.set(d, { load: l, dayNo, total, last: end });
    }
  }

  const stripLoadAt = (day: string): RunLoad | null => {
    const info = transitInfo.get(day);
    if (!info) return null;
    if ((byDay.get(day) ?? []).length > 0) return null;
    if (marks.get(day) != null) return null;
    if (suggested.has(day)) return null;
    if (!inWindow(day)) return null;
    return info.load;
  };

  const stripRun = (load: RunLoad, from: string): number => {
    let c = 0;
    for (let day = nextDay(from); stripLoadAt(day) === load; day = nextDay(day)) c += 1;
    return c;
  };

  const laneOf = (load: RunLoad): number => {
    const pickupDay = (load.pickup_date || '').slice(0, 10);
    return (byDay.get(pickupDay) ?? []).indexOf(load);
  };

  return {
    byDay, transitInfo, inWindow,
    stripLoadAt, stripRun, deliveryEnd, spanEnd, laneOf,
  };
}
