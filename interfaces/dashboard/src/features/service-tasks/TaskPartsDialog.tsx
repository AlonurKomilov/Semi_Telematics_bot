/**
 * Linked parts for one service task — the "task carries its parts"
 * half of the feature.
 *
 * Whatever is listed here is what a work order pre-fills when someone
 * picks this task, so the shop visit starts from the usual bill of
 * materials instead of being retyped. Quantities are DEFAULTS; the
 * invoice still decides what actually went on the truck, and unlinking
 * never touches work orders already created.
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Trash2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import {
  fetchTaskParts, linkTaskPart, unlinkTaskPart, type ServiceTask,
} from './api';

interface CatalogPart { id: number; name: string; part_number?: string }

export default function TaskPartsDialog({
  task, onClose, canManage,
}: {
  task: ServiceTask | null;
  onClose: () => void;
  canManage: boolean;
}) {
  const qc = useQueryClient();
  const [partId, setPartId] = useState('');
  const [qty, setQty] = useState('1');
  const [busy, setBusy] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['service-task-parts', task?.id],
    queryFn: () => fetchTaskParts(task!.id),
    enabled: !!task,
  });

  const { data: catalog } = useQuery({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON<{ parts: CatalogPart[] }>('/parts'),
    staleTime: 60_000,
    enabled: !!task && canManage,
  });

  const links = data?.parts ?? [];
  const refresh = () =>
    qc.invalidateQueries({ queryKey: ['service-task-parts', task?.id] });

  const add = async () => {
    if (!task || !partId) return;
    setBusy(true);
    try {
      await linkTaskPart(task.id, Number(partId), Number(qty) || 1);
      setPartId('');
      setQty('1');
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not link that part');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (linkId: number) => {
    if (!task) return;
    try {
      await unlinkTaskPart(task.id, linkId);
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not unlink');
    }
  };

  // Parts already linked shouldn't appear in the add picker.
  const linkedIds = new Set(links.map((l) => l.part_id));
  const options = (catalog?.parts ?? []).filter((p) => !linkedIds.has(p.id));

  return (
    <Dialog open={!!task} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{task?.name} — usual parts</DialogTitle>
          <DialogDescription>
            These pre-fill onto a work order when someone picks this task.
            Quantities are a starting point; the invoice still decides.
          </DialogDescription>
        </DialogHeader>

        {isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
        {!isLoading && links.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No parts linked yet — work orders for this task start empty.
          </p>
        )}
        {links.length > 0 && (
          <ul className="space-y-1.5">
            {links.map((l) => (
              <li
                key={l.id}
                className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 py-1.5"
              >
                <span className="text-sm text-foreground flex-1 min-w-0 truncate">
                  {l.part_name}
                  {l.part_number && (
                    <span className="text-xs text-muted-foreground"> · {l.part_number}</span>
                  )}
                </span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  ×{l.quantity}
                </span>
                {canManage && (
                  <button
                    type="button"
                    onClick={() => remove(l.id)}
                    aria-label={`Unlink ${l.part_name}`}
                    className="text-muted-foreground hover:text-destructive p-1"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {canManage && (
          <div className="flex items-end gap-2 pt-1">
            <label className="flex-1 min-w-0">
              <span className="block text-xs text-muted-foreground mb-1">Part</span>
              <select
                value={partId}
                onChange={(e) => setPartId(e.target.value)}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              >
                <option value="">Select a part…</option>
                {options.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
            <label className="w-20">
              <span className="block text-xs text-muted-foreground mb-1">Qty</span>
              <input
                type="number" min="0.01" step="0.5" inputMode="decimal"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums text-foreground focus:outline-none focus:border-ring"
              />
            </label>
            <Button size="sm" onClick={add} disabled={busy || !partId}>
              Link
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
