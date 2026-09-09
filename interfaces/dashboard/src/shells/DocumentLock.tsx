/**
 * The dashboard document does not scroll. Nothing enforced that until
 * now, and twice in one day the whole app was found shifted up with the
 * header and the sidebar's logo row above the fold.
 *
 * The shell is a fixed viewport box — `h-screen`, `overflow-hidden` —
 * with its own scrollers inside it, so `document.scrollingElement`
 * should never move. But a scroll position is a property of the
 * DOCUMENT, not of a route: this app has no scroll restoration, so a
 * position picked up anywhere (a transient taller document during a
 * load, a horizontal scrollbar eating the visual viewport, an in-page
 * find, an anchor jump) survives every client-side navigation
 * afterwards. Once acquired it never comes back on its own, and the
 * header is gone until a reload.
 *
 * So the class this mounts makes the position impossible rather than
 * unlikely, and the reset below clears whatever was already there.
 * Scoped to the shell, not global: the public pages — apply, status,
 * the carrier intake — are ordinary documents that must scroll, and
 * they render no shell.
 *
 * This is what makes it safe to put anything in flow at the top of the
 * shell (the invite banner): inside the box it takes its own row and
 * the rest shrinks. Above the box it would grow the document — which is
 * the bug this file closes — and with the lock on it would simply be
 * clipped and unreachable.
 */
import { useEffect } from 'react';

export const DOCUMENT_LOCK_CLASS = 'shell-locked';

export function DocumentLock() {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add(DOCUMENT_LOCK_CLASS);
    // The class stops NEW scrolling; it does not undo a position the
    // document already holds. A shell that mounts at scrollTop 200 with
    // overflow hidden stays there, header off-screen, with no way back.
    window.scrollTo(0, 0);
    return () => root.classList.remove(DOCUMENT_LOCK_CLASS);
  }, []);
  return null;
}
