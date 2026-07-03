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
import { Loader2, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import {
  LOAD_STATUSES, createLoad, deleteLoad, updateLoad,
} from './api';
import type { LoadDraft, LoadRow } from './api';

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

  const isEdit = load != null;

  useEffect(() => {
    if (!open) return;
    setError('');
    setDraft(load ? toDraft(load) : EMPTY);
  }, [open, load]);

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
            <select className={inputCls} value={draft.status} onChange={set('status')}>
              {LOAD_STATUSES.map((s) => (
                <option key={s} value={s}>{s.replace('_', '-')}</option>
              ))}
            </select>
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
            <select className={inputCls} value={draft.payment_status} onChange={set('payment_status')}>
              <option value="">—</option>
              <option value="unpaid">unpaid</option>
              <option value="paid">paid</option>
            </select>
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

        {error && <p className="text-sm text-danger">{error}</p>}

        <DialogFooter className="flex items-center justify-between gap-2">
          {isEdit ? (
            <Button
              variant="outline"
              onClick={remove}
              disabled={removing || saving}
              className="mr-auto"
            >
              {removing
                ? <Loader2 size={16} className="animate-spin" />
                : <Trash2 size={16} />}
              <span className="ml-1.5">Remove</span>
            </Button>
          ) : <span className="mr-auto" />}
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {isEdit ? 'Save changes' : 'Add load'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
