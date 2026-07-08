/**
 * Layover entry — the no-load line item.
 *
 * Layover pay exists precisely because there was NO load (the driver sat
 * waiting), so it attaches to a driver + date instead of a load, with the
 * responsible dispatcher stamped so the cost lands on their KPI gross.
 *
 * Driver / dispatcher options are derived from the loads already on
 * screen (the people who actually run freight here) — no extra
 * permission-gated endpoint needed.
 */

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { createLineItem } from './api';

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'text-foreground focus:outline-none focus:border-ring';

export interface PersonOption {
  id: number;
  name: string;
}

export default function LayoverDialog({
  open, drivers, dispatchers, onClose, onSaved,
}: {
  open: boolean;
  drivers: PersonOption[];
  dispatchers: PersonOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [driverId, setDriverId] = useState('');
  const [dispatcherId, setDispatcherId] = useState('');
  const [date, setDate] = useState('');
  const [amount, setAmount] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setDriverId('');
    setDispatcherId('');
    setDate('');
    setAmount('');
    setNotes('');
    setError('');
  }, [open]);

  const canSave = !!driverId && !!date && Number(amount) > 0;

  const save = async () => {
    if (!canSave || saving) return;
    setSaving(true);
    setError('');
    try {
      await createLineItem({
        kind: 'layover',
        amount: Number(amount),
        driver_user_id: Number(driverId),
        dispatcher_user_id: dispatcherId ? Number(dispatcherId) : undefined,
        item_date: date,
        notes,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the layover');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Add layover</DialogTitle>
          <DialogDescription>
            Guaranteed pay for a driver who sat without a load. It counts
            against the responsible dispatcher&apos;s KPI for that day.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
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
          <label className="text-sm col-span-2">
            <span className="text-muted-foreground">Responsible dispatcher (for KPI)</span>
            <select className={inputCls} value={dispatcherId} onChange={(e) => setDispatcherId(e.target.value)}>
              <option value="">—</option>
              {dispatchers.map((d) => (
                <option key={d.id} value={String(d.id)}>{d.name}</option>
              ))}
            </select>
          </label>
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
            {saving && <Loader2 size={16} className="animate-spin mr-1.5" />}
            Add layover
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
