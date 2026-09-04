import { describe, it, expect } from 'vitest';
import { isWideScope, hasWideScope } from './unitWidth';

describe('unit width (Team Management\'s answer)', () => {
  it('only "assigned" is narrow — missing width is wide, as the nav rule', () => {
    expect(isWideScope('all')).toBe(true);
    expect(isWideScope(undefined)).toBe(true);
    expect(isWideScope(null)).toBe(true);
    expect(isWideScope('assigned')).toBe(false);
  });

  it('hasWide asks both questions: the verb AND the width', () => {
    const grants = new Set(['can_view_vehicles']);
    const has = (...f: string[]) => f.some((k) => grants.has(k));
    expect(hasWideScope(has, 'all', 'can_view_vehicles')).toBe(true);
    // narrow member: the verb alone is not enough
    expect(hasWideScope(has, 'assigned', 'can_view_vehicles')).toBe(false);
    // wide member without the verb: the width alone is not enough
    expect(hasWideScope(has, 'all', 'can_view_parking')).toBe(false);
  });
});
