/**
 * "Review data conflicts" — owner surface for resolving cross-source
 * disagreements.
 *
 * When Samsara and Datatruck report DIFFERENT non-empty values for the
 * same vehicle field, precedence picks a winner but the disagreement is
 * recorded.  Here the operator sees both values and picks the correct
 * one — the choice is written + pinned (no sync can undo it).
 *
 * Lives on the Integrations page and is gated by the same permission as
 * the page (``can_manage_integrations``); the parent renders it only when
 * 2+ vehicle-writing integrations are connected.  Self-hides when there
 * are no open conflicts.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { toneText } from '../../lib/status';
import { getConflicts, resolveConflict } from './api';
import type { DataConflict } from './api';
import { cn } from '@/lib/utils';

const FIELD_LABEL: Record<string, string> = {
  vin: 'VIN',
  plate_number: 'Plate',
  make: 'Make',
  model: 'Model',
  year: 'Year',
};

const SOURCE_LABEL: Record<string, string> = {
  datatruck: 'Datatruck',
  samsara: 'Samsara',
};

export default function ConflictsPanel() {
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: ['data-conflicts'],
    queryFn: getConflicts,
  });

  const mutation = useMutation({
    mutationFn: ({ id, value }: { id: number; value: string }) =>
      resolveConflict(id, value),
    onSuccess: (res) =>
      qc.setQueryData(['data-conflicts'], { conflicts: res.conflicts }),
  });

  const conflicts: DataConflict[] = data?.conflicts ?? [];
  if (conflicts.length === 0) return null;

  return (
    <div className="mt-4 rounded-lg border border-border bg-card p-4">
      <div className="mb-1 flex items-center gap-2">
        <AlertTriangle className={cn(toneText('warn'), 'size-4')} />
        <h3 className="text-base font-semibold text-foreground">
          Review data conflicts ({conflicts.length})
        </h3>
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        Samsara and Datatruck reported different values for these fields. The
        value currently shown is in bold; pick the correct one. Your choice is
        pinned and won&apos;t be overwritten by a later sync.
      </p>
      <ul className="space-y-2">
        {conflicts.map((c) => (
          <li
            key={c.id}
            className="rounded-md border border-border p-2.5"
          >
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {FIELD_LABEL[c.field] ?? c.field}
            </div>
            <div className="flex flex-wrap gap-2">
              <ConflictChoice
                source={SOURCE_LABEL[c.current_source] ?? c.current_source}
                value={c.current_value}
                showing
                disabled={mutation.isPending}
                onClick={() =>
                  mutation.mutate({ id: c.id, value: c.current_value })
                }
              />
              <ConflictChoice
                source={SOURCE_LABEL[c.incoming_source] ?? c.incoming_source}
                value={c.incoming_value}
                disabled={mutation.isPending}
                onClick={() =>
                  mutation.mutate({ id: c.id, value: c.incoming_value })
                }
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConflictChoice({
  source,
  value,
  showing = false,
  disabled,
  onClick,
}: {
  source: string;
  value: string;
  showing?: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex-1 rounded-md border border-border bg-muted px-2.5 py-1.5 text-left hover:border-ring focus:outline-none focus:border-ring disabled:opacity-50 min-h-tap"
      title={`Use ${source}'s value`}
    >
      <div className="text-2xs text-muted-foreground">
        {source}
        {showing ? ' · showing now' : ''}
      </div>
      <div
        className={`text-sm text-foreground ${showing ? 'font-semibold' : ''}`}
      >
        {value || '—'}
      </div>
    </button>
  );
}
