/**
 * Inventory dialogs — Add (category/label/identifier/notes) and the item
 * detail (status actions, verify, transfer, remove + the accountability
 * trail: who did what, and which driver had the truck at that moment).
 *
 * Both use the shared Vehicle picker for anything that names a vehicle:
 * free text can't disambiguate duplicate unit numbers across companies
 * (103 G1 vs 103 OSY), and the item would land on the wrong truck.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowRightLeft, Check, Trash2 } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { Button } from '../../../components/ui/button';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../../components/ui/select';
import { Tip, InfoTip } from '../../../components/tooltip';
import { statusClasses, toneClasses } from '../../../lib/status';
import { formatDate, formatAgoShort } from '../../../utils/datetime';
import { useTimezone } from '../../../hooks/useTimezone';
import { VehiclePicker, type VehicleSummary } from '../../maintenance/pickers';
import { useInventoryEvents, useInventoryMutations } from './useInventory';
import type { InventoryItem } from './useInventory';
import { categoryMeta, STATUS_LABELS } from './categories';

const inputCls =
  'w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm';
const labelCls = 'text-xs font-medium text-muted-foreground';

/** Fleet list for the Vehicle pickers.  Shares the maintenance picker's
 *  cache key, so the list is fetched once per session regardless of which
 *  form opens first; walks all pages (backend caps page_size at 200). */
function useFleetList(enabled: boolean) {
  return useQuery({
    queryKey: ['maintenance-vehicles'],
    queryFn: async () => {
      const all: VehicleSummary[] = [];
      let page = 1;
      for (;;) {
        const res = await apiJSON<{ vehicles: VehicleSummary[]; total_pages: number }>(
          `/vehicles?page_size=200&page=${page}`,
        );
        all.push(...(res.vehicles ?? []));
        if (page >= (res.total_pages ?? 1)) break;
        page++;
      }
      return { vehicles: all };
    },
    enabled,
  });
}

// ── Add ──────────────────────────────────────────────────────────

export function AddItemDialog({ vehicleName, company, categories, onClose }: {
  /** Fixed vehicle context (the vehicle-detail card).  Omit on the
   *  account-wide page — the dialog then shows the Vehicle picker. */
  vehicleName?: string;
  company?: string;
  categories: string[];
  onClose: () => void;
}) {
  const [pickedName, setPickedName] = useState('');
  const [pickedCompany, setPickedCompany] = useState<string | undefined>(undefined);
  const targetVehicle = vehicleName ?? pickedName.trim();
  const { add } = useInventoryMutations(targetVehicle, company ?? pickedCompany);
  const [category, setCategory] = useState(categories[0] ?? 'other');
  const [label, setLabel] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [notes, setNotes] = useState('');
  const [err, setErr] = useState('');
  const { data: fleet, isLoading: fleetLoading } = useFleetList(vehicleName == null);

  // Category is an OPEN vocabulary: the server list (built-ins + this
  // account's customs) plus an "Add category…" entry that reveals a
  // free-text input — the server normalizes ("Safety Equipment" ->
  // safety_equipment).
  const CUSTOM = '__custom__';
  const [customCategory, setCustomCategory] = useState('');
  const items = [
    ...categories.map((c) => ({ value: c, label: categoryMeta(c).label })),
    { value: CUSTOM, label: 'Add category…' },
  ];

  const submit = async () => {
    setErr('');
    if (category === CUSTOM && !customCategory.trim()) {
      setErr('Name the new category first.');
      return;
    }
    try {
      await add.mutateAsync({
        category: category === CUSTOM ? customCategory.trim() : category,
        label: label.trim(),
        identifier: identifier.trim(),
        notes: notes.trim(),
      });
      onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not add the item'); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Add inventory item</DialogTitle></DialogHeader>
        <div className="space-y-3">
          {vehicleName == null && (
            <div>
              <span className={labelCls}>Vehicle</span>
              <div className="mt-1">
                <VehiclePicker
                  value={pickedName}
                  onChange={(name, v) => { setPickedName(name); setPickedCompany(v?.company || undefined); }}
                  vehicles={fleet?.vehicles ?? []}
                  loading={fleetLoading}
                />
              </div>
            </div>
          )}
          <label className="block">
            <span className={labelCls}>Category</span>
            <Select value={category} onValueChange={setCategory} items={items}>
              <SelectTrigger className="w-full mt-1" aria-label="Category"><SelectValue /></SelectTrigger>
              <SelectContent>
                {items.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          {category === CUSTOM && (
            <label className="block">
              <span className={labelCls}>New category</span>
              <input
                value={customCategory}
                onChange={(e) => setCustomCategory(e.target.value)}
                placeholder="e.g. Safety Equipment"
                className={`mt-1 ${inputCls}`}
              />
            </label>
          )}
          <label className="block">
            <span className={labelCls}>Label</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Samsara CM32, EFS card…" className={`mt-1 ${inputCls}`} />
          </label>
          {/* col-reverse: input FIRST in DOM so the implicit label keeps
              targeting it — an InfoTip <button> before the input would
              steal the association (label click toggles the tip). */}
          <label className="flex flex-col-reverse">
            <input value={identifier} onChange={(e) => setIdentifier(e.target.value)}
              placeholder="serial · card last-4 · transponder №" className={`mt-1 ${inputCls}`} />
            <span className={`${labelCls} inline-flex items-center gap-1`}>
              Identifier <InfoTip size={12} label="The serial number, card last-4 or transponder № — what proves THIS unit is the one that went missing or was damaged." />
            </span>
          </label>
          <label className="block">
            <span className={labelCls}>Notes <span className="font-normal">(optional)</span></span>
            <input value={notes} onChange={(e) => setNotes(e.target.value)} className={`mt-1 ${inputCls}`} />
          </label>
          {err && <p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={add.isPending || !label.trim() || !targetVehicle}>
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
  // History shows the LATEST 5 by default — an old item can carry dozens
  // of events, and a wall of history buries the actions above it.
  const [showAllEvents, setShowAllEvents] = useState(false);
  const [err, setErr] = useState('');
  const { label: catLabel, Icon } = categoryMeta(item.category);
  const { data: fleet, isLoading: fleetLoading } = useFleetList(canManage);

  const statusItems = statuses.map((s) => ({ value: s, label: STATUS_LABELS[s] ?? s }));
  const busy = patch.isPending || verify.isPending || transfer.isPending || remove.isPending;

  const run = async (fn: () => Promise<unknown>, close = false) => {
    setErr('');
    try { await fn(); if (close) onClose(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Action failed'); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2 min-w-0">
              <Icon size={18} className="text-muted-foreground shrink-0" />
              <span className="truncate">{item.label}</span>
              <span className={`px-2 py-0.5 rounded-md text-xs border shrink-0 ${statusClasses(item.status)}`}>
                {STATUS_LABELS[item.status] ?? item.status}
              </span>
            </span>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Identity line — category · identifier · which vehicle it's on. */}
          <div className="text-sm text-muted-foreground">
            <span>{catLabel}</span>
            {item.identifier && (
              <> · <span className="font-mono text-xs text-foreground">{item.identifier}</span></>
            )}
            <> · on vehicle <span className="text-foreground font-medium">{vehicleName}</span></>
            {item.notes && <div className="text-xs mt-1">{item.notes}</div>}
          </div>

          {canManage && (
            <div className="space-y-4 border-t border-border pt-4">
              <div className="flex items-end gap-2">
                <label className="flex-1 min-w-0">
                  <span className={labelCls}>Status</span>
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

              <div className="flex items-end gap-2">
                <div className="flex-1 min-w-0">
                  <span className={labelCls}>Transfer to</span>
                  <div className="mt-1">
                    <VehiclePicker
                      value={transferTo}
                      onChange={(name) => setTransferTo(name)}
                      vehicles={(fleet?.vehicles ?? []).filter((v) => v.name !== vehicleName)}
                      loading={fleetLoading}
                    />
                  </div>
                </div>
                <Button
                  variant="outline"
                  disabled={busy || !transferTo.trim()}
                  onClick={() => run(() => transfer.mutateAsync({
                    itemId: item.id, toVehicleName: transferTo.trim(), note,
                  }), true)}
                >
                  <ArrowRightLeft size={14} /> Transfer
                </Button>
              </div>

              {/* ONE note field, stated plainly: it rides whichever action
                  the operator takes (status / transfer / verify / remove)
                  and lands in the permanent trail — placed beside the
                  action buttons it annotates. */}
              {/* col-reverse: input FIRST in DOM so the implicit label
                  keeps targeting it — an InfoTip <button> before the
                  input would steal the association. */}
              <label className="flex flex-col-reverse">
                <input
                  value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="Optional note…"
                  className={`mt-1 ${inputCls}`}
                />
                <span className={`${labelCls} inline-flex items-center gap-1`}>
                  Note <InfoTip size={12} label="Saved to the permanent history together with your next action (status change, transfer, verify or remove)." />
                </span>
              </label>

              <div className="flex items-center justify-between gap-2 pt-1">
                <Button variant="outline" size="sm" disabled={busy}
                  onClick={() => run(() => verify.mutateAsync(item.id), true)}>
                  <Check size={14} /> Verify present
                </Button>
                <Button variant="destructive" size="sm" disabled={busy}
                  onClick={() => run(() => remove.mutateAsync({ itemId: item.id, note }), true)}>
                  <Trash2 size={14} /> Remove
                </Button>
              </div>
              {err && <p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{err}</p>}
            </div>
          )}

          <div className="border-t border-border pt-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
              History{(eventsData?.events?.length ?? 0) > 0 && (
                <span className="ml-1.5 normal-case tracking-normal text-muted-foreground/70">({eventsData?.events?.length})</span>
              )}
            </div>
            <ul className="space-y-2 max-h-48 overflow-y-auto pr-1">
              {(showAllEvents ? (eventsData?.events ?? []) : (eventsData?.events ?? []).slice(0, 5)).map((e) => (
                <li key={e.id} className="text-xs text-muted-foreground">
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="text-foreground font-medium">
                      {EVENT_LABEL[e.event_type] ?? e.event_type}
                    </span>
                    {e.from_status && e.to_status && (
                      <span>
                        {STATUS_LABELS[e.from_status] ?? e.from_status} → {STATUS_LABELS[e.to_status] ?? e.to_status}
                      </span>
                    )}
                    {/* Exact timestamp on hover; the age is what's scannable. */}
                    <Tip label={formatDate(e.created_at, { timeZone: tz })}>
                      <span className="text-muted-foreground/70">{formatAgoShort(e.created_at, { timeZone: tz })}</span>
                    </Tip>
                  </div>
                  <div className="text-2xs text-muted-foreground/80">
                    {e.actor_name && <>by {e.actor_name}</>}
                    {e.driver_name && <> · driver at the time: <span className="text-foreground">{e.driver_name}</span></>}
                  </div>
                  {e.note && <div className="text-2xs text-muted-foreground/80 italic mt-0.5">“{e.note}”</div>}
                </li>
              ))}
              {(eventsData?.events ?? []).length === 0 && (
                <li className="text-xs text-muted-foreground/60 italic">No events yet.</li>
              )}
            </ul>
            {(eventsData?.events?.length ?? 0) > 5 && (
              <button
                type="button"
                onClick={() => setShowAllEvents((v) => !v)}
                className="mt-2 text-2xs text-muted-foreground hover:text-foreground transition-colors py-1 -my-1 min-h-tap"
              >
                {showAllEvents
                  ? 'Show less'
                  : `Show ${(eventsData?.events?.length ?? 0) - 5} more`}
              </button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
