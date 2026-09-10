import { useEffect, useState } from 'react';
import { RefreshCw } from '../../lib/icons';

import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { cn } from '@/lib/utils';
import { toneText } from '../../lib/status';

interface LastUpdatedProps {
  fetchedAt?: number | null;
  isFetching?: boolean;
  onRefresh?: () => void;
  /** The last refresh FAILED.  Without this the chip kept counting the
   *  age up — "Updated 4m ago", then 8m, then 15m — while every click
   *  on it was silently failing: a positive false claim on the one
   *  control whose whole job is stating data age.  Pass react-query's
   *  `error`; the chip then names the failure and dates the last GOOD
   *  load instead. */
  error?: unknown;
}

function formatRelative(ts: number, tz: string): string {
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  // Older than a day — show an absolute timestamp rendered in the
  // user's effective timezone (was browser locale before).
  return formatDate(ts, { timeZone: tz });
}

export default function LastUpdated({
  fetchedAt,
  isFetching,
  onRefresh,
  error,
}: LastUpdatedProps) {
  const [, setTick] = useState(0);
  const tz = useTimezone();

  useEffect(() => {
    if (!fetchedAt) return;
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [fetchedAt]);

  const label = fetchedAt ? formatRelative(fetchedAt, tz) : '—';
  const failed = error != null && !isFetching;

  return (
    <button
      onClick={onRefresh}
      disabled={!onRefresh || isFetching}
      className={cn(
        'inline-flex items-center gap-1.5 text-xs transition disabled:opacity-60 disabled:cursor-default py-1 -my-1 min-h-tap',
        failed ? toneText('danger') : 'text-muted-foreground hover:text-foreground',
      )}
      aria-label={failed
        ? (fetchedAt ? `Refresh failed. Showing data from ${label}. Retry` : 'Refresh failed. Retry')
        : (fetchedAt ? `Last updated ${label}. Refresh` : 'Not loaded yet')}
    >
      <RefreshCw
        className={cn(isFetching ? 'animate-spin' : '', 'size-3')}
      />
      <span>
        {failed
          ? (fetchedAt ? `Refresh failed · last good ${label}` : 'Refresh failed')
          : `Updated ${label}`}
      </span>
    </button>
  );
}
