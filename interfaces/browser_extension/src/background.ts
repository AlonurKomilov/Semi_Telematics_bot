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
 *
 * Being the one door is also what lets it answer ONCE for every tab
 * that asks.  The overlay runs per tab and polls on its own clock, so
 * three route tabs were three times the traffic for one identical
 * answer; dedupe.ts turns that back into one request.
 */
import { apiJSON, getToken, setToken } from './api/client';
import { acceptConnectMessage, clearPending, getPending, isTrustedOrigin, statePending } from './connect';
import { ACTIVE_FEATURE_KEY, CARD_ITEMS_MAX, OPEN_PANEL, OVERLAY_INVENTORY, OVERLAY_LIVE, OVERLAY_VEHICLES, PANEL_LIVE, toOverlayFixes, toOverlayVehicles, type InventoryCounts, type InventoryReply, type LiveReply, type OverlayReply, type PanelLiveReply } from './features/maps-overlay/bridge';
import { makeShared } from './features/maps-overlay/dedupe';
import { DASHBOARD_BASE } from './connect';
import type { LiveVehiclesResponse } from './features/live-map/types';

/** Both windows sit just under the poll they serve, so one tab alone
 *  keeps exactly the cadence it had; they exist for the second tab.
 *  Each ask carries the token it is asking with, so an answer can never
 *  outlive the connection that earned it — see dedupe.ts. */
const sharedLive = makeShared<LiveVehiclesResponse>(4_000);
const sharedList = makeShared<{ features?: unknown[] }>(25_000);
/** Its own window: the counts and the truck list are different answers
 *  with different shapes, and one shared cache cannot hold both. */
type FleetCounts = { vehicles?: { vehicle_id: number; total: number; attention: number }[] };
const sharedInventory = makeShared<FleetCounts>(25_000);

/** The one fetcher both askers share, typed as the API really answers.
 *  The panel is handed this payload whole and reads each reading's AGE
 *  from it, so the shape is a contract between the two — declared once
 *  here, where the compiler can hold both sides to it, rather than
 *  written out at each call and trusted to stay the same. */
const askLive = () => apiJSON<LiveVehiclesResponse>('/map/vehicles/live');

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

/** The overlay's one question.  ``onMessage`` (not ``onMessageExternal``)
 *  because the sender is our own content script, not a web page. */
chrome.runtime.onMessage.addListener((msg: unknown, sender, sendResponse) => {
  const m = msg as { type?: unknown } | null;
  if (m?.type === OPEN_PANEL) {
    // Opening a side panel needs a USER GESTURE, and a gesture does not
    // reliably survive the hop from a content-script click through
    // `sendMessage` to here — Chrome refuses, the promise rejects, and
    // the old `.catch(() => {})` swallowed it.  The owner pressed the
    // card's button and watched nothing happen but the map redraw, and
    // there was no way to tell why, because the only record of the
    // refusal was discarded.
    //
    // So: the press always does SOMETHING.  The panel is the better
    // answer — it sits beside the map, which is this product's whole
    // idea — and a tab is the honest fallback when Chrome will not
    // open it.
    const tabId = sender.tab?.id;
    (async () => {
      if (tabId !== undefined) {
        try {
          await chrome.sidePanel.open({ tabId });
          sendResponse({ ok: true, opened: 'panel' });
          return;
        } catch { /* fall through to the tab */ }
      }
      try {
        await chrome.tabs.create({ url: `${DASHBOARD_BASE}/inventory` });
        sendResponse({ ok: true, opened: 'tab' });
      } catch {
        sendResponse({ ok: false, opened: 'nothing' });
      }
    })();
    return true;
  }
  if (m?.type === PANEL_LIVE) {
    // The panel's own poll, answered from the same memory the overlay's
    // is — so two open surfaces cost one request, not two.
    (async () => {
      const token = await getToken();
      if (!token) { sendResponse({ ok: false } satisfies PanelLiveReply<never> ); return; }
      try {
        const wire = await sharedLive('live', token, askLive);
        sendResponse({ ok: true, wire } satisfies PanelLiveReply<LiveVehiclesResponse>);
      } catch {
        // The panel falls back to asking the API itself; saying so is
        // cheaper than making it wait for a retry here.
        sendResponse({ ok: false } satisfies PanelLiveReply<never>);
      }
    })();
    return true;
  }
  if (m?.type === OVERLAY_LIVE) {
    (async () => {
      const token = await getToken();
      if (!token) { sendResponse({ ok: false } satisfies LiveReply); return; }
      try {
        const data = await sharedLive('live', token, askLive);
        sendResponse({ ok: true, fixes: toOverlayFixes(data) } satisfies LiveReply);
      } catch {
        // The fast poll stays quiet, exactly as the panel's does: the
        // thirty-second one is what surfaces a real outage.
        sendResponse({ ok: false } satisfies LiveReply);
      }
    })();
    return true;
  }
  if (m?.type === OVERLAY_INVENTORY) {
    // One truck, asked for.  The page hands us the MAP's id; the
    // registry id it resolves to stays here, because that is the key
    // every scope decision in this product turns on.
    if (!sender.tab?.id) { sendResponse({ ok: false } satisfies InventoryReply); return true; }
    (async () => {
      try {
        const token = await getToken();
        if (!token) { sendResponse({ ok: false } satisfies InventoryReply); return; }
        // The PAGE already decided this — it only asks while its panel
        // is on Inventory.  Re-reading the key here decided it a second
        // time, from a different moment, and a switch landing between
        // the two dropped the answer with nothing said.  It was never a
        // wall either: /extension/inventory is permission-gated on the
        // server, which is where a wall belongs.
        const list = await sharedList('list', token,
          () => apiJSON<{ features?: unknown[] }>('/map/vehicles'));
        const rid = registryIdFor(list.features ?? [], String((m as { id?: unknown }).id ?? ''));
        if (rid == null) { sendResponse({ ok: false } satisfies InventoryReply); return; }
        const out = await apiJSON<{ items?: { label?: unknown; status?: unknown }[] }>(
          `/extension/inventory?vehicle=${encodeURIComponent(String(rid))}`);
        const all = out.items ?? [];
        sendResponse({
          ok: true,
          // Names and states.  NOT the identifier — the serial is what
          // makes a loss provable and it has no business on somebody
          // else's page, however narrow the moment.
          items: all.slice(0, CARD_ITEMS_MAX).map((i) => ({
            label: String(i.label ?? ''), status: String(i.status ?? ''),
          })),
          more: Math.max(0, all.length - CARD_ITEMS_MAX),
        } satisfies InventoryReply);
      } catch {
        sendResponse({ ok: false } satisfies InventoryReply);
      }
    })();
    return true;
  }
  if (m?.type !== OVERLAY_VEHICLES) return false;
  // Only from a tab we injected into.  A message with no tab is not a
  // content script; nothing else in this extension sends this type.
  if (!sender.tab?.id) { sendResponse({ ok: false, reason: 'error', detail: 'no tab' } satisfies OverlayReply); return true; }
  (async () => {
    const token = await getToken();
    if (!token) {
      // Signed out is not an error to shout about: an extension that
      // nags on a page the person did not open for it gets uninstalled.
      sendResponse({ ok: false, reason: 'signed-out' } satisfies OverlayReply);
      return;
    }
    try {
      const data = await sharedList('list', token,
        () => apiJSON<{ features?: unknown[] }>('/map/vehicles'));
      sendResponse({
        ok: true,
        vehicles: toOverlayVehicles((data.features ?? []) as never, await inventoryCounts(token)),
      } satisfies OverlayReply);
    } catch (e) {
      sendResponse({ ok: false, reason: 'error', detail: e instanceof Error ? e.message : 'failed' } satisfies OverlayReply);
    }
  })();
  return true;                            // the response is async
});

/** What is aboard each truck — asked ONLY while the panel is showing
 *  Inventory.  On Live Map this costs nothing and sends nothing: a page
 *  we do not own has no business carrying our inventory when nobody is
 *  looking at inventory.
 *
 *  Shared with the list's own de-duplication, so thirty tabs on
 *  google.com/maps ask once. */
async function inventoryCounts(token: string): Promise<InventoryCounts | undefined> {
  try {
    const got = await chrome.storage.local.get(ACTIVE_FEATURE_KEY);
    if (got[ACTIVE_FEATURE_KEY] !== 'inventory') return undefined;
    // ?all=1 — EVERY vehicle, carrying or not.  A zero is an answer on
    // this feature: somebody who switched to Inventory and clicked a
    // truck is owed "nothing recorded", not silence they have to read
    // as either "empty" or "still loading".  It reaches google.com only
    // while the panel is on Inventory, which is the whole guard: on
    // Live Map this function returns before it asks anything.
    const out = await sharedInventory('inventory', token,
      () => apiJSON<FleetCounts>('/extension/inventory-fleet?all=1'));
    const counts: InventoryCounts = new Map();
    for (const v of out.vehicles ?? []) {
      counts.set(Number(v.vehicle_id), {
        total: Number(v.total) || 0,
        attention: Number(v.attention) || 0,
      });
    }
    return counts;
  } catch {
    // A truck list that arrives without its counts is still a truck
    // list.  Losing the markers to a slow inventory read would be a
    // strange way to answer "where are my trucks".
    return undefined;
  }
}

/** The map id the page knows → the registry id we key on.  Kept here so
 *  the translation, and the registry id, stay out of the page. */
function registryIdFor(features: unknown[], id: string): number | null {
  for (const f of features) {
    const p = (f as { properties?: Record<string, unknown> })?.properties ?? {};
    if (String(p.id ?? p.name ?? '') !== id) continue;
    const rid = Number(p.registry_id);
    return Number.isFinite(rid) ? rid : null;
  }
  return null;
}

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
