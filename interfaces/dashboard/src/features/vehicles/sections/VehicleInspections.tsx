/**
 * PTI inspections history section.
 *
 * Thin wrapper around the existing ``VehicleInspectionsCard``
 * component — which already owns its own data fetching and
 * inspection-detail flow — so this section needs almost nothing
 * besides the wrapper margin that the original page applied.
 *
 * Permission gate: ``can_inspections_all`` (a fleet/safety
 * concern).  Layout-wise this section lives in Fleet + Safety
 * persona layouts.
 */
import { usePermissions } from '../../../hooks/usePermissions';
import { VehicleInspectionsCard } from '../VehicleInspectionsCard';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleInspections({ vehicleName }: VehicleSectionProps) {
  const { has } = usePermissions();
  if (!has('can_inspections_all')) return null;
  return (
    <div className="mt-6 lg:col-span-2">
      <VehicleInspectionsCard vehicleName={vehicleName} />
    </div>
  );
}
