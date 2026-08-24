import { describe, it, expect } from 'vitest';
import { toneClasses, statusClasses, statusTone, type Tone } from './status';

/**
 * Regression guard for the soft-pill border contract.
 *
 * ``toneClasses`` emits ``border-<hue>-bd``, which sets border-COLOUR
 * only.  Tailwind's preflight zeroes border-width on every element, so
 * without the bare ``border`` utility that colour paints a 0px edge —
 * an invisible border that reads, at a call-site, exactly like a
 * deliberate borderless design.  It shipped that way at 139 of 213
 * call-sites before anyone noticed, against a design.md §3 rule that
 * has always said the soft pill carries a 30% border.
 *
 * These tests pin BOTH halves of the contract so it cannot regress:
 * the width ships by default, and the ``{ border: false }`` escape
 * (for fixed-height chips whose line-height leaves no room for a
 * hairline) keeps working and stays greppable.
 */
const TONES: Tone[] = ['ok', 'warn', 'danger', 'info', 'neutral'];

describe('toneClasses border contract', () => {
  it('emits the width that makes the border colour visible', () => {
    for (const tone of TONES) {
      const cls = toneClasses(tone).split(/\s+/);
      expect(cls, tone).toContain('border');
    }
  });

  it('{ border: false } drops the width for fixed-height chips', () => {
    for (const tone of TONES) {
      const cls = toneClasses(tone, { border: false }).split(/\s+/);
      expect(cls, tone).not.toContain('border');
      // …but keeps the colour, so the chip still matches its tone.
      expect(cls.some((c) => c.startsWith('border-')), tone).toBe(true);
    }
  });

  it('every tone pre-wires a border COLOUR for the opt-in to reveal', () => {
    for (const tone of TONES) {
      // neutral borrows the shared surface token rather than a hue.
      const expected = tone === 'neutral' ? 'border-border' : `border-${tone}-bd`;
      expect(toneClasses(tone).split(/\s+/), tone).toContain(expected);
    }
  });

  it('statusClasses forwards the option through the status map', () => {
    expect(statusClasses('overdue').split(/\s+/)).toContain('border');
    expect(statusClasses('overdue', { border: false }).split(/\s+/)).not.toContain('border');
    // …and still resolves the domain string to the right tone.
    expect(statusTone('overdue')).toBe('danger');
    expect(statusClasses('overdue')).toBe(toneClasses('danger'));
  });
});
