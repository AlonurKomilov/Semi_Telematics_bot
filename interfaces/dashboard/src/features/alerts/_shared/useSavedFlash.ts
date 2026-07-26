/**
 * "Saved" confirmation for autosaving surfaces.
 *
 * The preferences page has no Save button — every toggle writes
 * immediately — but it only spoke up on FAILURE, so a successful save was
 * indistinguishable from nothing happening.  This gives the page one
 * transient "Saved" signal instead of a toast per checkbox (a matrix of
 * toggles would otherwise bury the screen in toasts).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const VISIBLE_MS = 2000;

export function useSavedFlash(): { saved: boolean; flashSaved: () => void } {
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flashSaved = useCallback(() => {
    setSaved(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setSaved(false), VISIBLE_MS);
  }, []);

  // Don't fire setState after unmount (a save can resolve as the user
  // navigates away).
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { saved, flashSaved };
}
