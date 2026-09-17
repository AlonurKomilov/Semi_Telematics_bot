/**
 * Shared label/value row used by Vehicle Info, Health, and Location
 * sections.  Extracted verbatim from the original VehicleDetail.tsx
 * so visual output is byte-identical.
 */
import type { ReactNode } from 'react';
import { Freshness } from '../../../../components/tooltip';

interface RowProps {
  label: string;
  value?: string | number | null;
  children?: ReactNode;
  /** ISO timestamp of THIS reading — adds the hover "updated Xs ago"
   *  tooltip + staleness cue (see components/Freshness). */
  ts?: string | null;
  /** Forwarded to Freshness — false = tooltip only, no visible cue. */
  cue?: boolean;
  /** Forwarded to Freshness — the reading's own tolerance in minutes
   *  (the row's ``sla_min``); absent = the flat hour. */
  sla?: number | null;
}

export function Row({ label, value, children, ts, cue, sla }: RowProps) {
  const content = children || <span>{value ?? '—'}</span>;
  return (
    <div className="flex justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      {ts ? <Freshness ts={ts} cue={cue} sla={sla}>{content}</Freshness> : content}
    </div>
  );
}
