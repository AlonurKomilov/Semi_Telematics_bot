import { Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/card';

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`animate-pulse bg-muted/60 rounded ${className}`} />;
}

/**
 * Tiny "Loading…" pill that sits above a skeleton block so users see an
 * explicit signal that the page is actively fetching — the
 * `animate-pulse` shimmer alone is too subtle on dark mode and reads as
 * "static empty boxes."
 *
 * `message` defaults to "Loading…" but pages can pass a more specific
 * label ("Loading safety events…") so a slow Samsara fallback at least
 * tells the user what it's waiting on.
 */
export function LoadingPill({ message = 'Loading…' }: { message?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
      <Loader2 className="animate-spin shrink-0 size-3" />
      <span>{message}</span>
    </div>
  );
}

export function KpiSkeleton({
  count = 4,
  message,
  showPill = true,
}: {
  count?: number;
  message?: string;
  showPill?: boolean;
}) {
  return (
    <div>
      {showPill && <LoadingPill message={message} />}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: count }).map((_, i) => (
          <Card key={i}>
            <Skeleton className="h-3 w-20 mb-3" />
            <Skeleton className="h-8 w-16" />
          </Card>
        ))}
      </div>
    </div>
  );
}

export function TableSkeleton({
  rows = 6,
  cols = 5,
  message,
  showPill = true,
}: {
  rows?: number;
  cols?: number;
  message?: string;
  showPill?: boolean;
}) {
  return (
    <div>
      {showPill && <LoadingPill message={message} />}
      <Card padding="none" className="overflow-hidden">
        <div className="border-b border-border bg-card px-4 py-3 flex gap-4">
          {Array.from({ length: cols }).map((_, i) => (
            <Skeleton key={i} className="h-3 flex-1" />
          ))}
        </div>
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="border-t border-border px-4 py-3 flex gap-4">
            {Array.from({ length: cols }).map((_, c) => (
              <Skeleton key={c} className="h-4 flex-1" />
            ))}
          </div>
        ))}
      </Card>
    </div>
  );
}

export function CardSkeleton({
  height = 'h-32',
  message,
  showPill = true,
}: {
  height?: string;
  message?: string;
  showPill?: boolean;
}) {
  return (
    <div>
      {showPill && <LoadingPill message={message} />}
      <div className={`bg-card border border-border rounded-xl p-5 ${height}`}>
        <Skeleton className="h-4 w-1/3 mb-3" />
        <Skeleton className="h-3 w-full mb-2" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </div>
  );
}

export default function LoadingSkeleton({
  variant = 'table',
  message,
}: {
  variant?: 'table' | 'kpi' | 'card';
  message?: string;
}) {
  if (variant === 'kpi') return <KpiSkeleton message={message} />;
  if (variant === 'card') return <CardSkeleton message={message} />;
  return <TableSkeleton message={message} />;
}
