/**
 * Persona overlay: Dispatch route view (placeholder).
 *
 * Intended to render: planned route polylines, ETA chips at each
 * waypoint, driver-assignment labels per vehicle, and an on-time /
 * delayed / off-route colour code.  Currently a no-op — the Dispatch
 * routes API + route geometry storage are still being scoped, so the
 * overlay file is here to lock in the registry slot.  When the
 * routes endpoint lands, the layer logic goes in this file without
 * any change to the LiveMap host.
 */
import type { LiveMapSectionProps } from './_shared/types';

export default function DispatchRouteOverlay(_props: LiveMapSectionProps) {
  return null;
}
