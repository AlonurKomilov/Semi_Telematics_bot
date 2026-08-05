// ── The mobile navigation drawer ────────────────────────────────────
//
// One component, six shells.  This was thirteen BYTE-IDENTICAL lines
// copy-pasted into DefaultShell / Fleet / Safety / Dispatch / HR /
// Accounting — so every fix to the app's most-used mobile control had to
// be made six times, and a fix applied five times is a bug in the sixth.
//
// The copy it replaces was a bare `fixed inset-0 bg-black/50` with the
// sidebar inside it.  On a phone that covers the screen and makes the
// page behind unusable, which is the definition of a modal — but it had
// none of the behaviour:
//
//   • the page behind still SCROLLED under the open drawer, so a stray
//     finger-drag on the scrim moved the dashboard and you came back to
//     somewhere else entirely
//   • Escape did nothing
//   • Tab walked out of the nav into the covered page, so focus went to
//     controls no one could see
//   • no ``aria-modal``, so a screen reader announced the whole document
//     as still available and the nav as merely more content
//
// <Sheet> supplies all four, plus the slide-in the hand-rolled version
// never had.

import { useEffect } from 'react';

import Sidebar from '../Sidebar';
import { Sheet, SheetContent, SheetTitle } from '../ui/sheet';

/** Matches the `lg:` breakpoint the trigger button is gated on. */
const DESKTOP = '(min-width: 1024px)';

export default function MobileNavDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // The old version leaned on ``lg:hidden`` to disappear at desktop
  // width.  A Sheet cannot: it PORTALS and locks the page's scroll, so
  // hiding it with a class would leave a desktop user unable to scroll
  // with no visible cause.  Close it on the same breakpoint instead.
  useEffect(() => {
    if (!open || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia(DESKTOP);
    if (mq.matches) { onOpenChange(false); return; }
    const onChange = (e: MediaQueryListEvent) => { if (e.matches) onOpenChange(false); };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [open, onOpenChange]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        // The Sidebar brings its own surface (``bg-sidebar``), its own
        // width (``w-56``, or ``w-14`` collapsed) and its own padding, so
        // the sheet must contribute NONE of those or the nav sits on a
        // popover-coloured slab three-quarters of the screen wide.
        className="gap-0 border-0 bg-transparent p-0 shadow-none"
        // Width goes in INLINE STYLE, not a class.  SheetContent's own
        // width is written as data-attribute variants
        // (``data-[side=left]:w-3/4`` + the size map's
        // ``data-[side=left]:sm:max-w-sm``); tailwind-merge files those
        // under different keys than a plain ``w-auto`` and keeps BOTH, so
        // which one wins would come down to Tailwind's CSS emit order.
        // Inline style outranks every class — the same reason the scroll
        // region's contract is inline (components/scrolling/CLAUDE.md).
        style={{ width: 'auto', maxWidth: 'none' }}
        // The topbar's ☰ already toggles this, and the Sidebar has its
        // own chrome — a second ✕ floating over the nav is clutter.
        showCloseButton={false}
      >
        <SheetTitle className="sr-only">Navigation</SheetTitle>
        <Sidebar forceExpanded />
      </SheetContent>
    </Sheet>
  );
}
