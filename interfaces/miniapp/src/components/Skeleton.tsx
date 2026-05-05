// Shimmer skeleton placeholders used in place of spinners on first paint.

import type { CSSProperties } from 'react';

interface Props {
  width?: number | string;
  height?: number | string;
  radius?: number;
  style?: CSSProperties;
  className?: string;
}

export function Skeleton({ width = '100%', height = 16, radius = 6, style, className }: Props) {
  return (
    <div
      className={`skeleton ${className ?? ''}`}
      style={{ width, height, borderRadius: radius, ...style }}
    />
  );
}

export function VehicleCardSkeleton() {
  return (
    <div className="vehicle-card-skeleton">
      <Skeleton height={32} width="40%" />
      <Skeleton height={14} width="60%" style={{ marginTop: 12 }} />
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <Skeleton height={56} width="33%" />
        <Skeleton height={56} width="33%" />
        <Skeleton height={56} width="33%" />
      </div>
      <Skeleton height={14} width="80%" style={{ marginTop: 16 }} />
    </div>
  );
}

export function ListRowsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div style={{ padding: 16 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0' }}>
          <Skeleton width={36} height={36} radius={18} />
          <div style={{ flex: 1 }}>
            <Skeleton height={14} width="70%" />
            <Skeleton height={12} width="50%" style={{ marginTop: 8 }} />
          </div>
          <Skeleton width={56} height={28} radius={14} />
        </div>
      ))}
    </div>
  );
}

export function ScoreSkeleton() {
  return (
    <div className="centered" style={{ paddingTop: 24, paddingBottom: 24 }}>
      <Skeleton width={200} height={200} radius={100} />
      <Skeleton height={18} width={160} style={{ marginTop: 16 }} />
      <Skeleton height={12} width={220} style={{ marginTop: 8 }} />
    </div>
  );
}
