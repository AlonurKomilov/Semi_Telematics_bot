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
  /** Which of the account's carriers this driver works for. An account
   *  is several legal companies; `''` on a single-company account. */
  company: string;
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

/** The four countdowns, by the ids the server and the providers share. */
export type HosClockId = 'drive' | 'shift' | 'cycle' | 'break';

export const CLOCK_ORDER: HosClockId[] = ['drive', 'shift', 'cycle', 'break'];

/** What one connected ELD is CAPABLE of reporting.
 *
 *  `clocks_reported` is `null` — not `[]` — when the provider is no
 *  longer registered and we genuinely cannot say. "Unknown" and
 *  "reports none" are different sentences and only one may be printed.
 */
export interface ProviderFact {
  name: string;
  clocks_reported: HosClockId[] | null;
}

export interface HoursResponse {
  /** The load-bearing field.  NOT derived from the row count: zero
   *  drivers because no ELD is connected and zero because the caller's
   *  scope is empty are different answers, and only one of them means
   *  the data is missing. */
  connected: boolean;
  count: number;
  /** Which of the four countdowns the account's connected ELD(s)
   *  publish. Not every ELD publishes any: some report duty status and
   *  nothing else, which is a real product working correctly. A column
   *  outside this set must not be RENDERED — four empty columns on a
   *  compliance page read as four zeroes. */
  clocks_reported: HosClockId[];
  /** The carriers present in what this caller can see. Empty on a
   *  single-company account, which is why the Company column is built
   *  from this rather than rendered unconditionally — a column of
   *  blanks is worse than no column. */
  companies: string[];
  /** Which ELD is behind the rows, so a surface can say whose
   *  limitation it is by name rather than blaming "the provider". */
  providers: Record<string, ProviderFact>;
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

/**
 * What this account's ELDs can and cannot tell us.
 *
 * `devices` names only the providers we can actually speak for — one
 * that is no longer registered reports `null`, and a sentence that
 * names it would be claiming knowledge we do not have.
 *
 * Written against the declared set rather than against the rows so it
 * survives the obvious third case: an ELD that reports two of the four.
 * A surface built around "is this ORIENT" would be wrong about that one
 * on its first day.
 */
export function clockCoverage(data: HoursResponse | undefined) {
  const reported = new Set<HosClockId>(data?.clocks_reported ?? []);
  const missing = CLOCK_ORDER.filter((c) => !reported.has(c));
  const devices = Object.values(data?.providers ?? {})
    .filter((p) => p.clocks_reported !== null)
    .map((p) => p.name);
  return {
    reported,
    missing,
    devices,
    /** No countdown at all — the case the page has to speak about. */
    none: reported.size === 0,
    /** The words for whoever is limiting us. Falls back to a neutral
     *  phrase rather than naming a device we cannot vouch for. */
    deviceLabel: devices.length ? devices.join(' and ') : 'The connected ELD',
  };
}

/** Under an hour of drive time left.  One hour is not a regulatory
 *  threshold — we do not know the ruleset — it is the span in which a
 *  dispatcher can still act, which is the only claim being made.
 *
 *  Only meaningful when the ELD reports the drive clock at all: on a
 *  device that does not, this returns 0 for every fleet, and rendering
 *  that 0 would state "nobody is close to their limit" on no evidence.
 *  Gate the call on `clockCoverage(data).reported.has('drive')`. */
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
