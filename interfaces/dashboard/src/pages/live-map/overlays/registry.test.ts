import { describe, it, expect } from 'vitest';
import { OVERLAYS } from './index';

/**
 * Every persona the dashboard recognises (must match VIEW_LABELS in
 * RoleViewContext).  Duplicated here on purpose: when someone adds a
 * new role they need to wire an overlay for it, and the duplication
 * is what forces that conversation — both lists must move together.
 */
const KNOWN_PERSONAS = ['owner', 'admin', 'fleet', 'dispatcher', 'safety', 'driver'];

describe('OVERLAYS registry', () => {
  it('has an entry for every known persona', () => {
    for (const persona of KNOWN_PERSONAS) {
      expect(OVERLAYS[persona], `missing overlay for ${persona}`).toBeDefined();
      expect(typeof OVERLAYS[persona]).toBe('function');
    }
  });

  it('does not expose unknown personas (catches accidental typos)', () => {
    for (const persona of Object.keys(OVERLAYS)) {
      expect(KNOWN_PERSONAS, `unknown persona key: ${persona}`).toContain(persona);
    }
  });
});
