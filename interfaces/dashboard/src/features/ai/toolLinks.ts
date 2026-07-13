/**
 * Deep links for AI tool steps — "the assistant looked at Maintenance,
 * here's a button to open it."
 *
 * NOT its own registry: the tool→route map is DERIVED from
 * `featureCatalog` (the nav SSOT that already pairs a feature `id` with
 * its `path`).  A tool named after its feature (`get_maintenance_*` →
 * `maintenance`) resolves automatically; only the handful whose data
 * lives under a differently-named feature need a one-line override
 * below.  Adding a new tool that follows the naming convention gets a
 * deep link for free — no edit here.
 *
 * (A future upgrade: let each `@register_tool` schema declare its
 * `feature` authoritatively, so even non-matching names need no
 * override.  Until then this resolver covers every current tool.)
 */
import { FEATURE_CATALOG } from '../../config/featureCatalog';

const BY_ID = new Map(FEATURE_CATALOG.map((f) => [f.id, f]));

// Tools whose data surfaces under a feature that its NAME doesn't match
// by prefix.  Everything not listed falls back to the name heuristic.
const TOOL_FEATURE: Record<string, string> = {
  // Live-fleet snapshots all open on the map.
  get_parked_vehicles: 'live_map',
  get_rolling_stopped: 'live_map',
  get_undriven_vehicles: 'live_map',
  get_vehicle_location: 'live_map',
  // Vehicle-scoped reads live on the Vehicles feature.
  get_vehicle_detail: 'vehicles',
  get_vehicle_odometer: 'vehicles',
  get_vehicle_history: 'vehicles',
  search_vehicles: 'vehicles',
  // Naming vs catalog-id mismatches.
  get_alert_history: 'alerts',
  get_events_summary: 'events',
  get_vehicle_events: 'events',
  get_recent_work_orders: 'work_orders',
  get_recent_inspections: 'inspections',
  get_vehicle_maintenance: 'maintenance',
  get_driver_scorecard: 'scorecards',
  get_driver_applications: 'applications',
  get_driver_efficiency: 'drivers',
  get_driver_hos_status: 'drivers',
  get_drivers_list: 'drivers',
  search_knowledge_base: 'knowledge_base',
  check_vehicle_camera: 'cameras',
  get_account_stats: 'overview',
  // Tools with no product page to open (no button rendered):
  //   get_weather, get_geofences→geofences (matches by prefix)
};

export interface ToolLink {
  path: string;
  labelKey: string;   // i18n key for "Open <Feature>"
  featureId: string;
}

/** Resolve a tool name to a deep-link target, or null when it has no
 *  product page (e.g. get_weather). */
export function toolDeepLink(toolName: string): ToolLink | null {
  if (!toolName) return null;
  const featureId =
    TOOL_FEATURE[toolName] ??
    toolName.replace(/^(get_|search_|check_)/, '').split('_')[0];
  const f = BY_ID.get(featureId);
  return f ? { path: f.path, labelKey: f.labelKey, featureId } : null;
}
