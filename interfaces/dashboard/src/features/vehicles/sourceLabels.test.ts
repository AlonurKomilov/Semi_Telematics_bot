import { describe, it, expect } from 'vitest';
import { orderedSources, sourceLabel, SOURCE_LABEL } from './sourceLabels';

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

