// The derived self-fill state of one carrier — the thing a recruiting
// manager is actually triaging, and the value the grid's "Fill link"
// column sorts, filters and counts by.
//
// A leaf module (no React, no imports) so the code↔label split below can
// be unit-tested: it is the kind of mistake that fails SILENTLY.

/** Order matters: needs-review outranks everything. */
export type IntakeState = 'review' | 'awaiting' | 'lapsed' | 'done' | 'none';

/** The fields of a carrier row this derivation reads.  Structural, so a
 *  fuller row satisfies it without a cast at the call site. */
export interface IntakeFields {
  intake_review_pending?: number | boolean;
  intake_expires_at?: string | null;
  intake_submitted_at?: string;
}

export function intakeStateOf(r: IntakeFields): IntakeState {
  if (r.intake_review_pending) return 'review';
  const exp = r.intake_expires_at ? new Date(r.intake_expires_at).getTime() : 0;
  const submitted = Boolean((r.intake_submitted_at || '').trim());
  if (submitted) return 'done';
  if (!exp) return 'none';           // never invited, or revoked (expiry NULLed)
  return exp > Date.now() ? 'awaiting' : 'lapsed';
}

// One noun for one object: the shareable URL is a FILL LINK in the column
// header, every tab, the detail status line and every toast. It had picked
// up five names ("Self-fill", "Carrier fill link", "invite link", …) and a
// reader can't tell whether those are one thing or several.
//
// ⚠️ These are LABELS — display text, reworded whenever the wording
// improves (this map has been reworded once already).  They must never
// become the value anything PERSISTS.  The grid column therefore filters
// on the state CODE via ``filterValue`` and shows this only via
// ``filterLabel``; a saved tab captures the code, so a future rewording
// cannot strand it.  ``intakeState.test.ts`` pins the two apart.
export const INTAKE_LABEL: Record<IntakeState, string> = {
  review: 'Needs review',
  awaiting: 'Awaiting carrier',
  lapsed: 'Lapsed',
  done: 'Filled in',
  none: '—',
};

export const INTAKE_TONE: Record<IntakeState, 'info' | 'warn' | 'danger' | 'ok' | 'neutral'> = {
  review: 'info', awaiting: 'neutral', lapsed: 'danger', done: 'ok', none: 'neutral',
};

/** The "Fill link" column's filter accessors, as ONE spread — so the
 *  code-matches / label-displays pair cannot be half-applied.  Dropping
 *  either one silently reverts the column to matching on the raw row
 *  value, which is the label. */
export const INTAKE_FILTER = {
  filterValue: (row: IntakeFields) => intakeStateOf(row),
  filterLabel: (row: IntakeFields) => INTAKE_LABEL[intakeStateOf(row)],
};
