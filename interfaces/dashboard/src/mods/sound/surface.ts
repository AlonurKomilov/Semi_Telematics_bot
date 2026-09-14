/**
 * A dialog or a sheet arriving, and leaving.
 *
 * The other act cues are classified from a click, which is what makes
 * them impossible for a feature to get wrong. A surface cannot be: code
 * opens dialogs, Escape closes them, and a backdrop press is not a
 * click on anything this product owns. So it is RAISED, from the two
 * wrappers every Dialog and Sheet in the product already goes through.
 *
 * WHICH SIGNAL, AND WHY IT IS NOT THE OBVIOUS ONE.
 *
 * `onOpenChange` looks like the answer and is half of one. Base UI calls
 * it from `DialogStore.setOpen` only — `dialog/store/DialogStore.js` —
 * which runs on INTERACTIONS: a trigger, the close button, Escape, a
 * dismissal. When a parent changes the `open` PROP instead, `DialogRoot`
 * syncs the store through `useControlledProp` and `setOpen` never runs,
 * so `onOpenChange` never fires.
 *
 * Every Dialog and Sheet in this product today is controlled — 46 of
 * them, and not one `<DialogTrigger>` — so a wrapper built on the
 * callback alone would have been silent everywhere, and green, because
 * nothing tests a sound that does not play.
 *
 * So: ONE detector per surface, chosen by which kind it is. A controlled
 * surface is read from its prop and its callback is left alone; an
 * uncontrolled one is read from its callback. Never both, because a
 * controlled surface closed by its own X button fires the callback AND
 * changes the prop, and two detectors would make one act two sounds.
 */
import { useEffect, useRef } from 'react';
import { playActCue } from './cue';

/** The shape both Base UI roots share, narrowed to what is read. */
interface SurfaceProps {
  open?: boolean;
  onOpenChange?: (open: boolean, ...rest: never[]) => void;
}

export function useSurfaceCue<P extends SurfaceProps>(props: P): P {
  const controlled = props.open !== undefined;
  const open = props.open;
  const seen = useRef<boolean | null>(null);

  useEffect(() => {
    if (!controlled) return;
    const was = seen.current;
    seen.current = open ?? false;
    // A surface that MOUNTS already open did open — several call sites
    // render the root only while it is up. One that mounts closed did
    // nothing, and that is the common case and must stay silent.
    if (was === null) { if (open) playActCue('surface_open'); return; }
    if (was === open) return;
    playActCue(open ? 'surface_open' : 'surface_close');
  }, [controlled, open]);

  if (controlled) return props;
  // Uncontrolled: no prop to watch, so the callback is the only signal.
  // Not dead code — `DialogTrigger` is exported and typed, and the day
  // somebody uses one this is the branch that speaks for it.
  return {
    ...props,
    onOpenChange: (next: boolean, ...rest: never[]) => {
      playActCue(next ? 'surface_open' : 'surface_close');
      props.onOpenChange?.(next, ...rest);
    },
  };
}
