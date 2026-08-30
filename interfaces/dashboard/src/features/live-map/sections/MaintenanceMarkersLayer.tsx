/**
 * Fleet overlay: wrench icon on trucks with overdue or pending
 * maintenance.
 *
 * Pulls /maintenance/due-locations once on mount (refreshes every
 * 5 minutes — maintenance state moves slowly).  Joins against the
 * vehicle markers already on the map by ``vehicle_id`` (preferred)
 * or ``vehicle_name`` (fallback for legacy tasks that pre-date the
 * vehicle_id column).  Drops a small wrench icon at the truck's
 * current position so a fleet manager can spot "needs shop time"
 * at a glance alongside the fault rings.
 *
 * Color logic: red wrench when overdue_count > 0, amber when only
 * pending — same severity ladder the Overview KPI cards use.  Click
 * opens a popup listing the counts + a deep link to the standalone
 * Maintenance page.
 *
 * Permission-gated by ``can_maintenance_all`` / ``can_maintenance_vehicle``.
 * No-ops cleanly for personas without access.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type L from 'leaflet';
import { apiJSON } from '../../../api/client';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { MAP_STATUS, MARKER_GLYPH, MARKER_HALO, POPUP_LINK } from '../../../config/mapColors';
import type { LiveMapSectionProps } from './_shared/types';

interface DueRow {
  vehicle_id: string;
  vehicle_name: string;
  overdue_count: number;
  pending_count: number;
}

interface DueLocationsResponse {
  items: DueRow[];
  count: number;
}

const POLL_INTERVAL_MS = 5 * 60_000;  // 5 minutes — maintenance is slow-moving

const COLOR_OVERDUE = MAP_STATUS.danger;  // overdue → danger
const COLOR_PENDING = MAP_STATUS.warn;    // pending → warn

export default function MaintenanceMarkersLayer({
  leafletMap,
  isReady,
  vehicles,
}: LiveMapSectionProps) {
  const { has } = useViewPermissions();
  const hasAccess =
    has('can_maintenance_all') || has('can_maintenance_vehicle');
  const [dueRows, setDueRows] = useState<DueRow[]>([]);
  const layerRef = useRef<L.LayerGroup | null>(null);

  // Fetch the due-locations list on mount + every POLL_INTERVAL_MS.
  // Separate from the marker-rendering effect below so a polling
  // refresh doesn't churn the whole layer when the underlying data
  // hasn't changed; React's reference-equality check on setDueRows
  // skips re-renders unless the array changed.
  useEffect(() => {
    if (!hasAccess) return;
    let cancelled = false;

    async function refresh() {
      try {
        const data = await apiJSON<DueLocationsResponse>(
          '/maintenance/due-locations',
        );
        if (!cancelled) setDueRows(data.items || []);
      } catch {
        // 401/403 → user lost permission mid-session; silent fail is
        // fine because the overlay just stops showing markers.
      }
    }
    refresh();
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hasAccess]);

  // Index vehicles by both id and name so the join handles either
  // identifier the backend sends.  Memoised so the marker effect
  // doesn't rebuild the index on every render.
  const vehicleIndex = useMemo(() => {
    const byId = new Map<string, [number, number]>();
    const byName = new Map<string, [number, number]>();
    for (const v of vehicles) {
      const coords = v.geometry?.coordinates;
      if (!coords || coords.length < 2) continue;
      const [lon, lat] = coords as [number, number];
      if (!isFinite(lat) || !isFinite(lon)) continue;
      const id = (v.properties.id as string | undefined) || '';
      const name = v.properties.name || '';
      if (id) byId.set(id, [lat, lon]);
      if (name) byName.set(name, [lat, lon]);
    }
    return { byId, byName };
  }, [vehicles]);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasAccess) return;
    const Leaf = window.L as typeof L;

    function clear() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    const group = Leaf.layerGroup();
    for (const row of dueRows) {
      const pos =
        vehicleIndex.byId.get(row.vehicle_id) ||
        vehicleIndex.byName.get(row.vehicle_name);
      if (!pos) continue;  // truck not currently on the map
      const color = row.overdue_count > 0 ? COLOR_OVERDUE : COLOR_PENDING;
      const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18">
          <circle cx="12" cy="12" r="10" fill="${color}" stroke="${MARKER_HALO}" stroke-width="1.5"/>
          <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4l-3.6 3.6 1.4 1.4 3.6-3.6a4 4 0 0 0 5.4-5.4l-2 2-1.4-1.4z" fill="${MARKER_GLYPH}"/>
        </svg>
      `;
      const marker = Leaf.marker(pos, {
        icon: Leaf.divIcon({
          html: svg,
          className: 'maintenance-due-marker',
          iconSize: [18, 18],
          // Anchor a bit above the truck marker so the wrench sits
          // next to it rather than on top.
          iconAnchor: [-8, 18],
        }),
        zIndexOffset: 400,
      });
      const escName = escapeHTML(row.vehicle_name || row.vehicle_id);
      marker.bindPopup(`
        <div style="min-width:170px">
          <div style="font-weight:600">${escName}</div>
          <div style="font-size:11px;color:var(--muted-foreground);margin-top:4px">
            ${/* The vivid MAP_STATUS hexes are for the MARKER, which sits
                   on tile imagery and must not follow the theme. As popup
                   TEXT on a themed surface they are a different job and
                   were failing it: #f59e0b measured 2.15:1 and #ef4444
                   3.76:1 at 11px on the light popover. The tone tokens
                   are the contrast-tuned twins of the same meanings. */''}
            ${row.overdue_count > 0
              ? `<span style="color:var(--danger)">${row.overdue_count} overdue</span>`
              : ''}
            ${row.overdue_count > 0 && row.pending_count > 0 ? ' · ' : ''}
            ${row.pending_count > 0
              ? `<span style="color:var(--warn)">${row.pending_count} pending</span>`
              : ''}
          </div>
          <a href="/maintenance?vehicle=${encodeURIComponent(row.vehicle_name)}"
             style="font-size:11px;color:${POPUP_LINK};display:inline-block;margin-top:6px">
            Open maintenance →
          </a>
        </div>
      `);
      group.addLayer(marker);
    }

    if (group.getLayers().length > 0) {
      group.addTo(leafletMap.current);
      clear();
      layerRef.current = group;
    } else {
      clear();
    }

    return clear;
  }, [isReady, hasAccess, dueRows, vehicleIndex, leafletMap]);

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
