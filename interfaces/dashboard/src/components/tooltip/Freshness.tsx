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
  children: ReactNode;
}

export function Freshness({ ts, cue = true, children }: FreshnessProps) {
  const tz = useTimezone();
  // 60s tick keeps the age honest while the page sits open.
  const now = useNow();

  const t = ts ? new Date(ts).getTime() : NaN;
  if (Number.isNaN(t)) return <>{children}</>;

  const age = now.getTime() - t;
  // "35s ago" / "2m ago" / "4d ago", then "Jul 14" (+year if not this
  // year) — past a month, WHEN beats how-long-ago.
  const rel = formatAgoShort(ts, { timeZone: tz });

  return (
    <Tip label={`Up ${rel}`}>
      <span className="inline-flex items-center gap-1.5 cursor-default">
        {children}
        {cue && age >= VERY_STALE_MS && (
          <span className={`text-2xs ${toneText('warn')}`}>· {rel}</span>
        )}
        {cue && age >= STALE_MS && (
          <span
            aria-label={`updated ${rel}`}
            className={`inline-block w-1.5 h-1.5 rounded-full bg-current ${toneText('warn')}`}
          />
        )}
      </span>
    </Tip>
  );
}
