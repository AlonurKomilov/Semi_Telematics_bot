/**
 * Merge a duplicate service task into the canonical one.
 *
 * The problem: "Brake Job", "brake job" and "Brakes" typed at three
 * different times split every cost report three ways. Merging repoints
 * all history — maintenance tasks, work-order parts and labor — onto
 * one task so the numbers add up again.
 *
 * Destructive and irreversible, so the dialog is explicit about which
 * task disappears and requires a deliberate second click.
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Merge } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import {
  SERVICE_TASKS_KEY, fetchServiceTasks, mergeServiceTasks,
  type ServiceTask,
} from './api';

export default function MergeTaskDialog({
  task, onClose,
}: {
  /** The task being merged AWAY (must be one of the account's own). */
  task: ServiceTask | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [winnerId, setWinnerId] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const { data } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, 'all'],
    queryFn: () => fetchServiceTasks(true),
    enabled: !!task,
  });

  const targets = (data?.service_tasks ?? []).filter((t) => t.id !== task?.id);
  const winner = targets.find((t) => String(t.id) === winnerId);

  const close = () => {
    setWinnerId('');
    setConfirming(false);
    onClose();
  };

  const merge = async () => {
    if (!task || !winner) return;
    if (!confirming) { setConfirming(true); return; }
    setBusy(true);
    try {
      await mergeServiceTasks(task.id, winner.id);
      qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
      toast.success(`Merged into “${winner.name}”`);
      close();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Merge failed');
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!task} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge “{task?.name}” into another task</DialogTitle>
          <DialogDescription>
            Everything recorded against this task — maintenance history,
            work-order parts and labor — moves to the task you pick. Use
            this when the same job got entered under two names and your
            reports are split.
          </DialogDescription>
        </DialogHeader>

        <label className="block">
          <span className="block text-xs text-muted-foreground mb-1">
            Keep this task
          </span>
          <select
            value={winnerId}
            onChange={(e) => { setWinnerId(e.target.value); setConfirming(false); }}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
          >
            <option value="">Select the task to keep…</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}{t.canonical_key ? ' (standard)' : ''}
              </option>
            ))}
          </select>
        </label>

        {confirming && winner && (
          <p className={`text-xs rounded-md border px-2.5 py-2 ${toneClasses('warn')}`}>
            “{task?.name}” will be deleted and its history moved to
            “{winner.name}”. This can’t be undone.
          </p>
        )}

        <DialogFooter className="gap-2">
          <Button variant="ghost" size="sm" onClick={close}>Cancel</Button>
          <Button size="sm" onClick={merge} disabled={busy || !winner}>
            <Merge size={14} />
            {confirming ? 'Yes, merge them' : 'Merge'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
