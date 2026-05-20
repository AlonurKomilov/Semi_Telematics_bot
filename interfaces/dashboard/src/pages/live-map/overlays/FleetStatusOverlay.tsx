import type { MapOverlayProps } from './types';

/**
 * Persona overlay: Fleet status.
 *
 * The shared LiveMap base already paints the Fleet view — vehicle
 * markers, clustering, moving/idle/stopped colour-coding, low-fuel
 * dots and fault rings.  This overlay is therefore a no-op today; it
 * exists so the registry contract is symmetric (every persona has an
 * overlay slot) and so future Fleet-only layers — e.g. heat by miles
 * driven, fuel-cost rings, maintenance-due halos — have an obvious
 * home that doesn't grow the LiveMap monolith.
 */
export default function FleetStatusOverlay(_props: MapOverlayProps) {
  return null;
}
