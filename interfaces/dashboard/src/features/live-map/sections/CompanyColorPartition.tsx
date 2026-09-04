/**
 * Owner / Admin / Accounting overlay: color-coded company partition.
 *
 * Adds a small colored dot beside each vehicle marker, where the
 * color is derived from the truck's ``company`` field.  Owner sees
 * "all my companies on one map" with a visual partition; Accounting
 * uses it to spot which assets belong to which billing entity at a
 * glance.
 *
 * Pure client-side — uses the same ``vehicles`` data the host page
 * already fetched, no extra API call.  Stable color hash so the
 * SAME company always gets the SAME color across page reloads (vs.
 * a random palette that would confuse "wait, my Company A trucks
 * were blue yesterday").
 *
 * Permission-gated by ``can_manage_companies`` OR account-wide
 * vehicle access (``can_view_vehicles`` with unit width 'all') — both
 * are members whose job involves spanning multiple companies under
 * one account.  The overlay no-ops for
 * personas without that scope.
 */
import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { COMPANY_PALETTE, MARKER_HALO, MARKER_SHADOW } from '../../../config/mapColors';
import type { LiveMapSectionProps } from './_shared/types';

/** Deterministic color hash so the SAME company name always gets the
 * SAME color across reloads.  Simple DJB2-like hash modulo palette
 * length — no crypto needed, just stable + cheap. */
function colorForCompany(name: string): string {
  if (!name) return COMPANY_PALETTE[0];
  let hash = 5381;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) | 0;
  }
  return COMPANY_PALETTE[Math.abs(hash) % COMPANY_PALETTE.length];
}

export default function CompanyColorPartition({
  leafletMap,
  isReady,
  vehicles,
  companyColorsOn,
}: LiveMapSectionProps) {
  const { has, hasWide } = useViewPermissions();
  const hasMultiCompanyScope =
    has('can_manage_companies') || hasWide('can_view_vehicles');
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!isReady || !leafletMap.current) return;
    if (!hasMultiCompanyScope) return;
    // User toggled off → previous run's cleanup removed the dots.
    if (!companyColorsOn) return;

    const Leaf = window.L as typeof L;

    function clear() {
      if (layerRef.current) {
        layerRef.current.remove();
        layerRef.current = null;
      }
    }

    // Skip the overlay entirely when there's only one company —
    // partitioning by company is meaningless if everything is
    // Company A, and the extra dots are visual noise.
    const distinctCompanies = new Set<string>();
    for (const v of vehicles) {
      const c = (v.properties.company as string | undefined) || '';
      if (c) distinctCompanies.add(c);
    }
    if (distinctCompanies.size <= 1) {
      clear();
      return;
    }

    const group = Leaf.layerGroup();
    for (const v of vehicles) {
      const company = (v.properties.company as string | undefined) || '';
      if (!company) continue;
      const coords = v.geometry?.coordinates;
      if (!coords || coords.length < 2) continue;
      const [lon, lat] = coords as [number, number];
      if (!isFinite(lat) || !isFinite(lon)) continue;

      const color = colorForCompany(company);
      // Small offset dot — placed up-and-right of the vehicle marker
      // so it doesn't overlap the marker's clickable area.  Uses
      // divIcon so the dot can be styled without rasterising.
      const dot = Leaf.marker([lat, lon], {
        icon: Leaf.divIcon({
          html: `<div style="
            width:8px;height:8px;border-radius:50%;
            background:${color};border:1.5px solid ${MARKER_HALO};
            box-shadow:0 0 2px ${MARKER_SHADOW};
            margin-left:14px;margin-top:-22px;
          "></div>`,
          className: 'company-partition-dot',
          iconSize: [22, 22],
          iconAnchor: [0, 0],
        }),
        // High z so the company dot stays visible above the truck
        // marker but below clickable overlays.
        zIndexOffset: 300,
        interactive: false,
      });
      group.addLayer(dot);
    }
    group.addTo(leafletMap.current);
    clear();
    layerRef.current = group;

    return clear;
  }, [isReady, hasMultiCompanyScope, companyColorsOn, vehicles, leafletMap]);

  return null;
}
