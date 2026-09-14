/**
 * Moving between pages, as two sounds.
 *
 * The other nine act cues are classified from a click, which is what
 * makes them impossible for a feature to get wrong. This one cannot be:
 * a route change is not always a click — the command palette navigates,
 * the browser's Back button navigates, and a redirect navigates with
 * nobody touching anything at all. So it is RAISED, from the one place
 * that sees every route change.
 *
 * The seam rule the axis follows: an act is delegated if one trusted
 * click carries both THAT it happened and WHICH act it was. Otherwise it
 * is raised. This is the second kind, and there are only four of them.
 */
import { useEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';
import { playActCue } from './cue';

/**
 * A page opening and a page closing.
 *
 * PUSH is forward — a link, a nav item, the palette. POP is backward —
 * Back, or a programmatic `navigate(-1)`. REPLACE is SILENT, and that
 * is the important one: `router.tsx` carries redirects, and a person
 * bounced from a page they may not see did not move anywhere. A sound
 * there would be the app announcing its own decision as if it were
 * theirs.
 *
 * Keyed on PATHNAME, never on the whole location. A grid writing its
 * segment into the query string is not a navigation, and this product
 * does that on nearly every table page.
 *
 * The FIRST render never sounds. `useNavigationType()` reports POP on a
 * cold load — the browser restoring a history entry — so a naive
 * version announces every page refresh as a page CLOSING, backwards and
 * unprompted. The ref also absorbs StrictMode's double-invoke: the
 * second pass sees the same pathname it just recorded.
 */
export function usePageCue(): void {
  const { pathname } = useLocation();
  const navType = useNavigationType();
  const seen = useRef<string | null>(null);

  useEffect(() => {
    const first = seen.current === null;
    const moved = seen.current !== pathname;
    seen.current = pathname;
    // Arriving, or re-rendering in place: a nav item that points where
    // you already are is not a move, and it is clicked often.
    if (first || !moved) return;
    if (navType === 'REPLACE') return;
    playActCue(navType === 'POP' ? 'page_close' : 'page_open');
  }, [pathname, navType]);
}
