/**
 * Dispatcher overlay: pins for trucks parked outside safe zones.
 *
 * Reads ``/parking/active?attention_only=1`` and drops a red pin at
 * each unsafe-or-unknown parking event.  Dispatch already sees these
 * counts on the Overview hero strip ("Parked unsafely 16") — this
 * overlay puts the SAME 16 trucks on the map so the dispatcher can
 * triage spatially (a cluster on one street vs. scattered across
 * three states changes the response priority).
 *
 * Permission-gated by ``can_alerts_all`` / ``can_alerts_vehicle`` /
 * ``can_vehicle_all`` — same gate the /parking/active route uses,
 * so the overlay silently no-ops for personas without access
 * (HR / Accounting / Driver).
 *
 * Pin click opens a popup with vehicle name, duration parked, and
 * a link to the standalone Parking page for the full triage flow.
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { MAP_STATUS, POPUP_LINK } from '../../../config/mapColors';
import type { ParkingEventsResponse } from '../../../types';
import type { LiveMapSectionProps } from './_shared/types';

const POLL_INTERVAL_MS = 60_000;  // refresh every minute — parking moves slowly

export default function UnsafeParkingMarkers({
  leafletMap,
  isReady,
}: LiveMapSectionProps) {
  const { has } = useViewPermissions();
  const hasAccess =
    has('can_alerts_all') || has('can_alerts_vehicle') || has('can_vehicle_all');
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasAccess) return;

    let cancelled = false;
    const Leaf = window.L as typeof L;

    function clear() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    function buildIcon(severity: 'unsafe' | 'unknown') {
      // Inline SVG marker — no external image fetch, matches the
      // base map's vehicle markers in render cost.  Unsafe is a
      // saturated red; unknown is amber so a dispatcher can
      // visually distinguish "definitely bad" vs. "needs review."
      const color = severity === 'unsafe' ? MAP_STATUS.danger : MAP_STATUS.warn;
      const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22">
          <path d="M12 2 L21 20 L3 20 Z" fill="${color}" stroke="#fff" stroke-width="1.5"/>
          <text x="12" y="17" text-anchor="middle" font-size="11" font-weight="bold" fill="#fff">P</text>
        </svg>
      `;
      return Leaf.divIcon({
        html: svg,
        className: 'unsafe-parking-marker',
        iconSize: [22, 22],
        iconAnchor: [11, 20],
      });
    }

    async function refresh() {
      try {
        const data = await apiJSON<ParkingEventsResponse>(
          '/parking/active?attention_only=1',
        );
        if (cancelled || !leafletMap.current) return;
        const group = Leaf.layerGroup();
        for (const e of data.events || []) {
          if (!isFinite(e.latitude) || !isFinite(e.longitude)) continue;
          if (e.latitude === 0 && e.longitude === 0) continue;
          const severity = e.location_class === 'unsafe' ? 'unsafe' : 'unknown';
          const marker = Leaf.marker([e.latitude, e.longitude], {
            icon: buildIcon(severity),
            // High z so the parking pin overlays vehicle markers
            // when a truck is sitting on top of its own pin.
            zIndexOffset: 500,
          });
          const hours = Number.isFinite(e.duration_hours)
            ? `${Math.round(e.duration_hours)}h`
            : '—';
          const cls = e.location_class || 'unknown';
          marker.bindPopup(`
            <div style="min-width:160px">
              <div style="font-weight:600">${escapeHTML(e.vehicle_name)}</div>
              <div style="font-size:11px;color:#666">Parked ${hours} · ${escapeHTML(cls)}</div>
              ${e.address ? `<div style="font-size:11px;margin-top:4px">${escapeHTML(e.address)}</div>` : ''}
              <a href="/parking" style="font-size:11px;color:${POPUP_LINK};display:inline-block;margin-top:6px">Triage →</a>
            </div>
          `);
          group.addLayer(marker);
        }
        group.addTo(leafletMap.current);
        clear();
        layerRef.current = group;
      } catch {
        // 401/403 fall through silently — the user lacks access to
        // active parking events on their account.  Permission
        // overrides above the role default land here.
      }
    }

    refresh();
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
      clear();
    };
  }, [isReady, hasAccess, leafletMap]);

  return null;
}


function escapeHTML(s: string): string {
  return (s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
