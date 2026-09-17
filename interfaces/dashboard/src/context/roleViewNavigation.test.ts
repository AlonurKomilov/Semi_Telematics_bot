import { describe, expect, it } from 'vitest';
import { clearPreviewNavigation, previewNavigationUrl, readPreviewNavigation } from './roleViewNavigation';
describe('preview navigation between persona subdomains', () => {
  it.each(['fleet', 'fleet__manager'])('carries role and tier (%s), preserving route, query and hash', (permissionKey) => {
    const href = previewNavigationUrl('https://dash.4truck.us/permissions?tab=roles#services',
      'fleet.4truck.us', 'fleet', permissionKey);
    expect(new URL(href).hostname).toBe('fleet.4truck.us');
    expect(readPreviewNavigation(href, ['owner', 'fleet'])).toEqual({ role: 'fleet', permissionKey });
    expect(clearPreviewNavigation(href)).toBe('/permissions?tab=roles#services');
  });
  it.each(['owner', 'owner__co', ''])('preserves owner preview or explicit self target: %s', permissionKey => {
    const href = previewNavigationUrl('https://fleet.4truck.us/chat?conversation=room-a',
      'dash.4truck.us', 'owner', permissionKey);
    expect(readPreviewNavigation(href, ['owner', 'fleet'])).toEqual({ role: 'owner', permissionKey });
    expect(clearPreviewNavigation(href)).toBe('/chat?conversation=room-a');
  });
  it('does not accept a permission row from another role', () => {
    expect(readPreviewNavigation('https://fleet.4truck.us/?view_role=fleet&view_row=owner', ['owner', 'fleet'])).toBeNull();
  });
  it('rejects unsupported roles and invalid or incomplete tiers', () => {
    for (const query of ['view_role=driver&view_tier=manager', 'view_role=fleet',
      'view_role=fleet&view_tier=superuser', 'view_tier=employee']) {
      expect(readPreviewNavigation(`https://fleet.4truck.us/?${query}`, ['owner', 'fleet'])).toBeNull();
    }
  });
});
