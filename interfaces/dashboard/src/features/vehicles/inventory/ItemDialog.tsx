/**
 * Inventory dialogs — Add (category/label/identifier/notes) and the item
 * detail (status actions, verify, transfer, remove + the accountability
 * trail: who did what, and which driver had the truck at that moment).
 */
import { useState } from 'react';
import { ArrowRightLeft, Check, Trash2 } from 'lucide-react';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { Button } from '../../../components/ui/button';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../../components/ui/select';
import { statusClasses } from '../../../lib/status';
import { toneClasses } from '../../../lib/status';
import { formatDate } from '../../../utils/datetime';
import { useTimezone } from '../../../hooks/useTimezone';
import { useInventoryEvents, useInventoryMutations } from './useInventory';
import type { InventoryItem } from './useInventory';
import { categoryMeta, STATUS_LABELS } from './categories';

const inputCls =
  'w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm';

// ── Add ──────────────────────────────────────────────────────────

export function AddItemDialog({ vehicleName, company, categories, onClose }: {
  /** Fixed truck context (vehicle detail card).  Omit on the fleet-wide
   *  page — the dialog then asks for the truck number, validated
   *  server-side against the registry (404 → inline error). */
  vehicleName?: string;
  company?: string;
  categories: string[];
  onClose: () => void;
}) {
  const [truck, setTruck] = useState('');
  const targetTruck = vehicleName ?? truck.trim();
  const { add } = useInventoryMutations(targetTruck, company);
  const [category, setCategory] = useState(categories[0] ?? 'other');
  const [label, setLabel] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [notes, setNotes] = useState('');
  const [err, setErr] = useState('');

  const items = categories.map((c) => ({ value: c, label: categoryMeta(c).label }));

  const submit = async () => {
    setErr('');
    try {
      await add.mutateAsync({ category, label: label.trim(), identifier: identifier.trim(), notes: notes.trim() });
      onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Add inventory item</DialogTitle></DialogHeader>
        <div className="space-y-3">
          {vehicleName == null && (
            <label className="block">
              <span className="text-xs font-medium text-muted-foreground">Truck №</span>
              <input value={truck} onChange={(e) => setTruck(e.target.value)}
                placeholder="which truck this item lives in" className={`mt-1 ${inputCls}`} />
            </label>
          )}
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">Category</span>
            <Select value={category} onValueChange={setCategory} items={items}>
              <SelectTrigger className="w-full mt-1" aria-label="Category"><SelectValue /></SelectTrigger>
              <SelectContent>
                {items.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">Label</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Samsara CM32, EFS card…" className={`mt-1 ${inputCls}`} />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">Identifier (serial / last-4 / transponder №)</span>
            <input value={identifier} onChange={(e) => setIdentifier(e.target.value)}
              placeholder="what makes THIS unit provable" className={`mt-1 ${inputCls}`} />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">Notes (optional)</span>
            <input value={notes} onChange={(e) => setNotes(e.target.value)} className={`mt-1 ${inputCls}`} />
          </label>
          {err && <p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={add.isPending || !label.trim() || !targetTruck}>
            {add.isPending ? 'Adding…' : 'Add item'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Item detail: actions + trail ─────────────────────────────────

const EVENT_LABEL: Record<string, string> = {
  installed: 'Installed',
  status_change: 'Status changed',
  transferred: 'Transferred',
  verified: 'Verified',
  edited: 'Edited',
  removed: 'Removed',
};

export function ItemDialog({ vehicleName, company, item, statuses, canManage, onClose }: {
  vehicleName: string;
  company?: string;
  item: InventoryItem;
  statuses: string[];
  canManage: boolean;
  onClose: () => void;
}) {
  const tz = useTimezone();
  const { patch, verify, transfer, remove } = useInventoryMutations(vehicleName, company);
  const { data: eventsData } = useInventoryEvents(item.id);
  const [status, setStatus] = useState(item.status);
  const [note, setNote] = useState('');
  const [transferTo, setTransferTo] = useState('');
  const [err, setErr] = useState('');
  const { label: catLabel, Icon } = categoryMeta(item.category);

  const statusItems = statuses.map((s) => ({ value: s, label: STATUS_LABELS[s] ?? s }));
  const busy = patch.isPending || verify.isPending || transfer.isPending || remove.isPending;

  const run = async (fn: () => Promise<unknown>, close = false) => {
    setErr('');
    try { await fn(); if (close) onClose(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Icon size={18} className="text-muted-foreground" />
              {item.label}
              <span className={`px-2 py-0.5 rounded-full text-xs border ${statusClasses(item.status)}`}>
                {STATUS_LABELS[item.status] ?? item.status}
              </span>
            </span>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="text-sm text-muted-foreground space-y-1">
            <div>{catLabel}{item.identifier && <> · <span className="font-mono text-xs text-foreground">{item.identifier}</span></>}</div>
            {item.notes && <div className="text-xs">{item.notes}</div>}
          </div>

          {canManage && (
            <div className="space-y-3 border-t border-border pt-3">
              <div className="flex items-end gap-2">
                <label className="flex-1">
                  <span className="text-xs font-medium text-muted-foreground">Status</span>
                  <Select value={status} onValueChange={setStatus} items={statusItems}>
                    <SelectTrigger className="w-full mt-1" aria-label="Status"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {statusItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </label>
                <Button
                  variant="outline"
                  disabled={busy || status === item.status}
                  onClick={() => run(() => patch.mutateAsync({ itemId: item.id, status, note }), true)}
                >
                  Apply
                </Button>
              </div>
              <input
                value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="Reason / note for the trail (recommended for damaged & missing)"
                className={inputCls}
              />
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={busy}
                  onClick={() => run(() => verify.mutateAsync(item.id), true)}>
                  <Check size={14} /> Verify present
                </Button>
                <div className="flex-1 flex items-center gap-1.5">
                  <input
                    value={transferTo} onChange={(e) => setTransferTo(e.target.value)}
                    placeholder="Truck №" aria-label="Transfer to truck"
                    className={`${inputCls} py-1.5 text-xs`}
                  />
                  <Button variant="outline" size="sm" disabled={busy || !transferTo.trim()}
                    onClick={() => run(() => transfer.mutateAsync({ itemId: item.id, toVehicleName: transferTo.trim(), note }), true)}>
                    <ArrowRightLeft size={14} /> Transfer
                  </Button>
                </div>
                <Button variant="ghost" size="sm" disabled={busy}
                  onClick={() => run(() => remove.mutateAsync({ itemId: item.id, note }), true)}>
                  <Trash2 size={14} /> Remove
                </Button>
              </div>
              {err && <p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{err}</p>}
            </div>
          )}

          <div className="border-t border-border pt-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">History</div>
            <ul className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
              {(eventsData?.events ?? []).map((e) => (
                <li key={e.id} className="text-xs text-muted-foreground">
                  <span className="text-foreground">{EVENT_LABEL[e.event_type] ?? e.event_type}</span>
                  {e.from_status && e.to_status && <> — {STATUS_LABELS[e.from_status] ?? e.from_status} → {STATUS_LABELS[e.to_status] ?? e.to_status}</>}
                  {e.event_type === 'transferred' && <> — truck {e.from_vehicle_id} → {e.to_vehicle_id}</>}
                  <span className="text-muted-foreground/70"> · {formatDate(e.created_at, { timeZone: tz })}</span>
                  {e.actor_name && <> · by {e.actor_name}</>}
                  {e.driver_name && <> · driver: <span className="text-foreground">{e.driver_name}</span></>}
                  {e.note && <div className="text-2xs text-muted-foreground/80 pl-3">“{e.note}”</div>}
                </li>
              ))}
              {(eventsData?.events ?? []).length === 0 && (
                <li className="text-xs text-muted-foreground/60 italic">No events yet.</li>
              )}
            </ul>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
