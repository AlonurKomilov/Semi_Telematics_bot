/**
 * Persona overlay: a 30-day safety-event heatmap.
 *
 * Visible by default when the Safety persona is active so a safety
 * manager opening the Live Map immediately sees where incidents are
 * clustering.  Other personas can still opt in via the manual toggle
 * in the side panel — the layer is the same.
 *
 * Reads the ``heatOn`` flag from the shared section props (the host
 * owns the toggle state because the side-panel checkbox + persona-
 * default initial value live on the page).  When ``heatOn`` is false
 * the layer is removed and the API call cancelled.
 *
 * Depends on the optional leaflet.heat plugin (loaded by the host
 * page).  If the plugin failed to load, the overlay silently no-ops
 * so the toggle still flips state without crashing.
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import { usePermissions } from '../../../hooks/usePermissions';
import type { LiveMapSectionProps } from './_shared/types';

export default function SafetyEventOverlay({
  leafletMap,
  isReady,
  heatOn,
}: LiveMapSectionProps) {
  const { has } = usePermissions();
  const hasEventsPerm = has('can_events_all') || has('can_events_vehicle');
  const layerRef = useRef<L.Layer | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    const LHeat = window.L as unknown as {
      heatLayer?: (
        points: Array<[number, number, number]>,
        opts?: object,
      ) => L.Layer;
    };

    function clearHeat() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    // Permission gate — overlay no-ops cleanly when the user doesn't
    // have safety-event read access.  Without this gate the overlay
    // would fire /safety/events/heatmap?days=30 on every persona that
    // includes it in their layout, eat the 401 silently, and add
    // noise to the network tab.  Layout-level inclusion ≠ user-level
    // access; the gate lives here so layouts can declare the overlay
    // without conditional logic per persona.
    //
    // Defensive clearHeat() on this branch: React's effect cleanup
    // already removes the layer when hasEventsPerm flips true→false
    // (the previous effect run's cleanup fires before this re-run),
    // but calling clearHeat here makes the intent obvious and covers
    // the StrictMode double-render case + any future code-path that
    // bypasses the cleanup contract.
    if (!hasEventsPerm) {
      clearHeat();
      return;
    }
    let cancelled = false;

    if (!heatOn) {
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
        // hasEventsPerm guard is redundant with the early-return
        // above (effect would have re-run already if it flipped),
        // but the explicit check makes the permission intent
        // obvious at the resolution site and survives any future
        // refactor that moves the fetch out of this effect.
        if (cancelled || !heatOn || !leafletMap.current || !hasEventsPerm) return;
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
  }, [heatOn, isReady, leafletMap, hasEventsPerm]);

  return null;
}
