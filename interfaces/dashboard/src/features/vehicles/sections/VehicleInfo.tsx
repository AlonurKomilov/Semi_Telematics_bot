/**
 * Vehicle Info card — VIN / make / model / year / plate / fuel /
 * odometer / engine hours.  Universal: every persona wants the basic
 * truck identity, even if their primary concern is elsewhere.
 *
 * Shares the ``['vehicle', name]`` query key with VehicleLocation;
 * TanStack Query dedupes so the /vehicles/{name} endpoint is hit once
 * regardless of how many sections need it.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import StatusBadge from '../../../components/StatusBadge';
import { CardSkeleton } from '../../../components/shell';
import type { Vehicle, VehiclesResponse } from '../../../types';
import { Row } from './_shared/Row';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleInfo({ vehicleName, company }: VehicleSectionProps) {
  const { data: v, isLoading } = useQuery<Vehicle | null>({
    // ``company`` is part of the query key so the cache keeps a
    // separate entry for each (name, company) pair — opening a
    // different "103" never recycles the previous one's data.
    queryKey: ['vehicle', vehicleName, company ?? ''],
    queryFn: async () => {
      const qs = company ? `?company=${encodeURIComponent(company)}` : '';
      const res = await apiJSON<VehiclesResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}${qs}`,
      );
      return res.vehicles?.[0] ?? null;
    },
    staleTime: 30_000,
  });

  if (isLoading || !v) return <CardSkeleton height="h-64" />;

  const fuel = v.fuelPercent ?? v.fuel_percent;
  const defPct = v.defPercent ?? v.def_percent;

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-3">
      <h2 className="text-lg font-semibold mb-3">Vehicle Info</h2>
      <Row label="VIN" value={v.vin} />
      <Row label="Make / Model" value={[v.make, v.model].filter(Boolean).join(' ') || '—'} />
      <Row label="Year" value={v.year} />
      <Row label="License Plate" value={v.licensePlate || v.license_plate} />
      <Row label="Company" value={v._org || v.company} />
      <Row label="Engine">
        <StatusBadge status={v.engineState || v.engine_state || 'Off'} />
      </Row>
      <Row label="Fuel" value={fuel != null ? `${Math.round(fuel)}%` : '—'} />
      {defPct != null && <Row label="DEF" value={`${Math.round(defPct)}%`} />}
      {/* Odometer + engine hours both come from the warehouse via
          /api/vehicles/{name}, refreshed every 60s by
          ingest_vehicle_state.  Always rendered (with "—" when the
          vehicle has no CAN bus gateway / Samsara plan doesn't
          expose the signal yet) so users can see the field is
          tracked even before the first reading lands. */}
      <Row
        label="Odometer"
        value={
          v.odometer_miles != null
            ? `${Math.round(v.odometer_miles).toLocaleString()} mi`
            : '—'
        }
      />
      <Row
        label="Engine Hours"
        value={
          v.engine_hours != null
            ? `${Math.round(v.engine_hours).toLocaleString()} h`
            : '—'
        }
      />
    </div>
  );
}
