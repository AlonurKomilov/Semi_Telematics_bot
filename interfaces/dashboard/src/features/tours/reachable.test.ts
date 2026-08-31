/**
 * The library's gate — a tour card exists only where its page does.
 */
import { describe, expect, it } from 'vitest';
import { reachableFeature } from './reachable';

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
