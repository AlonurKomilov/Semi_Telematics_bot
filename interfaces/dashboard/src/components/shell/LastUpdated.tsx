import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { cn } from '@/lib/utils';

interface LastUpdatedProps {
  fetchedAt?: number | null;
  isFetching?: boolean;
  onRefresh?: () => void;
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
}: LastUpdatedProps) {
  const [, setTick] = useState(0);
  const tz = useTimezone();

  useEffect(() => {
    if (!fetchedAt) return;
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [fetchedAt]);

  const label = fetchedAt ? formatRelative(fetchedAt, tz) : '—';

  return (
    <button
      onClick={onRefresh}
      disabled={!onRefresh || isFetching}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition disabled:opacity-60 disabled:cursor-default py-1 -my-1 min-h-tap"
      title={fetchedAt ? `Last updated ${label}` : 'Not loaded yet'}
    >
      <RefreshCw
        className={cn(isFetching ? 'animate-spin' : '', 'size-3')}
      />
      <span>Updated {label}</span>
    </button>
  );
}
