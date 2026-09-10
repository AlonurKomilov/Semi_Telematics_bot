/**
 * A colour input reports every FRAME of a drag; a look is written once.
 *
 * `onChange` on an `<input type="color">` is the `input` event, so
 * dragging across the picker fired a write per frame — and a write here
 * is not cheap: `setTheme` stores the theme, publishes the account-wide
 * appearance blob, and the provider re-derives the whole palette, four
 * per-place palettes and every ground. Dozens of times a second, on the
 * one interaction that has to feel smooth.
 *
 * Worse than the cost was the result. Frames the gate refused were
 * dropped and frames it accepted were kept, so releasing on a colour
 * that breaks a tone left a DIFFERENT colour painted — the last one
 * that happened to pass — while the note talked about the one you
 * stopped on.
 *
 * So: `input` previews, `change` commits. The preview drives the dot
 * and the note, which is where a person learns "this one would break
 * the warning colour" while still dragging; exactly one value is ever
 * written, and it is the one they stopped at. `blur` commits too, for
 * the browser that closes its picker without a `change`.
 */
import { useEffect, useRef, useState } from 'react';

export function useColorProbe(shown: string, commit: (hex: string) => void) {
  const ref = useRef<HTMLInputElement>(null);
  /** What the pointer is over right now, or null when nothing is being
   *  dragged and the stored value is the truth. */
  const [probe, setProbe] = useState<string | null>(null);
  const latest = useRef(commit);
  latest.current = commit;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const preview = () => setProbe(el.value);
    const done = () => {
      const picked = el.value;
      setProbe(null);
      // Idempotent: `change` and `blur` both fire in some browsers, and
      // a second write of the value already stored is a store write, a
      // publish and a palette derivation for nothing.
      if (picked.toLowerCase() !== shown.toLowerCase()) latest.current(picked);
    };
    el.addEventListener('input', preview);
    el.addEventListener('change', done);
    el.addEventListener('blur', done);
    return () => {
      el.removeEventListener('input', preview);
      el.removeEventListener('change', done);
      el.removeEventListener('blur', done);
    };
  }, [shown]);

  // Uncontrolled on purpose — a controlled `value` fights the native
  // picker mid-drag — so the stored value is pushed in whenever it
  // changes underneath and nothing is being dragged.
  useEffect(() => {
    const el = ref.current;
    if (el && probe === null && el.value !== shown) el.value = shown;
  }, [shown, probe]);

  return { ref, probe };
}
