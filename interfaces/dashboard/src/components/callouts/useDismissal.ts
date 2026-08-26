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

/**
 * The MANY-occurrence form, and the only implementation.
 *
 * A group states one callout once and lists the trucks it is true of,
 * so the fold belongs to the group: a row is already one line and has
 * nothing to shrink to.  Collapsing therefore marks every occurrence's
 * id, and the group counts as collapsed only when they ALL are.
 *
 * That last part is the load-bearing half.  A fourth truck developing
 * the same condition tomorrow arrives with an id nobody has folded, so
 * the group re-opens instead of inheriting a decision made about three
 * other trucks — which is how a fold quietly becomes a mute button.
 *
 * No new preference key: `callout.collapsed` is already a map of ids,
 * and a group is a set of ids.  One store, one meaning.
 */
export function useGroupDismissal(key: string, ids: string[]): DismissalState {
  const { value: collapsedMap, setValue: setCollapsed } =
    usePreference('callout.collapsed');
  const behaviour = dismissBehaviour(key);
  const present = ids.filter(Boolean);
  // A stable dep: the array is rebuilt every render, the string is not.
  const fingerprint = present.join('\u0000');

  const close = useCallback(async (): Promise<boolean> => {
    if (behaviour !== 'collapse' || !fingerprint) return false;
    const now = Date.now();
    const next = { ...collapsedMap };
    for (const id of fingerprint.split('\u0000')) next[id] = now;
    setCollapsed(next);
    return true;
  }, [behaviour, fingerprint, collapsedMap, setCollapsed]);

  const expand = useCallback(() => {
    if (!fingerprint) return;
    const next = { ...collapsedMap };
    for (const id of fingerprint.split('\u0000')) delete next[id];
    setCollapsed(next);
  }, [fingerprint, collapsedMap, setCollapsed]);

  return {
    collapsed: present.length > 0 && present.every((id) => collapsedMap[id]),
    behaviour,
    close,
    expand,
  };
}

/** One occurrence — a group of one, so the two cannot drift apart. */
export function useDismissal(c: CalloutData): DismissalState {
  return useGroupDismissal(c.key, [c.callout_id ?? '']);
}
