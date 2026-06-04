/**
 * Tests for the Alerts persona config — the locked filter defaults
 * for each persona.
 *
 * Locks the per-persona values so a future "we should default Safety
 * to 30 days" change has to update the test too — visible diff for
 * the reviewer.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import {
  PERSONA_FILTER_DEFAULTS,
  resolveFilterDefaults,
} from './personaConfig';

afterEach(cleanup);

describe('PERSONA_FILTER_DEFAULTS — per-persona first-land values', () => {
  it('Dispatcher lands on the flat ack queue (list view, 7 days, active)', () => {
    expect(PERSONA_FILTER_DEFAULTS.dispatcher).toEqual({
      viewMode: 'list',
      typeFilter: 'all',
      severityFilter: 'all',
      ackState: 'active',
      vehicleSearch: '',
      days: 7,
    });
  });

  it('Safety lands on safety_events filtered by-vehicle, 7 days', () => {
    expect(PERSONA_FILTER_DEFAULTS.safety).toEqual({
      viewMode: 'by-vehicle',
      typeFilter: 'safety_events',
      severityFilter: 'all',
      ackState: 'active',
      vehicleSearch: '',
      days: 7,
    });
  });

  it('Fleet lands on by-vehicle, 30 days (longer maintenance-trend window)', () => {
    expect(PERSONA_FILTER_DEFAULTS.fleet.viewMode).toBe('by-vehicle');
    expect(PERSONA_FILTER_DEFAULTS.fleet.days).toBe(30);
    expect(PERSONA_FILTER_DEFAULTS.fleet.typeFilter).toBe('all');
  });

  it('HR lands on safety_events, 30 days (pattern-finding window)', () => {
    expect(PERSONA_FILTER_DEFAULTS.hr.typeFilter).toBe('safety_events');
    expect(PERSONA_FILTER_DEFAULTS.hr.days).toBe(30);
  });

  it('Driver lands on list view (Mini App is primary; web is fallback)', () => {
    expect(PERSONA_FILTER_DEFAULTS.driver.viewMode).toBe('list');
  });
});

describe('resolveFilterDefaults — fallback chain', () => {
  it('returns the persona entry when present', () => {
    expect(resolveFilterDefaults('safety').typeFilter).toBe('safety_events');
  });

  it('every canonical persona has an entry (no fallback ever triggers in real code)', () => {
    const personas = [
      'owner', 'admin', 'fleet', 'dispatcher', 'safety',
      'hr', 'accounting', 'driver',
    ] as const;
    for (const p of personas) {
      expect(PERSONA_FILTER_DEFAULTS[p]).toBeDefined();
    }
  });
});
