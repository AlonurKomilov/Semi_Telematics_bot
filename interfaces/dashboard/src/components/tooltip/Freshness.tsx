/**
 * Per-metric freshness — wraps a displayed value with an "updated Xs ago"
 * hover tooltip, escalating to a visible staleness cue when the reading is
 * old enough to mislead:
 *
 *   fresh      (< 1 h):  tooltip only — zero visual noise
 *   stale      (≥ 1 h):  small warn dot next to the value
 *   very stale (≥ 24 h): dot + inline short age ("· 5d ago" / "· Jul 8")
 *
 * Telematics values can silently freeze (dead sensor, gateway swap, paused
 * integration) while still looking authoritative — the reading's OWN
 * timestamp is the only honest trust signal, and each metric carries its
 * own clock (fuel can be a week older than GPS on the same truck).
 *
 * THE HOUR IS A DEFAULT, NOT THE POLICY.  When the value's dataset
 * declares its own tolerance (``sla``, minutes — the ingest registry's
 * ``freshness_sla_min``, carried on the row as ``sla_min``), the dot
 * fires at THAT age instead.  Vehicle state declares 15: a 40-minute-old
 * engine state is 25 minutes past what its own feed calls stale, and
 * under the flat hour it drew nothing — the same number the backend
 * reader had already fallen back to the live provider over, and the
 * watchdog had already paged on.  One number, three places, now four.
 * The inline "· 5d ago" stays at 24h regardless: that threshold is
 * about when a DATE reads better than an age, not about trust.
 */
import type { ReactNode } from 'react';
import { Tip } from './Tip';
import { formatAgoShort } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import { useNow } from '../../hooks/useNow';
import { toneText } from '../../lib/status';

const STALE_MS = 60 * 60 * 1000;
const VERY_STALE_MS = 24 * STALE_MS;

interface FreshnessProps {
  /** ISO timestamp of the reading itself.  Null/undefined/invalid →
   *  children render untouched (no tooltip, no cue). */
  ts?: string | null;
  /** Show the visible staleness cue (dot / inline age).  Set false on
   *  secondary rows that share one timestamp with a primary row (e.g.
   *  Speed + Coordinates share the GPS fix shown on Address) so one
   *  stale fix doesn't paint three dots — tooltip stays on all. */
  cue?: boolean;
  /** The reading's OWN tolerance, in minutes — the dataset's declared
   *  ``freshness_sla_min``, carried on the row as ``sla_min``.  When
   *  given, the dot fires at this age instead of the flat hour.  Absent
   *  (or not a positive number) → the hour, unchanged. */
  sla?: number | null;
  children: ReactNode;
}

export function Freshness({ ts, cue = true, sla, children }: FreshnessProps) {
  const tz = useTimezone();
  // 60s tick keeps the age honest while the page sits open.
  const now = useNow();

  const t = ts ? new Date(ts).getTime() : NaN;
  if (Number.isNaN(t)) return <>{children}</>;

  const age = now.getTime() - t;
  // "35s ago" / "2m ago" / "4d ago", then "Jul 14" (+year if not this
  // year) — past a month, WHEN beats how-long-ago.
  const rel = formatAgoShort(ts, { timeZone: tz });
  // A declared tolerance beats the default hour; anything else (absent,
  // zero, negative, NaN) is "no declaration" and keeps the hour.
  const declared = typeof sla === 'number' && sla > 0 ? sla : null;
  const staleMs = declared ? declared * 60_000 : STALE_MS;
  // Say WHY the dot is there when the reading has a tolerance of its
  // own — "Up 40m ago" alone leaves the reader to guess the rule.
  const label = declared && age >= staleMs
    ? `Up ${rel} · past ${declared} min`
    : `Up ${rel}`;

  return (
    <Tip label={label}>
      <span className="inline-flex items-center gap-1.5 cursor-default">
        {children}
        {cue && age >= VERY_STALE_MS && (
          <span className={`text-2xs ${toneText('warn')}`}>· {rel}</span>
        )}
        {cue && age >= staleMs && (
          <span
            aria-label={`updated ${rel}`}
            className={`inline-block w-1.5 h-1.5 rounded-full bg-current ${toneText('warn')}`}
          />
        )}
      </span>
    </Tip>
  );
}
