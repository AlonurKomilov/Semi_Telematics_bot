/**
 * Tests for the Alerts persona config — the locked filter defaults
 * for each persona.
 *
 * Locks the per-persona values so a future "we should default Safety
 * to 30 days" change has to update the test too — visible diff for
 * the reviewer.
 *
 * ALSO guards the vocabulary: a default naming a type the database
 * never stores queries nothing — and because ``narrowed`` compares
 * against the default, the board renders the GENUINE all-clear over
 * thousands of open alerts.  Safety/HR shipped with
 * typeFilter='safety_events' while rows store 'events', and this
 * file's own lock asserted the broken value — locking values is not
 * the same as validating them, hence the vocabulary suite below.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import {
  PERSONA_FILTER_DEFAULTS,
  resolveFilterDefaults,
} from './personaConfig';
import { STORED_ALERT_TYPES } from './_shared/components';

afterEach(cleanup);

describe('PERSONA_FILTER_DEFAULTS — per-persona first-land values', () => {
  it('Dispatcher lands on the flat ack queue (list view, 7 days, new)', () => {
    expect(PERSONA_FILTER_DEFAULTS.dispatcher).toEqual({
      typeFilter: 'all',
      severityFilter: 'all',
      ackState: 'new',
      vehicleSearch: '',
      days: 7,
    });
  });

  it('Safety lands on safety events filtered, 7 days', () => {
    expect(PERSONA_FILTER_DEFAULTS.safety).toEqual({
      typeFilter: 'events',
      severityFilter: 'all',
      ackState: 'new',
      vehicleSearch: '',
      days: 7,
    });
  });

  it('Fleet lands on 30 days (longer maintenance-trend window)', () => {
    expect(PERSONA_FILTER_DEFAULTS.fleet.days).toBe(30);
    expect(PERSONA_FILTER_DEFAULTS.fleet.typeFilter).toBe('all');
  });

  it('HR lands on safety events, 30 days (pattern-finding window)', () => {
    expect(PERSONA_FILTER_DEFAULTS.hr.typeFilter).toBe('events');
    expect(PERSONA_FILTER_DEFAULTS.hr.days).toBe(30);
  });

  it('Driver lands on a short own-truck window (Mini App is primary)', () => {
    expect(PERSONA_FILTER_DEFAULTS.driver.days).toBe(7);
    expect(PERSONA_FILTER_DEFAULTS.driver.ackState).toBe('new');
  });
});

describe('persona defaults stay inside the stored vocabulary', () => {
  const allowed = new Set<string>(['all', ...STORED_ALERT_TYPES]);

  it('every persona typeFilter is a type the database can produce', () => {
    for (const [persona, defaults] of Object.entries(PERSONA_FILTER_DEFAULTS)) {
      expect(
        allowed.has(defaults.typeFilter),
        `${persona}: typeFilter '${defaults.typeFilter}' is not a stored alert_type`,
      ).toBe(true);
    }
  });

  it('severity defaults use the fixed severity vocabulary', () => {
    const sev = new Set(['all', 'critical', 'warning', 'info']);
    for (const [persona, defaults] of Object.entries(PERSONA_FILTER_DEFAULTS)) {
      expect(
        sev.has(defaults.severityFilter),
        `${persona}: severityFilter '${defaults.severityFilter}'`,
      ).toBe(true);
    }
  });
});

describe('resolveFilterDefaults — fallback chain', () => {
  it('returns the persona entry when present', () => {
    expect(resolveFilterDefaults('safety').typeFilter).toBe('events');
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
