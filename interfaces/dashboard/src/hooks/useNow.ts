/**
 * ``useNow()`` returns a ``Date`` that re-emits at a fixed cadence,
 * forcing a re-render so any clocks rendered from it stay fresh.
 *
 * Default tick is 60s — fine for "now in each timezone" labels on
 * the Settings page: the dropdown only matters while open, and
 * users don't keep it open across hour boundaries.
 */

import { useEffect, useState } from 'react';

export function useNow(intervalMs: number = 60_000): Date {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
