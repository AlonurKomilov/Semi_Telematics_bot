/**
 * Persona overlay: Fleet status.
 *
 * The shared LiveMap base already paints the Fleet view — vehicle
 * markers, clustering, moving/idle/stopped colour-coding, low-fuel
 * dots and fault rings.  This overlay is therefore a no-op today; it
 * exists so the registry contract is symmetric (every persona has an
 * overlay entry) and so future Fleet-only layers — e.g. heat by
 * miles driven, fuel-cost rings, maintenance-due halos — have an
 * obvious home that doesn't grow the LiveMap monolith.
 */
import type { LiveMapSectionProps } from './_shared/types';

export default function FleetStatusOverlay(_props: LiveMapSectionProps) {
  return null;
}
