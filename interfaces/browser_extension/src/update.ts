/**
 * Is this panel out of date, and whose job is it to fix that?
 *
 * TWO CHANNELS, TWO DIFFERENT ANSWERS, and conflating them is how a
 * notice becomes noise:
 *
 *   STORE    Chrome checks the Web Store on its own — roughly every few
 *            hours and at browser start — and installs a new version
 *            without asking.  There is nothing for the reader to DO, so
 *            a notice that demands action would be a lie, and one that
 *            appears for days while Google is still reviewing teaches
 *            people to ignore the strip that will one day matter.
 *
 *   SIDELOAD `Load unpacked` never updates itself.  Not slowly — never.
 *            The folder changes when a human replaces it, and the
 *            extension reloads when a human reloads it.  For this
 *            reader the notice is the ONLY signal that exists, which is
 *            why the sideload channel is kept alive at all: it is the
 *            escape hatch for a fix that cannot wait for a review.
 *
 * Telling them apart costs nothing.  `build_packages.py` strips `key`
 * from the store package (the store keeps the private half and refuses
 * a copy) and leaves it in the sideload one (so an unpacked copy
 * computes the same extension id).  That difference is already load-
 * bearing, so this reads it rather than inventing a second marker.
 *
 * If the reading is ever wrong, the WORDING changes and nothing else:
 * both messages name a real version and neither hides the download, so
 * a misread channel costs a clumsy sentence, never a dead end.
 */

export type Channel = 'store' | 'sideload';

/**
 * Compare two dotted version strings NUMERICALLY, segment by segment.
 *
 * THE OBVIOUS IMPLEMENTATION IS WRONG, and this project's own numbering
 * walks straight into it: `'0.5.19.0' > '0.5.9.0'` is FALSE as a string
 * comparison, because '1' sorts before '9'.  The panel would have gone
 * quiet at exactly the version where the third digit passed nine — a
 * bug that cannot appear in testing done the week the code is written,
 * and appears on its own months later.
 *
 * Missing segments are zero, so `0.5.19` and `0.5.19.0` are the same
 * version; a segment that is not a number counts as zero rather than
 * poisoning the comparison, because the alternative is a panel that
 * throws on a manifest it merely does not understand.
 */
export function compareVersions(a: string, b: string): number {
  const parts = (v: string) => String(v ?? '').split('.').map((p) => {
    const n = Number.parseInt(p, 10);
    return Number.isFinite(n) && n >= 0 ? n : 0;
  });
  const [x, y] = [parts(a), parts(b)];
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d !== 0) return d < 0 ? -1 : 1;
  }
  return 0;
}

/** Strictly newer — equal is not an update, and neither is older. */
export function isNewer(candidate: string, installed: string): boolean {
  if (!candidate || !installed) return false;
  return compareVersions(candidate, installed) > 0;
}

/** Which package this is, read off the manifest the browser loaded. */
export function channelOf(manifest: { key?: string } | null | undefined): Channel {
  return manifest?.key ? 'sideload' : 'store';
}

export interface Notice {
  version: string;
  channel: Channel;
}

/**
 * The notice to show, or null for silence.
 *
 * Dismissal is keyed to the VERSION dismissed, not to a boolean.  A
 * flag would silence every future version too: the reader closes one
 * strip and never hears about the release that fixes the thing they are
 * about to hit.
 */
export function noticeFor(args: {
  installed: string;
  latest: string;
  channel: Channel;
  dismissed?: string;
}): Notice | null {
  const { installed, latest, channel, dismissed } = args;
  if (!isNewer(latest, installed)) return null;
  // Dismissing 0.5.19.0 must not hide 0.5.20.0 — only versions at or
  // below what was dismissed stay quiet.
  if (dismissed && compareVersions(latest, dismissed) <= 0) return null;
  return { version: latest, channel };
}

/**
 * How long a version answer stays good enough to reuse.
 *
 * THE PANEL IS OPENED AND CLOSED ALL DAY — its own viewport cache says
 * so, and that is why this exists: asking the server on every open is
 * dozens of requests a day, per reader, for a number that changes about
 * once a week.  An hour is far finer than the release cadence and far
 * coarser than the panel's open/close rhythm.
 *
 * What the cache cannot do is keep nagging a reader who has UPDATED:
 * the notice is decided against the INSTALLED version, read fresh from
 * the manifest every time, so the strip goes as soon as they are
 * current, cache or no cache.
 *
 * What it CAN do, and the first draft of this comment wrongly claimed it
 * could not: advertise a version the server no longer has.  Pull a bad
 * build and restore the previous one, and every panel holding a cached
 * answer names the withdrawn version until its hour is up.  Bounded and
 * self-healing — the next check overwrites it, downwards included — but
 * real, and the operational cost of a rollback rather than a bug to fix
 * here.  Shortening the window would trade a rare hour of a wrong
 * number for dozens of requests a day, every day.
 */
export const CHECK_EVERY_MS = 60 * 60 * 1000;

/** Is the remembered answer too old to reuse? */
export function isCheckDue(lastAt: number, now: number): boolean {
  if (!Number.isFinite(lastAt) || lastAt <= 0) return true;
  // A clock that moved backwards (a laptop waking in a new timezone, a
  // corrected system time) must not wedge the check shut until the
  // future catches up.
  if (lastAt > now) return true;
  return now - lastAt >= CHECK_EVERY_MS;
}
