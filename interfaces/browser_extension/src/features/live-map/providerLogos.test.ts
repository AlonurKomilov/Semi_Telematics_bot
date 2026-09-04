/**
 * The panel's provider registry.
 *
 * The ARTWORK is guarded in Python — tests/test_provider_marks_generated.py
 * re-derives every client's providerMarks.tsx from the canonical asset,
 * so one check covers this app and the dashboard together. What is
 * per-app, and therefore tested here, is how a mark is USED.
 */
import { describe, it, expect } from 'vitest';
import { PROVIDER_LOGO, orderedSources, providerLogo, sourceLabel } from './providerLogos';

describe('the panel decides how a mark is used', () => {
  it('Samsara is a wordmark, so it stands in PLACE of the name', () => {
    expect(providerLogo('samsara')?.kind).toBe('wordmark');
  });
  it('a provider with no mark is simply its name — a complete answer', () => {
    expect(providerLogo('datatruck')).toBeNull();
    expect(providerLogo('manual')).toBeNull();
  });
  it('every registered mark has a component to draw it', () => {
    for (const [, logo] of Object.entries(PROVIDER_LOGO)) {
      expect(typeof logo.Mark).toBe('function');
      expect(['wordmark', 'glyph']).toContain(logo.kind);
    }
  });
});

describe('the panel and the dashboard say the same words', () => {
  it('manual reads as Local', () => expect(sourceLabel('manual')).toBe('Local'));
  it('an unnamed provider still reads as a name', () => expect(sourceLabel('motive')).toBe('Motive'));
  it('creator first, empties dropped', () => {
    expect(orderedSources(['samsara', '', 'manual'], 'samsara')).toEqual(['samsara', 'manual']);
    expect(orderedSources(null, 'datatruck')).toEqual(['datatruck']);
    expect(orderedSources(null, null)).toEqual([]);
  });
});
