import { describe, it, expect } from 'vitest';
import { generatePassphrase, PASSPHRASE_PATTERN } from './passphrase';

describe('generatePassphrase', () => {
  it('matches the documented shape', () => {
    for (let i = 0; i < 50; i += 1) {
      expect(generatePassphrase()).toMatch(PASSPHRASE_PATTERN);
    }
  });

  it('never emits a character people transcribe wrong', () => {
    // 0/O and 1/I are the pairs that break when this is read off a screen
    // and typed into a PDF prompt somewhere else. L is deliberately NOT
    // in this list — see the alphabet comment: it is unambiguous in
    // upper case, and removing it would cost the unbiased 32-symbol
    // mapping. An earlier version of this test asserted L was excluded
    // and failed against correct code.
    const joined = Array.from({ length: 200 }, generatePassphrase).join('');
    for (const bad of ['0', 'O', '1', 'I']) {
      expect(joined).not.toContain(bad);
    }
  });

  it('does not repeat', () => {
    // 100 bits — a collision in 500 draws would mean the generator is not
    // actually random (e.g. someone swapped in Math.random with a fixed
    // seed, or the alphabet index collapsed).
    const seen = new Set(Array.from({ length: 500 }, generatePassphrase));
    expect(seen.size).toBe(500);
  });

  it('uses the whole alphabet', () => {
    // A masking bug (& 15 instead of & 31) would silently halve the
    // alphabet and the entropy, and every other test above would pass.
    const joined = Array.from({ length: 500 }, generatePassphrase).join('');
    const used = new Set(joined.replace(/-/g, ''));
    expect(used.size).toBe(32);
  });
});
