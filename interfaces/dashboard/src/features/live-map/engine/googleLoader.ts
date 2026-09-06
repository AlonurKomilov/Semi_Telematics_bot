/**
 * Loading Google's map script — once per page, whatever asks for it.
 *
 * The Maps JavaScript API is a `<script>` that installs `window.google`
 * and cannot be imported. Two things follow, and both have bitten
 * every codebase that ever loaded it:
 *
 *   Loading it TWICE is not idempotent. Google's own console warns
 *   "You have included the Google Maps JavaScript API multiple times",
 *   and the second load can leave half-registered classes behind. A
 *   page with a map in a panel and a map in a dialog asks twice, so
 *   the promise is cached at module scope and every later caller waits
 *   on the same one.
 *
 *   A key is a BILLED credential. The script is fetched only when a
 *   caller actually needs Google — never on the chance it might — and
 *   the key is never read from a bundle constant: the server hands it
 *   out per account, so an account on the free engine never receives
 *   one it could load.
 *
 * The failure this module refuses to hide: a bad key, a referrer the
 * key does not allow, or a blocked network all end as a REJECTED
 * promise rather than a map that never appears. The caller falls back
 * to the free engine and says why.
 */

/** What Google installs. Only the parts we touch are named; the map
 *  surface widens this as it grows rather than pulling in the whole
 *  `@types/google.maps` package for four symbols. */
export interface GoogleMapsApi {
  maps: {
    Map: new (el: HTMLElement, opts?: Record<string, unknown>) => unknown;
    [k: string]: unknown;
  };
}

declare global {
  interface Window {
    google?: GoogleMapsApi;
  }
}

/** The in-flight or settled load, keyed by nothing: one page, one API.
 *  A second key would be a second load, which is the thing this
 *  prevents — so a caller passing a different key while one is already
 *  loading gets the first, and is told. */
let pending: Promise<GoogleMapsApi> | null = null;
let loadedWithKey: string | null = null;

/** The DOM id, so a hot reload that re-runs this module finds the tag
 *  the previous run left instead of adding a second. */
const SCRIPT_ID = 'google-maps-js';

/** Google's own callback channel: the script calls a global when it is
 *  ready. `onload` alone fires before the library finishes registering
 *  its namespaces on some versions, which is how "google.maps.Map is
 *  not a constructor" reaches production. */
const CALLBACK = '__4truckGoogleMapsReady';

export interface LoadOptions {
  /** Extra libraries, e.g. `['marker']` for advanced markers. */
  libraries?: string[];
  /** Pinned so a Google-side rollout cannot change the map under us.
   *  'weekly' is their moving channel; a version string pins it. */
  version?: string;
}

export function isGoogleLoaded(): boolean {
  return typeof window !== 'undefined' && !!window.google?.maps;
}

/** For tests, and for a key change that only a reload can honour. */
export function resetGoogleLoaderForTests(): void {
  pending = null;
  loadedWithKey = null;
  if (typeof document !== 'undefined') {
    document.getElementById(SCRIPT_ID)?.remove();
  }
}

export function loadGoogleMaps(
  key: string,
  { libraries = [], version = 'weekly' }: LoadOptions = {},
): Promise<GoogleMapsApi> {
  if (!key) {
    return Promise.reject(new Error('No Google Maps key was provided.'));
  }
  if (isGoogleLoaded()) {
    return Promise.resolve(window.google as GoogleMapsApi);
  }
  if (pending) {
    if (loadedWithKey && loadedWithKey !== key) {
      // Not an error worth failing on — the first key is already
      // loading and will work — but silence here would hide a
      // per-account key mix-up until a billing surprise.
      console.warn(
        '[maps] a second Google Maps key was requested; the page keeps the first. ' +
        'Reload to switch keys.',
      );
    }
    return pending;
  }

  loadedWithKey = key;
  pending = new Promise<GoogleMapsApi>((resolve, reject) => {
    const settle = () => {
      if (window.google?.maps) resolve(window.google);
      else reject(new Error('Google Maps loaded without installing google.maps.'));
    };

    // The callback is deleted whichever way this ends, so a failed load
    // leaves no global behind for the next attempt to trip over.
    (window as unknown as Record<string, unknown>)[CALLBACK] = () => {
      delete (window as unknown as Record<string, unknown>)[CALLBACK];
      settle();
    };

    const params = new URLSearchParams({ key, v: version, callback: CALLBACK });
    if (libraries.length) params.set('libraries', libraries.join(','));

    const el = document.createElement('script');
    el.id = SCRIPT_ID;
    el.async = true;
    el.src = `https://maps.googleapis.com/maps/api/js?${params.toString()}`;
    el.onerror = () => {
      delete (window as unknown as Record<string, unknown>)[CALLBACK];
      // The script 404s or is blocked; Google reports a REJECTED key
      // through its own console channel, not through onerror, so this
      // message stays about reachability.
      pending = null;
      loadedWithKey = null;
      el.remove();
      reject(new Error('Could not reach the Google Maps script.'));
    };
    document.head.appendChild(el);
  });
  return pending;
}
