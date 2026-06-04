/**
 * Shared label/value row used by Vehicle Info, Health, and Location
 * sections.  Extracted verbatim from the original VehicleDetail.tsx
 * so visual output is byte-identical.
 */
import type { ReactNode } from 'react';

interface RowProps {
  label: string;
  value?: string | number | null;
  children?: ReactNode;
}

export function Row({ label, value, children }: RowProps) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children || <span>{value ?? '—'}</span>}
    </div>
  );
}
