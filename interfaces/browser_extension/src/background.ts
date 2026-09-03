/**
 * The service worker — the only code that runs with the panel closed.
 *
 * Two jobs.  The toolbar icon opens the side panel (Chrome will not open
 * one without a user gesture, so "shows up on its own when you open
 * Google Maps" is not something an extension can do).  And it is the
 * one door through which a token enters: the dashboard's consent page
 * knocks with the panel's own one-time state, and only a scoped token
 * from a 4truck origin gets in — see connect.ts.
 */
import { setToken } from './api/client';
import { acceptConnectMessage, clearPending, getPending, isTrustedOrigin, statePending } from './connect';

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
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
