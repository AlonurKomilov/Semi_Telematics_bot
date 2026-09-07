/**
 * The service worker — the only code that runs with the panel closed.
 *
 * Two jobs.  The toolbar icon opens the side panel (Chrome will not open
 * one without a user gesture, so "shows up on its own when you open
 * Google Maps" is not something an extension can do).  And it is the
 * one door through which a token enters: the dashboard's consent page
 * knocks with the panel's own one-time state, and only a scoped token
 * from a 4truck origin gets in — see connect.ts.
 *
 * It has a third now: it FETCHES for the overlay that draws vehicles on
 * google.com/maps.  A content script's own fetches carry the page's
 * origin, so it could not call the API even with the extension's host
 * permission; and handing it a bearer token would leave a live
 * credential inside a page we do not control.  The worker holds the
 * token, answers one question, and returns positions — never the token.
 */
import { apiJSON, getToken, setToken } from './api/client';
import { acceptConnectMessage, clearPending, getPending, isTrustedOrigin, statePending } from './connect';
import { OVERLAY_VEHICLES, toOverlayVehicles, type OverlayReply } from './features/maps-overlay/bridge';

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

/** The overlay's one question.  ``onMessage`` (not ``onMessageExternal``)
 *  because the sender is our own content script, not a web page. */
chrome.runtime.onMessage.addListener((msg: unknown, sender, sendResponse) => {
  const m = msg as { type?: unknown } | null;
  if (m?.type !== OVERLAY_VEHICLES) return false;
  // Only from a tab we injected into.  A message with no tab is not a
  // content script; nothing else in this extension sends this type.
  if (!sender.tab?.id) { sendResponse({ ok: false, reason: 'error', detail: 'no tab' } satisfies OverlayReply); return true; }
  (async () => {
    if (!(await getToken())) {
      // Signed out is not an error to shout about: an extension that
      // nags on a page the person did not open for it gets uninstalled.
      sendResponse({ ok: false, reason: 'signed-out' } satisfies OverlayReply);
      return;
    }
    try {
      const data = await apiJSON<{ features?: unknown[] }>('/map/vehicles');
      sendResponse({ ok: true, vehicles: toOverlayVehicles((data.features ?? []) as never) } satisfies OverlayReply);
    } catch (e) {
      sendResponse({ ok: false, reason: 'error', detail: e instanceof Error ? e.message : 'failed' } satisfies OverlayReply);
    }
  })();
  return true;                            // the response is async
});

chrome.runtime.onMessageExternal.addListener((msg: unknown, sender, sendResponse) => {
  (async () => {
    const pending = await getPending();
    const m = msg as { type?: unknown; state?: unknown } | null;
    if (m?.type === '4truck:ping') {
      // "Is the extension here, and did it open this page?" — answered
      // before the page asks the server to mint anything.
      sendResponse({ ok: isTrustedOrigin(sender.origin) && statePending(pending, m.state) });
      return;
    }
    const verdict = acceptConnectMessage(msg, sender.origin, pending);
    if (!verdict.ok) { sendResponse({ ok: false }); return; }
    await clearPending();                 // one state, one token
    await setToken(verdict.token);        // the panel sees storage change and opens
    sendResponse({ ok: true });
  })().catch(() => sendResponse({ ok: false }));
  return true;                            // the response is async
});
