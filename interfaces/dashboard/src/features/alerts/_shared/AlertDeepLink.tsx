/**
 * ``?alertId=<id>`` → opens the incident drawer on that alert.
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
 */
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { apiJSON } from '../../../api/client';
import type { Alert } from '../../../types';
import { useAlertsSelection } from './AlertsSelectionContext';

export default function AlertDeepLink() {
  const [params, setParams] = useSearchParams();
  const { openDrillIn } = useAlertsSelection();
  const alertId = params.get('alertId');
  // One fetch per ARRIVAL — StrictMode double-invokes effects and
  // openDrillIn's identity churns on every drawer open/close, so a plain
  // dependency would refire.  The ref RESETS when the param clears, so
  // clicking the same bell row again later (a common move: close the
  // drawer, reopen the bell, click the same still-open alert) re-opens it
  // instead of silently doing nothing.
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!alertId) { handled.current = null; return; }
    if (handled.current === alertId) return;
    handled.current = alertId;
    let cancelled = false;
    (async () => {
      try {
        const r = await apiJSON<{ alert: Alert }>(
          `/alerts/by-id/${encodeURIComponent(alertId)}`);
        if (cancelled) return;
        openDrillIn(r.alert);
      } catch {
        // 404 covers "gone" and "not yours" alike (the endpoint doesn't
        // distinguish them on purpose).  Say what the user can act on.
        if (!cancelled) {
          toast.error('That alert is no longer available — it may have been resolved.');
        }
      } finally {
        // Drop the param either way: a reload shouldn't reopen the drawer,
        // and the URL shouldn't keep pointing at a dead alert.  `replace`
        // keeps it out of the back stack.
        if (!cancelled) {
          setParams((prev) => {
            const next = new URLSearchParams(prev);
            next.delete('alertId');
            return next;
          }, { replace: true });
        }
      }
    })();
    return () => { cancelled = true; };
  }, [alertId, openDrillIn, setParams]);

  return null;
}
