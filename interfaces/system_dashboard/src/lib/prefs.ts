/** Per-operator UI state, in one place with the keys written down.
 *
 *  Two pages were calling localStorage directly, each with its own
 *  try/catch and its own string key. The try/catch is not optional —
 *  a private window throws on the FIRST call, not on write — and the
 *  keys are not free-form: renaming one silently orphans whatever that
 *  operator had already chosen, with no error and no way back.
 *
 *  So the keys live here, frozen, and everything else asks this module.
 *  Anything the SERVER acts on is not a preference and does not belong
 *  in here — it is data, and it goes to the API.
 */

//: Frozen. Renaming a value here throws away every operator's setting
//: for it. Add a new key instead and migrate if it is worth migrating.
export const PREF_KEYS = {
  /** Security page: how many hours back the candidate list looks. */
  securityHours: 'sec.hours',
  /** Security page: the account filter, '' for every account. */
  securityAccount: 'sec.account',
} as const;

export type PrefKey = (typeof PREF_KEYS)[keyof typeof PREF_KEYS];

export function readPref(key: PrefKey): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    // A private window refuses storage outright; the caller's default is
    // the right answer and there is nothing to report.
    return null;
  }
}

export function writePref(key: PrefKey, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Same: the setting simply does not persist for this session.
  }
}
