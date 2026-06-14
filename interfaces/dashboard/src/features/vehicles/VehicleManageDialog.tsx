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

import { useEffect, useMemo, useState } from 'react';
import { Loader2, Sparkles, Trash2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { toneClasses } from '../../lib/status';
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
  open, vehicle, fleet = [], onClose, onSaved,
}: {
  open: boolean;
  /** The row being edited, or null to create a new one. */
  vehicle: Vehicle | null;
  /** The account's current fleet (registry-backed /vehicles list).
   *  Powers the unit-number autocomplete + "already in your fleet"
   *  detection so the operator doesn't re-add a vehicle Samsara
   *  already knows. */
  fleet?: Vehicle[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState('');
  // When the operator typing a NEW unit hits one that already exists,
  // they can promote the dialog to edit that row (pre-filled from its
  // Samsara details) instead of creating a duplicate.
  const [promoted, setPromoted] = useState<Vehicle | null>(null);
  const [pulling, setPulling] = useState(false);

  const target = vehicle ?? promoted;
  const editId = target?.registry_id ?? null;
  const isEdit = editId != null;

  useEffect(() => {
    if (!open) return;
    setError('');
    if (!vehicle) setPromoted(null);
  }, [open, vehicle]);

  useEffect(() => {
    if (!open) return;
    if (target) {
      setDraft({
        unit_number: target.name ?? '',
        vehicle_type: target.vehicle_type ?? 'truck',
        company_code: target.company ?? target._org ?? '',
        vin: target.vin ?? '',
        plate_number: target.license_plate ?? target.licensePlate ?? '',
        make: target.make ?? '',
        model: target.model ?? '',
        year: target.year ? String(target.year) : '',
        notes: '',
      });
    } else {
      setDraft(EMPTY);
    }
  }, [open, target]);

  // In create mode, does the typed unit already exist in the fleet?
  const existingMatch = useMemo(() => {
    if (isEdit) return null;
    const u = draft.unit_number.trim().toLowerCase();
    if (!u) return null;
    return fleet.find((v) => (v.name ?? '').trim().toLowerCase() === u) ?? null;
  }, [isEdit, draft.unit_number, fleet]);

  // Pull the matched vehicle's full Samsara spec and switch to editing
  // it — the comfort helper: VIN / make / model / plate fill in.
  const pullExisting = async () => {
    if (!existingMatch || pulling) return;
    setPulling(true);
    setError('');
    try {
      const name = existingMatch.name ?? '';
      const company = existingMatch.company ?? existingMatch._org ?? '';
      const qs = company ? `?company=${encodeURIComponent(company)}` : '';
      const detail = await apiJSON<Vehicle>(
        `/vehicles/${encodeURIComponent(name)}${qs}`,
      );
      // The detail endpoint returns "N/A" for static fields a Samsara
      // plan doesn't expose — scrub those to empty so they don't land
      // as literal "N/A" in the form.
      const clean = (s?: string | null) => (s && s !== 'N/A' ? s : '');
      setPromoted({
        ...detail,
        vin: clean(detail.vin),
        make: clean(detail.make),
        model: clean(detail.model),
        license_plate: clean(detail.license_plate ?? detail.licensePlate),
        // Carry the registry_id from the fleet row (the detail endpoint
        // is keyed by name; the registry id rides on the list row).
        registry_id: existingMatch.registry_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load details');
    } finally {
      setPulling(false);
    }
  };

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
              <Input
                value={draft.unit_number} onChange={set('unit_number')}
                placeholder="247" required autoFocus
                list={isEdit ? undefined : 'fleet-units'}
                autoComplete="off"
              />
              {!isEdit && (
                <datalist id="fleet-units">
                  {fleet.map((v) => (
                    <option key={`${v.company}-${v.name}`} value={v.name ?? ''} />
                  ))}
                </datalist>
              )}
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

          {/* Already-in-fleet helper: the typed unit matches a vehicle
              Samsara already reports.  Offer to pull its details and
              edit it rather than create a duplicate (which would 409). */}
          {existingMatch && (
            <div className={`${toneClasses('info')} rounded px-2.5 py-2 text-2xs flex items-center justify-between gap-2`}>
              <span>
                Unit <span className="font-medium">{existingMatch.name}</span> is
                already in your fleet
                {existingMatch.company ? ` (${existingMatch.company})` : ''} —
                pull its details to edit instead of adding a duplicate.
              </span>
              <Button
                type="button" variant="outline" size="xs"
                onClick={pullExisting} disabled={pulling}
              >
                {pulling ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                Use its details
              </Button>
            </div>
          )}

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
