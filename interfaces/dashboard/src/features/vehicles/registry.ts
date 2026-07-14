/**
 * Vehicle Detail section registry.
 *
 * Every section is wrapped in ``React.lazy`` so Vite emits a separate
 * JS chunk per file — a Dispatch user opening a vehicle detail page
 * downloads VehicleHeader, VehicleInfo, VehicleLocation, and
 * VehicleTimeline chunks; the Fault Codes / Health / Inspections
 * chunks are never fetched for that persona.
 *
 * Section ids referenced here must match the ids used in
 * ``layouts.ts`` — the ``check:layout-coverage`` audit catches drift
 * if a layout references a section that doesn't exist in the
 * registry.
 */
import { lazy } from 'react';
import type { SectionRegistry } from '../_lib/types';
import type { VehicleSectionProps } from './sections/_shared/types';

export const VEHICLE_SECTIONS: SectionRegistry<VehicleSectionProps> = {
  header: {
    Component: lazy(() => import('./sections/VehicleHeader')),
    label: 'Header',
  },
  info: {
    Component: lazy(() => import('./sections/VehicleInfo')),
    label: 'Vehicle Info',
  },
  location: {
    Component: lazy(() => import('./sections/VehicleLocation')),
    label: 'Location',
  },
  health: {
    Component: lazy(() => import('./sections/VehicleHealth')),
    label: 'Vehicle Health',
  },
  inventory: {
    Component: lazy(() => import('./inventory/InventoryCard')),
    label: 'Inventory',
  },
  faults: {
    Component: lazy(() => import('./sections/VehicleFaults')),
    label: 'Active Fault Codes',
  },
  inspections: {
    Component: lazy(() => import('./sections/VehicleInspections')),
    label: 'PTI Inspections',
  },
  timeline: {
    Component: lazy(() => import('./sections/VehicleTimeline')),
    label: '7-Day Activity',
  },
  usage: {
    Component: lazy(() => import('./sections/VehicleUsage')),
    label: 'Usage Trends',
  },
};
