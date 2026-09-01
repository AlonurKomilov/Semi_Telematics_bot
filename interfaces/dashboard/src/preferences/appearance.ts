/**
 * Appearance across devices — the seam between a DEVICE value and a
 * PERSON's default.
 *
 * The applied theme and size are `device` preferences and have to be:
 * they are stamped before the first paint (index.html), and a `synced`
 * value cannot get there — it arrives only after /user/me resolves AND
 * the bulk preference read returns, two round trips after the page has
 * already painted. A synced applied-value would show every page at the
 * default and then visibly jump.
 *
 * So the synced key holds a DEFAULT, not the applied value:
 *
 *   size / theme          device   what THIS screen renders, pre-painted
 *   appearance.default    synced   what a screen that has never been used
 *                                  should start from
 *   appearance.followMe   device   whether this screen contributes to,
 *                                  and adopts, that default
 *
 * The result a user sees: their laptop keeps its own settings forever,
 * and a browser they sign in to for the first time picks up what they
 * chose elsewhere — once, on that first load, and never again.
 */
import { get, set, reset } from './store';
import { hasStored } from './local';
import { DEFS } from './registry';
import type { ModSetting, SizeSetting } from './registry';

export interface AppearanceDefault {
  theme?: ModSetting;
  size?: SizeSetting;
}

/**
 * Publish this screen's appearance as the personal default.
 *
 * Called after the user changes something. A no-op when the user has
 * switched following off — their local changes then stay local, and any
 * previously published default is deliberately LEFT alone: switching the
 * feature off should not erase what other browsers are already using.
 * Reset is what clears it (see `resetAppearanceDefault`).
 */
export function publishAppearanceDefault(): void {
  if (!get('appearance.followMe')) return;
  set('appearance.default', {
    theme: get('mods.theme'),
    size: get('size'),
  } as AppearanceDefault);
}

/**
 * Adopt the personal default — but ONLY on a browser that has never
 * stored its own.
 *
 * The `hasStored` test is the whole safety property. Without it every
 * sign-in would overwrite whatever this screen was set to, so a monitor
 * deliberately kept at a larger size would be reset by signing in on a
 * laptop. With it, adoption happens at most once per browser.
 *
 * Returns what it adopted, for the caller to log or ignore.
 */
export function adoptAppearanceDefault(): AppearanceDefault | null {
  if (!get('appearance.followMe')) return null;

  const stored = get('appearance.default') as AppearanceDefault | null;
  if (!stored || typeof stored !== 'object') return null;

  const adopted: AppearanceDefault = {};
  // Through the registry's own sanitizers, not straight in: this payload
  // was written by a DIFFERENT device and arrives over the wire, so it is
  // untrusted like any other stored value. Skipping the guard would let a
  // malformed size (or one written by a newer client with a wider clamp)
  // set multipliers this build considers out of range.
  //
  // Each half is independent: a browser may have a theme of its own but
  // no size yet, and should then take only the size.
  const cleanTheme = DEFS['mods.theme'].sanitize?.(stored.theme) as ModSetting | undefined;
  if (cleanTheme && !hasStored('mods.theme')) {
    set('mods.theme', cleanTheme);
    adopted.theme = cleanTheme;
  }
  const cleanSize = DEFS.size.sanitize?.(stored.size) as SizeSetting | undefined;
  if (cleanSize && !hasStored('size')) {
    set('size', cleanSize);
    adopted.size = cleanSize;
  }
  return (adopted.theme || adopted.size) ? adopted : null;
}

/**
 * Forget the personal default as well as this screen's values.
 *
 * Reset has to reach the synced copy or it does not mean what it says:
 * the next unused browser would resurrect exactly the settings the user
 * just discarded.
 */
export function resetAppearanceDefault(): void {
  // `reset`, not `set(..., null)`: reset() removes the local copy AND
  // issues a backend delete, so the account row goes away instead of
  // being left holding a null that a future reader has to special-case.
  reset('appearance.default');
}
