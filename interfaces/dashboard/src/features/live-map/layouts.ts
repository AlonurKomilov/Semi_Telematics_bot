/**
 * Live Map per-persona overlay layouts.
 *
 * Each layout is an ordered list of overlay ids — the host LiveMap
 * renders them in order, each receiving the same ``LiveMapSectionProps``
 * (map handle, vehicles, selected, heatOn).  Overlays that
 * imperatively attach Leaflet layers do so via useEffect; the order
 * only affects React mount order, not visual z-index (z-index is
 * managed inside each overlay's leaflet pane).
 *
 * Today most personas share ``fleet_status`` because the shared
 * LiveMap base already paints the Fleet view — the overlay slot is
 * scaffolding for future per-persona layers (Dispatch route lines +
 * ETA chips when the routes API lands, Safety per-incident pins
 * separate from the heatmap, etc.).
 *
 * ``safety_heatmap`` lives in every visible persona's layout because
 * the side-panel checkbox needs the overlay mounted to toggle.  The
 * overlay reads ``heatOn`` from sectionProps and renders/clears the
 * heat layer accordingly; Safety persona defaults ``heatOn`` to true
 * at the page level, other personas default to false.
 */
import type { LayoutMap } from '../_lib/types';

export const LIVE_MAP_LAYOUTS: LayoutMap = {
  // Owner / Admin — cross-cutting executive view.  Company color
  // partition surfaces which trucks belong to which billing entity
  // at a glance; utilisation_heatmap shows where the fleet actually
  // operates (30-day parking density); safety_heatmap stays mounted
  // so a quick incident check is one toggle away from the side panel.
  owner: [
    'fleet_status',
    'company_color_partition',
    'utilisation_heatmap',
    'safety_heatmap',
  ],
  admin: [
    'fleet_status',
    'company_color_partition',
    'utilisation_heatmap',
    'safety_heatmap',
  ],

  // Fleet — fault markers (orange ring on DTC-carrying trucks) +
  // maintenance markers (wrench icon on overdue/pending tasks) give
  // a fleet manager the full mechanical-attention picture at a
  // glance.  safety_heatmap stays mounted for cross-context triage
  // but is off by default for non-Safety personas.
  fleet: [
    'fleet_status',
    'fault_markers',
    'maintenance_markers',
    'safety_heatmap',
  ],

  // Dispatcher — geofence boundaries + unsafe-parking pins are the
  // live-ops trifecta: where is everyone, where are they supposed
  // to be, and who's stopped outside the safe zone.  dispatch_route
  // is still a placeholder until the routes API ships; the
  // boundaries + parking overlays are the Phase A win.
  dispatcher: [
    'dispatch_route',
    'geofence_boundaries',
    'unsafe_parking_markers',
    'safety_heatmap',
  ],

  // Safety primary: fleet_status acts as the baseline vehicle layer;
  // safety_heatmap is auto-on (the host defaults heatOn=true for
  // this persona).
  safety: ['fleet_status', 'safety_heatmap'],

  // Driver — uses the Mini App in practice; this is a fallback for
  // the unlikely web-dashboard hit by a driver role.  No safety
  // heatmap because drivers don't triage events.
  driver: ['fleet_status'],

  // HR — sees the base vehicle layer (for "where is driver X right
  // now") plus the safety heatmap so they can see where incidents
  // cluster (drives coaching priority).  No fault / parking
  // overlays — HR doesn't action vehicle ops, just sees context.
  hr: ['fleet_status', 'safety_heatmap'],

  // Accounting — base vehicle layer + company color partition.
  // No operational overlays (incidents, faults, parking); accounting
  // reviews cost outcomes.  The company dots make "which billing
  // entity owns which asset" visible at a glance.
  accounting: ['fleet_status', 'company_color_partition'],
};
