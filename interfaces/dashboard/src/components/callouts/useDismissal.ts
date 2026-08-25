/**
 * Who has closed what — the two acts, kept apart.
 *
 * COLLAPSE is a display setting: the statement stays on screen as one
 * line, so nothing left the reader's view and the client owns it like
 * any other preference.
 *
 * DISMISS removes it, which is the act an owner may need to
 * reconstruct later.  The SERVER owns that key — this hook only reads
 * it and calls the endpoint, never writes it — because a preference
 * the client could write plus a separate audit call can disagree, and
 * a dismissal with no record is the gap the record exists to close.
 * If the endpoint fails, nothing is hidden: the callout stays.
 */
import { useCallback } from 'react';
import { apiJSON } from '../../api/client';
import { usePreference } from '../../preferences';
import { dismissBehaviour, type CalloutData } from './calloutCatalog';

export interface DismissalState {
  /** Hide entirely — this person dismissed it (server-recorded). */
  dismissed: boolean;
  /** Shrink to one line — still on screen. */
  collapsed: boolean;
  /** What the X offers here; 'none' means render no X. */
  behaviour: ReturnType<typeof dismissBehaviour>;
  /** Runs the X. Resolves false when a dismissal could not be recorded. */
  close: () => Promise<boolean>;
  /** Re-expand a collapsed callout (no record — nothing had left). */
  expand: () => void;
}

export function useDismissal(
  c: CalloutData,
  entity?: { type: string; id: string },
): DismissalState {
  const { value: collapsedMap, setValue: setCollapsed } =
    usePreference('callout.collapsed');
  const { value: dismissedMap } = usePreference('callout.dismissed');
  const id = c.callout_id ?? '';
  const behaviour = dismissBehaviour(c.key);

  const close = useCallback(async (): Promise<boolean> => {
    if (!id) return false;
    if (behaviour === 'collapse') {
      setCollapsed({ ...collapsedMap, [id]: Date.now() });
      return true;
    }
    if (behaviour !== 'remove') return false;
    try {
      // The server writes BOTH the trail entry and the preference; a
      // 5xx here means the dismissal was not recorded, so the callout
      // must stay visible rather than vanish unaccountably.
      await apiJSON('/callouts/dismiss', {
        method: 'POST',
        body: {
          callout_id: id,
          entity_type: entity?.type ?? '',
          entity_id: entity?.id ?? '',
        },
      });
      return true;
    } catch {
      return false;
    }
  }, [id, behaviour, collapsedMap, setCollapsed, entity]);

  const expand = useCallback(() => {
    if (!id) return;
    const next = { ...collapsedMap };
    delete next[id];
    setCollapsed(next);
  }, [id, collapsedMap, setCollapsed]);

  return {
    dismissed: Boolean(id && dismissedMap[id]),
    collapsed: Boolean(id && collapsedMap[id]),
    behaviour,
    close,
    expand,
  };
}
