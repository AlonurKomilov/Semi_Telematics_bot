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

/**
 * One of a fixed set of words.
 *
 * `getNumber` clamps a number into a range; a choice from a list needs
 * the same guard for the same reason — a value stored by an older build,
 * or by hand, must not become a state the panel cannot render.  Anything
 * not in `allowed` falls back, so a stale word costs a default and never
 * a blank map.
 */
export async function getChoice<T extends string>(
  key: string, fallback: T, allowed: readonly T[],
): Promise<T> {
  try {
    const got = await chrome.storage.local.get(key);
    const v = got[key];
    return (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;  /* storage refused; the default is a working answer */
  }
}

export async function setChoice(key: string, value: string): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch {
    /* the choice is lost on the next open, the session is not */
  }
}

/**
 * The same read, but able to say "nothing stored".
 *
 * `getChoice` cannot: its fallback is returned both for a value it
 * refused and for a key nobody ever wrote, and those are different
 * facts.  The map provider needs the difference — an account that runs
 * on Google should OPEN on Google, and only a person's own past press
 * should override that.  With `getChoice` alone the panel drew the free
 * map for everybody and called it a preference.
 */
export async function getStoredChoice<T extends string>(
  key: string, allowed: readonly T[],
): Promise<T | null> {
  try {
    const got = await chrome.storage.local.get(key);
    const v = got[key];
    return (allowed as readonly string[]).includes(v) ? (v as T) : null;
  } catch {
    return null;
  }
}

/**
 * A remembered SET of words — which map layers are switched on.
 *
 * Filtered through `allowed` on the way out for the same reason
 * `getChoice` is: a layer id that no longer exists would otherwise be
 * fetched forever, 422 forever, and show an error nobody can dismiss
 * because there is no row left to switch off.
 */
export async function getWords(key: string, allowed: readonly string[]): Promise<string[]> {
  try {
    const got = await chrome.storage.local.get(key);
    const v = got[key];
    if (!Array.isArray(v)) return [];
    return v.filter((w): w is string => typeof w === 'string' && allowed.includes(w));
  } catch {
    return [];
  }
}

export async function setWords(key: string, values: readonly string[]): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: [...values] });
  } catch { /* the layers come back off next open; nothing else breaks */ }
}

/** The basemap the panel draws, and whose it is.  Per device, like the
 *  splitter's share: two people sharing an account do not share a screen. */
export const MAP_TYPE_KEY = 'mapType';
export const MAP_PROVIDER_KEY = 'mapProvider';

/** Which overlay layers were left switched on.  Remembered because the
 *  side panel is closed and reopened all day: a driver who works with
 *  Truck parking on should not switch it on every time the panel shuts.
 *  Custom layer ids are allowed here too — they are checked against the
 *  layer list that is actually loaded, not a fixed vocabulary. */
export const POI_LAYERS_KEY = 'mapPoiLayers';

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
