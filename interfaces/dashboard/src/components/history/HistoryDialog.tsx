/**
 * The generic per-record History dialog — one component, any entity.
 *
 * Fetches `/activity/{entityType}/{entityId}` (the registry-driven
 * endpoint: the OWNING feature's permission gates it server-side) and
 * renders through the shared HistoryList, so a vendor's history reads
 * exactly like a task's or a team member's.  Features with scoped
 * endpoints of their own (maintenance) keep their dedicated dialogs.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { HistoryList, type ActivityEvent } from './HistoryList';
import { useTimezone } from '../../hooks/useTimezone';

export function HistoryDialog({
  entityType, entityId, title = 'Change history', open, onOpenChange,
}: {
  entityType: string;
  entityId: string | number | null;
  title?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const tz = useTimezone();
  const qc = useQueryClient();
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['entity-history', entityType, entityId],
    queryFn: () => apiJSON<{ events: ActivityEvent[] }>(
      `/activity/${entityType}/${entityId}`),
    enabled: open && entityId != null && entityId !== '',
  });
  // Append-only by design: a restore never rewrites the history — the
  // refetch simply shows the new "Restored" event under the delete.
  const restore = useMutation({
    mutationFn: (ev: ActivityEvent) =>
      apiJSON<{ restored_id: number }>(`/activity/restore/${String(ev.id).replace('trail:', '')}`,
        { method: 'POST' }),
    onSuccess: () => {
      toast.success('Record restored — the deletion stays in its history');
      void refetch();
      void qc.invalidateQueries();      // the record is back in its lists
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Restore failed'),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {isLoading ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Loading…</p>
          ) : (
            <HistoryList
              events={data?.events ?? []}
              tz={tz}
              emptyText="No recorded changes yet — edits from now on will appear here."
              onRestore={(ev) => restore.mutate(ev)}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The standard trigger — the same quiet text-button the maintenance
 * drawer established, so "View change history" looks identical on
 * every detail surface.
 */
export function HistoryTrigger({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
      <History size={14} /> View change history
    </button>
  );
}
