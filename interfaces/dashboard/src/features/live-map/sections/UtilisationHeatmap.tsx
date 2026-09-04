/**
 * Owner / Admin overlay: account-wide utilisation density heatmap.
 *
 * Pulls /parking/utilisation/heatmap?days=30 — server-aggregated parking
 * event points weighted by duration_hours.  Renders as a blue/green
 * heat layer that fades into the base map (distinct from Safety's
 * red/orange incident heatmap so a viewer can have BOTH on simultaneously
 * without confusion).
 *
 * Gated by ``can_view_vehicles`` AND unit width 'all' (``hasWide``) —
 * matches the backend's ``require_wide("vehicles")`` route guard.
 * No-ops cleanly for members without account-wide vehicle access.
 *
 * Uses the same ``leaflet.heat`` plugin SafetyEventOverlay uses;
 * silently no-ops when the plugin isn't loaded (older builds /
 * cold-start race condition).
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import { HEATMAP_GRADIENT } from '../../../config/mapColors';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import type { LiveMapSectionProps } from './_shared/types';

const DAYS = 30;
const POLL_INTERVAL_MS = 10 * 60_000;  // 10 min — utilisation moves slowly

export default function UtilisationHeatmap({
  leafletMap,
  isReady,
  utilHeatOn,
}: LiveMapSectionProps) {
  const { hasWide } = useViewPermissions();
  const hasAccess = hasWide('can_view_vehicles');
  const layerRef = useRef<L.Layer | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasAccess) return;
    // User toggled off → previous run's cleanup already removed the
    // layer; just don't redraw.
    if (!utilHeatOn) return;
    const LHeat = window.L as unknown as {
      heatLayer?: (
        points: Array<[number, number, number]>,
        opts?: object,
      ) => L.Layer;
    };
    if (typeof LHeat.heatLayer !== 'function') {
      // Plugin not loaded yet — skip silently.  Same behaviour as
      // SafetyEventOverlay so the page doesn't crash on a slow
      // CDN ride.
      return;
    }

    let cancelled = false;

    function clearHeat() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    async function refresh() {
      try {
        const data = await apiJSON<{ points: Array<[number, number, number]> }>(
          `/parking/utilisation/heatmap?days=${DAYS}`,
        );
        if (cancelled || !leafletMap.current) return;
        clearHeat();
        // Blue/green gradient (utilisation = "where work happens") to
        // visually distinguish from Safety's red/orange (incidents).
        // Both layers can sit on the map at the same time without
        // visual confusion.
        const layer = LHeat.heatLayer!(data.points || [], {
          radius: 30,
          blur: 22,
          maxZoom: 16,
          gradient: HEATMAP_GRADIENT,
        });
        layer.addTo(leafletMap.current);
        layerRef.current = layer;
      } catch {
        // 401/403 → fall silent.  Owner/admin route guard already
        // verified above; if the server still refused, no overlay.
      }
    }

    refresh();
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
      clearHeat();
    };
  }, [isReady, hasAccess, utilHeatOn, leafletMap]);

  return null;
}
