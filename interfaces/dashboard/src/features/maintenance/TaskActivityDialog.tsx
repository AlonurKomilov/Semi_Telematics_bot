/**
 * Per-task History — the record's own activity trail: who created,
 * edited, completed, snoozed, or deleted it, field-level old→new.
 * Works for deleted tasks too (that's the point — the trail outlives
 * the row).  Renders through the shared ActivityTrailList so every feature's
 * history reads identically.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { ActivityTrailList, type ActivityEvent } from '../../components/activity-trail/ActivityTrailList';
import { useTimezone } from '../../hooks/useTimezone';

export function TaskActivityDialog({
  taskId, open, onOpenChange,
}: {
  taskId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const tz = useTimezone();
  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-task-history', taskId],
    queryFn: () => apiJSON<{ events: ActivityEvent[] }>(
      `/maintenance/tasks/${taskId}/history`),
    enabled: open && taskId != null,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Task activity history</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {isLoading ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Loading…</p>
          ) : (
            <ActivityTrailList
              events={data?.events ?? []}
              tz={tz}
              emptyText="No recorded changes yet — edits from now on will appear here."
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
