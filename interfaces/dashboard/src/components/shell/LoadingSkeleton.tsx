interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`animate-pulse bg-muted/60 rounded ${className}`} />;
}

export function KpiSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-card border border-border rounded-xl p-5"
        >
          <Skeleton className="h-3 w-20 mb-3" />
          <Skeleton className="h-8 w-16" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden">
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
    </div>
  );
}

export function CardSkeleton({ height = 'h-32' }: { height?: string }) {
  return (
    <div className={`bg-card border border-border rounded-xl p-5 ${height}`}>
      <Skeleton className="h-4 w-1/3 mb-3" />
      <Skeleton className="h-3 w-full mb-2" />
      <Skeleton className="h-3 w-2/3" />
    </div>
  );
}

export default function LoadingSkeleton({
  variant = 'table',
}: {
  variant?: 'table' | 'kpi' | 'card';
}) {
  if (variant === 'kpi') return <KpiSkeleton />;
  if (variant === 'card') return <CardSkeleton />;
  return <TableSkeleton />;
}
