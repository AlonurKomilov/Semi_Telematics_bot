/**
 * Re-export of the Pattern B vehicle-detail page.
 *
 * The implementation moved to ``features/vehicle/VehicleDetail.tsx``
 * during the Phase 1 Pattern B migration.  This file stays as a thin
 * re-export so the router import path (``pages/vehicles/VehicleDetail``)
 * keeps working without touching ``router.tsx``.
 */
export { default } from '../../features/vehicle/VehicleDetail';
