/**
 * How old a position is — the panel's version of the dashboard's
 * Freshness tooltip, at the same thresholds.
 *
 * A map draws every truck the same way whether its fix arrived four
 * seconds or seventeen days ago, so a parked-and-forgotten unit reads
 * as live.  The panel is the surface most likely to be glanced at
 * rather than studied, which is exactly where that mistake gets made.
 *
 * Relative ages only, never a clock time: the panel has no account
 * timezone (it never asks for one), and an absolute time rendered in
 * the wrong zone is worse than no absolute time.
 */
export const STALE_MS = 60 * 60 * 1000;        // 1 h — a dot appears
export const VERY_STALE_MS = 24 * STALE_MS;    // 24 h — the age is spelled out

export type Staleness = 'fresh' | 'stale' | 'very_stale' | 'unknown';

export function ageMs(ts: string | null | undefined, now: number): number | null {
  if (!ts) return null;
  const t = new Date(ts).getTime();
  if (!Number.isFinite(t)) return null;
  // A clock skewed a few seconds into the future is not "in the future",
  // it is now — never render a negative age.
  return Math.max(0, now - t);
}

export function stalenessOf(age: number | null): Staleness {
  if (age === null) return 'unknown';
  if (age >= VERY_STALE_MS) return 'very_stale';
  if (age >= STALE_MS) return 'stale';
  return 'fresh';
}

/** "4m", "3h", "17d" — the compact form for a row; whole units only,
 *  because a position's age is never precise enough to deserve more. */
export function formatAge(age: number | null): string {
  if (age === null) return '';
  const s = Math.floor(age / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

/** The sentence a person reads on hover, or in the card. */
export function describeAge(age: number | null): string {
  if (age === null) return 'No position time reported';
  return `Position updated ${formatAge(age)} ago`;
}
