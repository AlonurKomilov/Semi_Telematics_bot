import { describe, it, expect } from 'vitest';
import { toolDeepLink } from './toolLinks';

// The resolver derives tool → route from featureCatalog: a tool named
// after its feature resolves by prefix; the mismatches use the override
// map; tools with no product page resolve to null.

describe('toolDeepLink', () => {
  it('resolves prefix-matching tools straight from the catalog', () => {
    expect(toolDeepLink('get_maintenance_summary')?.path).toBe('/maintenance');
    expect(toolDeepLink('get_geofences')?.path).toBe('/geofences');
  });

  it('resolves name/catalog mismatches via the override map', () => {
    expect(toolDeepLink('get_alert_history')?.path).toBe('/alerts');
    expect(toolDeepLink('get_parked_vehicles')?.path).toBe('/live-map');
    expect(toolDeepLink('get_events_summary')?.path).toBe('/safety-events');
    expect(toolDeepLink('get_recent_work_orders')?.path).toBe('/work-orders');
    expect(toolDeepLink('search_knowledge_base')?.path).toBe('/knowledge');
    expect(toolDeepLink('get_driver_scorecard')?.path).toBe('/scorecards');
  });

  it('returns a labelKey + featureId alongside the path', () => {
    const link = toolDeepLink('get_maintenance_summary')!;
    expect(link.featureId).toBe('maintenance');
    expect(link.labelKey).toBe('nav.maintenance');
  });

  it('returns null for tools with no product page', () => {
    expect(toolDeepLink('get_weather')).toBeNull();
    expect(toolDeepLink('')).toBeNull();
    expect(toolDeepLink('some_unknown_tool_xyz')).toBeNull();
  });
});
