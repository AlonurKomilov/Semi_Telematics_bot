import { describe, it, expect } from 'vitest';

import { parseAiAnalysis } from './aiAnalysis';

/**
 * The contract this pins: a parse failure must DEGRADE, never blank the
 * panel.  When neither field matches, the raw text has to survive in
 * ``rest`` — otherwise a reworded prompt silently renders an empty
 * explanation and looks like "the AI said nothing" rather than "we
 * couldn't read what it said".
 */
describe('parseAiAnalysis', () => {
  const CANONICAL = [
    'CLASSIFICATION: UNSAFE',
    'CONFIDENCE: HIGH',
    'REASON: The truck is parked on the shoulder of a major road, with no',
    'visible parking lot or truck stop nearby.',
  ].join('\n');

  it('pulls confidence and reason out of the canonical block', () => {
    const p = parseAiAnalysis(CANONICAL);
    expect(p.confidence).toBe('HIGH');
    expect(p.reason).toContain('parked on the shoulder');
    expect(p.rest).toBe('');
  });

  it('keeps a multi-line reason whole', () => {
    const p = parseAiAnalysis(CANONICAL);
    expect(p.reason).toContain('visible parking lot or truck stop nearby.');
  });

  it('drops CLASSIFICATION — the badge already says it', () => {
    const p = parseAiAnalysis(CANONICAL);
    expect(p.reason).not.toContain('CLASSIFICATION');
    expect(p.reason).not.toContain('UNSAFE');
  });

  it('stops the reason at the next FIELD: header, not mid-sentence', () => {
    const p = parseAiAnalysis(
      'REASON: Parked on a ramp.\nNOTE: something else entirely',
    );
    expect(p.reason).toBe('Parked on a ramp.');
  });

  it('tolerates lowercase / padded field names', () => {
    const p = parseAiAnalysis('  confidence:  medium \n  reason:  Ramp. ');
    expect(p.confidence).toBe('medium');
    expect(p.reason).toBe('Ramp.');
  });

  it('falls through unparseable text into rest rather than hiding it', () => {
    const p = parseAiAnalysis('The truck seems fine to me.');
    expect(p.confidence).toBe('');
    expect(p.reason).toBe('');
    expect(p.rest).toBe('The truck seems fine to me.');
  });

  it('returns all-empty for empty input without throwing', () => {
    expect(parseAiAnalysis('')).toEqual({ confidence: '', reason: '', rest: '' });
  });

  it('sets rest ONLY when nothing parsed', () => {
    // A partial parse must not ALSO dump the raw text — that would
    // render the reason twice.
    const p = parseAiAnalysis('CONFIDENCE: LOW');
    expect(p.confidence).toBe('LOW');
    expect(p.rest).toBe('');
  });
});
