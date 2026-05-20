import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import type { MapOverlayProps } from './types';

interface Props extends MapOverlayProps {
  /**
   * When true, fetch the last 30 days of safety events and render
   * them as a Leaflet heatmap.  When false (or toggled off), the
   * layer is removed and the API call cancelled.
   *
   * The host owns this flag so the side-panel checkbox + the
   * persona-default initial state stay co-located with the other map
   * UI controls; the overlay itself is responsible only for the
   * layer's lifecycle and data fetch.
   */
  active: boolean;
}

/**
 * Persona overlay: a 30-day safety-event heatmap.
 *
 * Visible by default when the Safety persona is active so a safety
 * manager opening the Live Map immediately sees where incidents are
 * clustering.  Other personas can still opt in via the manual toggle
 * in the side panel — the layer is the same.
 *
 * Depends on the optional leaflet.heat plugin (loaded by the host
 * page).  If the plugin failed to load, the overlay silently no-ops
 * so the toggle still flips state without crashing.
 */
export default function SafetyEventOverlay({ leafletMap, isReady, active }: Props) {
  const layerRef = useRef<L.Layer | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    let cancelled = false;
    const LHeat = window.L as unknown as {
      heatLayer?: (points: Array<[number, number, number]>, opts?: object) => L.Layer;
    };

    function clearHeat() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    if (!active) {
      clearHeat();
      return;
    }
    if (typeof LHeat.heatLayer !== 'function') {
      return;
    }

    apiJSON<{ points: Array<[number, number, number]> }>(
      '/safety/events/heatmap?days=30',
    )
      .then((d) => {
        if (cancelled || !active || !leafletMap.current) return;
        clearHeat();
        const layer = LHeat.heatLayer!(d.points || [], {
          radius: 25,
          blur: 18,
          maxZoom: 16,
        });
        layer.addTo(leafletMap.current);
        layerRef.current = layer;
      })
      .catch(() => {
        // Endpoint unavailable or unauthorised — render nothing.
      });

    return () => {
      cancelled = true;
      clearHeat();
    };
  }, [active, isReady, leafletMap]);

  return null;
}
