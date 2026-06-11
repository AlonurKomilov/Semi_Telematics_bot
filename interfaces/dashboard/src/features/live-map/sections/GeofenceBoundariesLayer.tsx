/**
 * Dispatcher overlay: geofence boundary polygons + circles.
 *
 * Renders every geofence the operator has access to as a translucent
 * polygon (or circle) on the Live Map.  Dispatch's job is "where is
 * everyone right now and are they where they should be" — having the
 * safe-zone boundaries on the same map as the moving trucks means a
 * dispatcher can spot a truck about to leave its designated zone at
 * a glance instead of toggling between the Geofences page and the
 * map.
 *
 * Reads ``/fleet/geofences`` (same endpoint as the standalone
 * Geofences page) so the data is consistent.  Permission-gated by
 * ``can_geofence_all`` / ``can_geofence_vehicle`` — overlay silently
 * no-ops when the user lacks geofence access, so layouts can
 * include this overlay without per-persona conditional logic.
 *
 * Imperative leaflet pattern — overlay returns null and manages a
 * Leaflet ``L.LayerGroup`` via useEffect.  Cleanup removes the
 * group on unmount so swapping personas at runtime doesn't leak
 * layers onto the map.
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import { usePermissions } from '../../../hooks/usePermissions';
import { GEOFENCE } from '../../../config/mapColors';
import type { GeofencesResponse, GeofenceFeature } from '../../../types';
import type { LiveMapSectionProps } from './_shared/types';

// Dispatch-blue with low opacity — visible without competing with
// vehicle markers (which use the stronger persona colors).  Stroke
// is slightly more opaque than fill so the boundary stays readable
// on busy maps.
const FILL_COLOR = GEOFENCE.fill;
const FILL_OPACITY = 0.08;
const STROKE_COLOR = GEOFENCE.stroke;
const STROKE_OPACITY = 0.55;
const STROKE_WIDTH = 1.5;

export default function GeofenceBoundariesLayer({
  leafletMap,
  isReady,
}: LiveMapSectionProps) {
  const { has } = usePermissions();
  const hasGeofencePerm = has('can_geofence_all') || has('can_geofence_vehicle');
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasGeofencePerm) return;

    let cancelled = false;
    const Leaf = window.L as typeof L;

    function clear() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    apiJSON<GeofencesResponse>('/fleet/geofences')
      .then((data) => {
        if (cancelled || !leafletMap.current) return;
        const group = Leaf.layerGroup();

        for (const feature of data.features || []) {
          const layer = featureToLayer(Leaf, feature);
          if (layer) {
            layer.bindTooltip(
              feature.properties.name || 'Geofence',
              { sticky: true, opacity: 0.9 },
            );
            group.addLayer(layer);
          }
        }

        group.addTo(leafletMap.current);
        clear(); // remove any stale layer from a previous render
        layerRef.current = group;
      })
      .catch(() => {
        // /fleet/geofences may 403 for users without geofence access
        // even though the client-side ``hasGeofencePerm`` check
        // passed (account-level permission overrides) — silently
        // skip rather than blowing up the map.
      });

    return () => {
      cancelled = true;
      clear();
    };
  }, [isReady, hasGeofencePerm, leafletMap]);

  return null;
}


// Convert a GeoJSON-like geofence feature to a Leaflet layer.  Returns
// null when the feature shape isn't renderable (missing coords, etc.)
// rather than throwing — one bad feature shouldn't take down the
// entire overlay.
function featureToLayer(Leaf: typeof L, feature: GeofenceFeature): L.Layer | null {
  const style = {
    color: STROKE_COLOR,
    opacity: STROKE_OPACITY,
    weight: STROKE_WIDTH,
    fillColor: FILL_COLOR,
    fillOpacity: FILL_OPACITY,
  };
  const geom = feature.geometry;
  if (!geom) return null;
  if (geom.type === 'Point') {
    const radius =
      feature.properties.radius_meters ?? feature.properties.radius ?? 0;
    const coords = geom.coordinates as [number, number];
    if (!isFinite(coords[0]) || !isFinite(coords[1]) || radius <= 0) return null;
    // GeoJSON coordinates are [lon, lat]; Leaflet expects [lat, lon].
    return Leaf.circle([coords[1], coords[0]], { radius, ...style });
  }
  if (geom.type === 'Polygon') {
    const rings = geom.coordinates as [number, number][][];
    if (!rings.length || !rings[0]?.length) return null;
    const latlngs = rings.map((ring) =>
      ring.map(([lon, lat]) => [lat, lon] as [number, number]),
    );
    return Leaf.polygon(latlngs, style);
  }
  return null;
}
