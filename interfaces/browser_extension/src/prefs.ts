/**
 * The panel's own preferences — one storage mechanism for all of them.
 *
 * Each is a boolean a person set once and expects to find again: the
 * panel is a strip beside their work, and re-making the same choice
 * every morning is the fastest way to make it feel disposable.
 *
 * ``chrome.storage.local`` (not ``session``): the choice outlives the
 * browser, unlike the connect ``state``, which must not.
 */
export async function getFlag(key: string, fallback: boolean): Promise<boolean> {
  try {
    const got = await chrome.storage.local.get(key);
    const v = got[key];
    return typeof v === 'boolean' ? v : fallback;
  } catch {
    // A storage read must never be the reason a panel does not open.
    return fallback;
  }
}

export async function setFlag(key: string, value: boolean): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch { /* the choice is lost, the session is not */ }
}


/** A remembered NUMBER — a split position, in percent of the column.
 *
 *  Kept beside the flags because it is the same kind of thing: a choice
 *  a person made once and expects to find again.  Out-of-range and
 *  unparseable values fall back rather than throw: storage survives a
 *  version where the meaning of a key changed, and a panel that refuses
 *  to render because a stored number is odd is worse than one that
 *  starts at its default.
 */
export async function getNumber(key: string, fallback: number,
                                lo: number, hi: number): Promise<number> {
  try {
    const got = await chrome.storage.local.get(key);
    const n = Number(got[key]);
    return Number.isFinite(n) && n >= lo && n <= hi ? n : fallback;
  } catch {
    return fallback;  /* storage refused; the default is a working answer */
  }
}

export async function setNumber(key: string, value: number): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch {
    /* the split is lost on the next open, the session is not */
  }
}


/* ── Follow in Google Maps ──────────────────────────────────────────
 *
 * A PANEL preference, not a feature's.  It lived in
 * features/live-map/googleMaps.ts, which made the shell's Settings
 * screen import from a feature to read it, and made Inventory import
 * from live-map for a setting about neither.  It is about what the
 * PANEL does with your Google Maps tab, so it lives with the panel's
 * other preferences.
 *
 * The Google URL and tab helpers stay in features/live-map — those are
 * genuinely about Google's map.
 */

export const FOLLOW_KEY = 'followGoogleMaps';

/** The one-time notice shown the first time following is switched on. */
export const FOLLOW_WARNED_KEY = 'followGoogleMapsWarned';

/** What the first-time notice says.  One string, because it is now
 *  shown in one place — it used to be typed out in two panels. */
export const FOLLOW_WARNING =
  'Selecting a vehicle will replace whatever is open in your Google Maps tab.';

/** OFF by default, and deliberately so.  Following REPLACES what is in
 *  the person's open Google Maps tab — a route they were planning is
 *  gone, with no undo.  A setting that can destroy somebody's work is
 *  not one to switch on for them; they turn it on knowing what it does,
 *  which is what the first-time notice is for. */
export async function getFollowPref(): Promise<boolean> {
  return getFlag(FOLLOW_KEY, false);
}
export async function setFollowPref(on: boolean): Promise<void> {
  await setFlag(FOLLOW_KEY, on);
}
export async function wasFollowWarned(): Promise<boolean> {
  return getFlag(FOLLOW_WARNED_KEY, false);
}
export async function markFollowWarned(): Promise<void> {
  await setFlag(FOLLOW_WARNED_KEY, true);
}
