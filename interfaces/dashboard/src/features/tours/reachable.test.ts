/**
 * The library's gate — a tour card exists only where its page does.
 */
import { describe, expect, it } from 'vitest';
import { reachableFeature } from './reachable';
import { bulkAdd } from '../maintenance/tour/bulkAdd';

const access = (flags: string[], modules?: string[]) => ({
  hasAny: (...want: string[]) => want.some((w) => flags.includes(w)),
  enabledModules: modules,
});

describe('reachableFeature', () => {
  it('grants maintenance to a role holding either of its flags', () => {
    expect(reachableFeature('maintenance',
      access(['can_maintenance_vehicle']))?.path).toBe('/maintenance');
  });
  it('refuses maintenance to a role with neither flag', () => {
    expect(reachableFeature('maintenance', access([]))).toBeNull();
  });
  it('a permissionless feature needs only its module', () => {
    expect(reachableFeature('knowledge_base', access([]))?.path).toBe('/knowledge');
  });
  it('an unknown feature id is a null, never a throw', () => {
    expect(reachableFeature('not_a_feature', access([]))).toBeNull();
  });
});

describe('the library never offers what the viewer cannot do', () => {
  // The layered answer to "can a tour expose a feature the role is
  // denied?" — three independent gates, and this file owns the first.
  it('a feature the role lacks yields no card at all', () => {
    // The owner's scenario: fleet role, applications denied.  Even
    // once an applications tour exists, reachableFeature refuses it.
    expect(reachableFeature('applications', access(['can_maintenance_all'])))
      .toBeNull();
  });

  it('module enablement refuses it too, independently of permission', () => {
    // An account that never bought the module: permission alone must
    // not open the door.
    expect(reachableFeature('applications',
      access(['can_manage_applications'], ['core']))).toBeNull();
  });

  it('a wider PAGE grant does not imply the tour is offerable', () => {
    // Maintenance opens on _vehicle; the bulk-add walk needs _all.
    // reachableFeature answers only for the page — the library asks
    // the tour's own `requires` on top (ToursPage), and the guard in
    // tour.test.ts makes a write tour declare it.
    const viewer = access(['can_maintenance_vehicle']);
    expect(reachableFeature('maintenance', viewer)?.path).toBe('/maintenance');
    expect(bulkAdd.requires).toContain('can_maintenance_all');
    expect(viewer.hasAny(...(bulkAdd.requires ?? []))).toBe(false);
  });
});
