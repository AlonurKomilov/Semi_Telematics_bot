/**
 * Hours of Service — the read hook.
 *
 * One endpoint answers both shapes this feature has: the fleet-wide
 * list, and one driver for the Drivers card's HOS tab.  There is no
 * second store and no second query — a person's hours must read the
 * same on both surfaces or one of them is lying.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';

/** One clock, in the two units the page and a human each want. */
export interface Clock {
  seconds: number;
  hours: number;
}

export interface DriverHours {
  driver: string;
  user_id: number | null;
  /** False when the provider reports this driver but nobody has
   *  matched them to our roster yet. */
  linked: boolean;
  vehicle: string;
  duty_status: string;
  since: string;
  /** Every clock counts DOWN.  `null` means the ELD did not report it,
   *  which is a different fact from zero — zero means the driver has
   *  run out.  They must never render the same. */
  drive_remaining: Clock | null;
  shift_remaining: Clock | null;
  cycle_remaining: Clock | null;
  break_in: Clock | null;
  source: string;
  /** When the PROVIDER observed this, never our write time. */
  as_of: string;
  age_minutes: number | null;
  stale: boolean;
}

export interface HoursResponse {
  /** The load-bearing field.  NOT derived from the row count: zero
   *  drivers because no ELD is connected and zero because the caller's
   *  scope is empty are different answers, and only one of them means
   *  the data is missing. */
  connected: boolean;
  count: number;
  /** How many drivers the caller's own vehicle access removed.  A
   *  surface showing three of ten and saying nothing is under-reporting
   *  in silence, which on a compliance page is the same failure class
   *  as answering zero. */
  hidden_by_scope: number;
  drivers: DriverHours[];
  stale_count: number;
  stale_after_minutes: number;
  record_of: string;
}

/**
 * `userId` reads one driver; omit it for the account.
 *
 * Refetched on an interval because a duty clock changes by the second
 * and a page left open would otherwise age silently — the same reason
 * every row carries `as_of`.  A minute is well inside the feed's own
 * five-minute cadence, so this costs a cache read, not a provider call.
 */
export function useHours(userId?: number | null, enabled = true) {
  return useQuery<HoursResponse>({
    queryKey: ['eld-hours', userId ?? 'all'],
    queryFn: () =>
      apiJSON<HoursResponse>(
        userId ? `/eld/hours/${userId}` : '/eld/hours',
      ),
    enabled,
    refetchInterval: 60_000,
  });
}

/** Under an hour of drive time left.  One hour is not a regulatory
 *  threshold — we do not know the ruleset — it is the span in which a
 *  dispatcher can still act, which is the only claim being made. */
export const LOW_DRIVE_SECONDS = 3600;

export function countLowOnDrive(drivers: DriverHours[]): number {
  return drivers.filter(
    (d) => d.drive_remaining !== null
      && d.drive_remaining.seconds < LOW_DRIVE_SECONDS,
  ).length;
}

/** `11h 30m` — or an em dash when the ELD did not report this clock. */
export function clockText(clock: Clock | null | undefined): string {
  if (!clock) return '—';
  const h = Math.floor(clock.seconds / 3600);
  const m = Math.floor((clock.seconds % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

/** Duty status → the words an operator uses, not the wire value. */
export const DUTY_LABELS: Record<string, string> = {
  off_duty: 'Off duty',
  sleeper: 'Sleeper berth',
  driving: 'Driving',
  on_duty: 'On duty',
  personal_conveyance: 'Personal conveyance',
  yard_move: 'Yard move',
  unknown: 'Unknown',
};
