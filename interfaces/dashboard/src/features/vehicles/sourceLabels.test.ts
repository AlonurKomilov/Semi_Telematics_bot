import { describe, it, expect } from 'vitest';
import { orderedSources, providerLogo, sourceLabel, PROVIDER_LOGO, SOURCE_LABEL, type ProviderLogo } from './sourceLabels';

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

describe('provider logos', () => {
  it('none yet — the marks render as words, which is the shipped state', () => {
    expect(providerLogo('samsara')).toBeNull();
    expect(providerLogo('datatruck')).toBeNull();
    expect(PROVIDER_LOGO).toEqual({});
  });
  it('an entry, when one lands, is a src and optionally a dark variant', () => {
    const withLogo: Record<string, ProviderLogo> = {
      samsara: { src: '/integrations/samsara/logo.svg' },
      datatruck: { src: '/a.svg', srcDark: '/a-dark.svg' },
    };
    expect(withLogo.samsara.srcDark).toBeUndefined();
    expect(withLogo.datatruck.srcDark).toBe('/a-dark.svg');
  });
});
