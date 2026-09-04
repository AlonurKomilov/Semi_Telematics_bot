import { describe, it, expect } from 'vitest';
import { orderedSources, positionSources, sourceLabel, PROVIDER_ROLE, SOURCE_LABEL } from './sourceLabels';

describe('source labels', () => {
  it('the wire word manual reads as Local', () => {
    expect(sourceLabel('manual')).toBe('Local');
    expect(SOURCE_LABEL.manual).toBe('Local');
  });
  it('a provider we have not named yet still reads as a name', () => {
    expect(sourceLabel('motive')).toBe('Motive');
    expect(sourceLabel('')).toBe('');
  });
  it('creator first, then the enrichers, in the order the server gave', () => {
    expect(orderedSources(['samsara', 'datatruck'], 'samsara')).toEqual(['samsara', 'datatruck']);
  });
  it('falls back to the single value when the list is absent', () => {
    expect(orderedSources(null, 'datatruck')).toEqual(['datatruck']);
    expect(orderedSources([], 'manual')).toEqual(['manual']);
  });
  it('a truck the registry has not caught yet has nothing to state', () => {
    expect(orderedSources(null, null)).toEqual([]);
    expect(orderedSources([], '')).toEqual([]);
  });
  it('drops empties rather than drawing a blank chip', () => {
    expect(orderedSources(['samsara', '', 'manual'], 'samsara')).toEqual(['samsara', 'manual']);
  });
});


describe('a map speaks only for who supplies the position', () => {
  it('a TMS does not get credit for a moving truck', () => {
    expect(positionSources(['samsara', 'datatruck'])).toEqual(['samsara']);
    expect(positionSources(['datatruck'])).toEqual([]);
    expect(positionSources(['manual'])).toEqual([]);
  });
  it('an unknown provider is excluded, not assumed', () => {
    expect(positionSources(['someone-new'])).toEqual([]);
  });
  it('every provider we can name has a declared role', () => {
    for (const key of Object.keys(SOURCE_LABEL)) {
      expect(PROVIDER_ROLE[key]).toBeDefined();
    }
  });
  it('the two apps agree on who does what — the panel keeps its own copy', () => {
    expect(PROVIDER_ROLE.samsara).toBe('telematics');
    expect(PROVIDER_ROLE.datatruck).toBe('tms');
    expect(PROVIDER_ROLE.manual).toBe('local');
  });
});
