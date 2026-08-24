/**
 * Vehicle Info card — VIN / make / model / year / plate / fuel /
 * odometer / engine hours.  Universal: every persona wants the basic
 * truck identity, even if their primary concern is elsewhere.
 *
 * Shares the ``['vehicle', name]`` query key with VehicleLocation;
 * TanStack Query dedupes so the /vehicles/{name} endpoint is hit once
 * regardless of how many sections need it.
 */
import StatusBadge from '../../../components/StatusBadge';
import { CardSkeleton } from '../../../components/shell';
import { Row } from './_shared/Row';
import { CalloutInline } from '../../../components/callouts';
import { useVehicle, useVehicleCallouts } from './_shared/useVehicle';
import type { VehicleSectionProps } from './_shared/types';
import { Card } from '@/components/ui/card';

export default function VehicleInfo({ vehicleName, company }: VehicleSectionProps) {
  const { vehicle: v, isLoading } = useVehicle(vehicleName, company);
  const callouts = useVehicleCallouts(vehicleName, company);

  if (isLoading || !v) return <CardSkeleton height="h-64" />;

  const fuel = v.fuelPercent ?? v.fuel_percent;
  // The condition that explains an empty engine reading.  Only this
  // key qualifies: a caveat about mileage says nothing about why the
  // odometer field itself is blank.
  // ``explained`` on every note below: VehicleHeader renders this
  // same callout as a strip at the top of the page, so the rows are
  // pointers to an explanation the reader already has.  Repeating
  // the paragraph behind each empty field put it on screen nine
  // times for one truck.
  const blindCallout =
    callouts.find((c) => c.key === 'vehicle.no_engine_data') ?? null;
  const defPct = v.defPercent ?? v.def_percent;

  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold mb-3">Vehicle Info</h2>
      <Row label="VIN" value={v.vin} />
      <Row label="Make / Model" value={[v.make, v.model].filter(Boolean).join(' ') || '—'} />
      <Row label="Year" value={v.year} />
      <Row label="License Plate" value={v.licensePlate || v.license_plate} />
      <Row label="Company" value={v._org || v.company} />
      {/* Per-metric ``ts`` — each reading carries its OWN Samsara clock
          (fuel can be days staler than GPS on the same truck); the Row
          wraps the value in the Freshness tooltip + staleness cue. */}
      {/* NOT `|| 'Off'`.  The backend deliberately refuses to guess:
          resolve_engine_state returns UNKNOWN ("") for a truck with no
          engine feed, precisely so silence is never counted as parked.
          Falling back to "Off" here re-told that lie on screen — this
          truck's engine badge read "Off" while nothing could see the
          engine at all. */}
      <Row label="Engine" ts={v.location?.time}>
        <StatusBadge status={v.engineState || v.engine_state || 'unknown'} />
      </Row>
      <Row label="Fuel" ts={v.fuel?.time}>
        {fuel != null ? (
          <span>{`${Math.round(fuel)}%`}</span>
        ) : blindCallout ? (
          <CalloutInline callout={blindCallout} explained />
        ) : (
          <span>—</span>
        )}
      </Row>
      {defPct != null && <Row label="DEF" ts={v.def_level?.time} value={`${Math.round(defPct)}%`} />}
      {/* Odometer + engine hours both come from the warehouse via
          /api/vehicles/{name}, refreshed every 60s by
          ingest_vehicle_state.  Always rendered so users can see the
          field is tracked even before the first reading lands — and
          when the device is the reason it is empty, the bare "—" is
          replaced by the callout that says so, because an unexplained
          dash reads as OUR bug rather than a truck's wiring. */}
      <Row label="Odometer" ts={v.odometer_time}>
        {v.odometer_miles != null ? (
          <span>{`${Math.round(v.odometer_miles).toLocaleString()} mi`}</span>
        ) : blindCallout ? (
          <CalloutInline callout={blindCallout} explained />
        ) : (
          <span>—</span>
        )}
      </Row>
      <Row label="Engine Hours" ts={v.engine_hours_time}>
        {v.engine_hours != null ? (
          <span>{`${Math.round(v.engine_hours).toLocaleString()} h`}</span>
        ) : blindCallout ? (
          <CalloutInline callout={blindCallout} explained />
        ) : (
          <span>—</span>
        )}
      </Row>
    </Card>
  );
}
