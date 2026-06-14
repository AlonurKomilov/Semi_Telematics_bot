/**
 * Add / edit a registry vehicle.
 *
 * The vehicle registry in our DB is the single source of truth — this
 * dialog is how an operator adds a truck or trailer by hand, including
 * equipment with no telematics.  Gated upstream by ``can_manage_vehicles``
 * (the page only renders the affordances when the user has it).
 *
 * Create  → POST   /vehicles/
 * Edit    → PUT    /vehicles/registry/{id}
 * Remove  → DELETE /vehicles/registry/{id}  (soft delete)
 */

import { useEffect, useState } from 'react';
import { Loader2, Trash2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import type { Vehicle } from '../../types';

const TYPES = [
  { value: 'truck', label: 'Truck' },
  { value: 'trailer', label: 'Trailer' },
  { value: 'other', label: 'Other' },
];

const inputCls =
  'w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm ' +
  'focus:outline-none focus:border-ring';

type Draft = {
  unit_number: string;
  vehicle_type: string;
  company_code: string;
  vin: string;
  plate_number: string;
  make: string;
  model: string;
  year: string;
  notes: string;
};

const EMPTY: Draft = {
  unit_number: '', vehicle_type: 'truck', company_code: '', vin: '',
  plate_number: '', make: '', model: '', year: '', notes: '',
};

export default function VehicleManageDialog({
  open, vehicle, onClose, onSaved,
}: {
  open: boolean;
  /** The row being edited, or null to create a new one. */
  vehicle: Vehicle | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState('');

  const editId = vehicle?.registry_id ?? null;
  const isEdit = editId != null;

  useEffect(() => {
    if (!open) return;
    setError('');
    if (vehicle) {
      setDraft({
        unit_number: vehicle.name ?? '',
        vehicle_type: vehicle.vehicle_type ?? 'truck',
        company_code: vehicle.company ?? vehicle._org ?? '',
        vin: vehicle.vin ?? '',
        plate_number: vehicle.license_plate ?? vehicle.licensePlate ?? '',
        make: vehicle.make ?? '',
        model: vehicle.model ?? '',
        year: vehicle.year ? String(vehicle.year) : '',
        notes: '',
      });
    } else {
      setDraft(EMPTY);
    }
  }, [open, vehicle]);

  const set = (k: keyof Draft) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => setDraft((d) => ({ ...d, [k]: e.target.value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving || !draft.unit_number.trim()) return;
    setSaving(true);
    setError('');
    const payload = {
      unit_number: draft.unit_number.trim(),
      vehicle_type: draft.vehicle_type,
      company_code: draft.company_code.trim(),
      vin: draft.vin.trim(),
      plate_number: draft.plate_number.trim(),
      make: draft.make.trim(),
      model: draft.model.trim(),
      year: draft.year ? Number(draft.year) : null,
      notes: draft.notes.trim(),
    };
    try {
      if (isEdit) {
        await apiJSON(`/vehicles/registry/${editId}`, { method: 'PUT', body: payload });
      } else {
        await apiJSON('/vehicles/', { method: 'POST', body: payload });
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    if (!isEdit || removing) return;
    if (!confirm(`Remove ${draft.unit_number}? Its history (maintenance, fuel, inspections) is kept.`)) return;
    setRemoving(true);
    setError('');
    try {
      await apiJSON(`/vehicles/registry/${editId}`, { method: 'DELETE' });
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setRemoving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit vehicle' : 'Add vehicle'}</DialogTitle>
          <DialogDescription>
            Vehicles live in your 4truck registry. Telematics (Samsara)
            enriches them — a trailer or a truck without a device works
            here on its own.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Unit number</label>
              <Input value={draft.unit_number} onChange={set('unit_number')} placeholder="247" required autoFocus />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Type</label>
              <select value={draft.vehicle_type} onChange={set('vehicle_type')} className={inputCls}>
                {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Company code</label>
              <Input value={draft.company_code} onChange={set('company_code')} placeholder="PTG" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Year</label>
              <Input value={draft.year} onChange={set('year')} placeholder="2027" inputMode="numeric" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">VIN</label>
              <Input value={draft.vin} onChange={set('vin')} placeholder="3AKJJHDR7VSXC469" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Plate</label>
              <Input value={draft.plate_number} onChange={set('plate_number')} placeholder="PXF8448" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Make</label>
              <Input value={draft.make} onChange={set('make')} placeholder="Freightliner" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Model</label>
              <Input value={draft.model} onChange={set('model')} placeholder="Cascadia" />
            </div>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Notes</label>
            <Input value={draft.notes} onChange={set('notes')} placeholder="optional" />
          </div>

          {error && <p className="text-xs text-danger">{error}</p>}

          <DialogFooter className="flex items-center justify-between gap-2">
            {isEdit ? (
              <Button type="button" variant="ghost" size="sm" onClick={handleRemove} disabled={removing} className="text-danger">
                {removing ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Remove
              </Button>
            ) : <span />}
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="sm" onClick={onClose}>Cancel</Button>
              <Button type="submit" size="sm" disabled={saving || !draft.unit_number.trim()}>
                {saving ? <Loader2 size={14} className="animate-spin" /> : null}
                {isEdit ? 'Save' : 'Add vehicle'}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
