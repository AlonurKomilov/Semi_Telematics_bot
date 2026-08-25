/**
 * Device-identity events — the watch's resolution card.
 *
 * The ingest watches the identity anchors behind every telematics id
 * (VIN, gateway serial, odometer scale) and records changes.  An open
 * vin_change asks a question only a human can answer: is this the same
 * truck (VIN corrected) or a different truck behind the gateway?
 * "Different truck" performs the registry split server-side: a new
 * unit is created with the new VIN, the telematics link moves onto it,
 * and the old unit keeps its true VIN and all of its history.
 *
 * Rendered on the Vehicles page for holders of ``can_manage_vehicles``
 * only while at least one event is open; resolved events stay in the
 * event log (server-side) but this card shows only what needs a human.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Callout, type CalloutData } from '../../components/callouts';
import type { Vehicle } from '../../types';

export interface DeviceEvent {
  id: number;
  registry_id: number | null;
  vehicle_id: string;
  vehicle_name: string;
  kind: string;
  old_value: string;
  new_value: string;
  observed_at: string;
  status: string;
  resolution: string;
  resolved_at: string;
}

interface EventsResponse {
  events: DeviceEvent[];
  open_count: number;
}

/** ``device_event_log.kind`` → the callout key that renders it.
 *
 *  The event vocabulary predates the callouts lane and is stored in
 *  the log's rows, so it is MAPPED here rather than renamed — the
 *  backend keeps saying ``vin_change`` and the reader sees one shape
 *  of statement across the whole page.  Mirrors
 *  ``EVENT_CALLOUT_KEYS`` in features/vehicles/callouts.py. */
const EVENT_CALLOUT_KEY: Record<string, string> = {
  vin_change: 'vehicle.vin_changed',
  gateway_swap: 'vehicle.gateway_swapped',
  odo_rebase: 'vehicle.odometer_rebased',
};

/** One event as the callouts lane wants it.  ``params`` feed the copy
 *  ("{{old}} → {{new}}"), so the values a person needs to judge the
 *  change stay in the sentence rather than in a separate mono column. */
function eventCallout(e: DeviceEvent, scoped: boolean): CalloutData | null {
  const key = EVENT_CALLOUT_KEY[e.kind];
  if (!key) return null;
  return {
    key,
    // Same entity shape the vehicles endpoints emit, so a dismissal or
    // a trail entry lands on the truck either way.
    entity: `vehicle:${e.vehicle_id}`,
    since: e.observed_at,
    callout_id: `${key}@vehicle:${e.vehicle_id}#${e.observed_at}`,
    params: {
      old: e.old_value,
      new: e.new_value,
      // The `where` line answers "which truck?" — a question the
      // account-wide list leaves open and a truck's own page has
      // already answered.  Empty resolves the line to nothing and the
      // strip omits it, so the copy needs no scoped variant.
      unit: scoped ? '' : e.vehicle_name || e.vehicle_id,
    },
  };
}

export default function DeviceEventsCard({
  canManage, vehicles, onResolved, vehicleName,
}: {
  canManage: boolean;
  /** The page's vehicle list — powers the company datalist. */
  vehicles: Vehicle[];
  /** Refetch hook so the vehicle list reflects a split immediately. */
  onResolved: () => void;
  /**
   * Narrow to ONE truck's questions.
   *
   * Omitted on the vehicles list, where the card is the account-wide
   * review queue.  Set on a vehicle's own page, because a question
   * about THAT truck belongs where someone is already looking at it —
   * finding it only by scrolling a fleet-wide list is how a VIN change
   * sits unanswered for weeks.  Filtered client-side: the endpoint
   * returns only open events (capped at 100) and the answer flow is
   * shared, so a second query would buy nothing.
   */
  vehicleName?: string;
}) {
  const { data, refetch } = useQuery<EventsResponse>({
    queryKey: ['device-events'],
    queryFn: () => apiJSON<EventsResponse>('/vehicles/device-events'),
    enabled: canManage,
    staleTime: 60_000,
  });
  const [busyId, setBusyId] = useState<number | null>(null);
  const [splitEvent, setSplitEvent] = useState<DeviceEvent | null>(null);
  const [error, setError] = useState('');

  const open = (data?.events ?? [])
    .filter((e) => e.status === 'open')
    .filter((e) => !vehicleName || e.vehicle_name === vehicleName);
  if (!canManage || open.length === 0) return null;

  const resolveSimple = async (e: DeviceEvent, action: 'same_truck' | 'dismissed') => {
    setBusyId(e.id); setError('');
    try {
      await apiJSON(`/vehicles/device-events/${e.id}/resolve`, {
        method: 'POST', body: JSON.stringify({ action }),
      });
      await refetch();
      onResolved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not resolve the event');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mb-4">
      {/* No card and no icon-plus-title of its own: each strip below
          already carries both, and wrapping them repeated the warning
          icon, the heading and the border around a single statement.
          A plain label survives only to group several. */}
      {open.length > 1 && (
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Device changes to review
        </p>
      )}
      {/* Rendered through the callouts lane so a device question wears
          the same shape as every other statement on the page — one
          vocabulary for the reader.  The DATA and the answer flow stay
          here: the buttons below perform registry surgery, which the
          lane must never learn about, so they ride its actions slot. */}
      <ul className="space-y-2">
        {open.map((e) => {
          const c = eventCallout(e, Boolean(vehicleName));
          if (!c) return null;
          return (
            <li key={e.id}>
              <Callout
                callout={c}
                entity={{ type: 'vehicle', id: e.vehicle_name || e.vehicle_id }}
                actions={
                  e.kind === 'vin_change' ? (
                    <>
                      <Button
                        type="button" variant="outline" size="sm"
                        disabled={busyId === e.id}
                        onClick={() => resolveSimple(e, 'same_truck')}
                      >
                        {busyId === e.id && <Loader2 className="animate-spin" />}
                        Same truck
                      </Button>
                      {/* Outline, NOT the filled primary it used to
                          be.  Splitting mints a new unit, moves the
                          telematics ref and forks the truck's history —
                          the hard-to-reverse answer of the two.  Giving
                          it the button that reads as "the default"
                          nudged toward it; both answers now carry the
                          same weight so the choice is made on the VIN
                          evidence above, not on styling. */}
                      <Button
                        type="button" variant="outline" size="sm"
                        disabled={busyId === e.id}
                        onClick={() => { setError(''); setSplitEvent(e); }}
                      >
                        Different truck…
                      </Button>
                    </>
                  ) : (
                    <Button
                      type="button" variant="outline" size="sm"
                      disabled={busyId === e.id}
                      onClick={() => resolveSimple(e, 'dismissed')}
                    >
                      {busyId === e.id && <Loader2 className="animate-spin" />}
                      Dismiss
                    </Button>
                  )
                }
              />
            </li>
          );
        })}
      </ul>
      {error && !splitEvent && (
        <p className="mt-2 text-xs text-destructive">{error}</p>
      )}

      {splitEvent && (
        <SplitDialog
          event={splitEvent}
          vehicles={vehicles}
          onClose={() => setSplitEvent(null)}
          onDone={async () => {
            setSplitEvent(null);
            await refetch();
            onResolved();
          }}
        />
      )}
    </div>
  );
}

function SplitDialog({
  event, vehicles, onClose, onDone,
}: {
  event: DeviceEvent;
  vehicles: Vehicle[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [company, setCompany] = useState(() => {
    const owner = vehicles.find((v) => v.registry_id === event.registry_id);
    return owner?.company ?? '';
  });
  const [unit, setUnit] = useState('');
  const [archiveOld, setArchiveOld] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const companies = useMemo(
    () => [...new Set(vehicles.map((v) => v.company).filter(Boolean))].sort(),
    [vehicles],
  );
  const oldName = event.vehicle_name || event.vehicle_id;

  const submit = async () => {
    setSaving(true); setError('');
    try {
      await apiJSON(`/vehicles/device-events/${event.id}/resolve`, {
        method: 'POST',
        body: JSON.stringify({
          action: 'different_truck',
          company_code: company.trim(),
          unit_number: unit.trim(),
          archive_old: archiveOld,
        }),
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Split failed');
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !saving) onClose(); }}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>A different truck is behind {oldName}</DialogTitle>
          <DialogDescription>
            Name the new truck. It takes over the telematics link and the
            new VIN ({event.new_value}); {oldName} keeps its own VIN and
            all of its history, with no telematics until re-linked.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label htmlFor="split-company" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Company
            </label>
            <Input
              id="split-company" list="split-company-options"
              value={company} maxLength={64}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. PTG"
            />
            <datalist id="split-company-options">
              {companies.map((c) => <option key={c} value={c} />)}
            </datalist>
          </div>
          <div>
            <label htmlFor="split-unit" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              New unit number
            </label>
            <Input
              id="split-unit" value={unit} maxLength={64}
              onChange={(e) => setUnit(e.target.value)}
              placeholder="the number on the new truck's door"
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox" checked={archiveOld}
              onChange={(e) => setArchiveOld(e.target.checked)}
            />
            Also retire {oldName} — it has left the account
          </label>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={saving || !unit.trim()} onClick={submit}>
            {saving && <Loader2 className="animate-spin" />}
            Create unit &amp; move link
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
