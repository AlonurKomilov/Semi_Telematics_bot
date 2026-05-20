import type { MutableRefObject } from 'react';
import type L from 'leaflet';
import type { MapVehicleFeature, MapVehicleProperties } from '../../../types';

/**
 * Common props every map overlay receives from the host LiveMap page.
 *
 * Overlays are persona-flavoured visual layers that ride on top of the
 * shared base (vehicle markers, clustering, POI layers, search/filter
 * sidebar) — for example a 30-day safety-event heatmap for the Safety
 * persona, or route-line + ETA chips for Dispatch.  They mount and
 * unmount as the active persona changes, but the underlying map state
 * (selection, search, zoom) is preserved by the host.
 *
 * Overlays should:
 *   • Only attach Leaflet layers when ``isReady`` is true.
 *   • Clean up their layers + any pending fetches on unmount, so
 *     switching personas doesn't leak markers or in-flight requests.
 *   • Treat ``vehicles`` and ``selected`` as read-only.  If an overlay
 *     needs to influence the host's selection, add an explicit callback
 *     prop rather than mutating these.
 */
export interface MapOverlayProps {
  /** Imperative leaflet map handle from useLeafletMap. */
  leafletMap: MutableRefObject<L.Map | null>;
  /** True once the leaflet container has been initialised. */
  isReady: boolean;
  /** Current vehicle features rendered on the map. */
  vehicles: MapVehicleFeature[];
  /** Currently selected vehicle, or null for the all-vehicles view. */
  selected: MapVehicleProperties | null;
}
