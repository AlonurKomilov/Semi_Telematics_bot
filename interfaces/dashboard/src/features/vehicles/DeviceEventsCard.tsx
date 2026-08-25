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
import { AlertTriangle, Loader2 } from 'lucide-react';

import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { toneText } from '../../lib/status';
import type { Vehicle } from '../../types';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

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

const KIND_LABEL: Record<string, string> = {
  vin_change: 'VIN changed — is a different truck behind this unit?',
  gateway_swap: 'Telematics gateway swapped',
  odo_rebase: 'Odometer changed scale',
};

export default function DeviceEventsCard({
  canManage, vehicles, onResolved,
}: {
  canManage: boolean;
  /** The page's vehicle list — powers the company datalist. */
  vehicles: Vehicle[];
  /** Refetch hook so the vehicle list reflects a split immediately. */
  onResolved: () => void;
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

  const open = (data?.events ?? []).filter((e) => e.status === 'open');
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
    <Card className="mb-4">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className={cn(toneText('warn'), 'size-4')} />
        <h3 className="text-base font-semibold">
          Device change{open.length === 1 ? '' : 's'} to review
        </h3>
      </div>
      <ul className="space-y-3">
        {open.map((e) => (
          <li key={e.id} className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <Badge tone="warn">
              {e.vehicle_name || e.vehicle_id}
            </Badge>
            <span className="text-sm">
              {KIND_LABEL[e.kind] ?? e.kind}
            </span>
            <span className="text-xs text-muted-foreground font-mono">
              {e.old_value} → {e.new_value}
            </span>
            <span className="flex items-center gap-2 ml-auto">
              {e.kind === 'vin_change' ? (
                <>
                  <Button
                    type="button" variant="outline" size="sm"
                    disabled={busyId === e.id}
                    onClick={() => resolveSimple(e, 'same_truck')}
                  >
                    {busyId === e.id && <Loader2 className="animate-spin" />}
                    Same truck
                  </Button>
                  <Button
                    type="button" size="sm"
                    disabled={busyId === e.id}
                    onClick={() => { setError(''); setSplitEvent(e); }}
                  >
                    Different truck…
                  </Button>
                </>
              ) : (
                /* "Dismiss", not "Acknowledge".  Acknowledging is the
                   ALERTS workspace action — a person accepting a work
                   item that was routed to them.  Nothing is routed
                   here and nothing is accepted: this row asks for no
                   decision, so the button only takes it off the list.
                   Using the alerts verb made a passive notice look
                   like assigned work. */
                <Button
                  type="button" variant="outline" size="sm"
                  disabled={busyId === e.id}
                  onClick={() => resolveSimple(e, 'dismissed')}
                >
                  {busyId === e.id && <Loader2 className="animate-spin" />}
                  Dismiss
                </Button>
              )}
            </span>
          </li>
        ))}
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
    </Card>
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
