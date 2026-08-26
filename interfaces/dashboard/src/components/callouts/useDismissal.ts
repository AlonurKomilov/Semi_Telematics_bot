/**
 * Who has collapsed what.
 *
 * COLLAPSE is a display setting: the statement stays on screen as one
 * line, so nothing left the reader's view and the client owns it like
 * any other preference.
 *
 * There was a third act here — DISMISS, which removed a callout from
 * one person's view and wrote an audit entry so an owner could later
 * ask "did the system tell them, or did they close it?".  It is gone,
 * and the reason is worth keeping: no callout ever wanted it.  A
 * caveat qualifies a number so it must not be hideable; a condition
 * can come back so it collapses; and the dismissible-advice kind it
 * was built for was never created here.  It was also quietly broken —
 * the client never sent the rendered text or the undo flag the
 * endpoint documented as its whole justification, and the tests
 * called the endpoint directly, so eight green tests covered a road
 * with no on-ramp.
 *
 * If dismissible advice ever appears, that is the moment to bring it
 * back: build it CLIENT-first so the path is exercised end to end.
 * Note that answering a device question is NOT that act — a resolved
 * device event goes inactive account-wide, for everyone, and is
 * recorded against the truck in the activity trail.
 */
import { useCallback } from 'react';
import { usePreference } from '../../preferences';
import { dismissBehaviour, type CalloutData } from './calloutCatalog';

export interface DismissalState {
  /** Shrink to one line — still on screen. */
  collapsed: boolean;
  /** What the control offers here; 'none' means render none. */
  behaviour: ReturnType<typeof dismissBehaviour>;
  /** Runs the control. Resolves false when there was nothing to do. */
  close: () => Promise<boolean>;
  /** Re-expand a collapsed callout (no record — nothing had left). */
  expand: () => void;
}

export function useDismissal(c: CalloutData): DismissalState {
  const { value: collapsedMap, setValue: setCollapsed } =
    usePreference('callout.collapsed');
  const id = c.callout_id ?? '';
  const behaviour = dismissBehaviour(c.key);

  const close = useCallback(async (): Promise<boolean> => {
    if (!id) return false;
    if (behaviour !== 'collapse') return false;
    setCollapsed({ ...collapsedMap, [id]: Date.now() });
    return true;
  }, [id, behaviour, collapsedMap, setCollapsed]);

  const expand = useCallback(() => {
    if (!id) return;
    const next = { ...collapsedMap };
    delete next[id];
    setCollapsed(next);
  }, [id, collapsedMap, setCollapsed]);

  return {
    collapsed: Boolean(id && collapsedMap[id]),
    behaviour,
    close,
    expand,
  };
}
