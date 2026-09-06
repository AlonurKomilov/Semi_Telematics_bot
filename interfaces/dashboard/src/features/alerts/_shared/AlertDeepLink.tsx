/**
 * ``?alertId=<id>`` ⇄ the open alert-details drawer, in BOTH directions.
 *
 * The notification bell used to send you to the bare board: you clicked
 * ONE alert and landed on a 2,500-row queue with no filter and no
 * highlight, so the record you cared about was gone.  This makes the click
 * land on the alert itself.
 *
 * It fetches the alert BY ID rather than looking it up in the loaded page,
 * because the board is windowed and filtered — and the alerts most worth
 * deep-linking (a chronic fault older than the window) are exactly the
 * ones missing from that list.  It also means the link survives a refresh
 * and can be shared with a colleague.
 *
 * Mounted inside the page's AlertsSelectionProvider (not the per-persona
 * layout) so it works for every role that can open the board.
 *
 * The reverse direction matters as much as the forward one: opening the
 * drawer WRITES ``?alertId``, so the address bar always names the record
 * on screen.  Without it the drawer was a dead end — you could be sent a
 * link to an alert but couldn't send one, and a refresh lost your place.
 */
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from '../../../lib/toast';
import { apiJSON } from '../../../api/client';
import type { Alert } from '../../../types';
import { useAlertsSelection } from './AlertsSelectionContext';

export default function AlertDeepLink() {
  const [params, setParams] = useSearchParams();
  const { openDrillIn, drillInAlert } = useAlertsSelection();
  const alertId = params.get('alertId');
  // One fetch per ARRIVAL — StrictMode double-invokes effects and
  // openDrillIn's identity churns on every drawer open/close, so a plain
  // dependency would refire.  The ref RESETS when the param clears, so
  // clicking the same bell row again later (a common move: close the
  // drawer, reopen the bell, click the same still-open alert) re-opens it
  // instead of silently doing nothing.
  const handled = useRef<string | null>(null);
  // The id currently being resolved, or null.  A request is abandoned ONLY
  // when a newer one supersedes it — deliberately NOT via an effect-cleanup
  // ``cancelled`` flag.  This effect's deps include ``openDrillIn`` (its
  // identity churns on every selection-context change) and ``setParams``
  // (churns on every URL change), so a cleanup-based abort let any
  // unrelated update kill an in-flight lookup.  That is exactly how a
  // shared link resolved to nothing: landing on a bare /alerts?alertId=N
  // triggers the filter hook's first-load write, the param moves, and the
  // fetch that was about to open the drawer was cancelled.
  const inflight = useRef<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    if (!alertId) { handled.current = null; return; }
    if (handled.current === alertId) return;
    handled.current = alertId;
    const mine = alertId;
    inflight.current = mine;
    (async () => {
      try {
        const r = await apiJSON<{ alert: Alert }>(
          `/alerts/by-id/${encodeURIComponent(mine)}`);
        if (!mounted.current || inflight.current !== mine) return;
        openDrillIn(r.alert);
      } catch {
        if (!mounted.current || inflight.current !== mine) return;
        // 404 covers "gone" and "not yours" alike (the endpoint doesn't
        // distinguish them on purpose).  Say what the user can act on.
        toast.error('That alert is no longer available — it may have been resolved.');
        // Only a FAILED lookup drops the param: the URL shouldn't keep
        // pointing at an alert that isn't there.  A successful one keeps
        // it, so the address bar names what's on screen and the link can
        // be shared or refreshed.  `replace` keeps it out of the back stack.
        setParams((prev) => {
          const next = new URLSearchParams(prev);
          next.delete('alertId');
          return next;
        }, { replace: true });
      } finally {
        if (inflight.current === mine) inflight.current = null;
      }
    })();
  }, [alertId, openDrillIn, setParams]);

  // Drawer → URL.  Opening from a row click (or closing from anywhere)
  // has to move the address bar too, or the two disagree about what's
  // open.  Writing the param re-runs the effect above, but `handled`
  // already holds this id, so it never refetches what we just opened.
  const openId = drillInAlert ? String(drillInAlert.id) : null;
  useEffect(() => {
    if (openId === alertId) return;      // already in sync
    if (inflight.current) return;        // an inbound link is mid-flight
    if (openId) handled.current = openId;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (openId) next.set('alertId', openId);
      else next.delete('alertId');
      return next;
    }, { replace: true });
  }, [openId, alertId, setParams]);

  return null;
}
