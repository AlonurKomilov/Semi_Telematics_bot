/** The console's modal.
 *
 *  Two overlays were hand-rolled before this: a fixed backdrop, a
 *  centred box, and nothing else.  What "nothing else" means, for the
 *  operator who does not use a mouse:
 *
 *    - Tab walks OUT of the dialog and keeps going through the page
 *      underneath, which is still there and still focusable, so the
 *      caret ends up somewhere invisible behind a black overlay.
 *    - Escape does nothing; one of the two could only be dismissed by
 *      clicking the backdrop, which a keyboard cannot do.
 *    - No role or aria-modal, so a screen reader announces the page
 *      behind it as if the dialog were not there.
 *    - The page behind scrolls under the dialog on a wheel or an arrow.
 *
 *  None of that is exotic; it is what a dialog IS, and it is why this
 *  is a primitive rather than four lines copied into the next one.
 */

import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/** Everything the browser lets a person land on with Tab. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface Props {
  /** Named for the screen reader; also the visible heading. */
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** `lg` for forms with several fields, `md` for a question or two. */
  size?: 'md' | 'lg';
}

export function Dialog({ title, onClose, children, size = 'md' }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    // Where the focus came from, so it can go back there on close —
    // otherwise it restarts at the top of the document and the operator
    // has to find their place again.
    returnTo.current = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !panel.current) return;
      // The trap: Tab off either end wraps to the other, so focus can
      // never reach the page behind the backdrop.
      const stops = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null);
      if (stops.length === 0) return;
      const edge = e.shiftKey ? stops[0] : stops[stops.length - 1];
      if (document.activeElement === edge) {
        e.preventDefault();
        (e.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = previousOverflow;
      returnTo.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`bg-slate-900 border border-slate-700 rounded-lg w-full p-5 outline-none ${
          size === 'lg' ? 'max-w-lg' : 'max-w-md'}`}
      >
        {children}
      </div>
    </div>
  );
}
