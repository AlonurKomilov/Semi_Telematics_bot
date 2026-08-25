/**
 * Off-load driver adjustment — a line item with NO load, attached to a
 * driver + date.  Two shapes share this one entry point:
 *
 *   * Layover (an addition) — pay for a driver who sat without a load;
 *     stamped with the responsible dispatcher so the cost lands on their
 *     KPI gross.
 *   * A deduction (advance / escrow / insurance / fuel card / charge) —
 *     money withheld from the driver's net that isn't tied to any load
 *     (a cash advance on a date).  No dispatcher — deductions never touch
 *     dispatcher KPI.
 *
 * Both flow through the same load-line-item mechanism the on-load
 * "Extra pay & costs" editor uses, so the settlement statement reads them
 * uniformly.  Driver / dispatcher options are derived from the loads on
 * screen (the people who actually run freight here).
 */

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { createLineItem, LINE_ITEM_BUCKET } from './api';

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'text-foreground focus:outline-none focus:border-ring';

export interface PersonOption {
  id: number;
  name: string;
}

// Off-load kinds: layover (addition) + the deductions.  A per-load charge
// stays on the load; these are the ones that legitimately have no load.
const OFF_LOAD_KINDS = [
  { value: 'layover', label: 'Layover (pay)' },
  { value: 'advance', label: 'Advance (deduction)' },
  { value: 'escrow', label: 'Escrow (deduction)' },
  { value: 'insurance', label: 'Insurance (deduction)' },
  { value: 'fuel_card', label: 'Fuel card (deduction)' },
  { value: 'charge', label: 'Charge (deduction)' },
];

export default function LayoverDialog({
  open, drivers, dispatchers, onClose, onSaved,
}: {
  open: boolean;
  drivers: PersonOption[];
  dispatchers: PersonOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [kind, setKind] = useState('layover');
  const [driverId, setDriverId] = useState('');
  const [dispatcherId, setDispatcherId] = useState('');
  const [date, setDate] = useState('');
  const [amount, setAmount] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setKind('layover');
    setDriverId('');
    setDispatcherId('');
    setDate('');
    setAmount('');
    setNotes('');
    setError('');
  }, [open]);

  const isDeduction = LINE_ITEM_BUCKET[kind] === 'deduction';
  const canSave = !!driverId && !!date && Number(amount) > 0;

  const save = async () => {
    if (!canSave || saving) return;
    setSaving(true);
    setError('');
    try {
      await createLineItem({
        kind,
        amount: Number(amount),
        driver_user_id: Number(driverId),
        // Dispatcher attribution is only meaningful for layover (it hits
        // that dispatcher's KPI); a deduction has none.
        dispatcher_user_id: !isDeduction && dispatcherId ? Number(dispatcherId) : undefined,
        item_date: date,
        notes,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the entry');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add off-load entry</DialogTitle>
          <DialogDescription>
            Pay or a deduction for a driver that isn&apos;t tied to a load — a
            layover they sat through, or an advance/insurance withheld. It
            lands on that driver&apos;s settlement for the date.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Type</span>
            <select className={inputCls} value={kind} onChange={(e) => setKind(e.target.value)}>
              {OFF_LOAD_KINDS.map((k) => (
                <option key={k.value} value={k.value}>{k.label}</option>
              ))}
            </select>
          </label>
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Driver</span>
            <select className={inputCls} value={driverId} onChange={(e) => setDriverId(e.target.value)}>
              <option value="">Select driver…</option>
              {drivers.map((d) => (
                <option key={d.id} value={String(d.id)}>{d.name}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Date</span>
            <input className={inputCls} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="text-muted-foreground">Amount ($)</span>
            <input className={inputCls} type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          {!isDeduction && (
            <label className="text-sm col-span-2">
              <span className="text-muted-foreground">Responsible dispatcher (for KPI)</span>
              <select className={inputCls} value={dispatcherId} onChange={(e) => setDispatcherId(e.target.value)}>
                <option value="">—</option>
                {dispatchers.map((d) => (
                  <option key={d.id} value={String(d.id)}>{d.name}</option>
                ))}
              </select>
            </label>
          )}
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Note (optional)</span>
            <input className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => { void save(); }} disabled={saving || !canSave}>
            {saving && <Loader2 className="animate-spin mr-1.5" />}
            {isDeduction ? 'Add deduction' : 'Add layover'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
