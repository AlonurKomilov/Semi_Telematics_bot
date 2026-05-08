// Renders a relative timestamp (e.g. "5m ago") that re-computes itself
// every minute so the screen stays fresh without re-fetching data.

import { useEffect, useState } from 'react';

interface Props {
  iso: string | null | undefined;
  /** Optional IANA timezone for absolute fallback, e.g. "America/New_York". */
  timezone?: string;
  /** When the time is older than this (seconds), show absolute date. */
  absoluteAfterSec?: number;
}

function fmt(iso: string, timezone?: string, absoluteAfterSec = 86400): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const diffSec = Math.max(0, (Date.now() - t) / 1000);
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < absoluteAfterSec) return `${Math.round(diffSec / 3600)}h ago`;
  try {
    return new Date(t).toLocaleDateString(undefined, {
      timeZone: timezone,
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
}

export function RelativeTime({ iso, timezone, absoluteAfterSec = 86400 }: Props) {
  const [, force] = useState(0);
  // Once the timestamp is older than the absolute-fallback window, the
  // formatter returns a static date that won't change — there's nothing
  // to refresh, so skip the interval entirely.  With ~100 history items
  // on screen this avoids ~100 needless renders/min.
  const isFrozen = iso
    ? Math.max(0, (Date.now() - Date.parse(iso)) / 1000) >= absoluteAfterSec
    : true;
  useEffect(() => {
    if (isFrozen) return;
    const t = window.setInterval(() => force(n => n + 1), 60_000);
    return () => window.clearInterval(t);
  }, [isFrozen]);
  if (!iso) return null;
  return <>{fmt(iso, timezone, absoluteAfterSec)}</>;
}
