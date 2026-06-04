/**
 * Live Map overlay section registry.
 *
 * Unlike Vehicle Detail's sections which render visible JSX, Live Map
 * overlays render ``null`` and attach Leaflet layers imperatively via
 * useEffect — they're "side-effect components" that mount/unmount
 * layer state onto the map.  They still fit Pattern B perfectly:
 * lazy-loaded so non-Dispatch personas never download the
 * DispatchRouteOverlay chunk, persona-keyed visibility via
 * ``LIVE_MAP_LAYOUTS``, error boundaries isolate broken overlay code.
 *
 * Section ids referenced here must match the ids in ``layouts.ts``.
 */
import { lazy } from 'react';
import type { SectionRegistry } from '../_lib/types';
import type { LiveMapSectionProps } from './sections/_shared/types';

export const LIVE_MAP_SECTIONS: SectionRegistry<LiveMapSectionProps> = {
  fleet_status: {
    Component: lazy(() => import('./sections/FleetStatusOverlay')),
    label: 'Fleet status overlay',
  },
  dispatch_route: {
    Component: lazy(() => import('./sections/DispatchRouteOverlay')),
    label: 'Dispatch route overlay',
  },
  safety_heatmap: {
    Component: lazy(() => import('./sections/SafetyEventOverlay')),
    label: 'Safety event heatmap',
  },
  geofence_boundaries: {
    Component: lazy(() => import('./sections/GeofenceBoundariesLayer')),
    label: 'Geofence boundary polygons (Dispatch)',
  },
  unsafe_parking_markers: {
    Component: lazy(() => import('./sections/UnsafeParkingMarkers')),
    label: 'Unsafe-parking red pins (Dispatch)',
  },
  fault_markers: {
    Component: lazy(() => import('./sections/FaultMarkersLayer')),
    label: 'Fault-ring overlay around trucks with active DTCs (Fleet)',
  },
  company_color_partition: {
    Component: lazy(() => import('./sections/CompanyColorPartition')),
    label: 'Per-company color dots (Owner / Admin / Accounting)',
  },
  maintenance_markers: {
    Component: lazy(() => import('./sections/MaintenanceMarkersLayer')),
    label: 'Wrench markers on trucks with overdue/pending maintenance (Fleet)',
  },
  utilisation_heatmap: {
    Component: lazy(() => import('./sections/UtilisationHeatmap')),
    label: 'Account-wide trip-density heatmap (Owner / Admin)',
  },
};
