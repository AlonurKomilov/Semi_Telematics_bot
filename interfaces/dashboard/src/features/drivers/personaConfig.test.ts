/**
 * Tests for the Drivers persona config — locks each persona's drawer
 * tab set so changing who sees what is a visible diff for the reviewer.
 */
import { describe, it, expect } from 'vitest';
import {
  TABS_FOR_PERSONA,
  tabsForPersona,
  showsExpiringBanner,
} from './personaConfig';
import type { Persona } from '../_lib/types';

const ALL_PERSONAS: Persona[] = [
  'owner', 'admin', 'fleet', 'dispatcher', 'safety', 'driver', 'hr', 'accounting',
  'recruiter',
];

describe('TABS_FOR_PERSONA — per-persona drawer tab sets', () => {
  it('covers every persona explicitly (no superset fallback in practice)', () => {
    for (const p of ALL_PERSONAS) expect(TABS_FOR_PERSONA[p]).toBeDefined();
  });

  it('every persona starts on Profile (the detailTab reset target)', () => {
    for (const p of ALL_PERSONAS) expect(tabsForPersona(p)[0]).toBe('profile');
  });

  it('Owner/Admin keep the full superset in the original order', () => {
    const all = ['profile', 'vehicles', 'documents', 'inspections', 'trainings', 'hos'];
    expect(tabsForPersona('owner')).toEqual(all);
    expect(tabsForPersona('admin')).toEqual(all);
  });

  it('Dispatch is assignment + availability only (no docs/trainings)', () => {
    expect(tabsForPersona('dispatcher')).toEqual(['profile', 'vehicles', 'hos']);
  });

  it('Safety has compliance + behavior but not vehicle assignment', () => {
    expect(tabsForPersona('safety')).toEqual(
      ['profile', 'documents', 'inspections', 'trainings', 'hos'],
    );
  });

  it('HR owns people/compliance; HOS (ops) is omitted', () => {
    expect(tabsForPersona('hr')).toEqual(
      ['profile', 'vehicles', 'documents', 'inspections', 'trainings'],
    );
  });
});

describe('showsExpiringBanner — follows the Documents tab', () => {
  it('document-actioning personas see the expiring banner', () => {
    for (const p of ['owner', 'admin', 'hr', 'fleet', 'safety', 'driver'] as Persona[]) {
      expect(showsExpiringBanner(p)).toBe(true);
    }
  });
  it('dispatch + accounting do not', () => {
    expect(showsExpiringBanner('dispatcher')).toBe(false);
    expect(showsExpiringBanner('accounting')).toBe(false);
  });
});
