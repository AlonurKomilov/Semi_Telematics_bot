/**
 * PTI inspections history section.
 *
 * Thin wrapper around the existing ``VehicleInspectionsCard``
 * component — which already owns its own data fetching and
 * inspection-detail flow — so this section needs almost nothing
 * besides the wrapper margin that the original page applied.
 *
 * Permission gate: ``can_manage_inspections`` (a fleet/safety
 * concern).  Layout-wise this section lives in Fleet + Safety
 * persona layouts.
 */
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { VehicleInspectionsCard } from '../VehicleInspectionsCard';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleInspections({ vehicleName }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  if (!has('can_manage_inspections')) return null;
  return (
    <div className="mt-6 lg:col-span-2">
      <VehicleInspectionsCard vehicleName={vehicleName} />
    </div>
  );
}
