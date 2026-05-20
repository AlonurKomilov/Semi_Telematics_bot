/**
 * Map overlay registry — keyed by the active persona view.
 *
 * Each persona gets a "baseline" overlay component the host LiveMap
 * renders alongside the shared base (vehicle markers, clustering,
 * POI layers, search/filter sidebar).  Adding a new persona-tuned
 * layer set only requires creating a new overlay file and pointing
 * the persona at it here — the LiveMap host stays untouched.
 *
 * Today the entries are scaffolding: FleetStatusOverlay and
 * DispatchRouteOverlay are intentionally no-ops because the existing
 * shared base already paints the Fleet view, and the Dispatch route
 * geometry endpoint isn't shipped yet.  SafetyEventOverlay holds the
 * 30-day event heatmap logic, but the heatmap is also exposed as a
 * manual toggle to every persona via a separate render path in the
 * host — the host renders it with `active={heatOn}` and defaults
 * `heatOn` based on whether the active persona is Safety.  So when
 * a real Safety-only layer (e.g. harsh-event markers separate from
 * the heat) lands, this registry entry becomes the place to compose
 * it; until then the file is here to lock in the contract.
 */
import type { ComponentType } from 'react';
import FleetStatusOverlay from './FleetStatusOverlay';
import DispatchRouteOverlay from './DispatchRouteOverlay';
import type { MapOverlayProps } from './types';

export type OverlayComponent = ComponentType<MapOverlayProps>;

export const OVERLAYS: Record<string, OverlayComponent> = {
  owner: FleetStatusOverlay,
  admin: FleetStatusOverlay,
  fleet: FleetStatusOverlay,
  dispatcher: DispatchRouteOverlay,
  safety: FleetStatusOverlay,
  driver: FleetStatusOverlay,
};

export { FleetStatusOverlay, DispatchRouteOverlay };
export { default as SafetyEventOverlay } from './SafetyEventOverlay';
export type { MapOverlayProps };
