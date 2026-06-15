/**
 * Fleet overlay: visual indicator for trucks with active fault codes.
 *
 * The base LiveMap renders every truck as a colored marker (moving /
 * idle / stopped).  This overlay drops an orange ring AROUND each
 * truck that has ``fault_count > 0`` so a fleet manager can spot
 * "needs mechanical attention" at a glance without reading the
 * marker popup.  Click-through to the truck's detail page goes
 * through the base marker — this overlay is decorative.
 *
 * Reads ``vehicles`` from the shared section props (already fetched
 * by the host LiveMap page).  No additional API call.  Permission-
 * gated by ``can_faults`` so the overlay is a no-op for personas
 * without fault access.
 *
 * The fault ring uses the shared ``warn`` map colour, which stays
 * visually distinct from the ``danger`` (red) warn-ring the base map
 * paints for low fuel / DEF — at a glance a fleet manager can tell
 * "low fuel / DEF" (red) from "active DTCs" (amber) without zooming.
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { MAP_STATUS } from '../../../config/mapColors';
import type { LiveMapSectionProps } from './_shared/types';

const RING_COLOR = MAP_STATUS.warn;
const RING_WEIGHT = 3;
const RING_OPACITY = 0.85;
// Pixel radius is in map-projection meters at the marker's lat; this
// constant is the desired pixel radius — Leaflet's circleMarker uses
// pixel radius directly (unlike circle, which uses meters).
const RING_PIXEL_RADIUS = 12;

export default function FaultMarkersLayer({
  leafletMap,
  isReady,
  vehicles,
}: LiveMapSectionProps) {
  const { has } = useViewPermissions();
  const hasFaultsPerm = has('can_faults');
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasFaultsPerm) return;

    const Leaf = window.L as typeof L;

    function clear() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    const group = Leaf.layerGroup();
    for (const v of vehicles) {
      const props = v.properties;
      const faultCount = (props.fault_count as number | undefined) ?? 0;
      if (faultCount <= 0) continue;

      const coords = v.geometry?.coordinates;
      if (!coords || coords.length < 2) continue;
      const [lon, lat] = coords as [number, number];
      if (!isFinite(lat) || !isFinite(lon)) continue;

      const ring = Leaf.circleMarker([lat, lon], {
        radius: RING_PIXEL_RADIUS,
        color: RING_COLOR,
        opacity: RING_OPACITY,
        weight: RING_WEIGHT,
        fill: false,
        // Below the vehicle marker so the truck icon stays clickable;
        // the ring is purely visual.  ``interactive: false`` ensures
        // clicks pass through to the marker.
        interactive: false,
      });
      group.addLayer(ring);
    }

    if (group.getLayers().length > 0) {
      group.addTo(leafletMap.current);
      clear();
      layerRef.current = group;
    } else {
      clear();
    }

    return clear;
  }, [isReady, hasFaultsPerm, vehicles, leafletMap]);

  return null;
}
