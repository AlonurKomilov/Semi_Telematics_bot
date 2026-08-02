import { describe, it, expect } from 'vitest';
import {
  intakeStateOf, INTAKE_LABEL, INTAKE_TONE, INTAKE_FILTER, type IntakeState,
} from './intakeState';

const DAY = 864e5;
const future = new Date(Date.now() + 30 * DAY).toISOString();
const past = new Date(Date.now() - 30 * DAY).toISOString();

const CODES: IntakeState[] = ['review', 'awaiting', 'lapsed', 'done', 'none'];

describe('intakeStateOf — precedence', () => {
  it('needs-review outranks everything', () => {
    expect(intakeStateOf({
      intake_review_pending: 1,
      intake_submitted_at: '2026-01-01',
      intake_expires_at: future,
    })).toBe('review');
  });

  it('a submitted profile is done, whatever the expiry says', () => {
    expect(intakeStateOf({ intake_submitted_at: '2026-01-01', intake_expires_at: past })).toBe('done');
    // Blank-but-present must not count as submitted.
    expect(intakeStateOf({ intake_submitted_at: '   ', intake_expires_at: future })).toBe('awaiting');
  });

  it('no expiry means never invited (or revoked)', () => {
    expect(intakeStateOf({})).toBe('none');
    expect(intakeStateOf({ intake_expires_at: null })).toBe('none');
  });

  it('an unexpired link is awaiting, an expired one lapsed', () => {
    expect(intakeStateOf({ intake_expires_at: future })).toBe('awaiting');
    expect(intakeStateOf({ intake_expires_at: past })).toBe('lapsed');
  });
});

// The silent-failure guard.  The "Fill link" column filters on the CODE
// (filterValue) and displays the LABEL (filterLabel), because a saved tab
// PERSISTS whatever filterValue returns.  If the two vocabularies ever
// overlap, a label could be stored as a criterion and the next rewording
// would strand every stored tab at zero rows with no error.
describe('code / label separation — what a saved tab persists', () => {
  it('every state has a label and a tone', () => {
    for (const c of CODES) {
      expect(INTAKE_LABEL[c], `label for ${c}`).toBeTruthy();
      expect(INTAKE_TONE[c], `tone for ${c}`).toBeTruthy();
    }
    expect(Object.keys(INTAKE_LABEL).sort()).toEqual([...CODES].sort());
  });

  it('no label is also a state code', () => {
    for (const label of Object.values(INTAKE_LABEL)) {
      expect(CODES).not.toContain(label as IntakeState);
    }
  });

  it('codes are stable identifiers, not prose', () => {
    // Read off the MODULE, not the fixture above — a code added later
    // has to pass this too.  A code that reads like a sentence is a
    // label wearing a code's hat.
    for (const c of Object.keys(INTAKE_LABEL)) expect(c).toMatch(/^[a-z]+$/);
  });
});

// Pins the accessors the column actually spreads — not just the
// vocabulary.  If someone drops them, the column silently falls back to
// matching the raw row value, which is the label.
describe('INTAKE_FILTER — what the column spreads', () => {
  const row = { intake_expires_at: future };

  it('filterValue yields the CODE (what a saved tab stores)', () => {
    expect(INTAKE_FILTER.filterValue(row)).toBe('awaiting');
    expect(CODES).toContain(INTAKE_FILTER.filterValue(row));
  });

  it('filterLabel yields the LABEL (what the dropdown shows)', () => {
    expect(INTAKE_FILTER.filterLabel(row)).toBe('Awaiting carrier');
  });

  it('the two never return the same string for any state', () => {
    const rows: Array<[IntakeState, Parameters<typeof intakeStateOf>[0]]> = [
      ['review', { intake_review_pending: 1 }],
      ['awaiting', { intake_expires_at: future }],
      ['lapsed', { intake_expires_at: past }],
      ['done', { intake_submitted_at: '2026-01-01' }],
      ['none', {}],
    ];
    for (const [expected, r] of rows) {
      expect(INTAKE_FILTER.filterValue(r)).toBe(expected);
      expect(INTAKE_FILTER.filterValue(r)).not.toBe(INTAKE_FILTER.filterLabel(r));
    }
  });
});
