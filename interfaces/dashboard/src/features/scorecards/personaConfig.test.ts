/**
 * Tests for the Scorecards persona config — locks each persona's page
 * block set so changing who sees what is a visible diff for the reviewer.
 */
import { describe, it, expect } from 'vitest';
import { BLOCKS_FOR_PERSONA, blocksForPersona } from './personaConfig';
import type { Persona } from '../_lib/types';

const ALL_PERSONAS: Persona[] = [
  'owner', 'admin', 'fleet', 'dispatcher', 'safety', 'driver', 'hr', 'accounting',
  'recruiter',
];

describe('BLOCKS_FOR_PERSONA — per-persona page blocks', () => {
  it('covers every persona explicitly', () => {
    for (const p of ALL_PERSONAS) expect(BLOCKS_FOR_PERSONA[p]).toBeDefined();
  });

  it('the table renders for everyone — it IS the feature', () => {
    for (const p of ALL_PERSONAS) expect(blocksForPersona(p)).toContain('table');
  });

  it('analysis personas get the full page', () => {
    const all = ['kpi', 'distribution', 'top_bottom', 'table'];
    for (const p of ['owner', 'admin', 'safety', 'fleet', 'hr'] as Persona[]) {
      expect(blocksForPersona(p)).toEqual(all);
    }
  });

  it('dispatch + accounting get the quick-lookup page (KPI + table)', () => {
    expect(blocksForPersona('dispatcher')).toEqual(['kpi', 'table']);
    expect(blocksForPersona('accounting')).toEqual(['kpi', 'table']);
  });

  it('a driver sees only the table — fleet aggregates are noise for one card', () => {
    expect(blocksForPersona('driver')).toEqual(['table']);
  });
});
