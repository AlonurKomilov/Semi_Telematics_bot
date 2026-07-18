/**
 * OS-aware keyboard-shortcut display.
 *
 * The key HANDLERS already accept both modifiers (`e.metaKey ||
 * e.ctrlKey`); this is purely about what we SHOW.  A Windows/Linux user
 * seeing "⌘K" is wrong — their key is Ctrl.  One detector, one
 * formatter, used by every shortcut label so the whole app matches the
 * user's actual keyboard.
 */

function detectMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  // userAgentData.platform is the modern signal; fall back to the
  // (deprecated but universal) platform string, then the UA.
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const src = nav.userAgentData?.platform || navigator.platform || navigator.userAgent || '';
  return /mac|iphone|ipad|ipod/i.test(src);
}

/** True on macOS / iOS — where the primary modifier is ⌘. */
export const isMac = detectMac();

/** The primary modifier label for this OS: "⌘" on Mac, "Ctrl" elsewhere. */
export const modKey = isMac ? '⌘' : 'Ctrl';

/**
 * A modifier chord for display: ``shortcut('K')`` → ``⌘K`` on Mac,
 * ``Ctrl+K`` on Windows/Linux (Mac convention omits the separator; the
 * rest use "+").  Pass a already-spaced sequence ("g h") verbatim via
 * the raw key — this only wraps a single mod-key combo.
 */
export function shortcut(key: string): string {
  return isMac ? `${modKey}${key}` : `${modKey}+${key}`;
}
