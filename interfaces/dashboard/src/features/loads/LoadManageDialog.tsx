/**
 * Add / edit a load.
 *
 * OUR loads table is the single source of truth — this dialog is manual
 * entry (source #1); a connected TMS projects in as source #2.  Gated
 * upstream by ``can_manage_loads`` (the page only renders the affordances
 * when the caller has it).
 *
 * Create → POST   /loads/
 * Edit   → PUT    /loads/{id}
 * Remove → DELETE /loads/{id}  (soft delete)
 */

import { useEffect, useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Tip } from '../../components/tooltip';
import { formatDate, formatAgoShort } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import {
  LINE_ITEM_KINDS, LINE_ITEM_BUCKET, LOAD_STATUSES,
  createLineItem, createLoad, deleteLineItem, deleteLoad,
  listLineItems, listLoadHistory, updateLoad,
} from './api';
import type { LineItem, LoadDraft, LoadEvent, LoadRow } from './api';

// The trail's event labels + which diff keys are worth a human's eyes.
// *_user_id keys are suppressed when their *_name twin changed too —
// "dispatcher: Maria → Cody" says it; "dispatcher_user_id: 7 → 12" is noise.
const EVENT_LABEL: Record<string, string> = {
  created: 'Created',
  edited: 'Edited',
  deleted: 'Deleted',
  line_item_added: 'Extra item added',
  line_item_removed: 'Extra item removed',
};
const FIELD_LABEL: Record<string, string> = {
  load_number: 'Load #', status: 'status', payment_status: 'payment',
  customer: 'customer', company_code: 'company',
  pickup_location: 'pickup', pickup_date: 'pickup date',
  delivery_location: 'delivery', delivery_date: 'delivery date',
  driver_name: 'driver', dispatcher_name: 'dispatcher',
  vehicle_unit: 'truck', trailer_unit: 'trailer',
  total_rate: 'rate', loaded_miles: 'loaded mi', empty_miles: 'empty mi',
  driver_pay: 'driver pay', other_costs: 'other costs', notes: 'notes',
  kind: 'kind', amount: 'amount',
};
const fmtVal = (v: unknown): string => (v == null || v === '' ? '—' : String(v));
const visibleChanges = (changes: LoadEvent['changes']): [string, [unknown, unknown]][] =>
  Object.entries(changes).filter(([k]) =>
    !(k.endsWith('_user_id') && `${k.slice(0, -8)}_name` in changes));

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'text-foreground focus:outline-none focus:border-ring';

type Draft = {
  load_number: string;
  status: string;
  payment_status: string;
  customer: string;
  company_code: string;
  pickup_location: string;
  pickup_date: string;
  delivery_location: string;
  delivery_date: string;
  driver_name: string;
  dispatcher_name: string;
  vehicle_unit: string;
  trailer_unit: string;
  total_rate: string;
  loaded_miles: string;
  empty_miles: string;
  driver_pay: string;
  other_costs: string;
  notes: string;
};

const EMPTY: Draft = {
  load_number: '', status: 'upcoming', payment_status: '',
  customer: '', company_code: '',
  pickup_location: '', pickup_date: '', delivery_location: '', delivery_date: '',
  driver_name: '', dispatcher_name: '', vehicle_unit: '', trailer_unit: '',
  total_rate: '', loaded_miles: '', empty_miles: '', driver_pay: '',
  other_costs: '', notes: '',
};

function toDraft(l: LoadRow): Draft {
  const s = (v: number | null) => (v == null ? '' : String(v));
  return {
    load_number: l.load_number, status: l.status,
    payment_status: l.payment_status,
    customer: l.customer, company_code: l.company_code,
    pickup_location: l.pickup_location, pickup_date: l.pickup_date,
    delivery_location: l.delivery_location, delivery_date: l.delivery_date,
    driver_name: l.driver_name, dispatcher_name: l.dispatcher_name,
    vehicle_unit: l.vehicle_unit, trailer_unit: l.trailer_unit,
    total_rate: s(l.total_rate), loaded_miles: s(l.loaded_miles),
    empty_miles: s(l.empty_miles), driver_pay: s(l.driver_pay),
    other_costs: s(l.other_costs), notes: l.notes,
  };
}

function toPayload(d: Draft): LoadDraft {
  const num = (v: string) => (v.trim() === '' ? undefined : Number(v));
  return {
    load_number: d.load_number, status: d.status,
    payment_status: d.payment_status,
    customer: d.customer, company_code: d.company_code,
    pickup_location: d.pickup_location, pickup_date: d.pickup_date,
    delivery_location: d.delivery_location, delivery_date: d.delivery_date,
    driver_name: d.driver_name, dispatcher_name: d.dispatcher_name,
    vehicle_unit: d.vehicle_unit, trailer_unit: d.trailer_unit,
    total_rate: num(d.total_rate), loaded_miles: num(d.loaded_miles),
    empty_miles: num(d.empty_miles), driver_pay: num(d.driver_pay),
    other_costs: num(d.other_costs), notes: d.notes,
  };
}

export default function LoadManageDialog({
  open, load, onClose, onSaved,
}: {
  open: boolean;
  /** The row being edited, or null to create a new one. */
  load: LoadRow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState('');
  // Extra pay & costs (line items) — edit mode only; a new load has no
  // id to attach items to until it's saved.
  const [items, setItems] = useState<LineItem[]>([]);
  const [itemKind, setItemKind] = useState<string>('tonu');
  const [itemAmount, setItemAmount] = useState('');
  const [itemNote, setItemNote] = useState('');
  const [itemBusy, setItemBusy] = useState(false);

  const isEdit = load != null;
  // The accountability trail — read-only; fetched fresh on open so an
  // edit made from ANOTHER tab still shows before this user saves over it.
  const [events, setEvents] = useState<LoadEvent[]>([]);
  const [showAllEvents, setShowAllEvents] = useState(false);
  const tz = useTimezone();

  useEffect(() => {
    if (!open) return;
    setError('');
    setDraft(load ? toDraft(load) : EMPTY);
    setItems([]);
    setItemKind('tonu');
    setItemAmount('');
    setItemNote('');
    setEvents([]);
    setShowAllEvents(false);
    if (load) {
      listLineItems(load.id)
        .then(setItems)
        .catch(() => { /* section shows empty; add still works */ });
      listLoadHistory(load.id)
        .then(setEvents)
        .catch(() => { /* trail shows empty; the write path is unaffected */ });
    }
  }, [open, load]);

  const addItem = async () => {
    if (!load || itemBusy) return;
    const amount = Number(itemAmount);
    if (!amount || amount <= 0) return;
    setItemBusy(true);
    try {
      await createLineItem({
        kind: itemKind, amount, load_id: load.id, notes: itemNote,
      });
      setItemAmount('');
      setItemNote('');
      setItems(await listLineItems(load.id));
      onSaved();          // gross changed — refresh the list behind us
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add the item');
    } finally {
      setItemBusy(false);
    }
  };

  const removeItem = async (it: { id: number; kind: string; amount: number }) => {
    if (!load || itemBusy) return;
    const amt = `$${it.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
    if (!confirm(`Remove ${it.kind} (${amt})? This can't be undone.`)) return;
    setItemBusy(true);
    try {
      await deleteLineItem(it.id);
      setItems(await listLineItems(load.id));
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the item');
    } finally {
      setItemBusy(false);
    }
  };

  const set = (k: keyof Draft) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setDraft((d) => ({ ...d, [k]: e.target.value }));

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      if (isEdit && load) await updateLoad(load.id, toPayload(draft));
      else await createLoad(toPayload(draft));
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the load');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!isEdit || !load || removing) return;
    setRemoving(true);
    setError('');
    try {
      await deleteLoad(load.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the load');
    } finally {
      setRemoving(false);
    }
  };

  // Select item lists — built from the same source arrays so labels match.
  const statusItems = LOAD_STATUSES.map((s) => ({ value: s, label: s.replace('_', '-') }));
  const paymentItems = [
    { value: '', label: '—' },
    { value: 'unpaid', label: 'unpaid' },
    { value: 'paid', label: 'paid' },
  ];
  // Layover has no load (it's the no-load case) → not offered here; every
  // other kind — additions, company costs, and driver deductions — can
  // attach to a load, grouped by bucket in the label so it's clear which
  // way the money moves.
  const bucketLabel = (k: string) => {
    const b = LINE_ITEM_BUCKET[k];
    return b === 'driver_pay' ? `+ ${k}` : b === 'deduction' ? `− ${k.replace('_', ' ')}` : `cost · ${k}`;
  };
  const itemKindItems = LINE_ITEM_KINDS
    .filter((k) => k !== 'layover')
    .map((k) => ({ value: k, label: bucketLabel(k) }));

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit load ${load?.load_number || `#${load?.id}`}` : 'Add load'}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? 'Update the load. Synced fields may be refreshed by the TMS integration.'
              : 'Enter a load by hand — works with or without a TMS connected.'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            <span className="text-muted-foreground">Load # (broker / BOL reference)</span>
            <input className={inputCls} value={draft.load_number} onChange={set('load_number')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Status</span>
            <Select value={draft.status} onValueChange={(v) => setDraft((d) => ({ ...d, status: v ?? d.status }))} items={statusItems}>
              <SelectTrigger className="w-full" aria-label="Status"><SelectValue /></SelectTrigger>
              <SelectContent>
                {statusItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Customer / broker</span>
            <input className={inputCls} value={draft.customer} onChange={set('customer')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Pickup location</span>
            <input className={inputCls} value={draft.pickup_location} onChange={set('pickup_location')} placeholder="City, ST" />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Pickup date</span>
            <input className={inputCls} type="date" value={draft.pickup_date} onChange={set('pickup_date')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Delivery location</span>
            <input className={inputCls} value={draft.delivery_location} onChange={set('delivery_location')} placeholder="City, ST" />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Delivery date</span>
            <input className={inputCls} type="date" value={draft.delivery_date} onChange={set('delivery_date')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Driver</span>
            <input className={inputCls} value={draft.driver_name} onChange={set('driver_name')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Dispatcher</span>
            <input className={inputCls} value={draft.dispatcher_name} onChange={set('dispatcher_name')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Truck unit</span>
            <input className={inputCls} value={draft.vehicle_unit} onChange={set('vehicle_unit')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Trailer unit</span>
            <input className={inputCls} value={draft.trailer_unit} onChange={set('trailer_unit')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Company code</span>
            <input className={inputCls} value={draft.company_code} onChange={set('company_code')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Payment</span>
            <Select value={draft.payment_status} onValueChange={(v) => setDraft((d) => ({ ...d, payment_status: v ?? d.payment_status }))} items={paymentItems}>
              <SelectTrigger className="w-full" aria-label="Payment"><SelectValue /></SelectTrigger>
              <SelectContent>
                {paymentItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Rate ($)</span>
            <input className={inputCls} type="number" min="0" value={draft.total_rate} onChange={set('total_rate')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Loaded miles</span>
            <input className={inputCls} type="number" min="0" value={draft.loaded_miles} onChange={set('loaded_miles')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Empty miles</span>
            <input className={inputCls} type="number" min="0" value={draft.empty_miles} onChange={set('empty_miles')} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Driver pay ($)</span>
            <input className={inputCls} type="number" min="0" value={draft.driver_pay} onChange={set('driver_pay')} />
          </label>
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Notes</span>
            <textarea className={inputCls} rows={2} value={draft.notes} onChange={set('notes')} />
          </label>
        </div>

        {isEdit && (
          <div className="mt-1 border-t border-border pt-3">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Extra pay &amp; costs
            </div>
            {items.length > 0 && (
              <ul className="mb-2 space-y-1">
                {items.map((it) => (
                  <li key={it.id} className="flex items-center gap-2 text-sm">
                    <span className="capitalize text-foreground">{it.kind}</span>
                    <span className="text-2xs text-muted-foreground">
                      {it.bucket === 'driver_pay' ? 'driver pay' : 'expense'}
                      {it.notes ? ` · ${it.notes}` : ''}
                    </span>
                    <span className="ml-auto tabular-nums font-medium text-foreground">
                      ${it.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      disabled={itemBusy}
                      onClick={() => { void removeItem(it); }}
                      aria-label="Remove item"
                      title="Remove item"
                    >
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center gap-2">
              <Select value={itemKind} onValueChange={(v) => setItemKind(v ?? 'tonu')} items={itemKindItems}>
                <SelectTrigger className="w-full" aria-label="Item kind"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {itemKindItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <input
                className={inputCls}
                type="number"
                min="0"
                placeholder="Amount $"
                value={itemAmount}
                onChange={(e) => setItemAmount(e.target.value)}
                aria-label="Item amount"
              />
              <input
                className={inputCls}
                placeholder="Note (optional)"
                value={itemNote}
                onChange={(e) => setItemNote(e.target.value)}
                aria-label="Item note"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={itemBusy || !Number(itemAmount)}
                onClick={() => { void addItem(); }}
              >
                <Plus className="mr-1" />
                Add
              </Button>
            </div>
            <p className="mt-1 text-2xs text-muted-foreground">
              TONU / bonus / detention count as driver pay; tolls / lumper as
              load expenses — both reduce this load's gross. Layover (no load)
              is entered from the Loads page.
            </p>
          </div>
        )}

        {/* The accountability trail — every human change, who and what,
            old → new.  This is what replaced the own-dispatcher edit
            wall: anyone with Manage can edit, and this block answers
            "who touched my load".  Mirrors Inventory's History block. */}
        {isEdit && (
          <div className="border-t border-border pt-3">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              History
              {events.length > 0 && (
                <span className="ml-1.5 normal-case tracking-normal text-muted-foreground/70">({events.length})</span>
              )}
            </div>
            <ul className="space-y-2 max-h-48 overflow-y-auto pr-1">
              {events.length === 0 && (
                <li className="text-xs text-muted-foreground/60 italic">No changes recorded yet.</li>
              )}
              {(showAllEvents ? events : events.slice(0, 5)).map((e) => (
                <li key={e.id} className="text-xs text-muted-foreground">
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="text-foreground font-medium">
                      {EVENT_LABEL[e.event_type] ?? e.event_type}
                    </span>
                    <Tip label={formatDate(e.created_at, { timeZone: tz })}>
                      <span className="text-muted-foreground/70">
                        {formatAgoShort(e.created_at, { timeZone: tz })}
                      </span>
                    </Tip>
                  </div>
                  {visibleChanges(e.changes).length > 0 && (
                    <div className="text-2xs text-muted-foreground/80">
                      {visibleChanges(e.changes).map(([k, [from, to]]) => (
                        <span key={k} className="mr-2 whitespace-nowrap">
                          {FIELD_LABEL[k] ?? k}: {fmtVal(from)} → <span className="text-foreground">{fmtVal(to)}</span>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="text-2xs text-muted-foreground/80">
                    {e.actor_name && <>by {e.actor_name}</>}
                    {e.dispatcher_name && (
                      <> · dispatcher at the time: <span className="text-foreground">{e.dispatcher_name}</span></>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            {events.length > 5 && (
              <button
                type="button"
                onClick={() => setShowAllEvents((s) => !s)}
                className="mt-2 text-2xs text-muted-foreground hover:text-foreground transition-colors py-1 -my-1 min-h-tap"
              >
                {showAllEvents ? 'Show less' : `Show ${events.length - 5} more`}
              </button>
            )}
          </div>
        )}

        {error && <p className="text-sm text-danger">{error}</p>}

        <DialogFooter justify="between">
          {isEdit ? (
            <Button
              variant="outline"
              onClick={remove}
              disabled={removing || saving}
              className="mr-auto"
            >
              {removing
                ? <Loader2 className="animate-spin" />
                : <Trash2 />}
              <span className="ml-1.5">Remove</span>
            </Button>
          ) : <span className="mr-auto" />}
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 className="animate-spin mr-1.5" />}
            {isEdit ? 'Save changes' : 'Add load'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
