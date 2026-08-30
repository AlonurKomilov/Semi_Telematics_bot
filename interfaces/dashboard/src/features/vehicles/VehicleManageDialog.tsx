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

import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RotateCcw, Sparkles, Trash2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { toneClasses } from '../../lib/status';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import type { Vehicle } from '../../types';
import { ActivityTrailDialog, ActivityTrailTrigger } from '../../components/activity-trail/ActivityTrailDialog';

const TYPES = [
  { value: 'truck', label: 'Truck' },
  { value: 'trailer', label: 'Trailer' },
  { value: 'other', label: 'Other' },
];

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

/** The wire says `manual`; a person reading it gets "Local" — the same
 *  word the Source column uses.  One value, one name. */
const sourceLabel = (x: string) =>
  x === 'manual' ? 'Local' : x.charAt(0).toUpperCase() + x.slice(1);

/** "Samsara" · "Samsara and Datatruck" · "A, B and C" */
const joinNames = (xs: string[]) =>
  xs.length <= 1
    ? (xs[0] ?? '')
    : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;

/** What the typed identity already matches in the registry.  `exists`
 *  and `archived` are unit+company hits; `vin` is the same physical
 *  truck under another number; `ambiguous` means the unit number alone
 *  cannot say which truck is meant. */
type RegistryMatch =
  | { kind: 'exists' | 'archived' | 'vin'; vehicle: Vehicle }
  | { kind: 'ambiguous'; vehicles: Vehicle[] }
  | null;

const EMPTY: Draft = {
  unit_number: '', vehicle_type: 'truck', company_code: '', vin: '',
  plate_number: '', make: '', model: '', year: '', notes: '',
};

export default function VehicleManageDialog({
  open, vehicle, existingVehicles = [], onClose, onSaved,
}: {
  open: boolean;
  /** The row being edited, or null to create a new one. */
  vehicle: Vehicle | null;
  /** The account's existing vehicles (the registry-backed /vehicles
   *  list).  Powers the unit-number autocomplete + "already in your
   *  vehicles" detection so the operator doesn't re-add one an
   *  integration already knows.  Role-neutral: this is the account's
   *  vehicle registry, not a fleet-role concept. */
  existingVehicles?: Vehicle[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [error, setError] = useState('');
  // When the operator typing a NEW unit hits one that already exists,
  // they can promote the dialog to edit that row (pre-filled from its
  // Samsara details) instead of creating a duplicate.
  const [promoted, setPromoted] = useState<Vehicle | null>(null);
  const [pulling, setPulling] = useState(false);
  const [restoring, setRestoring] = useState(false);

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
      // One possible company = not a question worth asking.  This is
      // also the company-restricted user's case: their rows carry one
      // code, so the field they MUST fill (the API refuses a blank one
      // from them) arrives filled.
      const opts = companyOptionsRef.current;
      setDraft({ ...EMPTY, company_code: opts.length === 1 ? opts[0] : '' });
    }
  }, [open, target]);

  // What the typed identity already matches.  The registry MIRRORS
  // every provider vehicle, so this answers "does Samsara already have
  // this truck?" with no provider round-trip — and instantly, from
  // rows the page already holds.
  //
  // A unit number is a LABEL, reused across companies (this account
  // runs 001 in two of them and 103 in three).  Matching it without
  // the company is how this dialog offered the WRONG truck: the same
  // identity-vs-label mistake that once mis-linked four devices.  So
  // the company must agree when it is typed, and when it is not, only
  // an unambiguous single row can be meant.
  const registryMatch = useMemo<RegistryMatch>(() => {
    if (isEdit) return null;
    const unit = draft.unit_number.trim().toLowerCase();
    const company = draft.company_code.trim().toLowerCase();
    const vin = draft.vin.trim().toUpperCase();

    if (unit) {
      const named = existingVehicles.filter(
        (v) => (v.name ?? '').trim().toLowerCase() === unit,
      );
      const scoped = company
        ? named.filter(
            (v) => (v.company ?? v._org ?? '').trim().toLowerCase() === company)
        : named;
      if (scoped.length === 1) {
        const hit = scoped[0];
        return { kind: hit.archived ? 'archived' : 'exists', vehicle: hit };
      }
      if (!company && named.length > 1) {
        return { kind: 'ambiguous', vehicles: named };
      }
    }
    // A VIN names the PHYSICAL truck, so it matches across companies
    // and across the archive line — this is the check that catches a
    // second row for a truck we already have.  Length-guarded: a
    // half-typed VIN must not claim a match, and list rows carry the
    // literal "N/A" where a provider exposes none.
    if (vin.length >= 11 && vin !== 'N/A') {
      const byVin = existingVehicles.find(
        (v) => (v.vin ?? '').trim().toUpperCase() === vin);
      if (byVin) return { kind: 'vin', vehicle: byVin };
    }
    return null;
  }, [isEdit, draft.unit_number, draft.company_code, draft.vin,
      existingVehicles]);

  // The company codes this operator can actually mean, from the rows
  // in hand — which the API already scoped to their access, so a
  // PTG-only user is never shown OSY.  Deliberately NOT /admin/companies:
  // that needs can_manage_companies (a gate this dialog does not want)
  // and returns the account's full list, which would name companies to
  // someone walled off from them.
  //
  // It is a datalist, so it SUGGESTS without restricting — the first
  // truck of a brand-new company (no rows yet, nothing to derive from)
  // is still typed in freely.
  const companyOptions = useMemo(() => {
    const found = new Set<string>();
    for (const v of existingVehicles) {
      const c = (v.company ?? v._org ?? '').trim();
      if (c) found.add(c);
    }
    return [...found].sort();
  }, [existingVehicles]);

  // Read through a ref by the reset effect below: depending on the
  // array directly would re-run that effect on every background
  // refetch and wipe a half-typed form.
  const companyOptionsRef = useRef(companyOptions);
  useEffect(() => { companyOptionsRef.current = companyOptions; },
            [companyOptions]);

  // Which providers actually supply vehicles here — derived from the
  // rows in hand (each carries its creator + enrichers), so it costs
  // no fetch and no config permission, and it states what HAS happened
  // rather than what is merely configured.
  const supplying = useMemo(() => {
    const found = new Set<string>();
    for (const v of existingVehicles) {
      for (const x of v.sources ?? []) if (x && x !== 'manual') found.add(x);
    }
    return [...found].sort().map(sourceLabel);
  }, [existingVehicles]);

  // Pull the matched vehicle's full Samsara spec and switch to editing
  // it — the comfort helper: VIN / make / model / plate fill in.
  const pullExisting = async (match: Vehicle) => {
    if (pulling) return;
    setPulling(true);
    setError('');
    try {
      const name = match.name ?? '';
      const company = match.company ?? match._org ?? '';
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
        // Carry the registry_id from the matched row (the detail
        // endpoint is keyed by name; the registry id rides on the row).
        registry_id: match.registry_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load details');
    } finally {
      setPulling(false);
    }
  };

  // The archived truck IS the row being asked for — bring it back
  // instead of minting a second one that would collide on the unique
  // (company, unit) anyway.  Its history and documents come with it.
  const restoreArchived = async (match: Vehicle) => {
    if (restoring || match.registry_id == null) return;
    setRestoring(true);
    setError('');
    try {
      await apiJSON(`/vehicles/registry/${match.registry_id}/restore`,
                    { method: 'POST' });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Restore failed');
    } finally {
      setRestoring(false);
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
            Vehicles live in your 4truck registry. A trailer or a truck
            with no device works here on its own; telematics enriches a
            truck it recognises.
          </DialogDescription>
        </DialogHeader>

        {/* Who contributed to this record — created-by plus every
            provider owning a field, derived server-side from
            provenance.  One truck can be created by one integration
            and enriched by another; the single `source` value cannot
            say that, and for weeks it actively misreported (any
            Samsara tick rewrote it).  Read-only: this states facts
            about the data, it is not a choice. */}
        {isEdit && (target?.sources?.length ?? 0) > 0 && (
          <p className="text-xs text-muted-foreground">
            Sources:{' '}
            <span className="text-foreground">
              {target!.sources!
                .map(sourceLabel)
                .join(' · ')}
            </span>
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Unit number</label>
              <Input
                value={draft.unit_number} onChange={set('unit_number')}
                placeholder="247" required autoFocus
                list={isEdit ? undefined : 'registry-units'}
                autoComplete="off"
              />
              {!isEdit && (
                <datalist id="registry-units">
                  {existingVehicles.map((v) => (
                    <option key={`${v.company}-${v.name}`} value={v.name ?? ''} />
                  ))}
                </datalist>
              )}
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Type</label>
              <Select value={draft.vehicle_type} onValueChange={(v) => setDraft((d) => ({ ...d, vehicle_type: v }))} items={TYPES}>
                <SelectTrigger className="w-full" aria-label="Type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TYPES.map((tp) => <SelectItem key={tp.value} value={tp.value}>{tp.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Company code</label>
              <Input
                value={draft.company_code} onChange={set('company_code')}
                placeholder="PTG"
                list={isEdit ? undefined : 'registry-companies'}
                autoComplete="off"
              />
              {!isEdit && (
                <datalist id="registry-companies">
                  {companyOptions.map((c) => <option key={c} value={c} />)}
                </datalist>
              )}
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Year</label>
              <Input value={draft.year} onChange={set('year')} placeholder="2027" inputMode="numeric" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">VIN</label>
              <Input value={draft.vin} onChange={set('vin')} placeholder="1HGTEST0000000001" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Plate</label>
              <Input value={draft.plate_number} onChange={set('plate_number')} placeholder="ABC1234" />
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

          {/* The typed identity already names something we have.  Each
              state gets the action that actually resolves it — and the
              ambiguous one gets no action at all, because the only
              honest answer there is "tell me which company". */}
          {registryMatch?.kind === 'ambiguous' && (
            <div className={`${toneClasses('info')} rounded-md px-2.5 py-2 text-2xs`}>
              {registryMatch.vehicles.length} vehicles are numbered{' '}
              <span className="font-medium">{draft.unit_number.trim()}</span>
              {' '}({registryMatch.vehicles
                .map((v) => v.company || '—').join(', ')}). Type the company
              code so this matches the right one.
            </div>
          )}

          {registryMatch?.kind === 'exists' && (
            <div className={`${toneClasses('info')} rounded-md px-2.5 py-2 text-2xs flex items-center justify-between gap-2`}>
              <span>
                Unit <span className="font-medium">{registryMatch.vehicle.name}</span>
                {registryMatch.vehicle.company ? ` (${registryMatch.vehicle.company})` : ''}
                {' '}already exists
                {(registryMatch.vehicle.sources?.length ?? 0) > 0
                  ? ` — ${registryMatch.vehicle.sources!.map(sourceLabel).join(' · ')}`
                  : ''}. Use its details instead of adding a second row.
              </span>
              <Button
                type="button" variant="outline" size="xs"
                onClick={() => pullExisting(registryMatch.vehicle)} disabled={pulling}
              >
                {pulling ? <Loader2 className="animate-spin" /> : <Sparkles />}
                Use its details
              </Button>
            </div>
          )}

          {registryMatch?.kind === 'vin' && (
            <div className={`${toneClasses('warn')} rounded-md px-2.5 py-2 text-2xs flex items-center justify-between gap-2`}>
              <span>
                This VIN is already on unit{' '}
                <span className="font-medium">{registryMatch.vehicle.name}</span>
                {registryMatch.vehicle.company ? ` (${registryMatch.vehicle.company})` : ''}
                {' '}— the same truck, under another number. Adding this row
                would give one truck two records.
              </span>
              <Button
                type="button" variant="outline" size="xs"
                onClick={() => pullExisting(registryMatch.vehicle)} disabled={pulling}
              >
                {pulling ? <Loader2 className="animate-spin" /> : <Sparkles />}
                Use its details
              </Button>
            </div>
          )}

          {registryMatch?.kind === 'archived' && (
            <div className={`${toneClasses('warn')} rounded-md px-2.5 py-2 text-2xs flex items-center justify-between gap-2`}>
              <span>
                Unit <span className="font-medium">{registryMatch.vehicle.name}</span>
                {registryMatch.vehicle.company ? ` (${registryMatch.vehicle.company})` : ''}
                {' '}is archived. Restore it — its history and documents come
                back with it — instead of adding a second row.
              </span>
              <Button
                type="button" variant="outline" size="xs"
                onClick={() => restoreArchived(registryMatch.vehicle)}
                disabled={restoring}
              >
                {restoring ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                Restore
              </Button>
            </div>
          )}

          {/* What happens after Add — the question this dialog never
              answered.  Derived from the rows in hand, so it names the
              providers that really supply this account rather than a
              provider hardcoded in the copy. */}
          {!isEdit && !registryMatch && (
            <p className="text-2xs text-muted-foreground">
              {supplying.length > 0 ? (
                <>
                  {joinNames(supplying)}{' '}
                  {supplying.length > 1 ? 'supply' : 'supplies'} vehicles here.
                  If {supplying.length > 1 ? 'one of them' : 'it'} reports this
                  unit in this company — or this VIN — the truck links
                  automatically, and Local stays its creator.
                </>
              ) : (
                <>
                  No integration supplies vehicles here yet, so this truck
                  stays Local until one reports it.
                </>
              )}
            </p>
          )}

          {error && <p className="text-xs text-danger">{error}</p>}

          {isEdit && vehicle && (
            <div className="pt-1">
              <ActivityTrailTrigger onClick={() => setHistoryOpen(true)} />
            </div>
          )}

          <DialogFooter justify="between">
            {isEdit ? (
              <Button type="button" variant="ghost" size="sm" onClick={handleRemove} disabled={removing} className="text-danger">
                {removing ? <Loader2 className="animate-spin" /> : <Trash2 />}
                Remove
              </Button>
            ) : <span />}
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="sm" onClick={onClose}>Cancel</Button>
              <Button type="submit" size="sm" disabled={saving || !draft.unit_number.trim()}>
                {saving ? <Loader2 className="animate-spin" /> : null}
                {isEdit ? 'Save' : 'Add vehicle'}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
      <ActivityTrailDialog
        entityType="vehicle"
        entityId={editId}
        title={`Vehicle ${draft.unit_number || ''} — activity history`}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />
    </Dialog>
  );
}
